"""openai>=3.14 把 APIError.code 一律转成 str(dependabot #118 的前置修复)。

流内错误({"error": {"code": 502, ...}},HTTP 200 的流里来)被 SDK 抛成**不带 status_code** 的裸
APIError,以前靠 int 型 .code 认出 502/429/401 → upstream/ratelimit/auth,首 token 前自动重试、
跨渠道 fallback 才会生效。3.14 起 code 变成 "502",_http_status 只认 int → 全部落 None。
有的中转站本来就把 code 发成字符串,所以 3.13 下也有这个缺口。

这组测试在 openai 3.13 与 3.16 下都要绿(锁 SDK 契约,不锁版本)。
"""
from __future__ import annotations

import httpx
import openai
import pytest

from agents.gm.stream_retry import _retryable_category, stream_with_pretoken_retry
from agents.provider_errors import classify_provider_error

_REQ = httpx.Request("POST", "https://openrouter.example/api/v1/chat/completions")


def _stream_error(code, message="Provider returned error"):
    return openai.APIError(message, _REQ, body={"message": message, "code": code})


def _cat(exc):
    known = classify_provider_error(exc)
    return known[0] if known else None


@pytest.mark.parametrize("code,expect", [
    (502, "upstream"), ("502", "upstream"), ("503", "upstream"),
    ("401", "auth"),
    ("429", "ratelimit"),   # 泛化 message 命不中限流措辞,只能靠 code
])
def test_stream_error_numeric_code_int_or_str(code, expect):
    assert _cat(_stream_error(code)) == expect


@pytest.mark.parametrize("code", ["1301", True, "5O2", "²²²", None])
def test_non_http_codes_are_not_read_as_status(code):
    """4 位业务码(智谱 1301)、bool、非 ASCII 数字都不能当 HTTP 状态码。"""
    from agents.provider_errors import _http_status
    exc = _stream_error(code, "系统检测到输入或生成内容可能包含不安全或敏感内容")
    assert _http_status(exc) is None
    assert _cat(exc) is None  # 敏感内容类流内错误:不归 upstream(不重试)


def test_status_code_attribute_wins_over_code():
    class _E(Exception):
        status_code = 429
        code = "502"
    assert _cat(_E("x")) == "ratelimit"


def test_sqlstate_like_code_is_not_status():
    class _E(Exception):
        code = "23505"
    assert _cat(_E("duplicate key")) is None


def test_stream_retry_retries_string_502_before_first_token():
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) < 3:
            def g():
                raise _stream_error("502")
                yield  # pragma: no cover
            return g()
        return iter([{"type": "text", "text": "好"}])

    assert _retryable_category(_stream_error("502")) == "upstream"
    out = list(stream_with_pretoken_retry(factory, sleep=lambda _s: None))
    assert len(attempts) == 3
    assert {"type": "text", "text": "好"} in out


def test_real_sdk_stream_error_event_is_retryable():
    """真走 SDK 的 SSE 解析:HTTP 200 流里的 {"error": {"code": 502}} → 可重试的 upstream。"""
    sse = b'data: {"error": {"message": "Provider returned error", "code": 502}}\n\n'

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse)

    client = openai.OpenAI(api_key="test-key", base_url="https://openrouter.example/api/v1",
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0)
    with pytest.raises(openai.APIError) as ei:
        for _ in client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}],
                                                stream=True):
            pass
    assert getattr(ei.value, "status_code", None) is None  # 流内错误没有 HTTP 状态码
    assert _retryable_category(ei.value) == "upstream"

