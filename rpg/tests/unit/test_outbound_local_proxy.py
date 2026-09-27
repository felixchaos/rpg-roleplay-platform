"""
test_outbound_local_proxy.py
============================

反馈 #107(本地版配 API 全部失败)的出站层回归:本地/自部署单用户模式下,

1. 没配凭据代理时跟随环境变量 / 系统代理 —— 2026-06 SSRF 加固给 httpx 塞了自定义
   transport,httpx 就不再读环境代理(`allow_env_proxies = trust_env and transport is None`),
   桌面版从此「浏览器能通、后端不通」。这里用本地起的代理桩验证请求真的经过了代理。
2. 显式凭据代理照常生效(httpx 与 urllib 两条出站都是)。
3. 无论显式代理还是环境代理,本机 / 局域网目标一律直连 —— 本机 Ollama 绝不被送进代理
   (httpx 的 NO_PROXY 不认网段,也不认 macOS / Windows 的系统例外,所以要我们自己兜)。
4. 服务器模式行为不变:挂 SSRF 守卫 transport,不读环境代理。

部署模式一律用 mock 钉住 `_ssrf_enforced`,不随运行环境漂;环境代理变量逐个 monkeypatch。
"""
from __future__ import annotations

import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.request import Request

import httpx
import pytest

from core import outbound
from core.outbound import UnsupportedProxy, _is_local_target, safe_httpx_client, safe_urlopen

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy")


def _serve(body_fn):
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = body_fn(self.path).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # 静音
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture()
def servers():
    # 代理桩:收到的是绝对 URI(GET http://upstream.example/x),原样回显以证明走了代理
    proxy = _serve(lambda path: f"via-proxy {path}")
    direct = _serve(lambda path: "direct")
    yield {
        "proxy": f"http://127.0.0.1:{proxy.server_address[1]}",
        "direct": f"http://127.0.0.1:{direct.server_address[1]}/ping",
    }
    proxy.shutdown()
    direct.shutdown()


@pytest.fixture()
def local_mode(monkeypatch):
    for k in _PROXY_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: False)


# 一个一定连不上的代理:谁要是把请求送进它,测试立刻失败
_DEAD_PROXY = "http://127.0.0.1:1"


class TestIsLocalTarget:
    @pytest.mark.parametrize("host", [
        "localhost", "LOCALHOST", "api.localhost", "my-pc.local", "127.0.0.1", "127.8.9.1",
        "::1", "[::1]", "10.0.0.5", "172.20.3.4", "192.168.1.20", "169.254.1.1",
        "100.101.102.103", "fd12:3456::1", "0.0.0.0", "ollama", "my-gpu-box",
        "::ffff:192.168.1.2",
    ])
    def test_local(self, host):
        assert _is_local_target(host) is True

    @pytest.mark.parametrize("host", [
        "api.openai.com", "relay.example.com", "8.8.8.8", "172.32.0.1", "11.0.0.1",
        "2001:4860:4860::8888", "", None,
        # 梯子 fake-ip 段的**字面量**不算本地(域名解析成它时本就不经过这里)
        "api.deepseek.com",
    ])
    def test_not_local(self, host):
        assert _is_local_target(host) is False


class TestLocalModeHttpx:
    def test_follows_env_proxy(self, local_mode, servers, monkeypatch):
        """没配凭据代理 → 跟随环境代理(回归修复:加固前就是这样)。"""
        monkeypatch.setenv("HTTP_PROXY", servers["proxy"])
        with safe_httpx_client(timeout=5, proxy=None) as c:
            r = c.get("http://upstream.example/x")
        assert r.text == "via-proxy http://upstream.example/x"

    def test_local_target_bypasses_env_proxy(self, local_mode, servers, monkeypatch):
        """环境代理开着、NO_PROXY 没写 → 本机模型仍然直连。"""
        monkeypatch.setenv("HTTP_PROXY", _DEAD_PROXY)
        with safe_httpx_client(timeout=5, proxy=None) as c:
            assert c.get(servers["direct"]).text == "direct"

    def test_explicit_proxy_used(self, local_mode, servers, monkeypatch):
        # 环境代理指向死端口,证明用的是显式代理而不是环境的
        monkeypatch.setenv("HTTP_PROXY", _DEAD_PROXY)
        with safe_httpx_client(timeout=5, proxy=servers["proxy"]) as c:
            r = c.get("http://upstream.example/y")
        assert r.text == "via-proxy http://upstream.example/y"

    def test_local_target_bypasses_explicit_proxy(self, local_mode, servers):
        with safe_httpx_client(timeout=5, proxy=_DEAD_PROXY) as c:
            assert c.get(servers["direct"]).text == "direct"

    def test_no_proxy_env_still_respected(self, local_mode, servers, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", servers["proxy"])
        monkeypatch.setenv("NO_PROXY", "upstream.example")
        with safe_httpx_client(timeout=5, proxy=None) as c:
            transport = c._transport_for_url(httpx.URL("http://upstream.example/x"))
        assert transport is c._transport

    def test_no_redirect_and_hook_present(self, local_mode):
        """httpx 升级若改了 `_transport_for_url` 这个名字,本地直连兜底就会悄悄失效 —— 在这里红。"""
        assert hasattr(httpx.Client, "_transport_for_url")
        cls = outbound._local_client_cls()
        assert cls._transport_for_url is not httpx.Client._transport_for_url
        with safe_httpx_client(timeout=5, proxy=None) as c:
            assert c.follow_redirects is False
            assert isinstance(c, cls)

    def test_socks_proxy_without_socksio_is_explained(self, local_mode):
        if importlib.util.find_spec("socksio") is not None:
            with safe_httpx_client(timeout=5, proxy="socks5://127.0.0.1:1080") as c:
                assert c is not None
            return
        with pytest.raises(UnsupportedProxy) as ei:
            safe_httpx_client(timeout=5, proxy="socks5://127.0.0.1:1080")
        assert "HTTP 代理" in str(ei.value)


class TestServerModeUnchanged:
    def test_guard_transport_and_env_ignored(self, servers, monkeypatch):
        for k in _PROXY_VARS:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("HTTP_PROXY", servers["proxy"])
        with mock.patch.object(outbound, "_ssrf_enforced", return_value=True):
            c = safe_httpx_client(timeout=5, proxy=None)
        try:
            assert isinstance(c._transport, outbound._SsrfGuardTransport)
            assert c._mounts == {}, "服务器模式不许读环境代理"
            assert type(c) is httpx.Client
        finally:
            c.close()


class TestLocalModeUrllib:
    def test_local_target_direct_even_with_env_proxy(self, local_mode, servers, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", _DEAD_PROXY)
        with safe_urlopen(Request(servers["direct"]), timeout=5) as resp:
            assert resp.read() == b"direct"

    def test_local_target_direct_even_with_explicit_proxy(self, local_mode, servers):
        with safe_urlopen(Request(servers["direct"]), timeout=5, proxy=_DEAD_PROXY) as resp:
            assert resp.read() == b"direct"

    def test_explicit_http_proxy_used(self, local_mode, servers, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", _DEAD_PROXY)
        with safe_urlopen(Request("http://upstream.example/z"), timeout=5,
                          proxy=servers["proxy"]) as resp:
            assert resp.read() == b"via-proxy http://upstream.example/z"

    def test_socks_proxy_rejected_with_chinese_hint(self, local_mode):
        with pytest.raises(UnsupportedProxy) as ei:
            safe_urlopen(Request("http://upstream.example/z"), timeout=5,
                         proxy="socks5://127.0.0.1:1080")
        assert "SOCKS" in str(ei.value) and "HTTP 代理" in str(ei.value)

    def test_server_mode_ignores_proxy_arg(self, monkeypatch):
        """服务器模式走 IP pin,凭据代理即便被误传也不生效(credential_proxy 本就恒 None)。"""
        import socket as _s

        def _addr(host, port, *a, **k):
            return [(_s.AF_INET, _s.SOCK_STREAM, _s.IPPROTO_TCP, "", ("169.254.169.254", port))]

        with mock.patch.object(outbound, "_ssrf_enforced", return_value=True), \
             mock.patch.object(outbound.socket, "getaddrinfo", _addr):
            with pytest.raises(outbound.OutboundBlocked):
                safe_urlopen(Request("http://attacker.example/"), timeout=5, proxy=_DEAD_PROXY)
