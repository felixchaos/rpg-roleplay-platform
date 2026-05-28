"""console_assistant.confirmation — apply_confirmation / apply_confirmation_stream。"""
from __future__ import annotations

from typing import Any, Callable, Iterator

from tools_dsl.command_dispatcher import ToolCallEnvelope

from console_assistant import _state
from console_assistant.conversations import _new_trace_id, _trim_messages
from console_assistant.llm_loop import (
    _format_tool_result_for_llm, _run_llm_loop, _sse_event,
)
from console_assistant.tools import dispatch_assistant_tool


def _resolve_pending(
    *, user_id: int, conversation_id: str, call_id: str, decision: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """共享步骤:校验 + pop pending。返回 (conv, pending, error_msg)。"""
    decision_norm = (decision or "").strip().lower()
    if decision_norm not in {"approve", "reject"}:
        return None, None, f"decision 非法: {decision!r} (允许 approve/reject)"
    with _state._lock:
        user_bucket = _state._conversations.get(user_id) or {}
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
    """[legacy] 对一个 pending destructive 工具调用做最终决策, 同步返回 dict。"""
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
        # task 57 navigate 哨兵识别
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
        conv["messages"].append({
            "role": "assistant",
            "content": _format_tool_result_for_llm(call_id, result),
        })
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
