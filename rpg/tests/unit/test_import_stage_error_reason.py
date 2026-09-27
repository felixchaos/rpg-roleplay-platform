"""导入链失败原因要让用户看见(巡检 2026-09-28,与 provider_errors 盲区同批)。

以前:
- 人物卡全挂时结果卡只写「N 个候选 LLM 调用报错」,不说为什么;模型下线时还要对 40 个候选挨个撞一遍。
- 世界书阶段 job.error 是原始串「_stage_worldbook: HTTPError: HTTP Error 410: Gone」,
  阶段条目没有 error 字段,前端显示「未知错误」。
现在:原因经 provider_errors 分类,挂在 job 级 JobController 上(不挂阶段函数属性 —— 并发导入会串号),
runner / rebuild worker 从同一个 ctl 读出来写进阶段条目(canon_extract / anchors 同样)。
全程不碰 DB(connect 全部打桩)。
"""
from __future__ import annotations

import io
import urllib.error
from unittest import mock

import pytest

from platform_app.import_pipeline import rebuild_worker, runner, stages_llm
from platform_app.import_pipeline.control import JobController

_EOL_BODY = b'{"error":{"message":"The model \'openai/gpt-oss-120b\' has reached its end of life"}}'


def _gone():
    exc = urllib.error.HTTPError("https://relay.example/v1/chat/completions", 410, "Gone", {},
                                 io.BytesIO(_EOL_BODY))
    from agents.provider_errors import attach_http_error_body
    attach_http_error_body(exc)
    return exc


class _Ctl(JobController):
    """真 JobController 的 job 级状态,DB 读写打桩。"""

    def __init__(self, job_id="job-test"):
        super().__init__(job_id)
        self.updates: list[dict] = []

    def update(self, **fields):
        self.updates.append(fields)

    def is_cancelled(self):
        return False

    def add_usage(self, *a, **k):
        pass


def _fake_connect(rows):
    cur = mock.MagicMock()
    cur.execute.return_value.fetchall.return_value = rows
    cur.execute.return_value.fetchone.return_value = {"id": 7, "content": "", "c": 0}
    cm = mock.MagicMock()
    cm.__enter__.return_value = cur
    return mock.MagicMock(return_value=cm)


# ── JobController:原因挂在 job 级对象上 ───────────────────────────────────
def test_stage_error_hints_are_per_job_and_first_wins():
    a, b = JobController("a"), JobController("b")
    a.note_stage_error("cards", "第一条")
    a.note_stage_error("cards", "第二条")
    assert a.stage_error_hints == {"cards": "第一条"}
    assert b.stage_error_hints == {}  # 两个并发 job 互不串号


# ── _provider_error_hint ─────────────────────────────────────────────────
def test_hint_for_retired_model_is_classified_names_the_extractor_and_is_fatal():
    hint, fatal = stages_llm._provider_error_hint(_gone(), api_id="relay", model="gpt-oss-120b")
    assert "下线" in hint and "提取模型 relay/gpt-oss-120b" in hint
    assert fatal is True


@pytest.mark.parametrize("status,fatal", [(401, True), (403, False)])
def test_hint_auth_fatal_only_for_401(status, fatal):
    exc = urllib.error.HTTPError("https://x", status, "err", {}, io.BytesIO(b""))
    assert stages_llm._provider_error_hint(exc, api_id="a", model="m")[1] is fatal


def test_hint_for_unclassified_http_error_keeps_provider_words():
    exc = urllib.error.HTTPError("https://x", 422, "Unprocessable Entity", {},
                                 io.BytesIO(b'{"error":{"message":"tool_choice object is not supported"}}'))
    from agents.provider_errors import attach_http_error_body
    attach_http_error_body(exc)
    hint, fatal = stages_llm._provider_error_hint(exc, api_id="a", model="m")
    assert "tool_choice object is not supported" in hint and fatal is False


# ── _stage_cards:模型下线 → 记原因、别再对剩下的候选挨个撞 ─────────────────
def test_stage_cards_stops_after_fatal_error_and_records_reason():
    ctl = _Ctl()
    names = ["张三丰", "李四海", "王五岳", "赵六合"]
    entities = [{"name": n, "count": 9} for n in names]
    rows = [{"chapter_index": 1, "content": "".join(names), "chapter": 1, "summary": ""}]
    calls = []

    def _call(*a, **k):
        calls.append(a)
        raise _gone()

    with mock.patch.object(stages_llm, "connect", _fake_connect(rows)), \
         mock.patch.object(stages_llm, "_resolve_extractor_llm", return_value=("relay", "gpt-oss-120b")), \
         mock.patch("agents._harness.call_agent_json_guarded", side_effect=_call):
        generated = stages_llm._stage_cards(ctl, 1, 12, entities)
    assert generated == 0
    assert len(calls) == 1, "模型已下线还在对剩下的候选挨个调用"
    assert stages_llm._stage_cards._last_aborted == len(names) - 1
    assert stages_llm._stage_cards._last_llm_failures == len(names)
    assert "下线" in ctl.stage_error_hints["cards"]
    warn = [u["warnings"] for u in ctl.updates if "warnings" in u][-1]
    assert warn["aborted"] == len(names) - 1 and "下线" in warn["reason"]


def test_stage_cards_keeps_going_after_non_fatal_error():
    ctl = _Ctl()
    names = ["张三丰", "李四海", "王五岳"]
    rows = [{"chapter_index": 1, "content": "".join(names), "chapter": 1, "summary": ""}]
    calls = []

    def _call(*a, **k):
        calls.append(a)
        raise urllib.error.HTTPError("https://x", 403, "Forbidden", {}, io.BytesIO(b""))

    with mock.patch.object(stages_llm, "connect", _fake_connect(rows)), \
         mock.patch.object(stages_llm, "_resolve_extractor_llm", return_value=("relay", "m")), \
         mock.patch("agents._harness.call_agent_json_guarded", side_effect=_call):
        stages_llm._stage_cards(ctl, 1, 12, [{"name": n, "count": 9} for n in names])
    assert len(calls) == len(names)  # 403 可能只是这一条内容被拦,其余候选照试
    assert stages_llm._stage_cards._last_aborted == 0
    assert "403" in ctl.stage_error_hints["cards"]


# ── _stage_worldbook:原因写进 ctl,job.error 只留短话 ─────────────────────
def test_stage_worldbook_records_classified_reason():
    ctl = _Ctl()
    with mock.patch.object(stages_llm, "connect", _fake_connect([])), \
         mock.patch.object(stages_llm, "_resolve_extractor_llm", return_value=("relay", "gpt-oss-120b")), \
         mock.patch("agents._harness.call_agent_json_guarded", side_effect=_gone()):
        assert stages_llm._stage_worldbook(ctl, 1, 12) == 0
    assert "下线" in ctl.stage_error_hints["worldbook"]
    err = [u["error"] for u in ctl.updates if "error" in u][-1]
    assert "HTTP Error" not in err and "世界书" in err


def test_stage_worldbook_unparseable_output_gets_a_reason():
    ctl = _Ctl()
    with mock.patch.object(stages_llm, "connect", _fake_connect([])), \
         mock.patch.object(stages_llm, "_resolve_extractor_llm", return_value=("relay", "m")), \
         mock.patch("agents._harness.call_agent_json_guarded", return_value=("我没法给出 JSON", {})):
        assert stages_llm._stage_worldbook(ctl, 1, 12) == 0
    assert "JSON" in ctl.stage_error_hints["worldbook"]


def test_rebuild_worldbook_llm_surfaces_reason_as_job_error():
    ctl = _Ctl()

    def _stage(c, uid, sid):
        c.note_stage_error("worldbook", "当前模型不可用:已被服务商下线")
        return 0

    with mock.patch.object(rebuild_worker, "connect", _fake_connect([])), \
         mock.patch.object(rebuild_worker, "_stage_worldbook", side_effect=_stage):
        res = rebuild_worker._rebuild_worldbook(ctl, 1, 12, {"source": "llm"})
    assert res["ok"] is False
    assert "下线" in res["error"] and "下线" in res["partial_failures"][0]["error"]


# ── runner:阶段条目带上原因(结果卡不再是「N 个候选报错」/「未知错误」)────────
def test_run_pipeline_writes_reasons_into_stage_entries():
    ctl = _Ctl("job-runner")

    def _cards(c, uid, sid, ents):
        c.note_stage_error("cards", "当前模型不可用:已被服务商下线(提取模型 relay/m)")
        f = runner._stage_cards  # runner 读的是它命名空间里的这个对象(此处为桩)
        f._last_llm_failures, f._last_exceptions, f._last_unusable = 4, 1, 0
        f._last_rejected = f._last_skipped_dup = f._last_no_context = 0
        f._last_aborted, f._last_targets = 3, 4
        return 0

    def _wb(c, uid, sid):
        c.note_stage_error("worldbook", "当前模型不可用:已被服务商下线(提取模型 relay/m)")
        return 0

    def _canon(c, uid, sid):
        c.note_stage_error("canon_extract", "全部弧段 LLM 提取失败:当前模型不可用:已被服务商下线(提取模型 relay/m)")
        return 0, 0, "error", "error"

    with mock.patch.object(runner, "JobController", return_value=ctl), \
         mock.patch.object(runner, "init_db"), \
         mock.patch.object(runner, "connect", _fake_connect([])), \
         mock.patch.object(runner, "_redis_sem_init"), \
         mock.patch.object(runner, "_redis_sem_acquire", return_value=(False, None)), \
         mock.patch.object(runner, "_redis_sem_release"), \
         mock.patch("platform_app.cluster.try_acquire_job_lock", return_value=True), \
         mock.patch("platform_app.cluster.release_job_lock"), \
         mock.patch.object(runner, "finalize_job_if_unterminated"), \
         mock.patch.object(runner, "_stage_chunks", return_value=1), \
         mock.patch.object(runner, "_stage_facts", return_value=1), \
         mock.patch.object(runner, "_stage_story_phase_llm"), \
         mock.patch.object(runner, "_stage_phase_digests", return_value=0), \
         mock.patch.object(runner, "_stage_entities", return_value=[]), \
         mock.patch.object(runner, "_stage_cards", side_effect=_cards), \
         mock.patch.object(runner, "_stage_worldbook", side_effect=_wb), \
         mock.patch.object(runner, "_stage_canon_extract", side_effect=_canon), \
         mock.patch.object(runner, "_stage_embeddings", return_value=("done", 1)):
        runner._run_pipeline("job-runner", 1, 12, {})
    stages = [u["stages"] for u in ctl.updates if "stages" in u and u["stages"]
              and isinstance(u["stages"][-1], dict) and "count" in u["stages"][-1]][-1]
    by_id = {s["id"]: s for s in stages}
    assert by_id["cards"]["status"] == "error"
    assert "剩下 3 个候选没有再试" in by_id["cards"]["error"]
    assert "原因:当前模型不可用" in by_id["cards"]["error"]
    assert by_id["worldbook"]["status"] == "error"
    assert "下线" in by_id["worldbook"]["error"]
    assert "下线" in by_id["canon_extract"]["error"]
    assert "规范实体提取失败" in by_id["anchors"]["error"]
