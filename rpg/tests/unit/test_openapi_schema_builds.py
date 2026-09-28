"""test_openapi_schema_builds.py — 全站路由的参数注解都要解析得出来。

routes/mcp.py 顶着 `from __future__ import annotations`,/api/admin/tool-usage 写了
`request: Request` 却没 import Request。注解成了解析不出的前向引用,FastAPI 就把 request 当成
一个必填的查询参数:这个管理员端点每次都 422「query.request Field required」,同时整站
app.openapi() 抛 PydanticUserError(/openapi.json、/docs 打不开,scripts/gen_openapi 也跑不动)。

守卫按「整站 schema 能生成」来断言,同一类漏 import(任何路由模块)都会在这里红。
"""
from __future__ import annotations

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def _walk(routes):
    # include_router 在新版 FastAPI 里包成惰性 _IncludedRouter,app.routes 顶层看不到里面的路由
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
            continue
        sub = getattr(r, "router", None) or getattr(r, "original_router", None)
        if sub is not None and hasattr(sub, "routes") and type(r).__name__ != "Mount":
            yield from _walk(sub.routes)


def test_app_openapi_schema_builds():
    import app as _app
    _app.app.openapi_schema = None
    schema = _app.app.openapi()
    assert len(schema.get("paths", {})) > 100


def test_admin_tool_usage_has_no_bogus_request_query_param():
    import app as _app
    from routes._deps_fastapi import get_current_admin_strict

    route = next(r for r in _walk(_app.app.router.routes) if r.path == "/api/admin/tool-usage")
    names = [p.name for p in route.dependant.query_params]
    assert "request" not in names, "Request 注解没解析出来,request 被当成了必填查询参数"

    _app.app.dependency_overrides[get_current_admin_strict] = lambda: {"id": 1, "role": "admin"}
    try:
        resp = TestClient(_app.app, raise_server_exceptions=False).get("/api/admin/tool-usage")
    finally:
        _app.app.dependency_overrides.pop(get_current_admin_strict, None)
    assert resp.status_code != 422, resp.text[:200]
