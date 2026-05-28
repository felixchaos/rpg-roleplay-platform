"""schemas.game — 游戏核心流程路由请求模型。"""
from __future__ import annotations
from typing import Optional, Any
from schemas._common import _BaseRequest


class NewGameRequest(_BaseRequest):
    script_card_id: Optional[Any] = None
    script_id: Optional[Any] = None
    user_card_id: Optional[Any] = None
    persona_id: Optional[Any] = None
    role: Optional[str] = ""
    name: Optional[str] = "无名者"
    background: Optional[str] = ""


class ChatEstimateRequest(_BaseRequest):
    message: Optional[str] = ""
    include_retrieval: Optional[bool] = True


class ChatRequest(_BaseRequest):
    message: Optional[str] = ""
    text: Optional[str] = ""
    attachments: Optional[list[Any]] = None
