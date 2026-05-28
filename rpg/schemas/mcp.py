"""schemas.mcp — MCP server 管理与工具调用路由请求模型。"""
from __future__ import annotations
from typing import Optional, Any
from schemas._common import _BaseRequest


class McpServerRequest(_BaseRequest):
    """upsert_mcp_server 直接消费整个 body,字段透传。"""
    model_config = __import__('pydantic').ConfigDict(extra="allow")


class McpServerEnabledRequest(_BaseRequest):
    id: Optional[str] = ""
    enabled: Optional[bool] = True


class McpServerDeleteRequest(_BaseRequest):
    id: Optional[str] = ""


class McpServerValidateRequest(_BaseRequest):
    id: Optional[str] = ""


class McpServerStartRequest(_BaseRequest):
    id: Optional[str] = ""


class McpServerStopRequest(_BaseRequest):
    id: Optional[str] = ""


class McpToolCallRequest(_BaseRequest):
    server_id: Optional[str] = ""
    tool: Optional[str] = ""
    arguments: Optional[dict[str, Any]] = None
    timeout: Optional[int] = 30
