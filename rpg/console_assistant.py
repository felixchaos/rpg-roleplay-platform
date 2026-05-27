"""
console_assistant.py — task 48: 侧栏控制台 AI 助手 (基础架构, 后端)

设计目标
========
让用户在 RPG Platform 侧栏与一个 AI 助手对话, 帮 ta 完成「平台层」操作:
  - 列/建/改/删 game_save / persona / character_card / script
  - 切换 GM 模型
  - 启动/查询导入任务
  - 启停 MCP server
  - 查 user_variables / world_state / 当前 scene

这个助手不是 GM。它跑在独立的 origin (`console_assistant`),
不参与 chat / 战斗叙事;它的工具集是 dispatcher 中所有标了 `console_assistant`
origin 的工具(详见 command_tools_*.py)。

协议
====

POST /api/console_assistant/chat (SSE):
  请求 body:
    { message: str, conversation_id?: str, page_context?: dict }

  SSE 事件:
    event: meta                  data: {conversation_id, trace_id}
    event: token                 data: {text}
    event: tool_call             data: {call_id, tool, args, server_id}
    event: tool_result           data: {call_id, ok, result, error}
    event: confirmation_required data: {call_id, tool, args, description, destructive: true}
    event: error                 data: {message}
    event: done                  data: {summary?}

POST /api/console_assistant/confirm:
  body: {conversation_id, call_id, decision: "approve"|"reject"}
  resp: {ok, result?, error?}

存储
====
in-memory only — 进程内 dict, 重启即丢:
  _conversations[user_id][conversation_id] = {
    "messages": [...],          # provider-neutral chat history
    "pending_confirmations": {  # call_id → ToolCallEnvelope dict
        call_id: {tool, args, save_id, script_id, description}
    },
    "created_at": iso,
    "last_used": iso,
  }

按 user_id 分桶, 跨用户严格隔离。

LLM backend
===========
复用 gm.py 的 _AnthropicBackend / _VertexBackend / _OpenAICompatBackend,
通过 stream_with_mcp_loop 跑 native tool_use 多轮 loop。tool 列表通过
command_dispatcher.get_registry().list_for_origin("console_assistant") 拿。
"""
from __future__ import annotations

import json
import secrets
import time
import uuid
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Iterator

from command_dispatcher import (
    ToolCallEnvelope, ToolDispatcher, ToolResult, get_registry,
)


# ────────────────────────────────────────────────────────────
# 状态存储 (进程内)
# ────────────────────────────────────────────────────────────


CONVERSATION_TTL_SECONDS = 60 * 60 * 6   # 6 小时不活跃后丢弃
MAX_CONVERSATIONS_PER_USER = 20
MAX_MESSAGES_PER_CONVERSATION = 60       # 防止 token 爆炸


_conversations: dict[int, dict[str, dict[str, Any]]] = {}
_lock = Lock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_conversation_id() -> str:
    return f"conv-{uuid.uuid4().hex[:12]}"


def _new_trace_id() -> str:
    return f"console-{secrets.token_urlsafe(6)}"


def _new_call_id() -> str:
    return f"cc-{secrets.token_urlsafe(6)}"


def _get_or_create_conversation(
    user_id: int, conversation_id: str | None,
) -> tuple[str, dict[str, Any]]:
    """按 user_id+conversation_id 取或新建。返回 (conversation_id, conv_state)。"""
    with _lock:
        user_bucket = _conversations.setdefault(user_id, {})
        _gc_user_bucket(user_bucket)
        if conversation_id and conversation_id in user_bucket:
            conv = user_bucket[conversation_id]
            conv["last_used"] = _now_iso()
            return conversation_id, conv
        new_id = conversation_id or _new_conversation_id()
        conv = {
            "messages": [],
            "pending_confirmations": {},
            "created_at": _now_iso(),
            "last_used": _now_iso(),
        }
        user_bucket[new_id] = conv
        return new_id, conv


def _gc_user_bucket(user_bucket: dict[str, dict[str, Any]]) -> None:
    """简单 TTL + LRU 维持 bucket 大小。"""
    if not user_bucket:
        return
    cutoff = datetime.now().timestamp() - CONVERSATION_TTL_SECONDS
    drop = []
    for cid, conv in user_bucket.items():
        try:
            ts = datetime.fromisoformat(conv["last_used"]).timestamp()
        except Exception:
            ts = 0
        if ts < cutoff:
            drop.append(cid)
    for cid in drop:
        user_bucket.pop(cid, None)
    if len(user_bucket) > MAX_CONVERSATIONS_PER_USER:
        # 按 last_used 排序, 丢最旧的
        items = sorted(
            user_bucket.items(),
            key=lambda kv: kv[1].get("last_used", ""),
        )
        for cid, _ in items[: len(user_bucket) - MAX_CONVERSATIONS_PER_USER]:
            user_bucket.pop(cid, None)


def _trim_messages(conv: dict[str, Any]) -> None:
    msgs = conv.get("messages") or []
    if len(msgs) > MAX_MESSAGES_PER_CONVERSATION:
        # 保留最近 N 条, 最早的丢弃。
        conv["messages"] = msgs[-MAX_MESSAGES_PER_CONVERSATION:]


def get_conversation_state(user_id: int) -> dict[str, dict[str, Any]]:
    """test hook: 直接拿某用户的全部 conversation。"""
    return _conversations.get(user_id, {})


def reset_all_conversations() -> None:
    """test hook: 进程内清空。"""
    with _lock:
        _conversations.clear()


# ────────────────────────────────────────────────────────────
# System prompt
# ────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """你是 RPG Platform 的「侧栏控制台助手」。不是游戏 GM, 不写故事、不推剧情。
帮用户管理平台资源 (存档/角色卡/persona/剧本/设置/MCP) — 所有 UI 按钮都能用工具操作。

你只有 4 把工具:

  · ui_describe(intent, page?)     — 按用户原话查可用 action, 返候选 + 参数表
  · ui_invoke(action_id, args)     — 执行 action; 缺 required 时系统会自动弹窗问用户, 你不用判断
  · ask_user_choice(q, options[])  — 主动问用户在 2-6 个选项里选 (前端渲按钮)
  · ask_user_text(q, placeholder?) — 主动问用户输入文本 (前端渲输入框)

(若用户明确说"打开/跳到 XX 页",通过 ui_invoke 调 navigate_to_setting action。
 "查看 / 列出 / 看看"绝不是导航,请用 list_* action。)

────────────────────────────────────────────────
唯一工作流 (适用所有意图)
────────────────────────────────────────────────

1. 用户说一件事 → 调 ui_describe(intent="他原话里的关键词")
2. 看候选 action 卡片 → 选 1 个最匹配的 action_id
3. 把已知信息填进 args, 调 ui_invoke(action_id, args)
4. 如果 ui_invoke 返 "NEEDS_USER_INPUT: ..." → 表示缺字段, 前端会自动弹询问框,
   用户答完触发新一轮, 你接着填好 args 再 ui_invoke
5. 如果 ui_invoke 返 "失败: ..." → 解释原因, 必要时问用户怎么改
6. 成功 → 一句话告诉用户做完了 (前端会自动刷新对应页面)

不要凭直觉直接调子工具。
不要在文本里裸列 1/2/3 让用户打字回复 (改用 ask_user_choice)。
不要编 ID / 名字 / 选项 — 不知道就 ui_describe(intent="list") 先查。

────────────────────────────────────────────────
list vs navigate (用户口语 → 工具 — 强约束)
────────────────────────────────────────────────
**"查看 / 列出 / 看看 / 我有哪些 / 显示" → 永远调 ui_invoke 对应的 list_* action**
  (例: list_my_saves / list_scripts / list_my_character_cards / list_my_personas /
   list_available_models / list_modules / list_my_credentials_meta ...)
  → 这样结果直接展示在 chat 里,用户不离开当前页面,体验最快。

**仅在用户明确说"打开/跳到/进入 XX 页面"才用 navigate_to_setting**。
"查看模型" ≠ "进入模型设置页"。"看看存档" ≠ "去存档页"。

────────────────────────────────────────────────
区分 "建卡 / 建存档 / 改剧情人物" (高频混淆)
────────────────────────────────────────────────

  · "创建用户角色" / "建角色卡" / "做一张人设"
    → action: create_character_card (跨 save 复用的人设资产)
  · "新建存档" / "新开一局" / "开局"
    → action: create_save (一个游戏会话, 需要 script_id)
  · "把剧情里玩家叫 XX" / "改我的角色名"
    → 这是 save 内剧情字段, 不属于平台资源, 助手不管 —
       告诉用户去 Game Console 用 /set 命令

────────────────────────────────────────────────
写作风格
────────────────────────────────────────────────
中文, 简洁, 一次最多 4 个工具调用。不复述工具名给用户看 (用户只关心结果)。
"""


def build_system_prompt(page_context: dict[str, Any] | None) -> str:
    """根据 page_context 在 system prompt 末尾追加上下文。"""
    base = _SYSTEM_PROMPT.rstrip()
    if not page_context:
        return base + "\n\n当前页面: 未知。"
    pieces: list[str] = ["当前页面上下文:"]
    tab = page_context.get("tab")
    if tab:
        pieces.append(f"  · tab = {tab}")
    save_id = page_context.get("save_id")
    if save_id is not None:
        pieces.append(f"  · save_id = {save_id}")
    script_id = page_context.get("script_id")
    if script_id is not None:
        pieces.append(f"  · script_id = {script_id}")
    extra = page_context.get("note")
    if extra:
        pieces.append(f"  · note = {extra}")
    return base + "\n\n" + "\n".join(pieces)


# ────────────────────────────────────────────────────────────
# 工具表 (按 origin=console_assistant 过滤)
# ────────────────────────────────────────────────────────────


def list_assistant_tools() -> list[dict[str, Any]]:
    """返回 console_assistant 给 LLM 看的工具列表。

    task 68/72 改造: 不再把 52 把子工具全暴露 — 只暴露 5 把通用工具,
    让 LLM 用 ui_describe 动态发现 + ui_invoke 执行。这样:
      · LLM context 不会因为巨大的 tool list 而被污染
      · 加新功能不需要改 LLM prompt — 只需在 ui_manifest 加 keyword
      · 缺字段必问由 ui_invoke 的 NEEDS_USER_INPUT 哨兵机制层强制

    白名单的 4 把:
      ui_describe / ui_invoke      — 发现 + 执行 (核心)
      ask_user_choice / ask_user_text — 在 ui_invoke 缺参数时主动问用户
    (navigate_to_setting 仍可用,但只能经 ui_invoke 调,不直接暴露给 LLM —
     避免 LLM 把 "查看 X" 误解为 "跳到 X 页"。)
    """
    from chat_tool_router import DISPATCHER_SENTINEL
    ALLOWED = {"ui_describe", "ui_invoke", "ask_user_choice", "ask_user_text"}
    out: list[dict[str, Any]] = []
    for spec in get_registry().list_for_origin("console_assistant"):
        if spec.name not in ALLOWED:
            continue
        out.append({
            "server_id": DISPATCHER_SENTINEL,
            "name": spec.name,
            "description": spec.description,
            "schema": spec.input_schema,
            "destructive": spec.destructive,
            "scope": spec.scope,
        })
    return out


def get_tool_spec(name: str):
    return get_registry().get(name)


# ────────────────────────────────────────────────────────────
# 工具执行 (走 dispatcher, origin=console_assistant)
# ────────────────────────────────────────────────────────────


def dispatch_assistant_tool(
    *,
    user_id: int,
    tool: str,
    args: dict[str, Any],
    save_id: int | None,
    script_id: int | None,
    trace_id: str,
    call_id: str,
    state_provider: Callable[[ToolCallEnvelope], Any] | None = None,
) -> ToolResult:
    """统一入口:把一次工具调用包装成 ToolCallEnvelope 走 dispatcher。

    state_provider:
      console_assistant 默认不该改 save 内部 state (那是 GM 的事), 但 save
      scope 工具 (set_world_time / get_game_state 等) 还是允许的。
      调用方如果不传 state_provider, 这里走「按 user_id+save_id 现取
      state」的兜底 — 在测试中我们会 mock 掉。
    """
    dispatcher = ToolDispatcher(
        registry=get_registry(),
        state_provider=state_provider or (lambda env: None),
    )
    env = ToolCallEnvelope(
        user_id=user_id,
        save_id=save_id,
        script_id=script_id,
        tool=tool,
        args=args or {},
        origin="console_assistant",
        trace_id=trace_id,
        call_id=call_id,
        depth=1,
    )
    return dispatcher.dispatch_sync(env)


# ────────────────────────────────────────────────────────────
# SSE 流主循环
# ────────────────────────────────────────────────────────────


def _sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_tool_result_for_llm(call_id: str, result: ToolResult) -> str:
    """ToolResult → LLM-facing 文本 (会塞进 history 让助手下一轮看到)。"""
    head = "OK" if result.ok else "FAIL"
    body = result.result or result.error or ""
    return f"[tool {call_id} {head}]\n{body[:1500]}"


def _run_llm_loop(
    *,
    user_id: int,
    conv: dict[str, Any],
    page_context: dict[str, Any] | None,
    backend: Any,
    state_provider: Callable[[ToolCallEnvelope], Any] | None,
    trace_id: str,
    max_iterations: int,
    max_tokens: int,
) -> Iterator[str]:
    """task 58: 共享内核 — 跑 backend.stream_with_mcp_loop, yield SSE 字符串。

    入口约定 conv["messages"] 已包含「该轮 LLM 应当看到的最新对话历史」
    (含 user 最新消息 / 上一轮工具结果 / 拒绝记录…), 调用方负责 push。
    本函数不再 push user message, 只跑 LLM loop, 并把这轮的 assistant 文本
    写回 conv history。

    stream_chat 与 apply_confirmation_stream 都借这个生成器跑 LLM 续写。
    """
    system_prompt = build_system_prompt(page_context)
    tools = list_assistant_tools()

    # 等待二次确认时, 把 pending 信息塞给 LLM 让它知道还在等
    # (这里只是临时塞进发给 backend 的 messages, 不持久化到 conv["messages"])
    extra_pending_note: list[dict[str, Any]] = []
    if conv.get("pending_confirmations"):
        pending_summary = "(等待用户对以下调用做出 approve/reject 决定:\n" + json.dumps(
            list(conv["pending_confirmations"].values())[:3], ensure_ascii=False, indent=2,
        ) + "\n)"
        extra_pending_note.append({"role": "system", "content": pending_summary})

    # confirmation 中断标志
    pending_for_this_turn: list[dict[str, Any]] = []

    # 拿 save_id/script_id from page_context, 默认 None
    page_save_id = (page_context or {}).get("save_id")
    page_script_id = (page_context or {}).get("script_id")
    try:
        page_save_id = int(page_save_id) if page_save_id is not None else None
    except (TypeError, ValueError):
        page_save_id = None
    try:
        page_script_id = int(page_script_id) if page_script_id is not None else None
    except (TypeError, ValueError):
        page_script_id = None

    # mcp_call 闭包: 由 backend 在 tool_use 时调
    def _router(server_id: str, tool_name: str, arguments: dict) -> dict[str, Any]:
        spec = get_tool_spec(tool_name)
        if spec is None:
            return {"ok": False, "error": f"未知工具 {tool_name}"}
        call_id = _new_call_id()
        # destructive 走二次确认
        if spec.destructive:
            pending = {
                "call_id": call_id,
                "tool": tool_name,
                "args": arguments or {},
                "save_id": page_save_id,
                "script_id": page_script_id,
                "description": spec.description,
            }
            conv["pending_confirmations"][call_id] = pending
            pending_for_this_turn.append(pending)
            # 直接返回 ok=False 让 backend 不把这个 call_id 当成功执行;
            # 同时我们会跳出主循环, 等 /confirm 调用。
            return {
                "ok": False,
                "error": "DESTRUCTIVE_REQUIRES_CONFIRMATION",
                "result": json.dumps(pending, ensure_ascii=False),
            }
        # 非 destructive: 直接 dispatch
        result = dispatch_assistant_tool(
            user_id=user_id,
            tool=tool_name,
            args=arguments or {},
            save_id=page_save_id,
            script_id=page_script_id,
            trace_id=trace_id,
            call_id=call_id,
            state_provider=state_provider,
        )
        return {
            "ok": result.ok,
            "result": result.result,
            "error": result.error,
            "_call_id": call_id,
        }

    # 流式跑 backend
    try:
        messages_for_backend = _to_backend_messages(conv["messages"]) + [
            {"role": m["role"], "content": m["content"]} for m in extra_pending_note
            if m["role"] in ("user", "assistant")  # backend 不一定吃 system role
        ]
        assistant_text_acc = ""
        for ev in backend.stream_with_mcp_loop(
            system=system_prompt,
            messages=messages_for_backend,
            mcp_tools=tools,
            max_iterations=max_iterations,
            max_tokens=max_tokens,
            mcp_call=_router,
        ):
            etype = ev.get("type")
            if etype == "text":
                txt = ev.get("text") or ""
                if txt:
                    assistant_text_acc += txt
                    yield _sse_event("token", {"text": txt})
            elif etype == "tool_call":
                yield _sse_event("tool_call", {
                    "tool": ev.get("tool"),
                    "args": ev.get("arguments") or {},
                    "server_id": ev.get("server_id") or "dispatcher",
                })
            elif etype == "tool_result":
                # 检查是不是 destructive 触发的 pending
                err = ev.get("error") or ""
                if "DESTRUCTIVE_REQUIRES_CONFIRMATION" in err:
                    # 找出最近一条 pending, yield confirmation_required
                    pend = pending_for_this_turn[-1] if pending_for_this_turn else None
                    if pend:
                        yield _sse_event("confirmation_required", {
                            "call_id": pend["call_id"],
                            "tool": pend["tool"],
                            "args": pend["args"],
                            "description": pend["description"],
                            "destructive": True,
                        })
                    # 中断 backend loop (不再让 LLM 接着叙事)
                    break
                # task 57: navigate_to_setting 工具返回 NAVIGATE:target|reason
                # 哨兵字符串 — 这里识别并转成 navigation_required SSE 事件 yield 给前端。
                # tool_result 仍正常 yield 一遍 (LLM 也能从 history 看到自己调过)。
                result_str = ev.get("result") or ""
                if isinstance(result_str, str) and result_str.startswith("NAVIGATE:"):
                    payload = result_str[len("NAVIGATE:"):]
                    try:
                        target, _, reason = payload.partition("|")
                        target = (target or "").strip()
                        reason = (reason or "").strip()
                    except Exception:
                        target, reason = payload.strip(), ""
                    if target:
                        yield _sse_event("navigation_required", {
                            "target": target,
                            "reason": reason,
                            "dirty_check": True,
                        })
                # task 61: ask_user_choice 工具返回 USER_CHOICE:<json> 哨兵 —
                # 转成 user_choice_required SSE 事件, 并中断 LLM loop 等用户
                # 在 UI 上选择, 前端会带着 "我选: xxx" 作为新 message 触发下一轮。
                # 与 NAVIGATE 不同: 此处 *不* yield 标准 tool_result (UI 卡片是 tool 的
                # 直接替代品, 再 yield 一遍会产生空 tool 卡片污染界面),
                # 并且要 break 跳出 backend.stream_with_mcp_loop 当前迭代。
                if isinstance(result_str, str) and result_str.startswith("USER_CHOICE:"):
                    payload_str = result_str[len("USER_CHOICE:"):]
                    try:
                        payload = json.loads(payload_str)
                    except Exception:
                        payload = {"question": payload_str, "options": []}
                    # task 92: 如果本轮 LLM 一句话都还没说就直接弹问题, 注入一句
                    # 简短引言告诉用户"助手正在询问",否则用户看到的是空白 + 突然
                    # 出现一张选择卡, 容易当成"静默失败"。
                    if not assistant_text_acc.strip():
                        intro = "好的,先确认一下:"
                        assistant_text_acc += intro
                        yield _sse_event("token", {"text": intro})
                    yield _sse_event("user_choice_required", {
                        "call_id": ev.get("_call_id") or _new_call_id(),
                        "tool": "ask_user_choice",
                        "question": payload.get("question", ""),
                        "options": payload.get("options", []),
                        "allow_free_text": payload.get("allow_free_text", True),
                        "context": payload.get("context", ""),
                    })
                    # 中断当前 LLM loop, 等用户在前端选择 → 触发新一轮 chat
                    break
                # task 74: ask_user_text 哨兵 — 同 USER_CHOICE 但走文本输入框,
                # 用于姓名/描述这类不适合做选项的字段。
                if isinstance(result_str, str) and result_str.startswith("USER_TEXT:"):
                    payload_str = result_str[len("USER_TEXT:"):]
                    try:
                        payload = json.loads(payload_str)
                    except Exception:
                        payload = {"question": payload_str}
                    # task 92: 同 USER_CHOICE,空白 + 突然弹输入框 = 用户感觉静默失败
                    if not assistant_text_acc.strip():
                        intro = "好的,我需要先问你:"
                        assistant_text_acc += intro
                        yield _sse_event("token", {"text": intro})
                    yield _sse_event("user_text_required", {
                        "call_id": ev.get("_call_id") or _new_call_id(),
                        "tool": "ask_user_text",
                        "question": payload.get("question", ""),
                        "placeholder": payload.get("placeholder", ""),
                        "context": payload.get("context", ""),
                    })
                    break
                # task 68/72: ui_invoke 缺 required 字段 → NEEDS_USER_INPUT 哨兵。
                # 机制层强制 "先问后做",LLM 不用判断该不该问。
                # next_field 给前端用,前端弹合适的选择/文本框。
                if isinstance(result_str, str) and result_str.startswith("NEEDS_USER_INPUT:"):
                    payload_str = result_str[len("NEEDS_USER_INPUT:"):]
                    try:
                        payload = json.loads(payload_str)
                    except Exception:
                        payload = {"question": payload_str, "options": []}
                    options = payload.get("options") or []
                    # task 92: 空白回应 + 突然弹卡 = 用户感觉助手没反应。注入一句开场。
                    if not assistant_text_acc.strip():
                        action_id = payload.get("action_id") or ""
                        intro = (
                            f"好的,要执行 {action_id} 还差几项信息,先确认一下:"
                            if action_id else
                            "好的,我需要先确认几个细节:"
                        )
                        assistant_text_acc += intro
                        yield _sse_event("token", {"text": intro})
                    # 有 options 走选择框, 没有走文本框 — 同一个机制两种渲染
                    if options:
                        yield _sse_event("user_choice_required", {
                            "call_id": ev.get("_call_id") or _new_call_id(),
                            "tool": "ui_invoke",
                            "question": payload.get("question", ""),
                            "options": options,
                            "allow_free_text": payload.get("allow_free_text", True),
                            "context": payload.get("context", ""),
                            "action_id": payload.get("action_id"),
                            "next_field": payload.get("next_field"),
                        })
                    else:
                        yield _sse_event("user_text_required", {
                            "call_id": ev.get("_call_id") or _new_call_id(),
                            "tool": "ui_invoke",
                            "question": payload.get("question", ""),
                            "placeholder": payload.get("next_field", ""),
                            "context": payload.get("context", ""),
                            "action_id": payload.get("action_id"),
                            "next_field": payload.get("next_field"),
                        })
                    break
                yield _sse_event("tool_result", {
                    "call_id": ev.get("_call_id") or _new_call_id(),
                    "ok": bool(ev.get("ok")),
                    "result": ev.get("result"),
                    "error": ev.get("error"),
                })
            elif etype == "tool_error":
                yield _sse_event("error", {"message": ev.get("error") or "tool 调用错误"})
        # 把这轮 assistant 文本写回 conv history
        if assistant_text_acc:
            conv["messages"].append({"role": "assistant", "content": assistant_text_acc})
            _trim_messages(conv)
    except Exception as exc:
        yield _sse_event("error", {"message": f"{type(exc).__name__}: {exc}"})


def stream_chat(
    *,
    user_id: int,
    message: str,
    conversation_id: str | None,
    page_context: dict[str, Any] | None,
    backend: Any,
    state_provider: Callable[[ToolCallEnvelope], Any] | None = None,
    max_iterations: int = 4,
    max_tokens: int = 1200,
) -> Iterator[str]:
    """主循环 — yield SSE 文本块。

    backend 是 gm._VertexBackend / _AnthropicBackend / _OpenAICompatBackend 任一,
    必须实现 .stream_with_mcp_loop(system, messages, mcp_tools, max_iterations,
    max_tokens, mcp_call) -> Iterator[dict]。

    流程:
      1. 取/建 conversation, append user message
      2. yield meta
      3. 跑 backend.stream_with_mcp_loop (via _run_llm_loop):
         · 普通文本 chunk → yield token
         · 工具调用 → 判 destructive:
             destructive → yield confirmation_required, 把待执行调用挂到
                            conv.pending_confirmations, **中止本轮 backend loop**
             非 destructive → dispatch, yield tool_call+tool_result, 把结果送回
                              backend (由 stream_with_mcp_loop 内部完成)
      4. yield done
    """
    conv_id, conv = _get_or_create_conversation(user_id, conversation_id)
    trace_id = _new_trace_id()

    yield _sse_event("meta", {
        "conversation_id": conv_id,
        "trace_id": trace_id,
    })

    if not isinstance(message, str) or not message.strip():
        yield _sse_event("error", {"message": "message 不能为空"})
        yield _sse_event("done", {})
        return

    # 推入新一轮用户消息
    conv["messages"].append({"role": "user", "content": message.strip()})
    _trim_messages(conv)

    yield from _run_llm_loop(
        user_id=user_id,
        conv=conv,
        page_context=page_context,
        backend=backend,
        state_provider=state_provider,
        trace_id=trace_id,
        max_iterations=max_iterations,
        max_tokens=max_tokens,
    )

    yield _sse_event("done", {
        "pending_confirmations": list(conv["pending_confirmations"].keys()),
    })


def _to_backend_messages(messages: list[dict[str, Any]]) -> list[dict]:
    """conv["messages"] 用 {role, content:str} 简单形态, backend 直接吃。
    跳过 system role 项 (那本来就走 system 参数), 同时压扁 content 为字符串。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, list):
            # tool_result blocks etc. 压成 JSON 字符串
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        if not isinstance(content, str):
            content = str(content)
        out.append({"role": role, "content": content})
    return out


# ────────────────────────────────────────────────────────────
# 确认 endpoint 入口
# ────────────────────────────────────────────────────────────


def _resolve_pending(
    *, user_id: int, conversation_id: str, call_id: str, decision: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """共享步骤:校验 + pop pending。返回 (conv, pending, error_msg)。"""
    decision_norm = (decision or "").strip().lower()
    if decision_norm not in {"approve", "reject"}:
        return None, None, f"decision 非法: {decision!r} (允许 approve/reject)"
    with _lock:
        user_bucket = _conversations.get(user_id) or {}
        conv = user_bucket.get(conversation_id)
        if not conv:
            return None, None, f"conversation {conversation_id} 不存在或不属于当前用户"
        pending = conv.get("pending_confirmations", {}).pop(call_id, None)
    if not pending:
        return conv, None, f"call_id={call_id} 没有 pending 记录"
    return conv, pending, None


def apply_confirmation(
    *,
    user_id: int,
    conversation_id: str,
    call_id: str,
    decision: str,
    state_provider: Callable[[ToolCallEnvelope], Any] | None = None,
) -> dict[str, Any]:
    """[legacy] 对一个 pending destructive 工具调用做最终决策, 同步返回 dict。

    task 58 后端 endpoint 已切到 apply_confirmation_stream;此函数仅供测试 /
    其它工具复用 — 它不跑 LLM 续轮, 只把 tool_result 写进 history。
    """
    conv, pending, err = _resolve_pending(
        user_id=user_id, conversation_id=conversation_id,
        call_id=call_id, decision=decision,
    )
    if err:
        return {"ok": False, "error": err}
    decision_norm = decision.strip().lower()

    if decision_norm == "reject":
        conv["messages"].append({
            "role": "assistant",
            "content": f"[确认拒绝] 工具 {pending['tool']} (call_id={call_id}) 已被用户拒绝, 未执行。",
        })
        _trim_messages(conv)
        return {"ok": True, "decision": "reject", "tool": pending["tool"]}

    # approve → dispatch
    result = dispatch_assistant_tool(
        user_id=user_id,
        tool=pending["tool"],
        args=pending["args"],
        save_id=pending.get("save_id"),
        script_id=pending.get("script_id"),
        trace_id=_new_trace_id(),
        call_id=call_id,
        state_provider=state_provider,
    )
    conv["messages"].append({
        "role": "assistant",
        "content": _format_tool_result_for_llm(call_id, result),
    })
    _trim_messages(conv)
    return {
        "ok": result.ok,
        "decision": "approve",
        "tool": pending["tool"],
        "result": result.result,
        "error": result.error,
    }


def apply_confirmation_stream(
    *,
    user_id: int,
    conversation_id: str,
    call_id: str,
    decision: str,
    page_context: dict[str, Any] | None,
    backend: Any,
    state_provider: Callable[[ToolCallEnvelope], Any] | None = None,
    max_iterations: int = 4,
    max_tokens: int = 1200,
) -> Iterator[str]:
    """task 58: SSE 版 confirm — 执行/拒绝 destructive 工具, 然后让 LLM 续写。

    流程:
      1. 校验 + pop pending (出错 → yield error/done 退出)
      2. yield meta (conversation_id / trace_id)
      3. approve: yield tool_call + 真正 dispatch + yield tool_result + 把
         tool_result 写进 history
         reject: yield 一条 "reject" tool_result + 把 reject 备注写进 history
      4. 跑 _run_llm_loop(让 LLM 基于新 history 续写 — 可能再 token, 也可能再
         触发 destructive → 又 yield confirmation_required 等下一次 confirm)
      5. yield done

    协议与 /chat endpoint 完全一致, 前端可直接复用 buildHandlers。
    """
    trace_id = _new_trace_id()

    # 第 1 步:校验
    conv, pending, err = _resolve_pending(
        user_id=user_id, conversation_id=conversation_id,
        call_id=call_id, decision=decision,
    )
    if err:
        yield _sse_event("meta", {
            "conversation_id": conversation_id, "trace_id": trace_id,
        })
        yield _sse_event("error", {"message": err})
        yield _sse_event("done", {})
        return

    decision_norm = decision.strip().lower()

    yield _sse_event("meta", {
        "conversation_id": conversation_id, "trace_id": trace_id,
    })

    # 第 2 步:执行 or 拒绝
    if decision_norm == "reject":
        reject_note = (
            f"[确认拒绝] 工具 {pending['tool']} (call_id={call_id}) "
            f"已被用户拒绝, 未执行。"
        )
        conv["messages"].append({"role": "assistant", "content": reject_note})
        _trim_messages(conv)
        # 把这条信号也作为 tool_result 推给前端 (用 ok=False + error 字段)
        yield _sse_event("tool_result", {
            "call_id": call_id,
            "ok": False,
            "result": None,
            "error": "用户拒绝执行",
            "decision": "reject",
            "tool": pending["tool"],
        })
    else:
        # approve → yield tool_call (让前端 UI 把卡片状态从 confirm 切到 running),
        # 再真正 dispatch
        yield _sse_event("tool_call", {
            "tool": pending["tool"],
            "args": pending["args"] or {},
            "server_id": "dispatcher",
            "call_id": call_id,
        })
        result = dispatch_assistant_tool(
            user_id=user_id,
            tool=pending["tool"],
            args=pending["args"] or {},
            save_id=pending.get("save_id"),
            script_id=pending.get("script_id"),
            trace_id=trace_id,
            call_id=call_id,
            state_provider=state_provider,
        )
        # task 57 navigate 哨兵识别 (与 _run_llm_loop 一致)
        result_str = result.result or ""
        if isinstance(result_str, str) and result_str.startswith("NAVIGATE:"):
            payload = result_str[len("NAVIGATE:"):]
            try:
                target, _, reason = payload.partition("|")
                target = (target or "").strip()
                reason = (reason or "").strip()
            except Exception:
                target, reason = payload.strip(), ""
            if target:
                yield _sse_event("navigation_required", {
                    "target": target, "reason": reason, "dirty_check": True,
                })
        yield _sse_event("tool_result", {
            "call_id": call_id,
            "ok": bool(result.ok),
            "result": result.result,
            "error": result.error,
            "decision": "approve",
            "tool": pending["tool"],
        })
        # 写回 history 让 LLM 续轮能看见
        conv["messages"].append({
            "role": "assistant",
            "content": _format_tool_result_for_llm(call_id, result),
        })
        _trim_messages(conv)

    # 第 3 步:LLM 续写
    yield from _run_llm_loop(
        user_id=user_id,
        conv=conv,
        page_context=page_context,
        backend=backend,
        state_provider=state_provider,
        trace_id=trace_id,
        max_iterations=max_iterations,
        max_tokens=max_tokens,
    )

    yield _sse_event("done", {
        "pending_confirmations": list(conv["pending_confirmations"].keys()),
    })


__all__ = [
    "stream_chat",
    "apply_confirmation",
    "apply_confirmation_stream",
    "build_system_prompt",
    "list_assistant_tools",
    "dispatch_assistant_tool",
    "get_conversation_state",
    "reset_all_conversations",
    "_new_call_id",
    "_new_trace_id",
    "_new_conversation_id",
]
