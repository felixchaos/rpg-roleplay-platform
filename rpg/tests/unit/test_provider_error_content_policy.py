"""审核类错误单独成类 content_policy:不重试、不换渠道、不计渠道健康(巡检整合审查)。

流内错误(HTTP 200 的流里来了 error 事件)一律归 upstream 之后,只靠一张子串词表把审核类
排除出去,词表漏了真实措辞:
- Anthropic 输出审核:流内 error 事件,SDK 抛 status_code=200 的 APIStatusError,
  body = {type: error, error: {type: invalid_request_error,
  message: 'Output blocked by content filtering policy'}} —— 'content filtering' 不含 'content_filter';
- 通义兼容模式:code = data_inspection_failed,'Input data may contain inappropriate content.'。
这两种都被当成供应商临时故障,首 token 前用玩家的 key 把整段提示词再发两次,记成渠道故障,
开了 channel_fallback 还切备用渠道再发一遍,最后告诉玩家「稍等片刻重试」。
命中了词表的(DeepSeek 'Content Exists Risk' 等)又落回 None,拿到「请重试(错误码 Exxx)」。

同一个原因以 finish_reason 到达时,空回合分诊给的是 content_filter「换个说法或换模型」,
两条边界的说法要对齐(同一句「被所用模型的内容策略拦下了」+ 同一条建议)。
"""
from __future__ import annotations

import anthropic
import httpx
import openai
import pytest

from agents.gm.stream_retry import (
    _retryable_category,
    stream_with_channel_fallback,
    stream_with_pretoken_retry,
)
from agents.provider_errors import classify_provider_error

_REQ = httpx.Request("POST", "https://relay.example/v1/chat/completions")
_AREQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _anthropic_stream_error(err_type: str, message: str) -> anthropic.APIStatusError:
    return anthropic.APIStatusError(
        message, response=httpx.Response(200, request=_AREQ),
        body={"type": "error", "error": {"type": err_type, "message": message}},
    )


def _openai_stream_error(message: str, **body) -> openai.APIError:
    return openai.APIError(message, _REQ, body={"message": message, **body})


_POLICY_CASES = [
    _anthropic_stream_error("invalid_request_error", "Output blocked by content filtering policy"),
    _openai_stream_error("Input data may contain inappropriate content.",
                         code="data_inspection_failed", type="data_inspection_failed"),
    _openai_stream_error("Output data may contain inappropriate content.", code="data_inspection_failed"),
    _openai_stream_error("Content Exists Risk"),
    _openai_stream_error("blocked", code="content_policy_violation"),
    _openai_stream_error("系统检测到输入或生成内容可能包含不安全或敏感内容", code="1301"),
]
_POLICY_IDS = ["anthropic-200-filtering", "qwen-input", "qwen-output", "deepseek-risk",
               "openai-policy-code", "zhipu-1301"]


@pytest.mark.parametrize("exc", _POLICY_CASES, ids=_POLICY_IDS)
def test_in_stream_moderation_is_content_policy(exc):
    cat, msg = classify_provider_error(exc)
    assert cat == "content_policy"
    assert "内容策略拦下了" in msg and "换个说法" in msg
    assert "稍等片刻重试" not in msg
    assert _retryable_category(exc) is None


@pytest.mark.parametrize("status,body", [
    (400, {"error": {"message": "Input data may contain inappropriate content.",
                     "code": "data_inspection_failed", "type": "data_inspection_failed"}}),
    (400, {"error": {"message": "Content Exists Risk", "type": "invalid_request_error"}}),
    (400, {"error": {"message": "The response was filtered due to the prompt triggering Azure OpenAI's "
                                "content management policy.", "code": "content_filter"}}),
], ids=["qwen-400", "deepseek-400", "azure-400"])
def test_http_400_moderation_is_content_policy(status, body):
    """同一个原因走 HTTP 400(非流式、或流开始前就被拒)也归同一类,不落「请重试(错误码)」。"""
    exc = openai.BadRequestError("Error code: 400", response=httpx.Response(status, request=_REQ), body=body)
    assert classify_provider_error(exc)[0] == "content_policy"


def test_400_case_sensitive_wording_is_not_moderation():
    """400 只认强措辞:参数报错里的「大小写敏感」「违规参数」不能被说成内容被拦。"""
    exc = openai.BadRequestError(
        "Error code: 400", response=httpx.Response(400, request=_REQ),
        body={"error": {"message": "参数名大小写敏感,请检查 tool_choice 字段"}})
    known = classify_provider_error(exc)
    assert known is None or known[0] != "content_policy"


def test_anthropic_in_stream_invalid_request_is_not_retried():
    """流内 invalid_request_error 是请求本身被拒(非瞬时),没命中审核词也不归 upstream。"""
    exc = _anthropic_stream_error("invalid_request_error", "messages: something is not allowed here")
    known = classify_provider_error(exc)
    assert known is not None and known[0] not in ("upstream", "ratelimit")
    assert _retryable_category(exc) is None


def test_anthropic_in_stream_overloaded_still_upstream():
    exc = _anthropic_stream_error("overloaded_error", "Overloaded")
    assert classify_provider_error(exc)[0] == "upstream"
    assert _retryable_category(exc) == "upstream"


def _failing_after_reasoning(exc):
    attempts: list[int] = []

    def factory():
        attempts.append(1)

        def g():
            yield {"type": "reasoning", "text": "思考"}
            raise exc
        return g()
    return factory, attempts


@pytest.mark.parametrize("exc", _POLICY_CASES[:2], ids=_POLICY_IDS[:2])
def test_stream_retry_does_not_resend_moderated_prompt(exc):
    factory, attempts = _failing_after_reasoning(exc)
    out = []
    with pytest.raises(type(exc)):
        for ev in stream_with_pretoken_retry(factory, sleep=lambda _s: None):
            out.append(ev)
    assert len(attempts) == 1, "审核拦截重试也一样被拦,不该拿玩家的 key 再发整段提示词"
    assert not [e for e in out if e.get("type") == "retry_notice"]


def test_moderation_does_not_switch_channel(monkeypatch):
    import core.channel_fallback as cf
    import core.feature_flags as ff

    monkeypatch.setattr(ff, "feature_enabled", lambda key, uid=None: key == "channel_fallback")
    monkeypatch.setattr(cf, "resolve_fallback_channel", lambda uid, ex: ("anthropic", "claude"))
    factory, attempts = _failing_after_reasoning(_POLICY_CASES[0])
    with pytest.raises(anthropic.APIStatusError):
        list(stream_with_channel_fallback(
            factory, user_id=1, primary_api_id="relay",
            make_backup_factory=lambda a, m: (lambda: iter([{"type": "text", "text": "备用"}])),
            sleep=lambda _s: None,
        ))
    assert len(attempts) == 1


def test_moderation_not_counted_as_channel_failure(monkeypatch):
    import model_probe
    from routes.game._shared import _note_channel_health_failure

    noted: list[tuple] = []
    monkeypatch.setattr(model_probe, "note_channel_failure", lambda *a, **k: noted.append((a, k)))
    for exc in _POLICY_CASES:
        _note_channel_health_failure(exc, "relay", {"id": 1})
    assert noted == []
    # 对照组:真正的流内上游故障照常计入
    _note_channel_health_failure(_anthropic_stream_error("overloaded_error", "Overloaded"), "relay", {"id": 1})
    assert len(noted) == 1


def test_wording_matches_empty_turn_content_filter():
    """与空回合分诊 content_filter 同一口径:同一句「被所用模型的内容策略拦下了」+ 同一条建议。"""
    from types import SimpleNamespace

    from chat_pipeline.persist import _empty_turn_diagnosis

    ctx = SimpleNamespace(
        gm=SimpleNamespace(api_id="relay", _backend=SimpleNamespace(
            model_name="m", last_usage={"finish_reason": "content_filter"})),
        state=SimpleNamespace(data={}), turn_tool_errors=[], gm_max_tokens=0,
    )
    reason, diag, _facts = _empty_turn_diagnosis(ctx, 0)
    assert reason == "content_filter"
    shared = "被所用模型的内容策略拦下了"
    advice = diag[diag.index("可以换个说法"):]
    _cat, msg = classify_provider_error(_POLICY_CASES[0])
    assert shared in diag and shared in msg
    assert advice in msg, f"建议要和空回合分诊逐字一致:{advice}"
