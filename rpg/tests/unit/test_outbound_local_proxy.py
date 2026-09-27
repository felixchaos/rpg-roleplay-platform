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
5. 环境 / 系统代理里有 httpx 用不了的条目(Ubuntu/GNOME 导出的 all_proxy=socks://、Windows
   注册表只配 SOCKS 时映射出的 socks4://、端口写坏的地址)时,只跳过那一条,不让 Client 构造
   抛 ValueError 把本地模式所有出站(GM / 拉模型 / 本机 Ollama)一起带崩;同一环境里能用的
   https_proxy 照常生效。

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
        # 容器访问宿主机 / 路由器局域网域名 / RFC 8375
        "host.docker.internal", "HOST.DOCKER.INTERNAL.", "ollama.lan", "nas.home.arpa",
    ])
    def test_local(self, host):
        assert _is_local_target(host) is True

    @pytest.mark.parametrize("host", [
        "api.openai.com", "relay.example.com", "8.8.8.8", "172.32.0.1", "11.0.0.1",
        "2001:4860:4860::8888", "", None,
        # 后缀要整段匹配,域名里恰好含这些词的公网地址不算
        "internal.example.com", "lan.example.com", "api.homearpa.com",
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


class TestEnvProxyUnsupportedEntries:
    """环境 / 系统代理里 httpx 不认的条目:跳过那一条,绝不让 Client 构造失败。"""

    @pytest.mark.parametrize("env", [
        {"all_proxy": "socks://127.0.0.1:1/"},            # Ubuntu / GNOME 系统代理
        {"HTTPS_PROXY": "socks4://127.0.0.1:1", "HTTP_PROXY": "socks4://127.0.0.1:1"},  # Windows 注册表只配 SOCKS
        {"HTTPS_PROXY": "http://127.0.0.1:notaport"},     # 地址写坏
        {"HTTPS_PROXY": "ftp://127.0.0.1:21"},
    ])
    def test_client_builds_and_local_target_still_direct(self, local_mode, servers, monkeypatch, env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with safe_httpx_client(timeout=5, proxy=None) as c:
            assert c.get(servers["direct"]).text == "direct"
            # 这些条目全都用不了:一条代理传输层都不该挂上(None = 直连例外)
            assert all(t is None for t in c._mounts.values()), c._mounts

    def test_usable_entry_kept_when_sibling_unusable(self, local_mode, servers, monkeypatch):
        """Clash on Ubuntu 的典型环境:http(s)_proxy 能用 + all_proxy=socks:// 不能用 → 前者照常走。"""
        monkeypatch.setenv("http_proxy", servers["proxy"])
        monkeypatch.setenv("all_proxy", "socks://127.0.0.1:1/")
        with safe_httpx_client(timeout=5, proxy=None) as c:
            r = c.get("http://upstream.example/q")
        assert r.text == "via-proxy http://upstream.example/q"

    def test_unusable_entry_falls_back_to_broader_pattern(self, local_mode, servers, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "socks4://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY", servers["proxy"])
        with safe_httpx_client(timeout=5, proxy=None) as c:
            r = c.get("http://upstream.example/w")
        assert r.text == "via-proxy http://upstream.example/w"

    def test_env_socks5_without_socksio_skipped(self, local_mode, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "socks5h://127.0.0.1:1")
        monkeypatch.setattr(outbound, "_socksio_available", lambda: False)
        with safe_httpx_client(timeout=5, proxy=None) as c:
            assert c._mounts == {}

    def test_last_resort_fallback_is_direct(self, local_mode, monkeypatch):
        """过滤本身出意外(比如 httpx 升级改了内部接口)也退回直连,不崩。"""
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")

        def _boom():
            raise RuntimeError("httpx internals changed")

        monkeypatch.setattr(outbound, "_env_proxy_map", _boom)
        with safe_httpx_client(timeout=5, proxy=None) as c:
            assert c._mounts == {}
            assert c.follow_redirects is False

    @pytest.mark.parametrize("bad", ["http://127.0.0.1:notaport", "socks4://127.0.0.1:1080"])
    def test_explicit_bad_proxy_is_explained_not_ignored(self, local_mode, bad):
        """显式凭据代理写坏了:给一句能照着改的话,不悄悄退回直连(用户明确要走代理)。"""
        with pytest.raises(UnsupportedProxy) as ei:
            safe_httpx_client(timeout=5, proxy=bad)
        assert "HTTP 代理" in str(ei.value)

    def test_private_hooks_present(self):
        """两个内部钩子 httpx 升级若改名,环境代理过滤就会悄悄失效 —— 在这里红。"""
        from httpx import _utils

        assert hasattr(httpx.Client, "_get_proxy_map")
        assert callable(getattr(_utils, "get_environment_proxies", None))
        cls = outbound._local_client_cls()
        assert cls._get_proxy_map is not httpx.Client._get_proxy_map


class TestRedactProxyUrl:
    @pytest.mark.parametrize("raw,want", [
        ("http://user:pass@127.0.0.1:7890", "http://***@127.0.0.1:7890"),
        ("socks5://token@proxy.example:1080", "socks5://***@proxy.example:1080"),
        ("http://a:b@c@proxy.example:1", "http://***@proxy.example:1"),
        ("http://127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("http://127.0.0.1:7890/path@x", "http://127.0.0.1:7890/path@x"),
        ("", ""),
        (None, ""),
    ])
    def test_redact(self, raw, want):
        assert outbound.redact_proxy_url(raw) == want

    def test_gm_backends_log_redacted(self):
        """GM 后端「出站走用户代理」日志必须脱敏,代理里可能带账号密码。"""
        import re as _re
        from pathlib import Path
        root = Path(outbound.__file__).resolve().parent.parent / "agents" / "gm" / "backends"
        for name in ("openai_compat.py", "anthropic.py"):
            src = (root / name).read_text(encoding="utf-8")
            lines = [ln for ln in src.splitlines() if "出站走用户代理" in ln]
            assert lines, name
            for ln in lines:
                assert _re.search(r"redact_proxy_url\(_use_proxy\)", ln), f"{name}: {ln.strip()}"


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

    def test_socks_proxy_falls_back_to_env_proxy(self, local_mode, servers, monkeypatch):
        """urllib 不会 SOCKS:凭据代理是 socks5 时不抛错,退回环境/系统代理(改动前的行为)。

        以前这里抛 UnsupportedProxy,子代理 / 状态抽取 / /set 解析全被上层吞成空结果,
        聊天(httpx 走得了 SOCKS)照常,状态却一动不动。"""
        monkeypatch.setenv("HTTP_PROXY", servers["proxy"])
        with safe_urlopen(Request("http://upstream.example/z"), timeout=5,
                          proxy="socks5://127.0.0.1:1") as resp:
            assert resp.read() == b"via-proxy http://upstream.example/z"

    def test_non_proxy_scheme_still_rejected(self, local_mode):
        """写时闸只放行 http/https/socks5;真出现别的协议(存量脏数据)照旧明确报错。"""
        with pytest.raises(UnsupportedProxy):
            safe_urlopen(Request("http://upstream.example/z"), timeout=5,
                         proxy="ftp://127.0.0.1:21")

    def test_env_proxy_followed_when_no_credential_proxy(self, local_mode, servers, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", servers["proxy"])
        with safe_urlopen(Request("http://upstream.example/e"), timeout=5) as resp:
            assert resp.read() == b"via-proxy http://upstream.example/e"

    def test_env_proxy_filter_keeps_only_http(self, local_mode, monkeypatch):
        """Windows 注册表只配 SOCKS 时 Python 给出 https=socks4://…:urllib 会拿它当 HTTP 代理
        去 CONNECT。只留 http/https(不带协议的 host:port 按 http 算),其余跳过 = 直连。"""
        monkeypatch.setenv("HTTPS_PROXY", "socks4://127.0.0.1:1")
        monkeypatch.setenv("HTTP_PROXY", "127.0.0.1:7890")
        monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:1/")
        monkeypatch.setenv("NO_PROXY", "example.org")
        handler = outbound._urllib_env_proxy_handler()
        assert handler.proxies == {"http": "127.0.0.1:7890"}

    def test_server_mode_ignores_proxy_arg(self, monkeypatch):
        """服务器模式走 IP pin,凭据代理即便被误传也不生效(credential_proxy 本就恒 None)。"""
        import socket as _s

        def _addr(host, port, *a, **k):
            return [(_s.AF_INET, _s.SOCK_STREAM, _s.IPPROTO_TCP, "", ("169.254.169.254", port))]

        with mock.patch.object(outbound, "_ssrf_enforced", return_value=True), \
             mock.patch.object(outbound.socket, "getaddrinfo", _addr):
            with pytest.raises(outbound.OutboundBlocked):
                safe_urlopen(Request("http://attacker.example/"), timeout=5, proxy=_DEAD_PROXY)


# ── 凭据代理是 SOCKS 时 urllib 出站的口径(巡检整合审查)────────────────────────────
# 写时闸放行 socks5,聊天(httpx + socksio)也真能走 SOCKS;urllib 这一侧(子代理 harness /
# extractor / 验收器 / command_agent 的 OpenAI 兼容路径 / 向量 / 生图下载)不会 SOCKS。
# 约定:不抛错,退回环境/系统代理(凭据代理接进 urllib 之前就是这样),该代理只 warning 一次。

_SOCKS = "socks5://user:secret@127.0.0.1:1080"


@pytest.fixture()
def chat_proxy():
    """能回 POST 的代理桩:记下收到的绝对 URI,回一个 OpenAI 兼容的 chat completion。"""
    import json as _json

    seen: list[str] = []

    class _H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append(self.path)
            body = _json.dumps({"choices": [{"message": {"content": "[]"}}], "usage": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{srv.server_address[1]}", "seen": seen}
    srv.shutdown()


@pytest.fixture()
def socks_cred(monkeypatch):
    from platform_app import user_credentials

    monkeypatch.setattr(user_credentials, "resolve_api_key", lambda *a, **k: {
        "key": "sk-test", "base_url_override": "http://upstream.example/v1",
        "proxy": _SOCKS, "source": "user_db",
    })


class TestSocksCredentialProxyUrllib:
    def test_handler_falls_back_to_env_proxy(self, local_mode, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        handler = outbound._urllib_proxy_handler(_SOCKS)
        assert handler.proxies == {"https": "http://127.0.0.1:7890", "http": "http://127.0.0.1:7890"}

    def test_no_env_proxy_means_direct(self, local_mode, monkeypatch):
        monkeypatch.setattr(outbound.urllib.request, "getproxies", lambda: {})
        assert outbound._urllib_proxy_handler(_SOCKS).proxies == {}

    def test_warns_once_and_redacts(self, local_mode, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(outbound, "_WARNED_ENV_PROXIES", set())
        monkeypatch.setattr(outbound.urllib.request, "getproxies", lambda: {})
        with caplog.at_level(logging.WARNING, logger=outbound.__name__):
            outbound._urllib_proxy_handler(_SOCKS)
            outbound._urllib_proxy_handler(_SOCKS)
        hits = [r.getMessage() for r in caplog.records if "SOCKS" in r.getMessage()]
        assert len(hits) == 1, hits
        assert "secret" not in hits[0] and "127.0.0.1:1080" in hits[0]

    def test_extractor_json_mode_does_not_raise(self, local_mode, chat_proxy, socks_cred, monkeypatch):
        from agents import extractor

        monkeypatch.setenv("HTTP_PROXY", chat_proxy["url"])
        out = extractor._call_openai_compat_json_mode(
            api_id="relay", model="m", system_prompt="s", user_prompt="u", user_id=1, timeout_sec=5)
        assert out == "[]"
        assert chat_proxy["seen"] == ["http://upstream.example/v1/chat/completions"]

    def test_harness_json_mode_does_not_raise(self, local_mode, chat_proxy, socks_cred, monkeypatch):
        from agents import _harness

        monkeypatch.setenv("HTTP_PROXY", chat_proxy["url"])
        text, _usage = _harness._openai_compat_json_mode(
            "relay", "m", "s", "u", 1, 5, 64)
        assert text == "[]"
        assert chat_proxy["seen"] == ["http://upstream.example/v1/chat/completions"]

    def test_command_agent_does_not_raise(self, local_mode, chat_proxy, socks_cred, monkeypatch):
        from agents import command_agent

        monkeypatch.setenv("HTTP_PROXY", chat_proxy["url"])
        assert command_agent._call_openai_compat_tools("relay", "m", "u", 1, 5) == []
        assert chat_proxy["seen"] == ["http://upstream.example/v1/chat/completions"]


class TestSafeGetBytesProxy:
    """生图下载与提交同一条出站:提交走凭据代理,下载也得走(以前 safe_get_bytes 不收 proxy)。"""

    def test_explicit_proxy_used(self, local_mode, servers, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", _DEAD_PROXY)
        data = outbound.safe_get_bytes("http://upstream.example/img.png", timeout=5,
                                       proxy=servers["proxy"])
        assert data == b"via-proxy http://upstream.example/img.png"

    def test_download_url_passes_proxy(self, local_mode, servers, monkeypatch):
        from agents.image_gen.base import download_url

        monkeypatch.setenv("HTTP_PROXY", _DEAD_PROXY)
        assert download_url("http://upstream.example/a.png", timeout=5,
                            proxy=servers["proxy"]) == b"via-proxy http://upstream.example/a.png"

    def test_no_proxy_keeps_env_behavior(self, local_mode, servers, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", servers["proxy"])
        assert outbound.safe_get_bytes("http://upstream.example/e.png", timeout=5) \
            == b"via-proxy http://upstream.example/e.png"
