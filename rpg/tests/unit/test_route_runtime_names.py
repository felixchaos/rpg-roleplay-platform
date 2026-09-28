"""路由里「运行时才炸」的漏 import:ruff F821 全树只剩注解里的(不影响运行),这里锁住会炸的那类。

现场(巡检):
  1. routes/mcp.py 的 /api/admin/tool-usage 写了 `request: Request`,但文件没 import Request。
     模块开着 `from __future__ import annotations`,注解是字符串,FastAPI 解析不出来就把它当成
     一个**必填 query 参数** → 不带 `?request=` 调用一律 422,函数体里的 request.query_params
     也用不了。同一个函数的 SQL 还把占位符写进了字符串字面量 `interval '%s hours'`:psycopg3
     走服务端绑定,字面量里的 `$1` 不会被替换,PG 把 `'$1 hours'` 解析成 1 小时 → 不管
     window_hours 传多少都只统计最近 1 小时。
  2. routes/permissions.py 的 debug 注入端点 `raise HTTPException(...)` 没 import HTTPException,
     text 超过 500 字时 NameError → 500 而不是 400。

锁三件:全应用路由没有解析失败的参数注解(通用守卫,覆盖同类);tool-usage 不带 request
参数可调、窗口参数真的进了 SQL;debug 端点超长文本回 400。
"""
from __future__ import annotations

import asyncio
import typing
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def _iter_api_routes(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):          # FastAPI 0.14x 的 include_router 惰性包装
            yield from _iter_api_routes(r.original_router.routes)
        elif hasattr(r, "routes") and type(r).__name__ != "Mount":
            yield from _iter_api_routes(r.routes)


def _param_annotations(dependant, out):
    for attr in ("path_params", "query_params", "header_params", "cookie_params", "body_params"):
        for f in getattr(dependant, attr, None) or []:
            out.append((attr, f.name, getattr(f.field_info, "annotation", None)))
    for sub in dependant.dependencies:
        _param_annotations(sub, out)


class TestNoUnresolvedRouteAnnotations(unittest.TestCase):
    def test_every_route_param_annotation_resolves(self):
        import app as app_module
        routes = list(_iter_api_routes(app_module.app.routes))
        self.assertGreater(len(routes), 100, "没遍历到路由,守卫失效")
        bad = []
        for r in routes:
            params: list = []
            _param_annotations(r.dependant, params)
            for attr, name, ann in params:
                if isinstance(ann, (typing.ForwardRef, str)) or "ForwardRef" in repr(ann):
                    bad.append(f"{sorted(r.methods)} {r.path} {attr}:{name} -> {ann!r}")
        self.assertEqual(bad, [], "参数注解没解析出来(多半是漏 import),FastAPI 会把它当成必填 query 参数")


class _FakeDb:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        cur = MagicMock()
        cur.fetchall.return_value = [
            {"tool": "roll_dice", "calls": 4, "success": 3, "fail": 1, "distinct_users": 2, "distinct_saves": 2},
        ]
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestAdminToolUsage(unittest.TestCase):
    def _client(self):
        from routes import mcp
        from routes._deps_fastapi import get_current_admin_strict
        app = FastAPI()
        app.include_router(mcp.router)
        app.dependency_overrides[get_current_admin_strict] = lambda: {"id": 1, "role": "admin"}
        return TestClient(app)

    def _get(self, url):
        db = _FakeDb()
        registry = MagicMock()
        registry.list_for_origin.return_value = []
        with patch("platform_app.db.connect", return_value=db), \
             patch("tools_dsl.command_dispatcher.get_registry", return_value=registry):
            resp = self._client().get(url)
        return resp, db

    def test_callable_without_request_query_param(self):
        resp, _db = self._get("/api/admin/tool-usage")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["window_hours"], 24)
        self.assertEqual(body["by_tool"][0]["tool"], "roll_dice")

    def test_window_hours_is_bound_not_inside_literal(self):
        resp, db = self._get("/api/admin/tool-usage?window_hours=168&limit=5")
        self.assertEqual(resp.status_code, 200, resp.text)
        sql, params = db.calls[0]
        self.assertNotRegex(sql, r"'[^']*%s[^']*'", "占位符写在字符串字面量里,服务端绑定不会替换")
        self.assertEqual(params, (168, 5))


class TestDebugPendingQuestion(unittest.TestCase):
    def test_overlong_text_is_400_not_nameerror(self):
        import app as app_module
        from routes import permissions
        from schemas.permissions import DebugPendingQuestionRequest
        body = DebugPendingQuestionRequest(text="字" * 501)
        with patch("core.config.debug_ui", return_value=True), \
             patch.object(app_module, "_ensure_loaded", return_value=MagicMock()):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(permissions.api_debug_pending_question(body, api_user={"id": 1, "role": "admin"}))
        self.assertEqual(cm.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
