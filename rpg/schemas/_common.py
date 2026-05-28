"""schemas._common — 全局共享的基础模型与配置。"""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict


class _BaseRequest(BaseModel):
    """所有请求 model 的基类。extra='ignore' 容忍前端额外字段,保持向后兼容。"""
    model_config = ConfigDict(extra="ignore")


class OkResponse(BaseModel):
    """通用 ok 响应。"""
    ok: bool = True


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str = ""
