"""mcp.py — MCP server 管理与工具调用路由 (/api/tools + /api/mcp/*)。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
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
    - 普通用户：只看 running 数量与每条的 alive/tools_count，不暴露 server_id / server_info / stderr
    - admin：full stderr + server_info + 全部用户的 audit_log
    """
    is_admin = bool(api_user and api_user.get("role") == "admin")
    import mcp_broker
    payload = mcp_broker.status()
    if not is_admin:
        # 普通用户脱敏：屏蔽 server 标识与实现细节，避免情报收集
        _PUBLIC_FIELDS = {"alive", "tools_count"}
        sanitized = []
        for entry in payload.get("running") or []:
            sanitized.append({k: v for k, v in entry.items() if k in _PUBLIC_FIELDS})
        payload["running"] = sanitized
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
    api_user: dict[str, Any] = Depends(get_current_admin),
) -> JSONResponse:
    """前端或主 GM 调用 MCP 工具的统一入口。

    安全：MCP server 配置全局共享，调用任意工具 = 以服务进程身份执行。
    强制 admin（local 模式同样要求），不再做匿名豁免——避免本地端口被探测出 RCE。
    后续要让 MCP server 支持 per-user 注册再考虑放宽。
    """
    body_dict = body.model_dump(exclude_none=True)
    timeout = int(body_dict.get("timeout", 30))
    if timeout < 1 or timeout > 120:
        return JSONResponse({"ok": False, "error": "timeout 必须在 1-120 秒之间"}, status_code=400)
    import mcp_broker
    return JSONResponse(mcp_broker.call_tool(
        body_dict.get("server_id", ""),
        body_dict.get("tool", ""),
        body_dict.get("arguments", {}) or {},
        timeout=timeout,
        user_id=api_user["id"] if api_user else None,
    ))


@router.get("/api/mcp/tools")
async def api_mcp_tools(
    api_user: dict[str, Any] = Depends(get_current_admin),
) -> JSONResponse:
    """列出所有已启动 server 的工具清单（前端加号菜单/Skill 选择面板用）。

    限 admin：普通用户列出全部 MCP server+tool 名字+schema 等同于情报收集。
    前端"加号菜单"也按 admin 角色控制可见性。
    """
    import mcp_broker
    return JSONResponse({"ok": True, "tools": mcp_broker.discover_all_tools()})


@router.get("/api/admin/tool-usage")
async def api_admin_tool_usage(
    request: Request,
    api_user: dict[str, Any] = Depends(get_current_admin),
) -> JSONResponse:
    """聚合 tool_invocations 表数据 — 看 GM 实际调了哪些工具,失败率多少。

    query params:
      window_hours : 默认 24,聚合最近 N 小时
      user_id      : 可选,只看某用户
      save_id      : 可选,只看某存档
      limit        : top N 工具,默认 30
    """
    from platform_app.db import connect

    qp = request.query_params
    window_hours = int(qp.get("window_hours", "24"))
    user_id = qp.get("user_id")
    save_id = qp.get("save_id")
    limit = int(qp.get("limit", "30"))

    where = ["ts >= now() - interval '%s hours'"]
    params: list[Any] = [window_hours]
    if user_id:
        where.append("user_id = %s")
        params.append(int(user_id))
    if save_id:
        where.append("save_id = %s")
        params.append(int(save_id))

    sql = f"""
        select tool,
               count(*) as calls,
               sum(case when ok then 1 else 0 end) as success,
               sum(case when not ok then 1 else 0 end) as fail,
               count(distinct user_id) as distinct_users,
               count(distinct save_id) as distinct_saves
        from tool_invocations
        where {' and '.join(where)}
        group by tool
        order by calls desc
        limit %s
    """
    params.append(limit)
    with connect() as db:
        rows = db.execute(sql, tuple(params)).fetchall()

    # 也算"dark tools" — 注册了但 window 内 0 调用
    from tools_dsl.command_dispatcher import get_registry
    all_tools = {s.name for s in get_registry().list_for_origin("llm_chat")}
    seen = {r["tool"] for r in rows}
    dark = sorted(all_tools - seen)

    return JSONResponse({
        "ok": True,
        "window_hours": window_hours,
        "by_tool": [
            {
                "tool": r["tool"], "calls": int(r["calls"]),
                "success": int(r["success"]), "fail": int(r["fail"]),
                "fail_rate": round(float(r["fail"]) / float(r["calls"]) if r["calls"] else 0, 3),
                "distinct_users": int(r["distinct_users"]),
                "distinct_saves": int(r["distinct_saves"]),
            }
            for r in rows
        ],
        "dark_tools_count": len(dark),
        "dark_tools_sample": dark[:20],
        "total_registered_llm_chat": len(all_tools),
    })


@router.get("/api/admin/tool-debug")
async def api_admin_tool_debug(
    api_user: dict[str, Any] = Depends(get_current_admin),
) -> JSONResponse:
    """debug: 当前 chat 会注入 GM 的 unified_tools 摘要。
    返回:
      - mcp_tools_count, mcp_tools_sample
      - dispatcher_tools_count (origin=llm_chat),dispatcher_tools_names
      - vertex_fn_decls_simulated:模拟 Vertex backend 的 schema 转换,看会被 drop 多少
    """
    import mcp_broker
    from tools_dsl.chat_tool_router import build_unified_tool_list
    from tools_dsl.command_dispatcher import get_registry
    # 触发 ensure_registered(chat 进入时才注册,debug 端点也要兜)
    try:
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
    except Exception:
        pass
    mcp_tools = mcp_broker.discover_all_tools() or []
    unified = build_unified_tool_list(mcp_tools, origin="llm_chat")
    disp_specs = get_registry().list_for_origin("llm_chat")
    # 模拟 Vertex fn_decls 转换(只看 name 合法 + schema dict)
    import re as _re
    vertex_ok = []
    vertex_dropped = []
    for t in unified[:64]:
        sid = str(t.get("server_id", ""))
        tname = str(t.get("name", ""))
        if not sid or not tname:
            vertex_dropped.append({"reason": "empty_sid_or_name", "name": tname, "sid": sid})
            continue
        safe_sid = _re.sub(r"[^A-Za-z0-9_-]", "_", sid)
        safe_tname = _re.sub(r"[^A-Za-z0-9_-]", "_", tname)
        full = f"{safe_sid}__{safe_tname}"[:64]
        schema_raw = t.get("schema") or {"type": "object", "properties": {}}
        ok = isinstance(schema_raw, dict)
        vertex_ok.append({"full_name": full, "name_truncated": len(f"{safe_sid}__{safe_tname}") > 64,
                          "has_schema_dict": ok})
    return JSONResponse({
        "ok": True,
        "mcp_tools_count": len(mcp_tools),
        "mcp_tools_sample": [t.get("name") for t in mcp_tools[:5]],
        "dispatcher_tools_count": len(disp_specs),
        "dispatcher_tools_names": [s.name for s in disp_specs],
        "unified_tools_count": len(unified),
        "vertex_truncated_to_64": len(unified) > 64,
        "vertex_fn_decls_count": len(vertex_ok),
        "vertex_dropped": vertex_dropped,
        "vertex_fn_decls_sample": vertex_ok[:8],
    })
