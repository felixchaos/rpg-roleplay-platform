"""test_empty_turn_diagnosis.py — GM 空回合的确定性分诊(巡检 F5)。

此前清洗后正文为空时,玩家永远看到写死的「可能触发了模型的安全过滤,或者上下文出错,请换个说法」,
服务端只留一行 `len(raw)=0 save_id=None`。最常见的成因(思考模型把单轮输出上限吃光)换说法根本
没用,现场信号也一条没留。本文件锁:
  · _empty_turn_diagnosis 每个分支(复现脚本 A–E + 清洗后为空 + 内容策略一族 + 工具标记解析不了);
  · Phase 5 空分支:error 带 reason、文案可操作且无 emoji;token_usage 落库带 empty_response 元数据;
    游戏台发 usage SSE、酒馆不发(酒馆页会把用量挂到上一条 GM 回复下面);日志补齐证据、save_id 回退 early;
  · _stop_reason_notice:正文为空时不再先弹「截断了,说继续」;vertex SAFETY / anthropic refusal 也认。
"""
from __future__ import annotations

import asyncio
import copy
import logging
import re
import threading

import pytest

from chat_pipeline._common import PipelineContext
from chat_pipeline.gm import _stop_reason_notice
from chat_pipeline.persist import _empty_turn_diagnosis, persist_turn_phase
from state import GameState

# 与仓库 emoji 扫描同一组码位;用转义写,别把被禁的符号本身写进源码。
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2665\u2726\u25B2\u25BC\u25B6\u25C6]")


class _B:
    def __init__(self, last_usage=None):
        self.model_name = "qwen3-thinking"
        self.last_usage = dict(last_usage or {})


class _GM:
    def __init__(self, last_usage=None):
        self.api_id = "ollama-cloud"
        self._backend = _B(last_usage)


def _ctx(last_usage=None, *, reasoning=None, tool_ops=None, tool_errors=None, max_tokens=4096,
         tavern=False) -> PipelineContext:
    st = GameState.new()
    if tavern:
        from context_providers.registry import DEFAULT_TAVERN_MANIFEST
        st.data["content_pack"] = copy.deepcopy(DEFAULT_TAVERN_MANIFEST)
        st.data["tavern"] = {}
    st.data["_turn_reasoning"] = list(reasoning or [])
    st.data["_turn_tool_ops"] = list(tool_ops or [])
    c = PipelineContext(api_user={"id": 7}, state=st, gm=_GM(last_usage), sub_gm=None,
                        message_for_model="继续推进剧情", run_id=1, stop_event=threading.Event(),
                        chat_start_time=0.0)
    c.turn_tool_errors = list(tool_errors or [])
    c.gm_max_tokens = max_tokens
    c.bundle = {"prompt": "p", "debug": {}}
    return c


_CASES = [
    # (id, ctx kwargs, raw_len, 期望 reason, 文案必须包含)
    ("A_thinking_ate_budget", dict(last_usage={"finish_reason": "length", "output_tokens": 4096,
                                               "reasoning_tokens": 4000, "max_tokens": 4096},
                                   reasoning=["想" * 650]), 0, "reasoning_exhausted", "最大输出 token 数"),
    ("A2_reasoning_stream_only", dict(last_usage={"finish_reason": "length"}, reasoning=["嗯"]),
     0, "reasoning_exhausted", "思考"),
    ("A3_length_no_visible_thinking", dict(last_usage={"finish_reason": "length", "max_tokens": 4096}),
     0, "length", "最大输出 token 数"),
    ("B_reasoning_only_stop", dict(last_usage={"finish_reason": "stop"}, reasoning=["想了想"]),
     0, "reasoning_only", "重试"),
    ("B2_reasoning_tokens_only", dict(last_usage={"finish_reason": "stop", "reasoning_tokens": 300}),
     0, "reasoning_only", "重试"),
    ("C_tool_only", dict(last_usage={"finish_reason": "stop"},
                         tool_ops=[{"tool": "kb_search", "ok": True}]), 0, "tool_only", "重试"),
    ("D_upstream_empty", dict(last_usage={}), 0, "upstream_empty", "换个渠道"),
    ("stripped_to_empty", dict(last_usage={"finish_reason": "length"}, reasoning=["x"]),
     120, "stripped_to_empty", "重试"),
    ("openai_content_filter", dict(last_usage={"finish_reason": "content_filter"}), 0,
     "content_filter", "内容策略"),
    ("vertex_safety", dict(last_usage={"finish_reason": "SAFETY"}), 0, "content_filter", "内容策略"),
    ("vertex_prohibited", dict(last_usage={"finish_reason": "PROHIBITED_CONTENT"}), 0,
     "content_filter", "内容策略"),
    ("anthropic_refusal", dict(last_usage={"finish_reason": "refusal"}), 0, "content_filter", "内容策略"),
    ("dsml_unparsed", dict(last_usage={"finish_reason": "stop"}, tool_errors=["模型输出了无法解析的工具调用标记"]),
     0, "tool_markup_unparsed", "解析不了"),
    ("vertex_malformed_call", dict(last_usage={"finish_reason": "MALFORMED_FUNCTION_CALL"}), 0,
     "tool_markup_unparsed", "解析不了"),
]


@pytest.mark.parametrize("cid,kw,raw_len,reason,must", _CASES, ids=[c[0] for c in _CASES])
def test_diagnosis_branches(cid, kw, raw_len, reason, must):
    got_reason, msg, facts = _empty_turn_diagnosis(_ctx(**kw), raw_len)
    assert got_reason == reason
    assert must in msg
    assert not _EMOJI.search(msg), f"文案里有 emoji / 符号图标: {msg!r}"
    assert "→" not in msg
    if reason != "content_filter":
        assert "安全过滤" not in msg and "内容策略" not in msg, "不是风控就别让玩家以为是风控"
    assert facts["api_id"] == "ollama-cloud" and facts["raw_len"] == raw_len


def test_budget_numbers_are_shown_when_known():
    _, msg, facts = _empty_turn_diagnosis(_ctx(
        {"finish_reason": "length", "reasoning_tokens": 3900, "max_tokens": 4096}, reasoning=["x"]), 0)
    assert "4096" in msg and "3900" in msg
    assert facts["max_tokens"] == 4096


def test_max_tokens_falls_back_to_turn_setting():
    """vertex / anthropic 的 last_usage 不带 max_tokens,用 Phase 4 记下的本回合上限。"""
    _, msg, facts = _empty_turn_diagnosis(_ctx({"finish_reason": "length"}, max_tokens=2048), 0)
    assert facts["max_tokens"] == 2048 and "2048" in msg


def test_length_outranks_tools_and_filter_outranks_tools():
    c = _ctx({"finish_reason": "length"}, tool_ops=[{"tool": "t"}])
    assert _empty_turn_diagnosis(c, 0)[0] == "length"
    c = _ctx({"finish_reason": "SAFETY"}, tool_ops=[{"tool": "t"}], tool_errors=["x"])
    assert _empty_turn_diagnosis(c, 0)[0] == "content_filter"


def test_broken_ctx_does_not_raise():
    class _Boom:
        @property
        def gm(self):
            raise RuntimeError("boom")
    reason, msg, _ = _empty_turn_diagnosis(_Boom(), 0)
    assert reason == "upstream_empty" and msg


# ── Phase 5 空分支 ─────────────────────────────────────────────────────────────
async def _drain(agen):
    return [x async for x in agen]


def _run_persist(ctx, *, usage_payload=None):
    captured: dict = {}

    def _build_usage(*args, **kwargs):
        captured["args"], captured["kwargs"] = args, kwargs
        return usage_payload

    def _persist_chat_turn(*a, **k):
        raise AssertionError("空回合不该 record_turn / 落 commit")

    events = asyncio.run(_drain(persist_turn_phase(
        ctx, payload_fn=lambda u: {"ok": 1}, persist_chat_turn=_persist_chat_turn,
        build_usage_payload=_build_usage,
    )))
    return events, captured


def test_empty_branch_error_has_reason_and_actionable_text():
    c = _ctx({"finish_reason": "length", "reasoning_tokens": 4000, "max_tokens": 4096}, reasoning=["想"])
    c.response = ""
    events, _ = _run_persist(c)
    errs = [d for e, d in events if e == "error"]
    assert len(errs) == 1
    assert errs[0]["kind"] == "empty_response"
    assert errs[0]["reason"] == "reasoning_exhausted"
    assert "安全过滤" not in errs[0]["message"]
    assert events[-1][0] == "done" and events[-1][1]["empty"] is True


def test_empty_branch_records_usage_with_empty_metadata_and_early_ids():
    c = _ctx({"finish_reason": "stop"})
    c.early_persist_user_id, c.early_active_save_id = 7, 99
    c.response = ""
    usage = {"input_tokens": 1200, "output_tokens": 0, "reasoning_tokens": 0}
    events, cap = _run_persist(c, usage_payload=usage)
    assert cap["args"][4:6] == (7, 99), "persist_user_id / save_id 要回退到 Phase 1 解析的 early 值"
    assert cap["kwargs"]["extra_metadata"] == {"empty_response": True, "reason": "upstream_empty"}
    kinds = [e for e, _ in events]
    assert kinds.index("usage") < kinds.index("error"), "游戏台:usage 要先于 error 到,footer 才能显示本轮用量"


def test_tavern_empty_branch_records_usage_but_sends_no_usage_sse():
    c = _ctx({"finish_reason": "stop"}, tavern=True)
    c.persist_user_id, c.active_save_id = 7, 55
    c.response = ""
    events, cap = _run_persist(c, usage_payload={"input_tokens": 10})
    assert cap["args"][4:6] == (7, 55)
    assert "usage" not in [e for e, _ in events], "酒馆页会把这条用量挂到上一条 GM 回复下面"
    assert [d["reason"] for e, d in events if e == "error"] == ["upstream_empty"]


def test_empty_branch_survives_usage_writer_failure():
    c = _ctx({})
    c.response = ""

    def _boom(*a, **k):
        raise TypeError("旧签名不收 extra_metadata")

    events = asyncio.run(_drain(persist_turn_phase(
        c, payload_fn=lambda u: {}, persist_chat_turn=lambda *a, **k: None, build_usage_payload=_boom)))
    assert [e for e, _ in events] == ["error", "done"]


def test_empty_branch_log_carries_evidence(caplog):
    c = _ctx({"finish_reason": "length", "output_tokens": 4096, "max_tokens": 4096})
    c.early_active_save_id = 321
    c.response = ""
    with caplog.at_level(logging.WARNING):
        _run_persist(c)
    line = next(r.getMessage() for r in caplog.records if "GM 返回空响应" in r.getMessage())
    for needle in ("reason=length", "api_id=ollama-cloud", "model=qwen3-thinking", "finish_reason=length",
                   "out_tokens=4096", "max_tokens=4096", "save_id=321"):
        assert needle in line, f"日志缺 {needle}: {line}"
    assert "save_id=None" not in line


def test_set_directive_and_tavern_card_branches_unchanged():
    c = _ctx({})
    c.response = ""
    c.directive_updates = ["已设置 时间=傍晚"]
    events, cap = _run_persist(c)
    assert [e for e, _ in events] == ["done"] and not cap
    c = _ctx({})
    c.response = ""
    c.tavern_character_set = True
    events, cap = _run_persist(c)
    assert [e for e, _ in events] == ["done"] and not cap


def test_ops_only_output_is_stripped_to_empty():
    c = _ctx({"finish_reason": "stop"})
    c.response = '```json\n[{"op": "set", "path": "world.time", "value": "傍晚"}]\n```'
    events, _ = _run_persist(c)
    assert [d["reason"] for e, d in events if e == "error"] == ["stripped_to_empty"]


def test_whitespace_only_output_is_not_stripped_to_empty():
    """原始输出只有空白 = 上游没给东西,别判成「只输出了状态指令」。"""
    c = _ctx({})
    c.response = "  \n\n "
    events, _ = _run_persist(c)
    assert [d["reason"] for e, d in events if e == "error"] == ["upstream_empty"]


# ── _stop_reason_notice ────────────────────────────────────────────────────────
class _NoticeCtx:
    def __init__(self, fr):
        self.gm = _GM({"finish_reason": fr})


def test_notice_silent_when_body_empty():
    """空回合:「截断了,可以说继续」与「根本没有正文」矛盾,解释交给 Phase 5 分诊。"""
    assert _stop_reason_notice(_NoticeCtx("length"), body_empty=True) == []
    assert _stop_reason_notice(_NoticeCtx("content_filter"), body_empty=True) == []


@pytest.mark.parametrize("fr", ["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "refusal"])
def test_notice_recognizes_whole_content_filter_family(fr):
    out = _stop_reason_notice(_NoticeCtx(fr))
    assert len(out) == 1 and "内容策略" in out[0][1]
    assert not _EMOJI.search(out[0][1])


def test_output_regex_emptied_is_told_apart():
    """玩家自己的输出正则把正文整段换空:重试没用,得告诉他去看正则,而不是「直接重试」。"""
    reason, msg, _ = _empty_turn_diagnosis(_ctx({"finish_reason": "stop"}), 80, regex_emptied=True)
    assert reason == "output_regex_emptied" and "正则" in msg
    assert not _EMOJI.search(msg)


def test_output_regex_emptied_on_phase5(monkeypatch):
    import state.regex_scripts as rx
    monkeypatch.setattr(rx, "apply_output_regex", lambda text, uid: "")
    c = _ctx({"finish_reason": "stop"})
    c.response = "夜色渐深,远处传来钟声。"
    events, _ = _run_persist(c)
    assert [d["reason"] for e, d in events if e == "error"] == ["output_regex_emptied"]
