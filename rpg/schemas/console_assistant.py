"""schemas.console_assistant — 侧栏控制台助手路由请求模型。"""
from __future__ import annotations
from typing import Optional, Any
from schemas._common import _BaseRequest


class ConsoleAssistantDeleteConversationRequest(_BaseRequest):
    conversation_id: Optional[str] = ""


class ConsoleAssistantChatRequest(_BaseRequest):
    message: Optional[str] = ""
    conversation_id: Optional[str] = None
    page_context: Optional[dict[str, Any]] = None


class ConsoleAssistantConfirmRequest(_BaseRequest):
    conversation_id: Optional[str] = ""
    call_id: Optional[str] = ""
    decision: Optional[str] = ""
    page_context: Optional[dict[str, Any]] = None
