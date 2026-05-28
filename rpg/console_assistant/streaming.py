"""console_assistant.streaming — stream_chat 主入口。"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from console_assistant.conversations import (
    _get_or_create_conversation,
    _new_trace_id,
    _trim_messages,
)
from console_assistant.llm_loop import _run_llm_loop, _sse_event

# re-export for backward compat (tests / routes import _to_backend_messages from streaming indirectly)
from console_assistant.llm_loop import _to_backend_messages as _to_backend_messages  # noqa: F401
from tools_dsl.command_dispatcher import ToolCallEnvelope


def stream_chat(
    *,
    user_id: int,
    message: str,
    conversation_id: str | None,
    page_context: dict[str, Any] | None,
    backend: Any,
    state_provider: Callable[[ToolCallEnvelope], Any] | None = None,
    max_iterations: int = 10,
    max_tokens: int = 1200,
) -> Iterator[str]:
    """主循环 — yield SSE 文本块。"""
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

    conv["messages"].append({"role": "user", "content": message.strip()})
    conv["last_user_message"] = message.strip()
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
