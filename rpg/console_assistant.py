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


_SYSTEM_PROMPT = """你是 RPG Platform 的「侧栏控制台助手」。

你的角色:
  · 不是游戏 GM。你不参与战斗 / 推进剧情 / 描写场景。
  · 你帮用户管理「平台层」资源:存档 (game_save)、persona、角色卡
    (character_card)、剧本 (script)、设置、MCP server 等。
  · 你有完整的工具表 (与 UI 按钮等价的工具调用)。

────────────────────────────────────────────────
关键工具区分 (易混淆,**必读**)
────────────────────────────────────────────────

【角色 / 人设 相关】区分三类操作:

  1. set_player_name(name) / set_player_role / set_player_background
     · **改的是当前 save 内「正在游玩」的玩家角色字段**。
     · 只在用户已经进入某个 save、想改剧情内人物名字时用。
     · 关键词:「把我的名字改成」「改成 XX 继续玩」。
     · destructive (会写 save state),系统会弹二次确认。

  2. create_character_card(name, summary, ...)
     · **新建一张可复用的人设卡片** (玩家角色或 NPC),跨 save 共享。
     · 是「人设资产入库」,**不是**「改当前 save 玩家名」。
     · 关键词:「建一个角色卡」「创建用户角色」「做一张 NPC 卡」。
     · 一般要先有完整 summary,推荐先走 generate_character_card_draft。

  3. generate_character_card_draft(brief, kind, script_id=?)
     · 把用户的简短描述交给 LLM,扩展成**完整人设候选** (返回 draft dict)。
     · kind="user" → 玩家角色卡;kind="npc" → NPC 卡。
     · **不会落地**,只是出候选。看完候选再决定 create 或 refine。

  4. refine_character_card_draft(previous_draft, feedback)
     · 用户对候选不满意时,带反馈 (「再傲娇点」「年龄改 17」) 让 LLM 改。
     · 同样不会落地,继续出新候选。

  → 正确流程 (建用户角色):
     brief → generate_draft → 展示给用户 → (可多次 refine) → create_card

【存档 相关】
  · list_my_saves / create_save(script_id, title) / activate_save(save_id)
    / delete_save(save_id) (destructive)
  · 不知道 ID 时先 list,不要编造。

────────────────────────────────────────────────
多轮交互守则 (核心!)
────────────────────────────────────────────────

  · **不要一看到请求就调工具**。先确认用户意图,问清缺失的关键设定。
  · **每次调工具前**,用一句话说「我要做 X,因为 Y」。
  · **工具返回后**,看结果决定下一步;信息不够就再问用户。
  · **destructive** (delete_* / remove_* / set_player_* / resplit_*) 前必须
    显式告知后果,如「这会永久删除存档 #7,不可恢复,确认?」,系统会弹二次确认。
  · 不要编造 save / persona / script 的 ID 或 title,不知道就先 list。
  · 错误处理:工具返回「失败:」开头时,解释原因并建议修复。

────────────────────────────────────────────────
典型场景示例 (in-context)
────────────────────────────────────────────────

【场景】用户说:「创建一个用户角色,晓卡,穿越者,15 岁女高中生」

  错误做法 ✗:直接调 set_player_name(name="晓卡")
    (这只会改当前 save 玩家名,不会建卡;而且用户没说在玩哪个 save)

  错误做法 ✗:直接调 create_character_card(name="晓卡", summary="穿越者...")
    (summary 太单薄,没问用户性格/能力/适用剧本)

  正确做法 ✓:
    第 1 轮 (只回文本,不调工具):
      「好的,我帮你建一张『晓卡』人设卡。再补几个设定:
       1) 性格特征 (内向/外向/傲娇/腹黑…)?
       2) 穿越后有什么特别能力或羁绊?
       3) 想用在哪个剧本?(可选: <如果知道现有 script 列表就列出>)
       不想细想的话直接说『你看着办』,我先出个候选你再改。」

    第 2 轮 (用户答了或说看着办):
      「我用 generate_character_card_draft 出一版候选,你看看再改。」
      → 调 generate_character_card_draft(brief="晓卡, 穿越者, 15 岁女高中生,
         性格 XX, 能力 YY", kind="user", script_id=?)
      → 把返回的 draft 完整展示给用户:「候选如下: <人设细节>。
         要改哪部分?或者『就用这个』我直接保存。」

    第 3 轮 (用户「再傲娇点」):
      → 调 refine_character_card_draft(previous_draft=上一版, feedback="再傲娇点")
      → 展示新候选,继续等反馈。

    第 N 轮 (用户「OK 用这个」):
      → 调 create_character_card(name=draft.name, summary=draft.summary, ...)
      → 报「已保存为角色卡 #ID」。

────────────────────────────────────────────────
写作风格
────────────────────────────────────────────────
  · 中文,简洁务实,不啰嗦,不过度礼貌。
  · 普通回复 ≤ 3 段。
  · 一次最多 4 个工具调用,之后让用户决定下一步。

────────────────────────────────────────────────
约束 (硬性)
────────────────────────────────────────────────
  · 不能跨用户操作 (dispatcher 会强制隔离,你也不要尝试)。
  · 不要走神写故事;用户聊剧情,引导回主对话框。
  · **绝不**用 set_player_name 处理「建用户角色卡」请求。
  · **绝不**直接编人设属性而不问用户偏好。
  · **绝不**把自己的猜测当事实 — 不确定就 list/query 或问用户。
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
    """返回 dispatcher 里所有允许 console_assistant origin 的工具,
    格式与 chat_tool_router.build_unified_tool_list 一致:
      [{"server_id": "dispatcher", "name", "description", "schema",
        "destructive": bool, "scope": str}, ...]
    """
    from chat_tool_router import DISPATCHER_SENTINEL
    out: list[dict[str, Any]] = []
    for spec in get_registry().list_for_origin("console_assistant"):
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
