"""test_gm_turn_stop_signals.py — 空回合分诊依赖的三个后端确定性信号。

1. last_usage 每回合清零:后端只在收到 usage 时覆盖它,流里没有 usage 时读到的是上一次调用的
   残留(误报截断 / 重复记账 / 分诊被带偏)。清零放在 GameMaster.respond_stream_with_tools 入口。
2. 清零后,openai_compat 对「只发 finish_reason 不发 usage chunk」的渠道仍要留住 finish_reason
   —— 原来写 finish_reason 有个 `if self.last_usage:` 前提,清零后会把截断信号整个丢掉。
3. DSML 工具标记出现了却一个调用都没解析出来(截断在 invoke 里)→ 发 tool_error,
   native 循环与 text-marker 降级循环同款(此前两条路都只有一行日志,事件流是空的)。
"""
from __future__ import annotations

from agents.gm.backends._dsml import DSML_UNPARSED_ERROR
from agents.gm.backends.openai_compat import _OpenAICompatBackend
from agents.gm.helpers import _openai_text_marker_loop
from agents.gm.master import GameMaster
from chat_pipeline.gm import _stop_reason_notice


class _D:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _chunks(parts: list[str], finish_reason: str | None, *, reasoning: list[str] | None = None):
    """OpenAI 流式 chunk 桩;**不带 usage**(模拟不发 include_usage 的中转站)。"""
    for r in reasoning or []:
        yield _D(usage=None, choices=[_D(delta=_D(reasoning_content=r, content=None, tool_calls=None),
                                         finish_reason=None)])
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        yield _D(usage=None, choices=[_D(delta=_D(reasoning_content=None, content=p, tool_calls=None),
                                         finish_reason=finish_reason if last else None)])
    if not parts:
        yield _D(usage=None, choices=[_D(delta=_D(reasoning_content=None, content=None, tool_calls=None),
                                         finish_reason=finish_reason)])


def _backend(stream_factory) -> _OpenAICompatBackend:
    b = object.__new__(_OpenAICompatBackend)
    b.api_id, b.model_name, b.user_id = "relay", "some-model", 1
    b.last_usage = {}
    b._tuning_kwargs = lambda _t: {}
    b._create = lambda **kw: stream_factory()
    return b


# ── 1. 入口清零 ────────────────────────────────────────────────────────────────
class _SilentBackend:
    """上游 200 + 空流:一个 chunk 都没有,也没有 usage。"""
    supports_native_tools = False
    model_name = "m"

    def __init__(self):
        self.last_usage = {"finish_reason": "length", "output_tokens": 4096, "reasoning_tokens": 3900}

    def stream(self, system, messages, max_tokens):
        return iter(())


class _State:
    data: dict = {}

    def history_messages(self):
        return []


def _bare_gm(backend) -> GameMaster:
    gm = object.__new__(GameMaster)
    gm._backend = backend
    gm._build_system = lambda: "sys"
    gm._turn_message = lambda *a, **k: "u"
    return gm


def test_stale_last_usage_is_cleared_at_turn_entry():
    b = _SilentBackend()
    gm = _bare_gm(b)
    events = list(gm.respond_stream_with_tools("你好", "", _State(), tools=None, max_tokens=4096))
    assert events == []
    assert b.last_usage == {}, "上一次调用的残留还在,空流会被误报成截断 / 思考吃光"


def test_clear_happens_on_tools_path_too():
    b = _SilentBackend()
    gm = _bare_gm(b)
    gm._turn_content = lambda *a, **k: "u"
    list(gm.respond_stream_with_tools("你好", "", _State(),
                                      tools=[{"server_id": "s", "name": "t", "schema": {}}], max_tokens=4096))
    assert b.last_usage == {}


# ── 2. 清零后仍留住 finish_reason ──────────────────────────────────────────────
def test_stream_keeps_finish_reason_without_usage_chunk():
    b = _backend(lambda: _chunks(["半截"], "length"))
    assert "".join(b.stream("s", [{"role": "user", "content": "x"}], max_tokens=512)) == "半截"
    assert b.last_usage.get("finish_reason") == "length"
    assert b.last_usage.get("max_tokens") == 512


def test_mcp_loop_keeps_finish_reason_without_usage_chunk():
    b = _backend(lambda: _chunks(["半截"], "length"))
    events = list(b.stream_with_mcp_loop(
        system="s", messages=[{"role": "user", "content": "x"}],
        mcp_tools=[{"server_id": "srv", "name": "t", "schema": {"type": "object", "properties": {}}}],
        max_iterations=2, max_tokens=512, mcp_call=lambda *a: {"ok": True},
    ))
    assert [e["text"] for e in events if e["type"] == "text"] == ["半截"]
    assert b.last_usage.get("finish_reason") == "length"


class _Ctx:
    def __init__(self, backend):
        class _G:
            _backend = backend
        self.gm = _G()


def test_truncation_notice_survives_the_reset():
    """回归(复核实测):清零 + 保留旧的 `if self.last_usage:` 前提 → notice 变成 [],v1.84 的截断提示静默失效。"""
    b = _backend(lambda: _chunks(["半截"], "length"))
    list(b.stream("s", [{"role": "user", "content": "x"}], max_tokens=512))
    out = _stop_reason_notice(_Ctx(b))
    assert out and "截断" in out[0][1]


# ── 3. DSML 标记解析出 0 个调用 → tool_error ──────────────────────────────────
_TRUNCATED_DSML = '<｜DSML｜function_calls><｜DSML｜invoke name="dispatcher/list_pending_anchors"><｜DSML｜parameter name="limit">'


def test_native_loop_reports_unparsed_dsml():
    b = _backend(lambda: _chunks([_TRUNCATED_DSML[:30], _TRUNCATED_DSML[30:]], "length"))
    calls = []
    events = list(b.stream_with_mcp_loop(
        system="s", messages=[{"role": "user", "content": "x"}],
        mcp_tools=[{"server_id": "dispatcher", "name": "list_pending_anchors",
                    "schema": {"type": "object", "properties": {}}}],
        max_iterations=2, max_tokens=512, mcp_call=lambda *a: calls.append(a) or {"ok": True},
    ))
    assert calls == []
    assert [e for e in events if e["type"] == "text"] == []
    errs = [e for e in events if e["type"] == "tool_error"]
    assert errs and errs[0]["error"] == DSML_UNPARSED_ERROR


def test_native_loop_parsed_dsml_emits_no_tool_error():
    full = ('<｜DSML｜function_calls><｜DSML｜invoke name="t"></｜DSML｜invoke></｜DSML｜function_calls>')
    replies = iter([_chunks([full], "stop"), _chunks(["好了。"], "stop")])
    b = _backend(lambda: next(replies))
    events = list(b.stream_with_mcp_loop(
        system="s", messages=[{"role": "user", "content": "x"}],
        mcp_tools=[{"server_id": "srv", "name": "t", "schema": {"type": "object", "properties": {}}}],
        max_iterations=3, max_tokens=512, mcp_call=lambda *a: {"ok": True, "result": "ok"},
    ))
    assert not [e for e in events if e["type"] == "tool_error"]


def test_text_marker_loop_reports_unparsed_dsml():
    class _B:
        def stream(self, system, messages, max_tokens):
            yield from [_TRUNCATED_DSML[:20], _TRUNCATED_DSML[20:]]

    events = list(_openai_text_marker_loop(
        _B(), "sys", [{"role": "user", "content": "x"}],
        [{"server_id": "dispatcher", "name": "list_pending_anchors", "schema": {}}],
        3, 512, lambda *a: {"ok": True},
    ))
    assert [e for e in events if e["type"] == "text"] == []
    errs = [e for e in events if e["type"] == "tool_error"]
    assert errs and errs[0]["error"] == DSML_UNPARSED_ERROR
