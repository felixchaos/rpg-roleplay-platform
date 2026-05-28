"""schemas.worldline — 世界线变量管理路由请求模型。"""
from __future__ import annotations

from typing import Optional

from schemas._common import _BaseRequest


class WorldlineVariableRequest(_BaseRequest):
    key: str | None = ""
    value: str | None = ""


class WorldlineVariableRemoveRequest(_BaseRequest):
    key: str | None = ""
