"""mcp.py — MCP server 管理与工具调用路由 (/api/tools + /api/mcp/*)。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_admin, get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, ErrorResponse, GenericOkResponse
from schemas.mcp import (
    McpServerDeleteRequest,
    McpServerEnabledRequest,
    McpServerRequest,
    McpServerStartRequest,
    McpServerStopRequest,
    McpServerValidateRequest,
    McpToolCallRequest,
)

router = APIRouter()


@router.get("/api/tools")
async def api_tools(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    from app import _redact_tools, tool_payload
    is_admin = bool(api_user and api_user.get("role") == "admin")
    return JSONResponse({"ok": True, "tools": _redact_tools(tool_payload(), is_admin)})


@router.post("/api/mcp/server", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_mcp_server(
    body: McpServerRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import tool_payload, upsert_mcp_server
    try:
        body_dict = body.model_dump()
        catalog = upsert_mcp_server(body_dict)
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/mcp/server/enabled", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_mcp_server_enabled(
    body: McpServerEnabledRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import set_mcp_server_enabled, tool_payload
    body_dict = body.model_dump(exclude_none=True)
    try:
        catalog = set_mcp_server_enabled(body_dict.get("id", ""), bool(body_dict.get("enabled", True)))
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/mcp/server/delete", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_mcp_server_delete(
    body: McpServerDeleteRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import delete_mcp_server, tool_payload
    body_dict = body.model_dump(exclude_none=True)
    try:
        catalog = delete_mcp_server(body_dict.get("id", ""))
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except PermissionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/mcp/server/validate", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_mcp_server_validate(
    body: McpServerValidateRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import validate_mcp_server
    body_dict = body.model_dump(exclude_none=True)
    try:
        return JSONResponse({"ok": True, "result": validate_mcp_server(body_dict.get("id", ""))})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/mcp/server/start", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_mcp_server_start(
    body: McpServerStartRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    body_dict = body.model_dump(exclude_none=True)
    import mcp_broker
    return JSONResponse(mcp_broker.start_server(body_dict.get("id", "")))


@router.post("/api/mcp/server/stop", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_mcp_server_stop(
    body: McpServerStopRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    body_dict = body.model_dump(exclude_none=True)
    import mcp_broker
    return JSONResponse(mcp_broker.stop_server(body_dict.get("id", "")))


@router.get("/api/mcp/runtime")
async def api_mcp_runtime(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """MCP 运行时状态 + per-user 调用审计。
    - 普通用户：拿不到 stderr（可能含 token/路径），audit_log 只看自己的
    - admin：full stderr + 全部用户的 audit_log
    """
    is_admin = bool(api_user and api_user.get("role") == "admin")
    import mcp_broker
    payload = mcp_broker.status()
    if not is_admin:
        for entry in payload.get("running") or []:
            entry.pop("last_stderr", None)
    # P0 #3：附 audit_log，让管理员能查跨用户 MCP 调用
    try:
        audit = mcp_broker.get_audit_log(
            user_id=None if is_admin else (api_user["id"] if api_user else None),
            limit=200,
        )
        payload["audit_log"] = audit
    except Exception:
        payload["audit_log"] = []
    return JSONResponse(payload)


@router.post("/api/mcp/tool/call", response_model=GenericOkResponse, responses={**COMMON_ERROR_RESPONSES, 403: {"model": ErrorResponse}})
async def api_mcp_tool_call(
    body: McpToolCallRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """前端或主 GM 调用 MCP 工具的统一入口。

    安全：MCP server 配置目前是全局共享，调用任意工具等于以服务进程身份执行。
    在多用户/服务器模式下只允许 admin；本地匿名模式才允许任意调用。
    后续要让 MCP server 支持 per-user 注册再放宽。
    """
    from app import _api_auth_required
    if _api_auth_required() and (not api_user or api_user.get("role") != "admin"):
        return JSONResponse({"ok": False, "error": "MCP 工具调用目前仅限管理员（per-user 注册待支持）"}, status_code=403)
    body_dict = body.model_dump(exclude_none=True)
    import mcp_broker
    return JSONResponse(mcp_broker.call_tool(
        body_dict.get("server_id", ""),
        body_dict.get("tool", ""),
        body_dict.get("arguments", {}) or {},
        timeout=int(body_dict.get("timeout", 30)),
        user_id=api_user["id"] if api_user else None,
    ))


@router.get("/api/mcp/tools")
async def api_mcp_tools(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """列出所有已启动 server 的工具清单（前端加号菜单/Skill 选择面板用）。"""
    import mcp_broker
    return JSONResponse({"ok": True, "tools": mcp_broker.discover_all_tools()})
