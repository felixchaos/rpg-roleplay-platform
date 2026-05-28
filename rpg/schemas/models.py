"""schemas.models — 模型目录与 API 管理路由请求模型。"""
from __future__ import annotations

from typing import Any, Optional

from schemas._common import _BaseRequest


class ModelsSelectRequest(_BaseRequest):
    api_id: str | None = ""
    model_id: str | None = ""


class ModelsUpsertApiRequest(_BaseRequest):
    """upsert_api 直接消费整个 body dict,字段透传即可。"""
    model_config = __import__('pydantic').ConfigDict(extra="allow")


class ModelsUpsertModelRequest(_BaseRequest):
    """model 字段透传。允许前端直接发 flat payload (api_id + 各 model 字段)。"""
    model_config = __import__('pydantic').ConfigDict(extra="allow")
    api_id: str | None = ""
    model: dict[str, Any] | None = None


class ModelsDeleteModelRequest(_BaseRequest):
    api_id: str | None = ""
    model_id: str | None = None
    real_name: str | None = ""


class ModelsProbeRequest(_BaseRequest):
    api_id: str | None = ""
    model: str | None = None
    timeout: int | None = 15
