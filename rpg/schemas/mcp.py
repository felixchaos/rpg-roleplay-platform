"""schemas.mcp — MCP server 管理与工具调用路由请求模型。"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from schemas._common import _BaseRequest

_Str256 = Annotated[str, Field(max_length=256)]


class McpServerRequest(_BaseRequest):
    """upsert_mcp_server 直接消费整个 body,字段透传。
    已知字段加了 max_length=256 约束;其余透传字段仍允许(extra="allow")。
    """
    model_config = __import__('pydantic').ConfigDict(extra="allow")
    id: _Str256 | None = None
    name: _Str256 | None = None
    command: _Str256 | None = None
    url: _Str256 | None = None


class McpServerEnabledRequest(_BaseRequest):
    id: str | None = ""
    enabled: bool | None = True


class McpServerDeleteRequest(_BaseRequest):
    id: str | None = ""


class McpServerValidateRequest(_BaseRequest):
    id: str | None = ""


class McpServerStartRequest(_BaseRequest):
    id: str | None = ""


class McpServerStopRequest(_BaseRequest):
    id: str | None = ""


class McpToolCallRequest(_BaseRequest):
    server_id: str | None = ""
    tool: str | None = ""
    arguments: dict[str, Any] | None = None
    timeout: int | None = 30
