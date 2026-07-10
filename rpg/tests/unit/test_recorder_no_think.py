"""史官 recorder 思考黑洞根修回归测试(268 二连实锤,2026-07-10)。

行者无疆反馈「场景切换后 AIGM 时间倒流 + 反复描述同一批事件」根因链:
生产在线回合唯一验收路径 = recorder_bridge → recorder.record_turn(单次 LLM)→ 注入
预计算 judge。op 中转拒 tools 降级 json_object 后,思考模型(op/deepseek-v4-flash)无界
思考吃光 1200 预算(reasoning_tokens=1200,正文 0 字)→ 全字段静默解析成空 → 锚点永不
验收 → 同批 pending 锚点每回合重新注入 rail 目标 → GM 反复重演已过剧情/时间倒流。
v1.67.1 只把 no_think 接到 _default_judge(生产不走)。

本文件锁三条缝:
1. _openai_function_call(no_think=True) 的 tools 请求体带 thinking.disabled;
2. tools 形态被拒降级 json_object 时 thinking.disabled 透传 extra_body;
3. record_turn 空正文护栏:空 → 扩预算重试一次;再空 → 返回空结果但绝不静默(告警)。
"""
import json
import urllib.error

from agents import _harness

TOOL_SCHEMA = {
    "name": "emit_record",
    "description": "x",
    "input_schema": {"type": "object", "properties": {}},
}


def _patch_key(monkeypatch):
    import platform_app.user_credentials as uc
    monkeypatch.setattr(
        uc, "resolve_api_key",
        lambda uid, aid: {"key": "k", "base_url_override": "https://relay.example/v1"},
    )


def test_function_call_body_carries_thinking_disabled(monkeypatch):
    """no_think=True → tools 请求体顶层带 thinking.disabled(思考模型 forced tool-call
    同样会无界思考,必须在第一跳就禁)。"""
    _patch_key(monkeypatch)
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"tool_calls": [
                    {"function": {"name": "emit_record", "arguments": "{\"ok\":1}"}}
                ]}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }).encode("utf-8")

    def fake_urlopen(req, *, timeout):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(_harness, "_no_redirect_urlopen", fake_urlopen)
    text, _ = _harness._openai_function_call(
        "relay", "m", "sys", "user", TOOL_SCHEMA, 1, 30, 1200, no_think=True,
    )
    assert captured["body"].get("thinking") == {"type": "disabled"}
    assert text == "{\"ok\":1}"
    # 默认(no_think=False)不带,零行为变化
    _harness._openai_function_call("relay", "m", "sys", "user", TOOL_SCHEMA, 1, 30, 1200)
    assert "thinking" not in captured["body"]


def test_tools_reject_degrade_passes_thinking_extra_body(monkeypatch):
    """op 等中转拒 tools(400)→ 降级 json_object 时 no_think 必须透传 extra_body
    (生产真实路径:降级后才是思考黑洞现场)。"""
    _patch_key(monkeypatch)
    seen = {}

    def fake_json_mode(api_id, model, sp, up, uid, to, mt, extra_body=None):
        seen["extra_body"] = extra_body
        return ("{}", {"input_tokens": 0, "output_tokens": 0})

    monkeypatch.setattr(_harness, "_openai_compat_json_mode", fake_json_mode)

    def raise_400(req, *, timeout):
        raise urllib.error.HTTPError("u", 400, "err", None, None)

    monkeypatch.setattr(_harness, "_no_redirect_urlopen", raise_400)
    _harness._openai_function_call(
        "relay", "m", "sys", "user", TOOL_SCHEMA, 1, 30, 1200, no_think=True,
    )
    assert seen["extra_body"] == {"thinking": {"type": "disabled"}}
    _harness._openai_function_call("relay", "m", "sys", "user", TOOL_SCHEMA, 1, 30, 1200)
    assert seen["extra_body"] is None


def test_record_turn_empty_body_retries_then_gives_up(monkeypatch):
    """空正文绝不静默当空结果:第一次空 → 扩预算(2400)重试;再空 → 空结果放弃。
    锁「1300+ 回合零验收无人发觉」的静默病根。"""
    from agents import recorder as R

    monkeypatch.setattr(R, "_resolve_recorder_api_and_model", lambda *a: ("relay", "m"))
    calls = []

    def fake_call(**kw):
        calls.append(kw.get("max_tokens", 1200))
        return ("", {"output_tokens": 1200, "reasoning_tokens": 1200})

    monkeypatch.setattr(R, "_call_recorder", fake_call)
    out = R.record_turn("正文", {}, tasks=["anchors"], user_id=1)
    assert calls == [1200, 2400], "第一次默认预算,重试必须扩到 2400"
    assert out.get("reached") == [] and out.get("current_chapter") is None


def test_record_turn_retry_success_parses(monkeypatch):
    """重试拿到正文 → 正常解析(护栏只兜空,不动成功路径)。"""
    from agents import recorder as R

    monkeypatch.setattr(R, "_resolve_recorder_api_and_model", lambda *a: ("relay", "m"))
    payload = json.dumps({
        "reached": [{"anchor_key": "chapter:21:event:1", "drift_score": 0.2}],
        "current_chapter": 22,
        "progress_motion": 1,
    })
    seq = iter([("", {"reasoning_tokens": 1200}), (payload, {"output_tokens": 100})])

    monkeypatch.setattr(R, "_call_recorder", lambda **kw: next(seq))
    out = R.record_turn("正文", {}, tasks=["anchors"], user_id=1)
    assert out.get("reached") and out["reached"][0]["anchor_key"] == "chapter:21:event:1"
    assert out.get("current_chapter") == 22
