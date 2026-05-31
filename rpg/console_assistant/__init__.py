"""console_assistant — 主控台助手 (按职责拆分)."""
from console_assistant.confirmation import apply_confirmation, apply_confirmation_stream
from console_assistant.conversations import (
    _new_call_id,
    _new_conversation_id,
    _new_trace_id,
    _test_only_get_conversation_state,
    _test_only_reset_all_conversations,
    delete_conversation,
    get_conversation_state,
    list_conversations,
    new_conversation,
    reset_all_conversations,
)
from console_assistant.prompts import build_system_prompt
from console_assistant.streaming import stream_chat
from console_assistant.tools import dispatch_assistant_tool, get_tool_spec, list_assistant_tools

__all__ = [
    "new_conversation", "list_conversations", "delete_conversation",
    "_test_only_get_conversation_state", "_test_only_reset_all_conversations",
    # backward-compat aliases (keep until all tests updated)
    "get_conversation_state", "reset_all_conversations",
    "build_system_prompt", "list_assistant_tools", "get_tool_spec",
    "dispatch_assistant_tool", "stream_chat",
    "apply_confirmation", "apply_confirmation_stream",
    "_new_call_id", "_new_trace_id", "_new_conversation_id",
]
