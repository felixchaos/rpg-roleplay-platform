"""
command_dispatcher.py — task 87: 统一命令工具调用分发器。

设计要点 (用户反馈 / 可行性评估报告):
> 审查所有游戏接口,将所有的指令接口都做成工具调用接口,创建统一的队列机制,
> 确保工具调用可分账号、分存档、分剧本。

四件套:
  · ToolSpec      — 单个工具的元数据 (name/schema/executor/scope/origins/destructive)
  · ToolRegistry  — 进程内注册表,按 name 查工具,按 origin 过滤可用工具
  · ToolCallEnvelope — 单条调用请求,带 user/save/script 作用域与 trace 元数据
  · ToolDispatcher — 鉴权 / 作用域 / origin / 限流 / 锁 / 审计 / 执行

作用域语义:
  global  : 任意 user 可调,无锁 (例: list_models)
  user    : 限当前 user_id (例: list_my_saves, set_preference)
  script  : 限当前 user 在指定 script_id 上 (例: get_chapter_facts)
  save    : 限当前 user 在指定 save_id 上,持 (user_id, save_id) 锁 (例: set_world_time)

origin 白名单:
  llm_chat   : GM 流式响应中调用的工具 (写入受限)
  llm_set    : /set 命令解析出的工具 (command_agent)
  ui_button  : 前端按钮直触 (全开)
  mcp_call   : 通过 /api/mcp/tool/call 进来 (受限)
  api_direct : 直接调老 HTTP endpoint 兼容路径

队列:
  per (user_id, save_id) FIFO asyncio.Queue,同 save 串行执行避免竞争。
  global per-user 限流: 每秒最多 N 次工具调用 (防止 LLM 失控)。
  trace_id depth ≤ 3: 防止 LLM 链式调用堆栈无限增长。

审计:
  每次调用写到 state.permissions.audit_log + 进程内 _recent_audit 滚动缓冲。
"""
from __future__ import annotations

import asyncio
import queue as _queue
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# ────────────────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────────────────


Scope = Literal["global", "user", "script", "save"]
Origin = Literal[
    "llm_chat", "llm_chat_json_op", "llm_set", "ui_button", "mcp_call", "api_direct",
    # task 48: 侧栏控制台助手 — 独立 origin，子集介于 ui_button 与 llm_chat 之间。
    # 拥有 user 级 mutate 工具(activate/rename/create_*)和 destructive 工具(需二次确认),
    # 但不能 inject_pending_question / set_permission_mode / approve_pending_write 这些 UI-only 工具。
    "console_assistant",
    # sprint 5: 黑天鹅子代理 — post-GM hook 主动触发世界事件。
    # 受 validator 管线约束,不能调 destructive 工具。
    "autonomous_agent",
]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    executor: Callable[..., str | dict[str, Any]]
    scope: Scope = "save"
    origins: frozenset[str] = frozenset({"ui_button", "api_direct", "llm_set"})
    destructive: bool = False  # delete_* / 重置类操作,LLM 不能调
    # task 74: 统一 UI 机制 — 用于 ui_describe 工具的意图匹配 + 状态变更广播。
    # intent_keywords: 用户口语意图关键词 (供 ui_describe 模糊匹配,不必穷举,几个核心词即可)。
    # side_effect_topics: 工具成功后要广播的 state-event topic (用于 SSE → 前端订阅 → 各页面自动刷新)。
    intent_keywords: tuple[str, ...] = ()
    side_effect_topics: tuple[str, ...] = ()
    # task 98: Anthropic 2025-11 advanced tool use — input_examples 给 LLM 看几个
    # 具体调用样本,72% → 90% 准确率 (官方实验). 每个 example 是 args dict。
    input_examples: tuple[dict[str, Any], ...] = ()

    def to_anthropic_tool(self) -> dict[str, Any]:
        """转换为 Anthropic tool_use schema。
        examples 注入到 description 末尾, 兼容所有 backend (Anthropic 原生支持
        input_examples 字段;Gemini/OpenAI 没有但能从 description 学。
        )"""
        desc = self.description
        if self.input_examples:
            import json as _json
            lines = ["", "示例调用:"]
            for ex in self.input_examples[:3]:
                lines.append(f"  {_json.dumps(ex, ensure_ascii=False)}")
            desc = desc + "\n" + "\n".join(lines)
        out: dict[str, Any] = {
            "name": self.name,
            "description": desc,
            "input_schema": self.input_schema,
        }
        # Anthropic 原生 input_examples 字段也带上 (其他 backend 会忽略不报错)
        if self.input_examples:
            out["input_examples"] = list(self.input_examples)
        return out


@dataclass
class ToolCallEnvelope:
    user_id: int
    tool: str
    args: dict[str, Any]
    origin: str
    save_id: int | None = None
    script_id: int | None = None
    trace_id: str = ""
    depth: int = 0
    call_id: str = field(default_factory=lambda: secrets.token_urlsafe(8))
    ts: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class ToolResult:
    ok: bool
    # task 109b: result 现在可以是 str (正常工具) 或 dict (UI action 工具, 含
    # __ui_action__ 字段, 由 console_assistant 主循环识别后转 SSE event)
    result: Any = ""
    error: str | None = None
    audit: dict[str, Any] | None = None


# ────────────────────────────────────────────────────────────
# 注册器
# ────────────────────────────────────────────────────────────


class ToolRegistry:
    """进程内单例。按 name 索引;按 origin 过滤暴露给特定调用方的子表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具 {spec.name!r} 已注册")
        self._tools[spec.name] = spec

    def replace(self, spec: ToolSpec) -> None:
        """用于测试/热更新,允许覆盖已有工具。生产代码用 register。"""
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_for_origin(self, origin: str) -> list[ToolSpec]:
        """返回当前 origin 可见的工具子表 (用于 LLM prompt 注入)。"""
        return [s for s in self._tools.values() if origin in s.origins]

    def list_all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def clear(self) -> None:
        """仅供测试用。"""
        self._tools.clear()


# 进程内默认注册表 (单例)
_DEFAULT_REGISTRY = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _DEFAULT_REGISTRY


# ────────────────────────────────────────────────────────────
# 异常
# ────────────────────────────────────────────────────────────


class DispatchError(Exception):
    """Dispatcher 拒绝执行的明确原因。包装成 ToolResult.error 返回给调用方。"""

    def __init__(self, kind: str, detail: str):
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


# ────────────────────────────────────────────────────────────
# 分发器
# ────────────────────────────────────────────────────────────


MAX_TRACE_DEPTH = 3
MAX_CALLS_PER_USER_PER_SECOND = 20
# _trace_seen 去重表 LRU 上限:只需覆盖在途 trace(单回合内完成),1024 远超任何并发量
MAX_TRACE_SEEN = 1024
AUDIT_LOG_LIMIT = 200
RECENT_AUDIT_LIMIT = 1000

# SEC(H-14/H-15): dispatcher 多为「每请求新建实例」(chat_pipeline/chat_tool_router/apply_ops/
# black_swan_agent/ui_dispatch_helper),所以实例级限流 bucket / asyncio.Lock 都形同虚设
# (跨请求/跨实例不共享)。下面是进程级全局结构:限流回退用全局 bucket(优先 Redis 跨 worker),
# 同步写路径用全局 per-(user,save) RLock 串行化同一存档的并发回合(双 tab / 重叠 SSE)。
_GLOBAL_RATE_BUCKETS: dict[int, list[float]] = {}
_GLOBAL_RATE_LOCK = threading.Lock()
_SYNC_SCOPE_LOCKS: dict[tuple[int, int | None], threading.RLock] = {}
_SYNC_LOCKS_GUARD = threading.Lock()


def _get_sync_scope_lock(key: tuple[int, int | None]) -> threading.RLock:
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_SCOPE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SYNC_SCOPE_LOCKS[key] = lock
        return lock


# JSON-mode fallback 模型(无 native function calling 的中转/廉价模型)常把 integer 参数
# 传成玩家原话字符串(生产实锤:advance_story_progress to_chapter="第30章"/"36"/"３０")。
# schema 驱动的确定性纠偏:只对 input_schema 声明 type=integer 的顶层参数、且字符串里
# 恰好含唯一一个数字组时提取;0 组或 ≥2 组(如"从第3章到第5章")保持原值让 executor
# 按原语义报错 —— 宁漏勿误。
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_INT_GROUP_RE = re.compile(r"-?\d+")
# 工具失败结果串的两种既有惯例:"失败: ..."(裸前缀)与 "<tool_name> 失败: ..."。
# 老检测只认裸前缀,后者被记成 ok=True → 前端把失败计进「设定已更新」。
_RESULT_FAILURE_RE = re.compile(r"^(?:失败|ERROR|拒绝)|^\S{1,64}\s失败\s*[::]")


def _coerce_declared_integers(spec: ToolSpec, args: dict[str, Any]) -> None:
    """按 spec.input_schema 对 args 做保守的整数纠偏(原地修改)。"""
    props = (spec.input_schema or {}).get("properties") or {}
    for key, prop in props.items():
        if not isinstance(prop, dict) or prop.get("type") != "integer":
            continue
        v = args.get(key)
        if v is None or isinstance(v, bool) or isinstance(v, int):
            continue
        if isinstance(v, float):
            if v.is_integer():
                args[key] = int(v)
            continue
        if isinstance(v, str):
            groups = _INT_GROUP_RE.findall(v.strip().translate(_FULLWIDTH_DIGITS))
            if len(groups) == 1:
                args[key] = int(groups[0])


class ToolDispatcher:
    """中央分发器。所有工具调用必须通过它。

    用法:
        dispatcher = ToolDispatcher(registry, state_provider)
        result = await dispatcher.dispatch(envelope)

    state_provider(envelope) -> GameState 或 None。Dispatcher 不直接持有 GameState,
    交给外层(app.py)按 user_id/save_id 注入。如果作用域是 global/user 不需要 state,
    返回 None 不算错。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        state_provider: Callable[[ToolCallEnvelope], Any] | None = None,
        authorize: Callable[[int], bool] | None = None,
    ) -> None:
        self._registry = registry
        self._state_provider = state_provider or (lambda env: None)
        self._authorize = authorize or (lambda uid: True)
        # 队列与锁: key = (user_id, save_id) 或 (user_id, None)
        self._locks: dict[tuple[int, int | None], asyncio.Lock] = {}
        # 限流: per user_id 最近 1 秒内调用数
        self._rate_buckets: dict[int, list[float]] = {}
        # trace 内去重: trace_id → set of (tool, args_json)
        # LRU 有界:trace_id 按回合唯一且永不主动清理,plain dict 会随累计回合数无限增长
        # (dispatcher 是进程级单例 → 真实内存泄漏)。用 OrderedDict + MAX_TRACE_SEEN 上限,
        # 只保留最近 N 个 trace 的去重集(远超任何在途 trace —— 一个 trace 在单回合内完成)。
        self._trace_seen: OrderedDict[str, set[tuple[str, str]]] = OrderedDict()
        # 滚动审计缓冲: 按 user_id 分桶, 防止单例化后跨用户信息泄漏
        self._recent_audit: dict[int, list[dict[str, Any]]] = {}

    # ── 公共 API ───────────────────────────────────────────

    async def dispatch(self, env: ToolCallEnvelope) -> ToolResult:
        """主入口。"""
        try:
            spec = self._validate(env)
        except DispatchError as exc:
            return self._reject(env, exc)

        # 锁: save 级用 (user_id, save_id), user 级用 (user_id, None), global 不锁
        if spec.scope in ("save", "script", "user"):
            lock_key = (env.user_id, env.save_id if spec.scope == "save" else None)
            lock = self._locks.setdefault(lock_key, asyncio.Lock())
            async with lock:
                return self._execute(env, spec)
        return self._execute(env, spec)

    def dispatch_sync(self, env: ToolCallEnvelope) -> ToolResult:
        """同步入口,给非 async 调用方用 (chat handler 现在是 sync streaming)."""
        try:
            spec = self._validate(env)
        except DispatchError as exc:
            return self._reject(env, exc)
        # SEC(H-15): 同步写路径过去无锁 → 同一存档两并发回合(双 tab/重叠 SSE)read-modify-write
        # 互覆盖。用进程级全局 per-(user,save) RLock 串行化(advisory lock 只串 DB 提交,不护内存态)。
        if spec.scope in ("save", "script", "user"):
            lock_key = (env.user_id, env.save_id if spec.scope == "save" else None)
            with _get_sync_scope_lock(lock_key):
                return self._execute(env, spec)
        return self._execute(env, spec)

    def recent_audit(self, limit: int = 50, user_id: int | None = None) -> list[dict[str, Any]]:
        """返回最近的审计记录。

        user_id 参数: admin 视图不传 (返回所有用户最近记录); 普通视图必须传 user_id
        以确保只返回该用户自身的记录 (防止跨用户泄漏)。
        """
        if user_id is not None:
            bucket = self._recent_audit.get(int(user_id)) or []
            return list(bucket[-limit:])
        # admin 视图: 合并所有用户桶, 按 ts 排序后取最近 limit 条
        all_entries: list[dict[str, Any]] = []
        for bucket in self._recent_audit.values():
            all_entries.extend(bucket)
        all_entries.sort(key=lambda e: e.get("ts", ""))
        return all_entries[-limit:]

    # ── 内部步骤 ───────────────────────────────────────────

    def _validate(self, env: ToolCallEnvelope) -> ToolSpec:
        # 1) 鉴权
        if not self._authorize(env.user_id):
            raise DispatchError("auth_failed",
                                f"user_id={env.user_id} 未通过鉴权")
        # 2) 工具是否存在
        spec = self._registry.get(env.tool)
        if spec is None:
            raise DispatchError("unknown_tool", f"未注册工具: {env.tool}")
        # 3) origin 白名单
        if env.origin not in spec.origins:
            raise DispatchError(
                "origin_forbidden",
                f"工具 {env.tool} 不允许从 origin={env.origin} 调用 "
                f"(允许: {sorted(spec.origins)})",
            )
        # 4) save 级工具必须带 save_id
        if spec.scope == "save" and env.save_id is None:
            raise DispatchError(
                "scope_missing_save",
                f"save 级工具 {env.tool} 必须带 save_id",
            )
        # 5) script 级工具必须带 script_id (允许从 save 派生)
        if spec.scope == "script" and env.script_id is None and env.save_id is None:
            raise DispatchError(
                "scope_missing_script",
                f"script 级工具 {env.tool} 必须带 script_id 或 save_id",
            )
        # 6) 递归深度
        if env.depth > MAX_TRACE_DEPTH:
            raise DispatchError(
                "depth_exceeded",
                f"trace 深度 {env.depth} 超过上限 {MAX_TRACE_DEPTH} (防递归死锁)",
            )
        # 7) 限流: per-user 每秒上限
        if not self._rate_ok(env.user_id):
            raise DispatchError(
                "rate_limited",
                f"user_id={env.user_id} 每秒工具调用数超 {MAX_CALLS_PER_USER_PER_SECOND}",
            )
        # task 97: 8) required 字段检查 — 字段完全不存在 (None) 时返友好错误,
        # LLM 读了自己调 ask_user_choice。
        # 注意: 字段存在但为空字符串/空列表时不在此处拦截 — 让 tool handler 自己
        # 产生专属错误消息 (e.g. "server_id 为空"),测试可直接断言 r.result。
        required = (spec.input_schema or {}).get("required") or []
        missing = []
        for fld in required:
            v = env.args.get(fld)
            if v is None:
                missing.append(fld)
        if missing:
            # 给出每个缺失字段的类型提示,方便模型自我纠正
            props = (spec.input_schema or {}).get("properties") or {}
            hints = []
            for fld in missing:
                p = props.get(fld) or {}
                ftype = p.get("type") or "any"
                fdesc = p.get("description") or ""
                hint = f"{fld}({ftype})"
                if fdesc:
                    hint += f": {fdesc}"
                hints.append(hint)
            # 附带模型实际传入的字段名,帮助模型发现拼写/命名错误
            actual_keys = sorted(env.args.keys()) if env.args else []
            actual_hint = f"。你传入的字段是 {actual_keys}" if actual_keys else ""
            # 当模型传入了与缺失字段名相似的字段时,显式提示正确字段名
            typo_hint = ""
            if actual_keys and missing:
                for m in missing:
                    # 简单相似度:首字母相同 or 编辑距离近的字段
                    candidates = [k for k in actual_keys if k != m and (
                        k[0] == m[0] or abs(len(k) - len(m)) <= 2
                    )]
                    if candidates:
                        typo_hint = f"。注意:你可能误用了 {candidates},正确字段名是 '{m}'"
                        break
            raise DispatchError(
                "missing_required",
                f"工具 {env.tool} 缺必填字段: {', '.join(hints)}{actual_hint}{typo_hint}",
            )
        # 8.5) schema 驱动整数纠偏 — 放在去重签名/审计/执行之前,下游统一看到纠偏后的值
        _coerce_declared_integers(spec, env.args)
        # 9) trace 内去重 (同 trace 同 tool+args 只执行一次)
        if env.trace_id:
            sig = (env.tool, _stable_json(env.args))
            seen = self._trace_seen.get(env.trace_id)
            if seen is None:
                seen = set()
                self._trace_seen[env.trace_id] = seen
            self._trace_seen.move_to_end(env.trace_id)  # LRU:活跃 trace 推到末尾,不会被淘汰
            while len(self._trace_seen) > MAX_TRACE_SEEN:
                self._trace_seen.popitem(last=False)  # 淘汰最久未用的 trace 去重集,防无界增长
            if sig in seen:
                raise DispatchError(
                    "trace_duplicate",
                    f"trace_id={env.trace_id} 已执行过相同 ({env.tool}, args)",
                )
            seen.add(sig)
        # 9) destructive 工具不能从 llm_chat / autonomous_agent origin 调
        if spec.destructive and env.origin in ("llm_chat", "autonomous_agent"):
            raise DispatchError(
                "destructive_blocked",
                f"破坏性工具 {env.tool} 不允许从 llm_chat 调用 (需 ui_button 显式审批)",
            )
        return spec

    def _execute(self, env: ToolCallEnvelope, spec: ToolSpec) -> ToolResult:
        state = None
        if spec.scope in ("save", "script", "user"):
            state = self._state_provider(env)
        try:
            if spec.scope == "global":
                text = spec.executor(env.args)
            elif spec.scope == "user":
                text = spec.executor(env.user_id, env.args)
            elif spec.scope == "script":
                # 安全围栏(与 save 级对称, 纵深防御):script 级工具的 script_id 恒等于服务端
                # 绑定且已 owner 校验的 env.script_id。**无条件覆盖** args 里 LLM/调用方可能伪造的
                # script_id —— 当前各执行器靠 _resolve_sid(env 优先)+ 进库 script_owned 兜底已安全,
                # 此覆盖杜绝「未来新增 script-write 工具忘记调 owner 闸」时 args[script_id] 伪造面重开。
                if env.script_id is not None:
                    env.args["script_id"] = env.script_id
                text = spec.executor(env.user_id, env.script_id, env.args, state)
            else:  # save
                # 安全围栏(防 LLM 跨档注入):save 级工具的 save_id 必须恒等于服务端绑定的
                # env.save_id。**无条件覆盖** args 里任何调用方/LLM 传入的 save_id —— 旧实现
                # 只在 "save_id" not in args 时注入,LLM 把 save_id 写进 tool args 即可绕过,
                # 令 worldbook_add / set_tavern_character 等据此向**他人存档**读写(跨用户越权)。
                # env.save_id 由 chat handler 从已鉴权会话绑定,LLM 不可控,故覆盖是安全且正确的
                # (save 级语义恒为"当前绑定存档",不存在合法的跨档 save 级调用)。
                if env.save_id is not None:
                    env.args["save_id"] = env.save_id
                text = spec.executor(state, env.args)
            # task 109b: 工具可以返 dict (e.g. ui_set_field 返 __ui_action__ payload);
            # dict 默认 ok=True, 由上层 console_assistant 解释 __ui_action__ 转 SSE
            if isinstance(text, dict):
                ok = True
            else:
                # str 走老路径: "失败/ERROR/拒绝" 前缀 或 "<tool> 失败:" 惯例判失败
                ok = not _RESULT_FAILURE_RE.match(str(text))
            return self._record(env, spec, ok=ok, result=text)
        except Exception as exc:
            return self._record(
                env, spec, ok=False,
                result="", error=f"{type(exc).__name__}: {exc}",
            )

    def _record(self, env: ToolCallEnvelope, spec: ToolSpec,
                *, ok: bool, result: Any = "", error: str | None = None) -> ToolResult:
        # task 109b: result 可以是 dict (UI action) 或 str (普通工具); audit 字段只存
        # 字符串版本以防 jsonb / log 长度问题
        if isinstance(result, dict):
            import json as _json
            try:
                audit_result = _json.dumps(result, ensure_ascii=False)[:240]
            except Exception:
                audit_result = str(result)[:240]
        else:
            audit_result = (str(result) if result is not None else "")[:240]
        audit = {
            "ts": env.ts,
            "kind": "tool_call",
            "tool": env.tool,
            "origin": env.origin,
            "user_id": env.user_id,
            "save_id": env.save_id,
            "script_id": env.script_id,
            "trace_id": env.trace_id,
            "call_id": env.call_id,
            "depth": env.depth,
            "args": env.args,
            "result": audit_result,
            "error": error,
            "ok": ok,
        }
        # 持久化到 tool_invocations 表(fire-and-forget,不阻塞主流程)
        _persist_invocation_async(env, ok=ok, error=error, error_kind=None)
        # 进程级滚动缓冲 (按 user_id 分桶)
        uid = int(env.user_id)
        user_bucket = self._recent_audit.setdefault(uid, [])
        user_bucket.append(audit)
        if len(user_bucket) > RECENT_AUDIT_LIMIT:
            self._recent_audit[uid] = user_bucket[-RECENT_AUDIT_LIMIT:]
        # state-level audit (save 级工具才有 state)
        try:
            state = self._state_provider(env)
            if state is not None and hasattr(state, "data"):
                permissions = state.data.setdefault("permissions", {})
                state_audit = permissions.setdefault("audit_log", [])
                state_audit.append(audit)
                if len(state_audit) > AUDIT_LOG_LIMIT:
                    permissions["audit_log"] = state_audit[-AUDIT_LOG_LIMIT:]
        except Exception:
            pass  # 审计写入不阻塞主流程
        # task 69: 工具成功后向 state-event 总线广播 — 前端 SSE 订阅者收到后
        # 把它转为现有 rpg-{topic}-updated CustomEvent,各页面已有的 listener
        # 自动 reload,无需手动 F5。
        if ok and spec.side_effect_topics:
            try:
                from state_event_bus import emit as _emit_event
                for topic in spec.side_effect_topics:
                    _emit_event(env.user_id, topic, _topic_op_from_tool(env.tool), {
                        "tool": env.tool,
                        "args": env.args,
                        "call_id": env.call_id,
                    })
            except Exception:
                pass  # 广播失败不阻塞主流程
        return ToolResult(ok=ok, result=result, error=error, audit=audit)

    def _reject(self, env: ToolCallEnvelope, exc: DispatchError) -> ToolResult:
        audit = {
            "ts": env.ts,
            "kind": "tool_call_rejected",
            "tool": env.tool,
            "origin": env.origin,
            "user_id": env.user_id,
            "save_id": env.save_id,
            "script_id": env.script_id,
            "reject_kind": exc.kind,
            "detail": exc.detail,
        }
        uid = int(env.user_id)
        user_bucket = self._recent_audit.setdefault(uid, [])
        user_bucket.append(audit)
        if len(user_bucket) > RECENT_AUDIT_LIMIT:
            self._recent_audit[uid] = user_bucket[-RECENT_AUDIT_LIMIT:]
        # 拒绝路径也持久化(reject_kind 区分越权 / 不存在 / 限流 / origin 禁用)
        _persist_invocation_async(env, ok=False, error=exc.detail, error_kind=exc.kind)
        return ToolResult(ok=False, error=f"[{exc.kind}] {exc.detail}", audit=audit)

    def _rate_ok(self, user_id: int) -> bool:
        # SEC(H-14): dispatcher 多为每请求新建实例 → 实例级 bucket 形同虚设(跨请求/跨 worker 不共享)。
        def _sliding(bucket: list[float]) -> bool:
            now = time.monotonic()
            cutoff = now - 1.0
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if len(bucket) >= MAX_CALLS_PER_USER_PER_SECOND:
                return False
            bucket.append(now)
            return True
        # 测试环境:用实例级 bucket,保持各测试隔离(每测试新建 dispatcher → 干净);
        # test_rate_limit 在单实例内连续调用仍能触发上限,机制不被绕过。
        import os as _os
        if _os.environ.get("PYTEST_CURRENT_TEST"):
            return _sliding(self._rate_buckets.setdefault(user_id, []))
        # 生产:优先 Redis 固定窗口跨 worker 共享计数;不可用回退进程级全局 bucket(跨请求/跨实例)。
        try:
            import redis_bus
            cnt = redis_bus.rate_incr(f"tooluse:{int(user_id)}", 1)
            if cnt is not None:
                return cnt <= MAX_CALLS_PER_USER_PER_SECOND
        except Exception:
            pass
        with _GLOBAL_RATE_LOCK:
            return _sliding(_GLOBAL_RATE_BUCKETS.setdefault(user_id, []))

    # ── 测试 hook ─────────────────────────────────────────

    def reset_rate_limits(self) -> None:
        self._rate_buckets.clear()
        with _GLOBAL_RATE_LOCK:
            _GLOBAL_RATE_BUCKETS.clear()
        self._trace_seen.clear()


# ────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────


def _persist_invocation_async(env: ToolCallEnvelope, *, ok: bool,
                              error: str | None, error_kind: str | None) -> None:
    """fire-and-forget 写 tool_invocations 表。

    有界队列 + 固定 drain worker 跑 INSERT,主路径不阻塞。失败 silent — 仅 log.debug。
    """
    import json as _json
    import logging

    log = logging.getLogger(__name__)

    try:
        args_summary = _json.dumps(env.args or {}, ensure_ascii=False)[:240]
    except Exception:
        args_summary = str(env.args or {})[:240]

    def _do_insert() -> None:
        try:
            from platform_app.db import connect
            from psycopg.types.json import Jsonb
            with connect() as db:
                db.execute(
                    """
                    insert into tool_invocations (
                      ts, user_id, save_id, script_id, tool, origin,
                      ok, error_kind, latency_ms, args_summary, metadata
                    )
                    values (now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        env.user_id, env.save_id, env.script_id,
                        env.tool, env.origin or "",
                        bool(ok),
                        error_kind,
                        None,  # latency_ms 暂未测量,后续可加
                        args_summary,
                        Jsonb({
                            "trace_id": env.trace_id,
                            "call_id": env.call_id,
                            "depth": env.depth,
                            "error": (error or "")[:240] if error else None,
                        }),
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            log.debug("[telemetry] persist tool_invocations failed: %s", exc)

    _submit_telemetry(_do_insert)


# ── 遥测写入:有界队列 + 固定 drain worker ─────────────────────────────────────
# 原实现每次工具调用 threading.Thread().start():高负载下(多用户 × 每回合多工具调用)
# 线程爆炸,且这些线程与主请求争抢同一 DB 连接池(25/worker)→ 可耗尽连接池阻塞游戏请求。
# 改为固定 2 个 drain worker 从有界队列消费:并发遥测连接 ≤2,队列满则丢弃(遥测是
# best-effort),永不阻塞 chat 主路径,也不让积压无界增长(DB 慢/down 时)。
_TELEMETRY_QUEUE: "_queue.Queue | None" = None
_TELEMETRY_LOCK = threading.Lock()
_TELEMETRY_WORKERS = 2
_TELEMETRY_QUEUE_MAX = 2000


def _telemetry_drain(q: "_queue.Queue") -> None:
    while True:
        fn = q.get()
        try:
            fn()
        except Exception:
            pass
        finally:
            q.task_done()


def _submit_telemetry(fn: Callable[[], None]) -> None:
    global _TELEMETRY_QUEUE
    q = _TELEMETRY_QUEUE
    if q is None:
        with _TELEMETRY_LOCK:
            if _TELEMETRY_QUEUE is None:
                _TELEMETRY_QUEUE = _queue.Queue(maxsize=_TELEMETRY_QUEUE_MAX)
                for i in range(_TELEMETRY_WORKERS):
                    threading.Thread(
                        target=_telemetry_drain, args=(_TELEMETRY_QUEUE,),
                        daemon=True, name=f"tool-telemetry-{i}",
                    ).start()
            q = _TELEMETRY_QUEUE
    try:
        q.put_nowait(fn)
    except _queue.Full:
        # 积压(DB 持续慢/down)→ 丢弃该遥测,绝不阻塞或拖垮主路径
        pass


def _stable_json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _topic_op_from_tool(tool_name: str) -> str:
    """根据工具名前缀推断动作 (created/updated/deleted/activated/renamed)。
    用于 state-event payload,前端可以按 op 类型决定是否需要全量刷或局部刷。"""
    n = tool_name.lower()
    if n.startswith(("create_", "import_", "start_", "add_")):
        return "created"
    if n.startswith(("delete_", "remove_", "cancel_", "stop_")):
        return "deleted"
    if n.startswith("activate_"):
        return "activated"
    if n.startswith("rename_"):
        return "renamed"
    if n.startswith(("approve_", "reject_", "dismiss_")):
        return "resolved"
    return "updated"


__all__ = [
    "Scope",
    "Origin",
    "ToolSpec",
    "ToolCallEnvelope",
    "ToolResult",
    "ToolRegistry",
    "ToolDispatcher",
    "DispatchError",
    "get_registry",
    "MAX_TRACE_DEPTH",
    "MAX_CALLS_PER_USER_PER_SECOND",
]
