"""schemas.memory — 记忆管理路由请求模型。"""
from __future__ import annotations

from typing import Optional

from schemas._common import _BaseRequest


class MemoryModeRequest(_BaseRequest):
    mode: str | None = "normal"


class MemoryAddRequest(_BaseRequest):
    bucket: str | None = "notes"
    text: str | None = ""


class MemoryRemoveRequest(_BaseRequest):
    bucket: str | None = "notes"
    index: int | None = -1
