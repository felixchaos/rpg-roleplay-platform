"""schemas.models — 模型目录与 API 管理路由请求模型。"""
from __future__ import annotations
from typing import Optional, Any
from schemas._common import _BaseRequest


class ModelsSelectRequest(_BaseRequest):
    api_id: Optional[str] = ""
    model_id: Optional[str] = ""


class ModelsUpsertApiRequest(_BaseRequest):
    """upsert_api 直接消费整个 body dict,字段透传即可。"""
    model_config = __import__('pydantic').ConfigDict(extra="allow")


class ModelsUpsertModelRequest(_BaseRequest):
    """model 字段透传。允许前端直接发 flat payload (api_id + 各 model 字段)。"""
    model_config = __import__('pydantic').ConfigDict(extra="allow")
    api_id: Optional[str] = ""
    model: Optional[dict[str, Any]] = None


class ModelsDeleteModelRequest(_BaseRequest):
    api_id: Optional[str] = ""
    model_id: Optional[str] = None
    real_name: Optional[str] = ""


class ModelsProbeRequest(_BaseRequest):
    api_id: Optional[str] = ""
    model: Optional[str] = None
    timeout: Optional[int] = 15
