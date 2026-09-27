"""
test_outbound_ssrf.py
=====================

`core.outbound.safe_urlopen` —— 所有「base_url 用户/admin 可控 + 携 Authorization」出站
请求的统一安全出口。锁死两条 SSRF 不变量(均为防回归):

(a) **不跟随重定向**:攻击者端点用 301 把携凭据的请求跳到 169.254.169.254(云元数据)/
    内网。safe_urlopen 必须拒绝跟随 → 30x 直接抛 HTTPError,绝不二次拨号到元数据地址。
(b) **use-time 重解析 + IP pin(抗 DNS rebinding)**:写时闸 `_validate_base_url` 只在存
    base_url 那一刻解析。攻击者可让域名写入时解析公网、请求时 rebind 到内网。safe_urlopen
    在每次发请求前重解析,任一 IP 内网即拒,并把 socket pin 到已校验 IP。

风格对照 `test_credential_proxy.py`:既有真行为测试(起本地 server / patch 解析器),也有
静态源码巡检(锁死四处调用点确实收口到 safe_urlopen,不许任何裸 urlopen 复活)。
"""
from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request

from core import outbound
from core.outbound import OutboundBlocked, safe_get_bytes, safe_urlopen

PROJECT = Path(__file__).resolve().parents[3]


def _read(rel: str) -> str:
    return (PROJECT / "rpg" / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 本地一次性 HTTP server:用于「真的发一次请求」的行为测试。                       #
# safe_urlopen 默认会拒绝 127.0.0.1(内网)→ 这些测试 patch 掉内网判定,只为让本地    #
# server 可达;被测的是「重定向是否被跟随 / 正常 200 是否打通」,与 IP 判定正交。      #
# --------------------------------------------------------------------------- #
def _make_handler(record: dict):
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            record["hits"] = record.get("hits", 0) + 1
            record.setdefault("paths", []).append(self.path)
            mode = record["mode"]
            if mode == "redirect":
                self.send_response(301)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()
            else:  # "ok"
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        # POST 走同样逻辑(extractor / embedding 实际是 POST)
        do_POST = do_GET

        def log_message(self, *a):  # 静音
            pass

    return _H


class _LocalServer:
    def __init__(self, mode: str):
        self.record = {"mode": mode, "hits": 0}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.record))
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/chat/completions"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _make_route_handler(record: dict):
    """按 path 路由的 handler:给 safe_get_bytes 的「合法跳转 / 跳内网 / 体积超限」用。"""

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            record["hits"] = record.get("hits", 0) + 1
            p = self.path
            if p == "/ok":
                body = b"PINNED-BYTES-OK"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif p == "/redirect-local":
                self.send_response(302)
                self.send_header("Location", "/ok")  # 相对跳转,验证 urljoin
                self.end_headers()
            elif p == "/redirect-meta":
                self.send_response(301)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()
            elif p == "/big":
                body = b"x" * 100
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):  # 静音
            pass

    return _H


class _RouteServer:
    def __init__(self):
        self.record = {"hits": 0}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_route_handler(self.record))
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class SafeUrlopenNoRedirect(unittest.TestCase):
    """(a) 30x 不被跟随 —— 元数据地址绝不会被二次访问。"""

    def test_301_to_metadata_is_refused(self):
        srv = _LocalServer("redirect")
        self.addCleanup(srv.stop)
        # 放行 127.0.0.1(本地 server 可达),其余 IP 判定无关——本测试不触发二次解析
        with mock.patch.object(outbound, "_ip_is_internal", return_value=False):
            with self.assertRaises(HTTPError) as ctx:
                safe_urlopen(Request(srv.url), timeout=5)
        # 重定向被拒 → 拿到的是 301 本身,而不是 200 元数据响应
        self.assertEqual(ctx.exception.code, 301)
        self.assertEqual(
            ctx.exception.headers.get("Location"),
            "http://169.254.169.254/latest/meta-data/",
        )
        # server 只被打了一次(没有跟随到第二跳)
        self.assertEqual(srv.record["hits"], 1)


class SafeUrlopenHappyPath(unittest.TestCase):
    """pin 连接的管线本身能打通正常 200(证明安全改造没把正常请求弄坏)。"""

    def test_normal_200_round_trip(self):
        srv = _LocalServer("ok")
        self.addCleanup(srv.stop)
        with mock.patch.object(outbound, "_ip_is_internal", return_value=False):
            with safe_urlopen(Request(srv.url), timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.read(), b'{"ok": true}')
        self.assertEqual(srv.record["hits"], 1)


class SafeUrlopenRebinding(unittest.TestCase):
    """(b) DNS rebinding:host 在请求时解析到内网/元数据 → use-time 闸必须拒,且绝不拨号。"""

    def test_rebind_to_metadata_ip_is_blocked(self):
        # 模拟「写时解析公网、请求时 rebind 到 169.254.169.254」:patch 请求时的解析器。
        # 这里**不** patch _ip_is_internal —— 要验证真实的 _ip_is_internal 把元数据 IP 判为内网。
        import socket as _socket

        def _fake_getaddrinfo(host, port, *a, **k):
            return [(_socket.AF_INET, _socket.SOCK_STREAM, _socket.IPPROTO_TCP, "",
                     ("169.254.169.254", port))]

        dialed = {"n": 0}

        def _no_dial(*a, **k):
            dialed["n"] += 1
            raise AssertionError("不应拨号:use-time 闸应在连接前就拒绝")

        with mock.patch.object(outbound.socket, "getaddrinfo", _fake_getaddrinfo), \
             mock.patch.object(outbound.socket, "create_connection", _no_dial):
            with self.assertRaises(OutboundBlocked) as ctx:
                safe_urlopen(Request("http://attacker.example/v1/chat/completions"), timeout=5)
        self.assertIn("169.254.169.254", str(ctx.exception))
        self.assertEqual(dialed["n"], 0, "被拒绝的请求不得发起任何 socket 连接")

    def test_one_internal_among_many_rejects_all(self):
        """多 A 记录中只要有一条内网就整体拒(与 _validate_base_url 同语义)。"""
        import socket as _socket

        def _mixed(host, port, *a, **k):
            return [
                (_socket.AF_INET, _socket.SOCK_STREAM, _socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
                (_socket.AF_INET, _socket.SOCK_STREAM, _socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
            ]

        with mock.patch.object(outbound.socket, "getaddrinfo", _mixed):
            with self.assertRaises(OutboundBlocked):
                safe_urlopen(Request("http://mixed.example/v1"), timeout=5)


class SafeUrlopenSchemeGuard(unittest.TestCase):
    def test_non_http_schemes_blocked(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x/"):
            with self.assertRaises(OutboundBlocked):
                safe_urlopen(Request(url), timeout=5)

    def test_missing_host_blocked(self):
        with self.assertRaises(OutboundBlocked):
            safe_urlopen(Request("http:///no-host"), timeout=5)


class SafeGetBytes(unittest.TestCase):
    """safe_get_bytes:手动跟随重定向,但**每一跳都重新过 safe_urlopen 的私网校验**,且限体积。"""

    def setUp(self):
        self.srv = _RouteServer()
        self.addCleanup(self.srv.stop)
        # 放行 loopback(本地 server 可达),拦其余(含 169.254)→ 用来验证「跳内网被拦」
        patcher = mock.patch.object(
            outbound, "_ip_is_internal", side_effect=lambda ip: not ip.startswith("127.")
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plain_200_returns_bytes(self):
        self.assertEqual(safe_get_bytes(self.srv.url("/ok"), timeout=5), b"PINNED-BYTES-OK")

    def test_follows_legit_redirect_revalidating_each_hop(self):
        # /redirect-local → /ok:合法 302 必须被跟随到底
        self.assertEqual(
            safe_get_bytes(self.srv.url("/redirect-local"), timeout=5), b"PINNED-BYTES-OK"
        )

    def test_redirect_to_internal_is_blocked(self):
        # 公网 200 端点用 301 把你引到 169.254 → 下一跳重解析时必须拒
        with self.assertRaises(OutboundBlocked):
            safe_get_bytes(self.srv.url("/redirect-meta"), timeout=5)

    def test_size_cap_enforced(self):
        with self.assertRaises(OutboundBlocked):
            safe_get_bytes(self.srv.url("/big"), timeout=5, max_bytes=10)

    def test_redirect_count_capped(self):
        # max_redirects=0 → 一次跳转都不许 → 超限
        with self.assertRaises(OutboundBlocked):
            safe_get_bytes(self.srv.url("/redirect-local"), timeout=5, max_redirects=0)


class SafeHttpxClientGate(unittest.TestCase):
    """httpx 传输层 SSRF 闸(给 OpenAI/Anthropic SDK 等必须用 httpx 的出站点)。"""

    @staticmethod
    def _req(url: str):
        import httpx
        return httpx.Request("GET", url)

    @staticmethod
    def _addrinfo(ip: str, port: int):
        import socket as _s
        return [(_s.AF_INET, _s.SOCK_STREAM, _s.IPPROTO_TCP, "", (ip, port))]

    def test_internal_host_blocked_before_inner(self):
        inner = mock.Mock()
        transport = outbound._SsrfGuardTransport(inner)
        with mock.patch.object(
            outbound.socket, "getaddrinfo",
            return_value=self._addrinfo("169.254.169.254", 80),
        ):
            with self.assertRaises(OutboundBlocked):
                transport.handle_request(self._req("http://attacker.example/v1"))
        inner.handle_request.assert_not_called()

    def test_external_host_passes_through(self):
        inner = mock.Mock()
        inner.handle_request.return_value = "RESP"
        transport = outbound._SsrfGuardTransport(inner)
        with mock.patch.object(
            outbound.socket, "getaddrinfo",
            return_value=self._addrinfo("93.184.216.34", 80),
        ):
            out = transport.handle_request(self._req("http://ok.example/v1"))
        self.assertEqual(out, "RESP")
        inner.handle_request.assert_called_once()

    def test_safe_httpx_client_is_no_redirect_and_guarded(self):
        client = outbound.safe_httpx_client(timeout=5)
        self.addCleanup(client.close)
        self.assertFalse(client.follow_redirects)
        self.assertIsInstance(getattr(client, "_transport", None), outbound._SsrfGuardTransport)

    def test_safe_httpx_client_http2_when_available_else_fallback(self):
        # http2=True(默认)在装了 h2 时开 HTTP/2(run 内多流式调用多路复用同连接,省 ×N 握手);
        # 没装 h2 时优雅回退 HTTP/1.1 不报错。守卫不变(上面已验)。
        try:
            import h2  # noqa: F401
            h2_present = True
        except Exception:
            h2_present = False
        client = outbound.safe_httpx_client(timeout=5)  # 默认 http2=True
        self.addCleanup(client.close)
        inner = client._transport._inner  # _SsrfGuardTransport 包的 httpx.HTTPTransport
        pool = inner._pool
        # httpcore 池的 http2 标志反映实际是否启用(装了 h2 → True;没装 → 回退 False)
        self.assertEqual(bool(getattr(pool, "_http2", False)), h2_present)
        # 显式关闭时一定是 HTTP/1.1
        c1 = outbound.safe_httpx_client(timeout=5, http2=False)
        self.addCleanup(c1.close)
        self.assertFalse(bool(getattr(c1._transport._inner._pool, "_http2", False)))


class SsrfGuardTransportContract(unittest.TestCase):
    """httpx 约定 transport 只抛 TransportError(dependabot #118 前置修复)。

    openai 3.14.1 起 SDK 只把 httpx.RequestError 包成 APIConnectionError 并重试;以前闸门抛裸
    OutboundBlocked(ValueError) 会原样穿出去:分类器认不出、SDK 不重试(服务器模式 DNS 瞬时失败
    直接报错)。现在抛 OutboundBlockedTransportError:既是 httpx.ConnectError 也是 OutboundBlocked。
    `_ssrf_enforced` 显式打桩为 True,不依赖测试执行顺序 / RPG_REQUIRE_AUTH 环境。
    """

    def setUp(self):
        p = mock.patch.object(outbound, "_ssrf_enforced", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def _assert_contract(self, exc):
        import httpx
        self.assertIsInstance(exc, httpx.TransportError)
        self.assertIsInstance(exc, OutboundBlocked)
        self.assertIsInstance(exc, ValueError)  # 既有 `except ValueError` 写法照样接得住

    def test_internal_target_raises_transport_error(self):
        client = outbound.safe_httpx_client(timeout=5)
        self.addCleanup(client.close)
        with self.assertRaises(OutboundBlocked) as cm:
            client.get("http://127.0.0.1:9/")
        self._assert_contract(cm.exception)

    def test_bad_scheme_and_missing_host_raise_transport_error(self):
        import httpx
        transport = outbound._SsrfGuardTransport(mock.Mock())
        for url in ("ftp://relay.example/v1", "http:///v1"):
            with self.assertRaises(OutboundBlocked) as cm:
                transport.handle_request(httpx.Request("GET", url))
            self._assert_contract(cm.exception)

    def test_dns_failure_raises_transport_error(self):
        import socket as _s
        transport = outbound._SsrfGuardTransport(mock.Mock())
        with mock.patch.object(outbound.socket, "getaddrinfo", side_effect=_s.gaierror(8, "nodename nor servname")):
            with self.assertRaises(OutboundBlocked) as cm:
                import httpx
                transport.handle_request(httpx.Request("GET", "https://no-such-host.invalid/v1"))
        self._assert_contract(cm.exception)

    def test_openai_sdk_wraps_it_as_connection_error_classified_network(self):
        import openai

        from agents.provider_errors import classify_provider_error
        client = openai.OpenAI(api_key="test-key", base_url="http://127.0.0.1:9/v1",
                               http_client=outbound.safe_httpx_client(timeout=5), max_retries=0)
        with self.assertRaises(openai.APIConnectionError) as cm:
            client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
        self.assertIsInstance(cm.exception.__cause__, OutboundBlocked)
        known = classify_provider_error(cm.exception)
        self.assertEqual((known or [None])[0], "network")
        self.assertIn("保留地址", known[1])  # 底层报错带上闸门的真实原因,而不是只有 "Connection error."


class ConsolidationSourceGuards(unittest.TestCase):
    """静态巡检:四处调用点必须收口到 safe_urlopen,不许裸 urlopen / 自建 redirect opener 复活。"""

    PROD_FILES = (
        "agents/extractor.py",
        "agents/command_agent.py",
        # embedding 拆包后出站网络代码分居两处:__init__(OpenAI 兼容通道 _embed_via_openai)
        # 与 _gemini(原生 embedContent 通道 _embed_via_gemini),两者都须收口到 safe_urlopen。
        "platform_app/knowledge/embedding/__init__.py",
        "platform_app/knowledge/embedding/_gemini.py",
    )

    def test_callers_use_safe_urlopen(self):
        for rel in self.PROD_FILES:
            src = _read(rel)
            self.assertIn("safe_urlopen", src, f"{rel} 应改用 safe_urlopen")
            self.assertNotIn(
                "urllib.request.urlopen(", src,
                f"{rel} 不得再有裸 urllib.request.urlopen( —— 会绕过 no-redirect/use-time 闸",
            )

    def test_no_local_redirect_openers_remain(self):
        """除 core/outbound.py 自身外,生产代码不得再自建 redirect opener。"""
        for rel in self.PROD_FILES + ("agents/_harness.py",):
            src = _read(rel)
            self.assertNotIn("build_opener", src, f"{rel} 不应再自建 opener,应走 safe_urlopen")

    def test_harness_delegates_to_safe_urlopen(self):
        src = _read("agents/_harness.py")
        self.assertIn("from core.outbound import safe_urlopen", src)
        self.assertIn("return safe_urlopen(req, timeout=timeout)", src)

    def test_core_outbound_invariants(self):
        src = _read("core/outbound.py")
        # 不跟随重定向 + 复用既有内网判定 + use-time 重解析 pin
        self.assertIn("_ip_is_internal", src)
        self.assertIn("from platform_app.user_credentials import _ip_is_internal", src)
        self.assertIn("class _NoRedirect", src)
        self.assertIn("create_connection", src)
        self.assertIn("server_hostname", src)  # TLS SNI/证书仍按原 hostname 校验


if __name__ == "__main__":
    unittest.main()
