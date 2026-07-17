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


class TestOriginAllowed:
    def test_local_mode_allows_dynamic_loopback_ports(self):
        """本地 Vite 端口变成 5174/5175 时,带 cookie 的 POST 不应被 Origin 守卫误拦。"""
        from core import startup
        with patch("core.startup._local_loopback_origins_allowed", return_value=True), \
                patch.object(startup, "_origins", ["http://127.0.0.1:7860"]):
            assert startup._origin_allowed("http://localhost:5174") is True
            assert startup._origin_allowed("http://127.0.0.1:5175") is True
            assert startup._origin_allowed("http://[::1]:5176") is True

    def test_local_mode_allows_private_lan_origins(self):
        """桌面/自托管:手机/同网设备经 LAN IP(192.168/10/172.16-31)访问应放行——
        修复「LAN 邀请链接/登录报 Origin 不在允许列表」。"""
        from core import startup
        with patch("core.startup._local_loopback_origins_allowed", return_value=True), \
                patch.object(startup, "_origins", ["http://127.0.0.1:7860"]):
            assert startup._origin_allowed("http://192.168.1.4:7860") is True
            assert startup._origin_allowed("http://10.0.0.5:7860") is True
            assert startup._origin_allowed("http://172.16.3.9:7860") is True
            # CGNAT/Tailscale(100.64/10)+ IPv6 ULA:网状 VPN 组网访问自部署应放行
            assert startup._origin_allowed("http://100.98.88.64:7860") is True
            assert startup._origin_allowed("http://100.64.0.1:7860") is True
            assert startup._origin_allowed("http://100.127.255.254:7860") is True
            assert startup._origin_allowed("http://[fd7a:115c:a1e0::1]:7860") is True
            # 边界:172.15/172.32 不在私网段,应拒;100.63/100.128 在 CGNAT 段外应拒
            assert startup._origin_allowed("http://172.15.0.1:7860") is False
            assert startup._origin_allowed("http://172.32.0.1:7860") is False
            assert startup._origin_allowed("http://100.63.0.1:7860") is False
            assert startup._origin_allowed("http://100.128.0.1:7860") is False

    def test_cors_regex_twin_stays_in_sync_with_nets(self):
        """CORS allow_origin_regex 与 _origin_allowed 的 nets 是孪生实现,CGNAT/ULA 两边都要认。"""
        import re
        from core import startup
        rx = re.compile(startup._LOCAL_LAN_ORIGIN_REGEX)
        for ok in ("http://100.98.88.64:7860", "http://192.168.1.4:7860",
                   "http://[fd7a:115c:a1e0::1]:7860", "http://localhost:5174"):
            assert rx.match(ok), ok
        for bad in ("http://8.8.8.8:7860", "http://100.63.0.1:7860", "http://evil.example:5174"):
            assert not rx.match(bad), bad

    def test_local_mode_still_rejects_non_loopback_non_lan_origins(self):
        from core import startup
        with patch("core.startup._local_loopback_origins_allowed", return_value=True), \
                patch.object(startup, "_origins", ["http://127.0.0.1:7860"]):
            assert startup._origin_allowed("http://evil.example:5174") is False
            # 公网 IP 不是私网段,即便本地模式也拒(防 DNS rebinding 到公网回指)
            assert startup._origin_allowed("http://8.8.8.8:7860") is False

    def test_server_mode_rejects_lan_origins(self):
        """server/production 永不放宽:LAN IP 也必须被拒。"""
        from core import startup
        with patch("core.startup._local_loopback_origins_allowed", return_value=False), \
                patch.object(startup, "_origins", ["https://rpg-roleplay.stellatrix.icu"]):
            assert startup._origin_allowed("https://rpg-roleplay.stellatrix.icu") is True
            assert startup._origin_allowed("http://localhost:5174") is False
            assert startup._origin_allowed("http://192.168.1.4:7860") is False


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
