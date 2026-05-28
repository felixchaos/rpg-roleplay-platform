"""schemas.console_assistant — 侧栏控制台助手路由请求模型。"""
from __future__ import annotations

from typing import Any

from schemas._common import _BaseRequest


class ConsoleAssistantDeleteConversationRequest(_BaseRequest):
    conversation_id: str | None = ""


class ConsoleAssistantChatRequest(_BaseRequest):
    message: str | None = ""
    conversation_id: str | None = None
    page_context: dict[str, Any] | None = None


class ConsoleAssistantConfirmRequest(_BaseRequest):
    conversation_id: str | None = ""
    call_id: str | None = ""
    decision: str | None = ""
    page_context: dict[str, Any] | None = None
