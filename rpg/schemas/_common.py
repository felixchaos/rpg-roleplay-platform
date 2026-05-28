"""schemas._common — 全局共享的基础模型与配置。"""
from __future__ import annotations

from typing import Any

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


class StateResponse(BaseModel):
    """通用 ok + state payload。state 字段结构复杂,允许任意嵌套。"""
    model_config = ConfigDict(extra="allow")
    ok: bool = True
    state: dict[str, Any] | None = None
    error: str | None = None


class GenericOkResponse(BaseModel):
    """通用响应(ok + 任意附加字段)。"""
    model_config = ConfigDict(extra="allow")
    ok: bool = True


# 所有 POST endpoint 统一声明的错误响应 schema
COMMON_ERROR_RESPONSES: dict = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
}
