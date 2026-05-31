"""tests/test_security_headers.py — 安全 headers 单元/集成测试。

覆盖:
  - HTML 路径返回 CSP / X-Frame-Options / HSTS(prod 反代模拟)
  - JSON API 路径不含 CSP
  - Sec-GPC: 1 → X-GPC-Acknowledged: 1
  - Set-Cookie 强制带 Secure/HttpOnly/SameSite=Lax
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保 rpg/ 在 sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ── 辅助:构造最简 Request / Response mock ─────────────────────────────────

def _make_request(path: str, scheme: str = "https", headers: dict | None = None,
                  xfp: str | None = None):
    """构造 Starlette Request mock。"""
    req = MagicMock()
    req.scope = {"path": path}
    req.method = "GET"
    hdr = dict(headers or {})
    if xfp:
        hdr["x-forwarded-proto"] = xfp
    req.headers = hdr
    req.url.scheme = scheme
    req.state = MagicMock()
    return req


def _make_response(content_type: str = "text/html", set_cookie: str | None = None):
    """构造 Starlette Response mock with MutableHeaders behaviour."""
    from starlette.responses import Response
    resp = Response(content=b"ok", media_type=content_type)
    if set_cookie:
        resp.headers.append("set-cookie", set_cookie)
    return resp


# ── 直接单元测试 _harden_set_cookie / _is_https / _build_csp ──────────────

class TestBuildCsp:
    def test_prod_csp_has_frame_ancestors_none(self):
        from core.startup import _build_csp
        csp = _build_csp(dev=False)
        assert "frame-ancestors 'none'" in csp

    def test_prod_csp_has_self_connect_src(self):
        from core.startup import _build_csp
        csp = _build_csp(dev=False)
        assert "connect-src" in csp
        assert "api.anthropic.com" in csp

    def test_dev_csp_includes_localhost_ws(self):
        from core.startup import _build_csp
        csp = _build_csp(dev=True)
        assert "ws://localhost:*" in csp

    def test_prod_csp_no_localhost(self):
        from core.startup import _build_csp
        csp = _build_csp(dev=False)
        assert "localhost" not in csp

    def test_form_action_self(self):
        from core.startup import _build_csp
        csp = _build_csp(dev=False)
        assert "form-action 'self'" in csp

    def test_base_uri_self(self):
        from core.startup import _build_csp
        csp = _build_csp(dev=False)
        assert "base-uri 'self'" in csp


class TestIsHttps:
    def test_direct_https(self):
        from core.startup import _is_https
        req = _make_request("/", scheme="https")
        assert _is_https(req) is True

    def test_direct_http_no_proxy(self):
        from core.startup import _is_https
        req = _make_request("/", scheme="http")
        with patch("core.startup._trusted_proxies", return_value=None):
            assert _is_https(req) is False

    def test_xfp_trusted_proxy(self):
        from core.startup import _is_https
        req = _make_request("/", scheme="http", xfp="https")
        with patch("core.startup._trusted_proxies", return_value="10.0.0.1"):
            assert _is_https(req) is True

    def test_xfp_ignored_without_trusted_proxy(self):
        from core.startup import _is_https
        req = _make_request("/", scheme="http", xfp="https")
        with patch("core.startup._trusted_proxies", return_value=None):
            assert _is_https(req) is False


class TestHardenSetCookie:
    def test_adds_httponly(self):
        from core.startup import _harden_set_cookie
        result = _harden_set_cookie("rpg_session=abc123", is_https=False)
        assert "HttpOnly" in result

    def test_adds_samesite_lax(self):
        from core.startup import _harden_set_cookie
        result = _harden_set_cookie("rpg_session=abc123", is_https=False)
        assert "SameSite=Lax" in result

    def test_adds_secure_on_https(self):
        from core.startup import _harden_set_cookie
        result = _harden_set_cookie("rpg_session=abc123", is_https=True)
        assert "Secure" in result

    def test_no_secure_on_http(self):
        from core.startup import _harden_set_cookie
        result = _harden_set_cookie("rpg_session=abc123", is_https=False)
        assert "Secure" not in result

    def test_adds_max_age_14days(self):
        from core.startup import _harden_set_cookie
        result = _harden_set_cookie("rpg_session=abc123", is_https=False)
        assert "Max-Age=1209600" in result

    def test_does_not_duplicate_httponly(self):
        from core.startup import _harden_set_cookie
        result = _harden_set_cookie("rpg_session=abc123; HttpOnly; SameSite=Strict", is_https=True)
        assert result.count("HttpOnly") == 1

    def test_non_whitelist_cookie_unchanged(self):
        from core.startup import _harden_set_cookie
        # csrf 不在白名单,不应被修改
        original = "csrf_token=xyz; Path=/"
        result = _harden_set_cookie(original, is_https=True)
        assert result == original

    def test_rpg_lang_cookie_hardened(self):
        from core.startup import _harden_set_cookie
        result = _harden_set_cookie("rpg.lang=zh-CN", is_https=True)
        assert "HttpOnly" in result


# ── GPC ───────────────────────────────────────────────────────────────────

class TestGpc:
    def test_parse_gpc_true(self):
        from platform_app.privacy import parse_gpc
        req = MagicMock()
        req.headers = {"sec-gpc": "1"}
        assert parse_gpc(req) is True

    def test_parse_gpc_false(self):
        from platform_app.privacy import parse_gpc
        req = MagicMock()
        req.headers = {}
        assert parse_gpc(req) is False

    def test_annotate_gpc_sets_header(self):
        from platform_app.privacy import annotate_gpc
        req = MagicMock()
        req.headers = {"sec-gpc": "1"}
        from starlette.responses import Response
        resp = Response(content=b"ok")
        annotate_gpc(req, resp)
        assert resp.headers.get("x-gpc-acknowledged") == "1"

    def test_annotate_gpc_no_header_when_absent(self):
        from platform_app.privacy import annotate_gpc
        req = MagicMock()
        req.headers = {}
        from starlette.responses import Response
        resp = Response(content=b"ok")
        annotate_gpc(req, resp)
        assert "x-gpc-acknowledged" not in resp.headers


# ── 集成:middleware 端到端(通过 ASGI TestClient)────────────────────────

class TestMiddlewareIntegration:
    """借助 FastAPI TestClient 验证 api_contract_middleware 行为。"""

    @pytest.fixture(autouse=True)
    def _patch_env(self, monkeypatch):
        monkeypatch.setenv("RPG_ENV", "prod")
        monkeypatch.setenv("RPG_TRUSTED_PROXIES", "10.0.0.1")
        monkeypatch.setenv("RPG_DEPLOYMENT_MODE", "production")

    def _build_app(self):
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse as _JSONResponse
        from core.startup import configure_app, lifespan

        # 最小 app,不启动 lifespan(避免 DB 依赖)
        mini = FastAPI()
        configure_app(mini)

        @mini.get("/")
        async def html_root():
            return HTMLResponse("<html><body>ok</body></html>")

        @mini.get("/api/v1/ping")
        async def api_ping():
            return _JSONResponse({"ok": True})

        @mini.get("/set-cookie-test")
        async def set_cookie_endpoint():
            from starlette.responses import Response
            r = Response(content=b'{"ok":true}', media_type="application/json")
            r.set_cookie("rpg_session", "test123")
            return r

        return mini

    def test_html_has_csp(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._build_app(), raise_server_exceptions=False)
        resp = client.get("/")
        assert "content-security-policy" in resp.headers or "Content-Security-Policy" in resp.headers

    def test_html_x_frame_deny(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._build_app(), raise_server_exceptions=False)
        resp = client.get("/")
        xfo = resp.headers.get("x-frame-options", "")
        assert xfo.upper() == "DENY"

    def test_api_no_csp(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._build_app(), raise_server_exceptions=False)
        resp = client.get("/api/v1/ping")
        assert "content-security-policy" not in resp.headers

    def test_hsts_with_xfp_https(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._build_app(), raise_server_exceptions=False)
        # TestClient 默认 http,但我们模拟 X-Forwarded-Proto: https
        resp = client.get("/", headers={"X-Forwarded-Proto": "https"})
        hsts = resp.headers.get("strict-transport-security", "")
        assert "max-age=31536000" in hsts

    def test_gpc_acknowledged(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._build_app(), raise_server_exceptions=False)
        resp = client.get("/", headers={"Sec-GPC": "1"})
        assert resp.headers.get("x-gpc-acknowledged") == "1"

    def test_no_gpc_header_when_not_sent(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._build_app(), raise_server_exceptions=False)
        resp = client.get("/")
        assert "x-gpc-acknowledged" not in resp.headers

    def test_set_cookie_hardened(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._build_app(), raise_server_exceptions=False)
        resp = client.get(
            "/set-cookie-test",
            headers={"X-Forwarded-Proto": "https"},
        )
        sc = resp.headers.get("set-cookie", "")
        assert "HttpOnly" in sc
        assert "SameSite" in sc
