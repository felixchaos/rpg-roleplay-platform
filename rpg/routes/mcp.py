"""mcp.py — MCP server 管理与工具调用路由 (/api/tools + /api/mcp/*)。"""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/tools")
async def api_tools(request: Request) -> JSONResponse:
    from app import _require_api_user, _redact_tools, tool_payload
    api_user = _require_api_user(request)
    is_admin = bool(api_user and api_user.get("role") == "admin")
    return JSONResponse({"ok": True, "tools": _redact_tools(tool_payload(), is_admin)})


@router.post("/api/mcp/server")
async def api_mcp_server(request: Request) -> JSONResponse:
    from app import _require_api_user, tool_payload, upsert_mcp_server
    _require_api_user(request, admin=True)
    try:
        catalog = upsert_mcp_server(await request.json())
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/mcp/server/enabled")
async def api_mcp_server_enabled(request: Request) -> JSONResponse:
    from app import _require_api_user, tool_payload, set_mcp_server_enabled
    _require_api_user(request, admin=True)
    body = await request.json()
    try:
        catalog = set_mcp_server_enabled(body.get("id", ""), bool(body.get("enabled", True)))
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/mcp/server/delete")
async def api_mcp_server_delete(request: Request) -> JSONResponse:
    from app import _require_api_user, tool_payload, delete_mcp_server
    _require_api_user(request, admin=True)
    body = await request.json()
    try:
        catalog = delete_mcp_server(body.get("id", ""))
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except PermissionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/mcp/server/validate")
async def api_mcp_server_validate(request: Request) -> JSONResponse:
    from app import _require_api_user, validate_mcp_server
    _require_api_user(request, admin=True)
    body = await request.json()
    try:
        return JSONResponse({"ok": True, "result": validate_mcp_server(body.get("id", ""))})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/mcp/server/start")
async def api_mcp_server_start(request: Request) -> JSONResponse:
    from app import _require_api_user
    _require_api_user(request, admin=True)
    body = await request.json()
    import mcp_broker
    return JSONResponse(mcp_broker.start_server(body.get("id", "")))


@router.post("/api/mcp/server/stop")
async def api_mcp_server_stop(request: Request) -> JSONResponse:
    from app import _require_api_user
    _require_api_user(request, admin=True)
    body = await request.json()
    import mcp_broker
    return JSONResponse(mcp_broker.stop_server(body.get("id", "")))


@router.get("/api/mcp/runtime")
async def api_mcp_runtime(request: Request) -> JSONResponse:
    """MCP 运行时状态 + per-user 调用审计。
    - 普通用户：拿不到 stderr（可能含 token/路径），audit_log 只看自己的
    - admin：full stderr + 全部用户的 audit_log
    """
    from app import _require_api_user
    api_user = _require_api_user(request)
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


@router.post("/api/mcp/tool/call")
async def api_mcp_tool_call(request: Request) -> JSONResponse:
    """前端或主 GM 调用 MCP 工具的统一入口。

    安全：MCP server 配置目前是全局共享，调用任意工具等于以服务进程身份执行。
    在多用户/服务器模式下只允许 admin；本地匿名模式才允许任意调用。
    后续要让 MCP server 支持 per-user 注册再放宽。
    """
    from app import _require_api_user, _api_auth_required
    api_user = _require_api_user(request)
    if _api_auth_required() and (not api_user or api_user.get("role") != "admin"):
        return JSONResponse({"ok": False, "error": "MCP 工具调用目前仅限管理员（per-user 注册待支持）"}, status_code=403)
    body = await request.json()
    import mcp_broker
    return JSONResponse(mcp_broker.call_tool(
        body.get("server_id", ""),
        body.get("tool", ""),
        body.get("arguments", {}) or {},
        timeout=int(body.get("timeout", 30)),
        user_id=api_user["id"] if api_user else None,
    ))


@router.get("/api/mcp/tools")
async def api_mcp_tools(request: Request) -> JSONResponse:
    """列出所有已启动 server 的工具清单（前端加号菜单/Skill 选择面板用）。"""
    from app import _require_api_user
    _require_api_user(request)
    import mcp_broker
    return JSONResponse({"ok": True, "tools": mcp_broker.discover_all_tools()})
