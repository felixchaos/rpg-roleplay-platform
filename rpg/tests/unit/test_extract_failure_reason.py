"""弧段 / 逐章提取失败时要带出原因(与 provider_errors 盲区同批,巡检 2026-09-28)。

extract_chapter 自己吞掉 LLM 调用异常、返回 raw_ok=False。以前:
- 只有一个布尔,原因丢了;
- arc_pipeline / pipeline 把 raw_ok=False 当成功计数,模型下线时 100 个弧全空、照样 ok=True
  (知识库提取入口还因此照扣一次月度额度);
- failed_arcs / failed_chapters 的记录写在 return 之后,是死代码,永远是空的;
- 导入的 canon_extract / anchors 阶段条目只显示「未知错误」。
全程打桩,不连 DB、不发请求。
"""
from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace
from unittest import mock

from extract.per_chapter import ChapterExtract, extract_chapter

_EOL = b'{"error":{"message":"The model \'x/old-model\' has reached its end of life"}}'


def _gone():
    from agents.provider_errors import attach_http_error_body
    exc = urllib.error.HTTPError("https://relay.example/v1", 410, "Gone", {}, io.BytesIO(_EOL))
    attach_http_error_body(exc)
    return exc


class _DeadLLM:
    def complete_json(self, *a, **k):
        raise _gone()


class _ProseLLM:
    def complete_json(self, *a, **k):
        return ["不是对象"]


def test_extract_chapter_keeps_classified_reason():
    ex = extract_chapter(_DeadLLM(), 3, "正文", era="")
    assert ex.raw_ok is False
    assert "下线" in ex.error


def test_extract_chapter_non_object_output_has_reason():
    ex = extract_chapter(_ProseLLM(), 3, "正文", era="")
    assert ex.raw_ok is False and "JSON" in ex.error


def _fake_db_connect(chapters):
    cur = mock.MagicMock()
    cur.execute.return_value.fetchall.return_value = chapters
    cm = mock.MagicMock()
    cm.__enter__.return_value = cur
    return mock.MagicMock(return_value=cm)


def _chapters(n):
    return [{"chapter_index": i, "title": f"第{i}章", "content": "正文" * 10, "content_descriptor": ""}
            for i in range(1, n + 1)]


def test_arc_pipeline_all_arcs_failed_returns_not_ok_with_reason():
    from extract import arc_pipeline
    seed = SimpleNamespace(era="", power_system=[], entity_vocab=[])
    dead = ChapterExtract(chapter=1, raw_ok=False, error="当前模型不可用:已被服务商下线或不存在")
    with mock.patch("platform_app.db.connect", _fake_db_connect(_chapters(10))), \
         mock.patch.object(arc_pipeline, "build_seed", return_value=seed), \
         mock.patch.object(arc_pipeline, "extract_arc", return_value=dead) as m_arc:
        res = arc_pipeline.run_arc_extraction(12, 7, user_id=1, model="old-model", api_id="relay",
                                              target_arcs=2, concurrency=2)
    assert res["ok"] is False
    assert "下线" in res["error"] and "提取模型 relay/old-model" in res["error"]
    assert "下线" in res["first_error"]
    # raw_ok=False 不重试:调用次数 = 弧数,模型下线时不会被多撞几轮
    assert m_arc.call_count == len(arc_pipeline.split_arcs(_chapters(10), target_arcs=2))


def test_per_chapter_pipeline_all_failed_returns_not_ok_with_reason():
    from extract import pipeline
    seed = SimpleNamespace(era="", power_system=[], entity_vocab=[])
    dead = ChapterExtract(chapter=1, raw_ok=False, error="当前模型不可用:已被服务商下线或不存在")
    with mock.patch("platform_app.db.connect", _fake_db_connect(_chapters(3))), \
         mock.patch.object(pipeline, "build_seed", return_value=seed), \
         mock.patch.object(pipeline, "extract_chapter", return_value=dead):
        res = pipeline.run_extraction(12, 7, user_id=1, model="old-model", api_id="relay", concurrency=2)
    assert res["ok"] is False
    assert "下线" in res["error"] and "提取模型 relay/old-model" in res["error"]


def test_stage_canon_extract_records_reason_on_ctl():
    from platform_app.import_pipeline import stages_core
    from platform_app.import_pipeline.control import JobController

    class _Ctl(JobController):
        def update(self, **fields):
            pass

    ctl = _Ctl("job-canon")
    cur = mock.MagicMock()
    cur.execute.return_value.fetchone.return_value = {"book_id": 7}
    cm = mock.MagicMock()
    cm.__enter__.return_value = cur
    arc_res = {"ok": False, "error": "全部弧段 LLM 提取失败:当前模型不可用:已被服务商下线(提取模型 relay/m)"}
    with mock.patch.object(stages_core, "connect", mock.MagicMock(return_value=cm)), \
         mock.patch.object(stages_core, "_resolve_extractor_llm", return_value=("relay", "m")), \
         mock.patch.object(stages_core, "_count_canon_and_anchors", return_value=(0, 0)), \
         mock.patch("extract.arc_pipeline.run_arc_extraction", return_value=arc_res):
        out = stages_core._stage_canon_extract(ctl, 1, 12)
    assert out[2] == "error"
    assert "下线" in ctl.stage_error_hints["canon_extract"]


def test_editor_selection_extract_reports_reason():
    from tools_dsl.command_tools_script_write import extract as tool
    dead = ChapterExtract(chapter=0, raw_ok=False, error="当前模型不可用:已被服务商下线或不存在")
    cur = mock.MagicMock()
    cur.execute.return_value.fetchall.return_value = []
    cm = mock.MagicMock()
    cm.__enter__.return_value = cur
    with mock.patch("platform_app.db.connect", mock.MagicMock(return_value=cm)), \
         mock.patch("platform_app.db.init_db"), \
         mock.patch.object(tool, "_user_can_read_script", return_value=True), \
         mock.patch("agents._harness.resolve_api_and_model", return_value=("relay", "m")), \
         mock.patch("extract.per_chapter.extract_chapter", return_value=dead):
        out = tool._t_extract_from_selection(1, 12, {"text": "一段选中的正文"}, None)
    assert "下线" in out, out


# ── 解析失败 ≠ 服务商报错(审查返修)──────────────────────────────────────
# parse_json 的异常文本里夹着模型输出的前 200 字(小说正文)。以前它和服务商异常一起进了
# provider_errors 分类器:正文里有「Forbidden Forest」→「请求被提供商拒绝(HTTP 403)」,
# 有「timed out」→「连不上接口地址」,有「insufficient balance」→ 余额不足。
import pytest  # noqa: E402

from extract.llm import ExtractLLM, ExtractOutputUnparseable, parse_json  # noqa: E402
from extract.per_chapter import UNUSABLE_OUTPUT_REASON  # noqa: E402


def _llm_answering(text: str) -> ExtractLLM:
    """走真实 complete_json → parse_json 路径,只把「模型回了什么」换掉。"""
    llm = ExtractLLM(model="m", api_id="relay")
    llm.complete_text = lambda *a, **k: text  # type: ignore[method-assign]
    return llm


@pytest.mark.parametrize("prose", [
    "Harry walked into the Forbidden Forest at dusk.",
    "他说代理 proxy 连接 timed out 了,然后转身离开。",
    "The merchant had insufficient balance to pay the knight.",
    "The model said nothing; the knight reached the end of life.",
    "",
])
def test_unparseable_output_is_not_classified_as_provider_error(prose):
    ex = extract_chapter(_llm_answering(prose), 3, "正文", era="")
    assert ex.raw_ok is False
    assert ex.error == UNUSABLE_OUTPUT_REASON
    for wrong in ("403", "连不上", "余额", "下线", "Error"):
        assert wrong not in ex.error, (prose, ex.error)


def test_parse_json_raises_dedicated_subclass_still_a_value_error():
    with pytest.raises(ExtractOutputUnparseable):
        parse_json("没有 JSON")
    with pytest.raises(ValueError):  # 既有 `except ValueError` 调用方照样接得住
        parse_json("")


def test_editor_selection_parse_failure_keeps_actionable_hint():
    from tools_dsl.command_tools_script_write import extract as tool
    cur = mock.MagicMock()
    cur.execute.return_value.fetchall.return_value = []
    cm = mock.MagicMock()
    cm.__enter__.return_value = cur
    with mock.patch("platform_app.db.connect", mock.MagicMock(return_value=cm)), \
         mock.patch("platform_app.db.init_db"), \
         mock.patch.object(tool, "_user_can_read_script", return_value=True), \
         mock.patch("agents._harness.resolve_api_and_model", return_value=("relay", "m")), \
         mock.patch.object(ExtractLLM, "complete_text", return_value="Into the Forbidden Forest."):
        out = tool._t_extract_from_selection(1, 12, {"text": "一段选中的正文"}, None)
    assert "缩短选区" in out and "403" not in out, out


# ── 本地异常(写库 / 消歧)不进服务商分类器 ─────────────────────────────────
def test_arc_pipeline_local_exception_is_not_called_model_unavailable():
    from extract import arc_pipeline
    seed = SimpleNamespace(era="", power_system=[], entity_vocab=[])
    boom = RuntimeError('relation "kb_canon_entities" does not exist')
    with mock.patch("platform_app.db.connect", _fake_db_connect(_chapters(4))), \
         mock.patch.object(arc_pipeline, "build_seed", return_value=seed), \
         mock.patch.object(arc_pipeline, "extract_arc", side_effect=boom), \
         mock.patch("time.sleep"):
        res = arc_pipeline.run_arc_extraction(12, 7, user_id=1, model="m", api_id="relay",
                                              target_arcs=1, concurrency=1)
    assert res["ok"] is False
    assert "RuntimeError" in res["error"] and "不可用" not in res["error"]


def test_per_chapter_pipeline_failed_chapters_with_none_index_do_not_crash():
    from extract import pipeline
    seed = SimpleNamespace(era="", power_system=[], entity_vocab=[])
    chapters = _chapters(2)
    chapters[0]["chapter_index"] = None  # 章号缺值时和 int 混比,min() 以前会 TypeError
    dead = ChapterExtract(chapter=1, raw_ok=False, error="当前模型不可用:已被服务商下线或不存在")
    with mock.patch("platform_app.db.connect", _fake_db_connect(chapters)), \
         mock.patch.object(pipeline, "build_seed", return_value=seed), \
         mock.patch.object(pipeline, "extract_chapter", return_value=dead):
        res = pipeline.run_extraction(12, 7, user_id=1, model="m", api_id="relay", concurrency=1)
    assert res["ok"] is False and "下线" in res["error"]


def test_stage_canon_extract_local_exception_is_not_classified():
    from platform_app.import_pipeline import stages_core
    from platform_app.import_pipeline.control import JobController

    class _Ctl(JobController):
        def update(self, **fields):
            pass

    ctl = _Ctl("job-canon-local")
    cur = mock.MagicMock()
    cur.execute.return_value.fetchone.return_value = {"book_id": 7}
    cm = mock.MagicMock()
    cm.__enter__.return_value = cur
    with mock.patch.object(stages_core, "connect", mock.MagicMock(return_value=cm)), \
         mock.patch.object(stages_core, "_resolve_extractor_llm", return_value=("relay", "m")), \
         mock.patch("extract.arc_pipeline.run_arc_extraction",
                    side_effect=RuntimeError('relation "kb_canon_entities" does not exist')):
        out = stages_core._stage_canon_extract(ctl, 1, 12)
    assert out[2] == "error"
    hint = ctl.stage_error_hints["canon_extract"]
    assert "中途出错" in hint and "不可用" not in hint and "提取模型" not in hint
