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
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal


# ────────────────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────────────────


Scope = Literal["global", "user", "script", "save"]
Origin = Literal["llm_chat", "llm_set", "ui_button", "mcp_call", "api_direct"]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    executor: Callable[..., str]
    scope: Scope = "save"
    origins: frozenset[str] = frozenset({"ui_button", "api_direct", "llm_set"})
    destructive: bool = False  # delete_* / 重置类操作,LLM 不能调

    def to_anthropic_tool(self) -> dict[str, Any]:
        """转换为 Anthropic tool_use schema。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


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
    result: str = ""
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
AUDIT_LOG_LIMIT = 200
RECENT_AUDIT_LIMIT = 1000


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
        self._trace_seen: dict[str, set[tuple[str, str]]] = {}
        # 滚动审计缓冲 (进程级,所有 user)
        self._recent_audit: list[dict[str, Any]] = []

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
        return self._execute(env, spec)

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._recent_audit[-limit:])

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
        # 8) trace 内去重 (同 trace 同 tool+args 只执行一次)
        if env.trace_id:
            sig = (env.tool, _stable_json(env.args))
            seen = self._trace_seen.setdefault(env.trace_id, set())
            if sig in seen:
                raise DispatchError(
                    "trace_duplicate",
                    f"trace_id={env.trace_id} 已执行过相同 ({env.tool}, args)",
                )
            seen.add(sig)
        # 9) destructive 工具不能从 llm_chat origin 调
        if spec.destructive and env.origin == "llm_chat":
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
                text = spec.executor(env.user_id, env.script_id, env.args, state)
            else:  # save
                text = spec.executor(state, env.args)
            ok = not text.startswith(("失败", "ERROR", "拒绝"))
            return self._record(env, spec, ok=ok, result=text)
        except Exception as exc:
            return self._record(
                env, spec, ok=False,
                result="", error=f"{type(exc).__name__}: {exc}",
            )

    def _record(self, env: ToolCallEnvelope, spec: ToolSpec,
                *, ok: bool, result: str = "", error: str | None = None) -> ToolResult:
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
            "result": (result or "")[:240],
            "error": error,
            "ok": ok,
        }
        # 进程级滚动缓冲
        self._recent_audit.append(audit)
        if len(self._recent_audit) > RECENT_AUDIT_LIMIT:
            self._recent_audit = self._recent_audit[-RECENT_AUDIT_LIMIT:]
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
        self._recent_audit.append(audit)
        if len(self._recent_audit) > RECENT_AUDIT_LIMIT:
            self._recent_audit = self._recent_audit[-RECENT_AUDIT_LIMIT:]
        return ToolResult(ok=False, error=f"[{exc.kind}] {exc.detail}", audit=audit)

    def _rate_ok(self, user_id: int) -> bool:
        now = time.monotonic()
        bucket = self._rate_buckets.setdefault(user_id, [])
        # 丢掉 1 秒前的
        cutoff = now - 1.0
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= MAX_CALLS_PER_USER_PER_SECOND:
            return False
        bucket.append(now)
        return True

    # ── 测试 hook ─────────────────────────────────────────

    def reset_rate_limits(self) -> None:
        self._rate_buckets.clear()
        self._trace_seen.clear()


# ────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────


def _stable_json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


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
