"""代理拒绝了 CONNECT 不是「提供商拒绝(HTTP 403)」(巡检整合审查)。

凭据代理接进 urllib 出站后,子代理 / 导入阶段也会经用户配的 HTTP 代理出去。代理拒绝建隧道
(Squid 访问控制、公司代理白名单)时,Python 抛 URLError(OSError('Tunnel connection failed:
403 Forbidden')),文本里带「403 forbidden」,以前命中强 403 标记被归成 auth,文案叫人「先换
一个模型试」—— 真正的问题是代理。httpx 那边同一件事是 httpx.ProxyError('403 Forbidden')
(httpcore 用 CONNECT 响应的状态行做消息),同样被说成提供商拒绝。

锁死:两条线都归 network,文案点明是代理拒绝了连接、请求没送到模型服务,不提换模型;
真正带 HTTP 403 状态码的提供商拒绝不受影响。
"""
from __future__ import annotations

import urllib.error

import httpx
import openai
import pytest

from agents.provider_errors import classify_provider_error

_REQ = httpx.Request("POST", "https://relay.example/v1/chat/completions")


def _tunnel(code_line: str) -> urllib.error.URLError:
    return urllib.error.URLError(OSError(f"Tunnel connection failed: {code_line}"))


@pytest.mark.parametrize("exc", [
    _tunnel("403 Forbidden"),
    _tunnel("407 Proxy Authentication Required"),
    _tunnel("502 Bad Gateway"),
    httpx.ProxyError("403 Forbidden", request=_REQ),
    httpx.ProxyError("407 Proxy Authentication Required", request=_REQ),
], ids=["urllib-403", "urllib-407", "urllib-502", "httpx-403", "httpx-407"])
def test_proxy_refusal_is_network_and_names_proxy(exc):
    cat, msg = classify_provider_error(exc)
    assert cat == "network"
    assert "代理拒绝了" in msg
    assert "换一个模型" not in msg and "提供商拒绝" not in msg


def test_sdk_wrapped_proxy_error_names_proxy():
    """SDK 把 httpx.ProxyError 包成 APIConnectionError('Connection error.'),原因在 __cause__ 上。"""
    try:
        try:
            raise httpx.ProxyError("403 Forbidden", request=_REQ)
        except httpx.ProxyError as inner:
            raise openai.APIConnectionError(request=_REQ) from inner
    except openai.APIConnectionError as exc:
        cat, msg = classify_provider_error(exc)
    assert cat == "network" and "代理拒绝了" in msg


def test_real_provider_403_still_auth():
    exc = openai.PermissionDeniedError(
        "Error code: 403 - forbidden", response=httpx.Response(403, request=_REQ),
        body={"error": {"message": "forbidden"}})
    assert classify_provider_error(exc)[0] == "auth"


def test_plain_connection_refused_keeps_generic_network_copy():
    exc = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    cat, msg = classify_provider_error(exc)
    assert cat == "network" and "代理拒绝了" not in msg
