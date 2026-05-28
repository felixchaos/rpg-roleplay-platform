"""schemas.memory — 记忆管理路由请求模型。"""
from __future__ import annotations
from typing import Optional
from schemas._common import _BaseRequest


class MemoryModeRequest(_BaseRequest):
    mode: Optional[str] = "normal"


class MemoryAddRequest(_BaseRequest):
    bucket: Optional[str] = "notes"
    text: Optional[str] = ""


class MemoryRemoveRequest(_BaseRequest):
    bucket: Optional[str] = "notes"
    index: Optional[int] = -1
