"""console_assistant.confirmation — apply_confirmation / apply_confirmation_stream。"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from console_assistant import _state
from console_assistant.conversations import (
    _load_conv_redis,
    _new_trace_id,
    _trim_messages,
    persist_conversation,
)
from console_assistant.llm_loop import (
    _format_tool_result_for_llm,
    _run_llm_loop,
    _sse_event,
)
from console_assistant.tools import dispatch_assistant_tool
from tools_dsl.command_dispatcher import ToolCallEnvelope


def _resolve_pending(
    *, user_id: int, conversation_id: str, call_id: str, decision: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """check-and-claim 原子化:在锁内同时校验+pop, 防止双 approve 导致 destructive tool 跑两次。

    旧实现锁内只 get、锁外 dispatch 后才 pop, 形成 TOCTOU 窗口。
    新实现：拿到锁就 pop, 返回 pending 副本; dispatch 失败时由调用方决定是否回填
    （目前 dispatch 失败也算"已消费"，避免无限重试 destructive 操作）。
    """
    decision_norm = (decision or "").strip().lower()
    if decision_norm not in {"approve", "reject"}:
        return None, None, f"decision 非法: {decision!r} (允许 approve/reject)"
    with _state._lock:
        user_bucket = _state._conversations.setdefault(user_id, {})
        conv = user_bucket.get(conversation_id)
        if not conv:
            # 多 worker:/chat 在别的 worker 建的对话本进程没有 → 从 Redis 拉回(根治"conversation 不存在")
            conv = _load_conv_redis(user_id, conversation_id)
            if conv is not None:
                user_bucket[conversation_id] = conv
        if not conv:
            # dual-store 不一致修:Redis TTL(6h)过期 / Redis 不可达时,回落 PG 永久层 —— 否则玩家
            # 隔几小时回来点确认就报「对话不存在」。_get_or_create 走的是 Redis||PG,这里之前只 Redis。
            from console_assistant.conversations import _load_conv_pg as _lcpg
            conv = _lcpg(user_id, conversation_id)
            if conv is not None:
                user_bucket[conversation_id] = conv
        if not conv:
            return None, None, f"conversation {conversation_id} 不存在或不属于当前用户"
        # 原子 pop：第二个 approve 拿到 None 直接返回错误
        pending = conv.get("pending_confirmations", {}).pop(call_id, None)
    # pop 后立刻回写 Redis,缩小跨 worker 双 approve 竞态窗口(worldbook 写本身幂等)
    if pending:
        persist_conversation(user_id, conversation_id, conv)
    if not pending:
        return conv, None, f"call_id={call_id} 没有 pending 记录或已被消费"
    return conv, pending, None


def _pop_pending(conv: dict[str, Any], call_id: str) -> None:
    """[已废弃] pop 已合并入 _resolve_pending 的原子段，保留 no-op 防止旧路径残留。"""
    return None


def apply_confirmation(
    *,
    user_id: int,
    conversation_id: str,
    call_id: str,
    decision: str,
    state_provider: Callable[[ToolCallEnvelope], Any] | None = None,
) -> dict[str, Any]:
    """[legacy] 对一个 pending destructive 工具调用做最终决策, 同步返回 dict。"""
    conv, pending, err = _resolve_pending(
        user_id=user_id, conversation_id=conversation_id,
        call_id=call_id, decision=decision,
    )
    if err:
        return {"ok": False, "error": err}
    decision_norm = decision.strip().lower()

    if decision_norm == "reject":
        _pop_pending(conv, call_id)
        conv["messages"].append({
            "role": "assistant",
            "content": f"[确认拒绝] 工具 {pending['tool']} (call_id={call_id}) 已被用户拒绝, 未执行。",
        })
        _trim_messages(conv)
        return {"ok": True, "decision": "reject", "tool": pending["tool"]}

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
    _pop_pending(conv, call_id)
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
    max_iterations: int = 10,
    max_tokens: int = 1200,
) -> Iterator[str]:
    """task 58: SSE 版 confirm — 执行/拒绝 destructive 工具, 然后让 LLM 续写。"""
    trace_id = _new_trace_id()

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

    if decision_norm == "reject":
        _pop_pending(conv, call_id)
        reject_note = (
            f"[确认拒绝] 工具 {pending['tool']} (call_id={call_id}) "
            f"已被用户拒绝, 未执行。"
        )
        conv["messages"].append({"role": "assistant", "content": reject_note})
        _trim_messages(conv)
        yield _sse_event("tool_result", {
            "call_id": call_id,
            "ok": False,
            "result": None,
            "error": "用户拒绝执行",
            "decision": "reject",
            "tool": pending["tool"],
        })
    else:
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
        _pop_pending(conv, call_id)
        # task 57 navigate 哨兵识别（白名单 + reason 净化, 同 llm_loop）
        result_str = result.result or ""
        if isinstance(result_str, str) and result_str.startswith("NAVIGATE:"):
            from console_assistant.llm_loop import _NAV_TARGETS_WHITELIST
            payload = result_str[len("NAVIGATE:"):]
            try:
                target, _, reason = payload.partition("|")
                target = (target or "").strip()
                reason = (reason or "").strip()
            except Exception:
                target, reason = payload.strip(), ""
            if target not in _NAV_TARGETS_WHITELIST:
                target = ""
            if reason:
                reason = "".join(ch for ch in reason if ch.isprintable())[:80]
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
        conv["messages"].append({
            "role": "assistant",
            "content": _format_tool_result_for_llm(call_id, result),
        })
        _trim_messages(conv)

    disconnected = False
    try:
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
    except GeneratorExit:
        disconnected = True
        raise
    finally:
        # 跨 worker:确认回合后(已消费 pending + 续写)把对话写回 Redis(persist 无 yield,断开时也安全)
        persist_conversation(user_id, conversation_id, conv)
        if not disconnected:
            yield _sse_event("done", {
                "pending_confirmations": list(conv["pending_confirmations"].keys()),
            })
