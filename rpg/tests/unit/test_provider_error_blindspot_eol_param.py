"""供应商错误分类盲区(巡检 2026-09-28):模型下线 / 未识别参数 / 流内错误 / urllib HTTPError。

生产实况:
- OpenRouter 410「The model 'openai/gpt-oss-120b' has reached its end of life」—— 玩家在对话和开场里
  只看到「本轮处理出错,请重试(错误码 Exxx)」,重试永远不会好。
- 中转站 400「code:400 请求失败:请求中含有未识别参数」、流内「上游未知错误」同样落泛化兜底。
- 子代理 harness 走 urllib:HTTPError 是 URLError 的子类,400/405/410/413/422 全被说成「连不上接口
  地址,请检查 base_url」;响应体从没读过,服务商写的真实原因对分类器不可见。
- vLLM 400 extra_forbidden 被说成「请求被提供商拒绝(HTTP 403)」。
- 导入:卡片全挂只写「N 个候选 LLM 调用报错」,世界书阶段条目显示「未知错误」。

夹具尽量用真实 SDK 构造(openai client._make_status_error_from_response / urllib HTTPError)。
"""
from __future__ import annotations

import io
import urllib.error
from unittest import mock

import httpx
import openai
import pytest

from agents import provider_errors as pe
from agents.provider_errors import (
    attach_http_error_body,
    classify_provider_error,
    provider_error_summary,
)

_REQ = httpx.Request("POST", "https://relay.example/v1/chat/completions")
_EOL = ("The model 'openai/gpt-oss-120b' has reached its end of life and is no longer available. "
        "Please switch to another model.")


def _openai_status_error(status: int, body):
    """与 SDK 收到 HTTP 响应时同一条构造路径(dict → JSON 响应;str → 纯文本响应)。"""
    if isinstance(body, dict):
        resp = httpx.Response(status, request=_REQ, json=body)
    else:
        resp = httpx.Response(status, request=_REQ, text=body)
    client = openai.OpenAI(api_key="test-key", base_url="https://relay.example/v1")
    return client._make_status_error_from_response(resp)


def _http_error(code: int, body: bytes = b"", reason: str = "X") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://relay.example/v1/chat/completions", code, reason, {},
                                  io.BytesIO(body))


def _cat(exc):
    known = classify_provider_error(exc)
    return known[0] if known else None


# ── 模型下线(410)─────────────────────────────────────────────────────────
def test_410_end_of_life_is_model_unavailable():
    cat, msg = classify_provider_error(_openai_status_error(410, {"error": {"message": _EOL, "code": 410}}))
    assert cat == "model_unavailable"
    assert "下线" in msg and "请重试" not in msg


def test_eol_wording_without_status_is_model_unavailable():
    assert _cat(RuntimeError(_EOL)) == "model_unavailable"


@pytest.mark.parametrize("text", [
    "The model `text-davinci-003` has been deprecated",
    "The model mixtral-8x7b-32768 has been decommissioned and is no longer supported",
    "models/gemini-1.0-pro-vision has been retired",
    "该模型已下线,请更换其他模型",
])
def test_other_model_gone_wordings(text):
    assert _cat(RuntimeError(text)) == "model_unavailable"


def test_client_safe_error_410_is_actionable_and_persisted_as_model_unavailable():
    from routes.game import _client_safe_error
    seen = {}

    def _rec(**kw):
        seen.update(kw)

    with mock.patch("platform_app.usage.record_provider_failure", side_effect=_rec):
        msg = _client_safe_error(_openai_status_error(410, {"error": {"message": _EOL}}))
    assert "下线" in msg and "请重试" not in msg
    assert seen.get("category") == "model_unavailable"


# ── 400 未识别 / 不支持的参数 ──────────────────────────────────────────────
def test_relay_plain_text_unknown_param_is_bad_request_with_original_words():
    cat, msg = classify_provider_error(_openai_status_error(400, "code:400 请求失败:请求中含有未识别参数"))
    assert cat == "bad_request"
    assert "中转站" in msg and "未识别参数" in msg


@pytest.mark.parametrize("message", [
    "Unrecognized request argument supplied: stream_options",
    "Unknown parameter: 'thinking'.",
    "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.",
])
def test_openai_parameter_rejections_are_bad_request(message):
    assert _cat(_openai_status_error(400, {"error": {"message": message, "type": "invalid_request_error"}})) \
        == "bad_request"


def test_vllm_extra_forbidden_is_bad_request_not_403():
    body = {"object": "error", "type": "BadRequestError", "code": 400,
            "message": "[{'type': 'extra_forbidden', 'loc': ('body', 'thinking'), "
                       "'msg': 'Extra inputs are not permitted', 'input': {'type': 'disabled'}}]"}
    cat, msg = classify_provider_error(_openai_status_error(400, body))
    assert cat == "bad_request"
    assert "403" not in msg


# ── 守卫:别误伤 ─────────────────────────────────────────────────────────
def test_unrelated_400_still_unclassified():
    assert _cat(_openai_status_error(400, {"error": {"message": "messages: last message must not be empty"}})) is None


def test_parameter_level_no_longer_supported_is_not_model_gone():
    exc = _openai_status_error(400, {"error": {"message": "The 'functions' parameter is no longer supported. "
                                                          "Please use 'tools' instead."}})
    assert _cat(exc) != "model_unavailable"


def test_echoed_story_text_end_of_life_is_not_model_gone():
    exc = _openai_status_error(400, {"error": {"message": "invalid input near: the knight reached the end of life"}})
    assert _cat(exc) != "model_unavailable"


@pytest.mark.parametrize("status,message", [
    (400, "upstream returned 403 Forbidden"),
    (500, "upstream returned 403 Forbidden"),
    (400, "Request forbidden by content policy"),
])
def test_wrapped_403_and_content_policy_forbidden_stay_auth(status, message):
    assert _cat(_openai_status_error(status, {"error": {"message": message}})) == "auth"


def test_504_stays_upstream_and_connection_errors_stay_network():
    assert _cat(_openai_status_error(504, {"error": {"message": "gateway timeout"}})) == "upstream"
    assert _cat(openai.APIConnectionError(request=_REQ)) == "network"
    assert _cat(urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))) == "network"


# ── 流内错误 ─────────────────────────────────────────────────────────────
def test_openai_in_stream_error_is_upstream_and_retryable():
    from agents.gm.stream_retry import _retryable_category
    exc = openai.APIError("上游未知错误", _REQ, body={"message": "上游未知错误", "code": "upstream_error"})
    cat, msg = classify_provider_error(exc)
    assert cat == "upstream" and "上游未知错误" in msg
    assert _retryable_category(exc) == "upstream"


def test_in_stream_content_policy_is_not_retried():
    from agents.gm.stream_retry import _retryable_category
    exc = openai.APIError("blocked", _REQ, body={"message": "blocked", "code": "content_policy_violation"})
    assert _cat(exc) != "upstream"
    assert _retryable_category(exc) is None


def test_google_genai_same_name_api_error_is_not_treated_as_stream_error():
    fake = type("APIError", (Exception,), {"__module__": "google.genai.errors"})
    exc = fake("something odd")
    exc.code = None
    assert _cat(exc) != "upstream"


def test_anthropic_in_stream_overloaded_error_is_upstream():
    import anthropic
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    exc = anthropic.APIStatusError(
        "Overloaded", response=httpx.Response(200, request=req),
        body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    assert _cat(exc) == "upstream"


# ── urllib HTTPError:有状态码 = 对面回过话,不是「连不上」──────────────────
@pytest.mark.parametrize("code", [400, 405, 409, 410, 413, 422])
def test_http_error_with_4xx_is_never_network(code):
    assert _cat(_http_error(code)) != "network"


def test_http_error_410_gone_is_model_unavailable():
    assert _cat(_http_error(410, reason="Gone")) == "model_unavailable"


@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_http_error_redirect_stays_network_with_base_url_hint(code):
    cat, msg = classify_provider_error(_http_error(code, reason="Moved"))
    assert cat == "network"
    assert "重定向" in msg and "base_url" in msg


def test_http_error_408_stays_network():
    assert _cat(_http_error(408, reason="Request Timeout")) == "network"


# ── 响应体挂到 HTTPError.body 上 ────────────────────────────────────────
def test_attach_plain_text_body_surfaces_original_words():
    exc = _http_error(400, "code:400 请求失败:请求中含有未识别参数".encode(), "Bad Request")
    attach_http_error_body(exc)
    cat, msg = classify_provider_error(exc)
    assert cat == "bad_request"
    assert "请求中含有未识别参数" in msg and "HTTP Error 400" not in msg


def test_attach_json_body_context_overflow():
    exc = _http_error(400, b'{"error":{"message":"This model\'s maximum context length is 8192 tokens"}}')
    attach_http_error_body(exc)
    assert _cat(exc) == "context"


def test_attach_is_idempotent_and_never_raises():
    exc = _http_error(410, b'{"error":{"message":"The model x has reached its end of life"}}', "Gone")
    attach_http_error_body(exc)
    first = exc.body
    attach_http_error_body(exc)
    assert exc.body == first == {"message": "The model x has reached its end of life"}

    class _Boom(urllib.error.HTTPError):
        def read(self, *a):
            raise OSError("stream closed")

    boom = _Boom("https://x", 500, "err", {}, io.BytesIO(b""))
    attach_http_error_body(boom)  # 不抛
    assert getattr(boom, "body", None) is None
    attach_http_error_body(ValueError("not an http error"))  # 没有 read() 也不抛


def test_attach_html_error_page_keeps_title_only():
    exc = _http_error(404, b"<html><head><title>404 Not Found</title></head><body>cloudflare</body></html>")
    attach_http_error_body(exc)
    assert exc.body == "404 Not Found"
    assert _cat(exc) == "model_unavailable"  # 404 的结论不被错误页里的 cloudflare 抢走


def test_harness_urlopen_attaches_body_on_http_error(monkeypatch):
    from agents import _harness
    from core import outbound

    def _raise(req, *, timeout):
        raise _http_error(410, ('{"error":{"message":"' + _EOL + '"}}').encode(), "Gone")

    monkeypatch.setattr(outbound, "safe_urlopen", _raise)
    with pytest.raises(urllib.error.HTTPError) as ei:
        _harness._no_redirect_urlopen("https://relay.example/v1/chat/completions", timeout=5)
    assert "end of life" in str(ei.value.body)
    assert _cat(ei.value) == "model_unavailable"


def test_extractor_second_hop_failure_carries_provider_words(monkeypatch):
    """extractor 400 降级重发那一跳失败时,异常上也要带服务商原话(以前只剩 HTTP Error 400)。"""
    from agents import extractor
    from core import outbound

    calls = []

    def _raise(req, *, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise _http_error(400, b'{"error":{"message":"response_format is not supported"}}', "Bad Request")
        raise _http_error(400, "code:400 请求失败:请求中含有未识别参数".encode(), "Bad Request")

    monkeypatch.setattr(outbound, "safe_urlopen", _raise)
    monkeypatch.setattr("platform_app.user_credentials.resolve_api_key",
                        lambda uid, api: {"key": "k", "base_url_override": "https://relay.example/v1"})
    monkeypatch.setattr("platform_app.user_credentials.resolved_is_usable", lambda cred: True)
    monkeypatch.setattr("platform_app.user_credentials.resolved_auth_token", lambda cred: "k")
    with pytest.raises(urllib.error.HTTPError) as ei:
        extractor._call_openai_compat_json_mode("relay", "m", "sys", "user", 1, 5)
    assert len(calls) == 2
    assert _cat(ei.value) == "bad_request"
    assert "未识别参数" in classify_provider_error(ei.value)[1]


# ── OutboundBlocked(服务器模式 SSRF 闸)────────────────────────────────
def test_bare_outbound_blocked_from_urllib_line_is_network_with_reason():
    from core.outbound import OutboundBlocked
    cat, msg = classify_provider_error(OutboundBlocked("出站目标无法解析:relay.example"))
    assert cat == "network" and "无法解析" in msg


def test_sdk_connection_error_caused_by_outbound_blocked_shows_reason():
    from core.outbound import OutboundBlocked
    try:
        try:
            raise OutboundBlocked("出站目标解析到私有/本地/保留地址,已拒绝:relay.example → 10.0.0.2")
        except OutboundBlocked as inner:
            raise openai.APIConnectionError(request=_REQ) from inner
    except openai.APIConnectionError as exc:
        cat, msg = classify_provider_error(exc)
    assert cat == "network" and "保留地址" in msg


# ── 非对话出错面(导入)的一句话原因 ─────────────────────────────────────
def test_summary_for_unclassified_http_error_uses_provider_words():
    exc = _http_error(422, b'{"error":{"message":"tool_choice object is not supported by this endpoint"}}',
                      "Unprocessable Entity")
    attach_http_error_body(exc)
    assert "tool_choice object is not supported" in provider_error_summary(exc)


def test_http_status_accepts_three_digit_string_code_only():
    class _E(Exception):
        pass
    for code, expect in (("502", 502), (" 401 ", 401), ("1301", None), ("5O2", None), (True, None)):
        e = _E("x")
        e.code = code
        assert pe._http_status(e) == expect, code


# ── 审查返修:「模型」只是宾语 / 别人引号里的 model / 本地异常,都不算模型下线或不存在 ─────
@pytest.mark.parametrize("status,message", [
    (400, "The 'max_tokens' parameter of this model is deprecated, use 'max_completion_tokens'."),
    (400, "Parameter 'logprobs' for this model is no longer supported."),
    (None, "该模型的 max_tokens 参数已弃用,请改用 max_completion_tokens"),
    (None, "此参数对该模型已弃用"),
])
def test_model_as_object_not_subject_is_not_model_gone(status, message):
    exc = _openai_status_error(status, {"error": {"message": message}}) if status else RuntimeError(message)
    assert _cat(exc) != "model_unavailable"


@pytest.mark.parametrize("message", [
    'relation "kb_canon_entities" does not exist',
    'column "model" does not exist',
    'function jsonb_path_exists(text) does not exist',
])
def test_database_does_not_exist_is_not_model_unavailable(message):
    assert _cat(RuntimeError(message)) != "model_unavailable"


@pytest.mark.parametrize("message", [
    "The model `gpt-9` does not exist or you do not have access to it.",
    "model 'claude-x' does not exist",
    "Model Not Exist",  # DeepSeek 400
])
def test_model_missing_wordings_are_model_unavailable(message):
    assert _cat(RuntimeError(message)) == "model_unavailable"
    assert _cat(_openai_status_error(400, {"error": {"message": message}})) == "model_unavailable"


def test_404_hints_base_url_but_410_does_not():
    _, msg404 = classify_provider_error(_openai_status_error(404, {"error": {"message": "Not Found"}}))
    _, msg410 = classify_provider_error(_openai_status_error(410, {"error": {"message": _EOL}}))
    assert "/v1" in msg404
    assert "/v1" not in msg410


def test_sdk_redirect_status_gets_redirect_hint():
    """openai SDK 的 307(http 被重定向到 https)以前落空成「请重试」;按状态码统一给 base_url 提示。"""
    cat, msg = classify_provider_error(_openai_status_error(307, "Temporary Redirect"))
    assert cat == "network"
    assert "重定向" in msg and "base_url" in msg


def test_unrelated_error_raised_while_handling_outbound_blocked_is_not_network():
    """只认显式 __cause__:except OutboundBlocked 里又抛的无关异常(隐式 __context__)别说成连不上。"""
    from core.outbound import OutboundBlocked
    try:
        try:
            raise OutboundBlocked("出站目标解析到私有/本地/保留地址,已拒绝:x → 10.0.0.2")
        except OutboundBlocked:
            raise ValueError("写日志时出错")  # noqa: B904
    except ValueError as exc:
        known = classify_provider_error(exc)
    assert known is None or (known[0] != "network" and "保留地址" not in known[1])


def test_in_stream_deepseek_risk_control_is_not_retried():
    from agents.gm.stream_retry import _retryable_category
    exc = openai.APIError("Content Exists Risk", _REQ, body={"message": "Content Exists Risk"})
    assert _cat(exc) != "upstream"
    assert _retryable_category(exc) is None
