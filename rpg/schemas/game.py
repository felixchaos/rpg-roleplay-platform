"""schemas.game — 游戏核心流程路由请求模型。"""
from __future__ import annotations

from typing import Any

from schemas._common import _BaseRequest


class NewGameRequest(_BaseRequest):
    script_card_id: Any | None = None
    script_id: Any | None = None
    user_card_id: Any | None = None
    persona_id: Any | None = None
    role: str | None = ""
    name: str | None = "无名者"
    background: str | None = ""


class ChatEstimateRequest(_BaseRequest):
    message: str | None = ""
    include_retrieval: bool | None = True


class ChatRequest(_BaseRequest):
    message: str | None = ""
    text: str | None = ""
    attachments: list[Any] | None = None
