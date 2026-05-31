"""schemas.worldline — 世界线变量管理路由请求模型。"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field

from schemas._common import _BaseRequest


class WorldlineVariableRequest(_BaseRequest):
    key: Annotated[str, Field(max_length=128)] | None = ""
    value: Annotated[str, Field(max_length=4000)] | None = ""


class WorldlineVariableRemoveRequest(_BaseRequest):
    key: Annotated[str, Field(max_length=128)] | None = ""
