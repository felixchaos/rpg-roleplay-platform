"""tests/unit/test_world_key_backfill.py — world_key 模型批次 3a 单测。

设计 docs/design/world_key_model_v1.md §2/§3/§4/§7(3a 行)。
纯函数层(classify_segments)全覆盖;IO 层(backfill_worldlines)只用 monkeypatch
假 rows 测 dry-run 一条,不连真 DB(遵批次 3a 任务指示)。
"""
from __future__ import annotations

import unittest
from unittest import mock

from extract.world_key_backfill import (
    backfill_worldlines,
    classify_segments,
    clean_in_world_time,
)


def _linear_chapters(n: int, *, with_generic_volumes: bool = False) -> list[dict]:
    """线性书:无卷或卷名平淡(『第一卷』式),标题不命中任何词表。"""
    chapters = []
    for i in range(1, n + 1):
        vol = ""
        if with_generic_volumes:
            vol = f"第{(i - 1) // 15 + 1}卷"
        chapters.append({
            "chapter_index": i,
            "title": f"第{i}章 平凡的一天",
            "volume_title": vol,
            "summary": f"主角在第{i}章度过了平凡的一天。",
        })
    return chapters


def _infinite_flow_chapters() -> list[dict]:
    """无限流样本:5 卷,3 卷命中『副本』关键词,分界应落在卷切换处。"""
    chapters = []
    ci = 1
    volumes = [
        ("第一卷 觉醒", 5),
        ("副本一：医院惊魂", 10),
        ("副本二：孤岛求生", 10),
        ("休整篇", 3),
        ("副本三：镜中世界", 8),
    ]
    for vol, count in volumes:
        for _ in range(count):
            chapters.append({
                "chapter_index": ci,
                "title": f"第{ci}章",
                "volume_title": vol,
                "summary": f"第{ci}章摘要",
            })
            ci += 1
    return chapters


class ClassifySegmentsLinearBook(unittest.TestCase):
    """线性书(无卷、无命中)→ 全 null。"""

    def test_no_volume_no_keyword_all_null(self):
        segs = classify_segments(_linear_chapters(60))
        self.assertTrue(segs)
        for s in segs:
            self.assertIsNone(s["world_label"])
            self.assertEqual(s["verdict"], "continuous")
        self.assertFalse(segs[0]["overcut"])

    def test_generic_volume_titles_still_all_null(self):
        """『第一卷/第二卷』式平淡分卷(无关键词命中)不应被误判 new_world——
        否则任何多卷书都会被结构先验层误切(见 backfill.py _is_new_world_candidate
        的核心论证:段边界本身就是 volume_title 变化处,不能拿"变了"本身当候选)。"""
        segs = classify_segments(_linear_chapters(60, with_generic_volumes=True))
        self.assertTrue(segs)
        for s in segs:
            self.assertIsNone(s["world_label"])
            self.assertEqual(s["verdict"], "continuous")
        self.assertFalse(segs[0]["overcut"])

    def test_fallback_window_is_20_chapters(self):
        segs = classify_segments(_linear_chapters(45))
        self.assertEqual([(s["ch_min"], s["ch_max"]) for s in segs],
                          [(1, 20), (21, 40), (41, 45)])


class ClassifySegmentsInfiniteFlow(unittest.TestCase):
    """无限流样本(5 卷,3 卷命中『副本』)→ ≥2 个 world,分界落在卷切换处。"""

    def test_at_least_two_worlds_detected(self):
        segs = classify_segments(_infinite_flow_chapters())
        distinct = {s["world_label"] for s in segs if s["world_label"]}
        self.assertGreaterEqual(len(distinct), 2)

    def test_boundaries_align_with_volume_switches(self):
        segs = classify_segments(_infinite_flow_chapters())
        # 卷边界:1-5(第一卷) / 6-15(副本一) / 16-25(副本二) / 26-28(休整篇) / 29-36(副本三)
        boundaries = [(s["ch_min"], s["ch_max"]) for s in segs]
        self.assertEqual(boundaries, [(1, 5), (6, 15), (16, 25), (26, 28), (29, 36)])

    def test_dungeon_segments_get_new_world_verdict(self):
        segs = classify_segments(_infinite_flow_chapters())
        by_range = {(s["ch_min"], s["ch_max"]): s for s in segs}
        self.assertEqual(by_range[(6, 15)]["verdict"], "new_world")
        self.assertEqual(by_range[(16, 25)]["verdict"], "new_world")
        self.assertEqual(by_range[(29, 36)]["verdict"], "new_world")
        # 命中关键词的两个不同副本应产生不同 world_label
        self.assertNotEqual(by_range[(6, 15)]["world_label"], by_range[(16, 25)]["world_label"])

    def test_not_overcut(self):
        segs = classify_segments(_infinite_flow_chapters())
        self.assertFalse(segs[0]["overcut"])


class ClassifySegmentsOvercutRollback(unittest.TestCase):
    """过切回退:每段都命中 → 退单世界。"""

    def test_every_segment_hits_keyword_triggers_overcut(self):
        chapters = []
        ci = 1
        for vol in ["副本一", "副本二", "副本三", "副本四", "副本五"]:
            for _ in range(5):
                chapters.append({
                    "chapter_index": ci, "title": f"第{ci}章",
                    "volume_title": vol, "summary": "",
                })
                ci += 1
        segs = classify_segments(chapters)
        self.assertTrue(segs[0]["overcut"])
        for s in segs:
            self.assertIsNone(s["world_label"])
            self.assertEqual(s["verdict"], "continuous")

    def test_overcut_ratio_boundary_not_triggered_below_threshold(self):
        """3/5 = 0.6 < 0.8 阈值,不应触发过切回退(无限流样本本身即此场景的实例,
        这里额外用整数比例边界值再验证一次阈值语义)。"""
        chapters = []
        ci = 1
        for vol in ["副本一", "日常一", "副本二", "日常二", "副本三"]:
            for _ in range(4):
                chapters.append({
                    "chapter_index": ci, "title": f"第{ci}章",
                    "volume_title": vol, "summary": "",
                })
                ci += 1
        segs = classify_segments(chapters)
        self.assertFalse(segs[0]["overcut"])
        distinct = {s["world_label"] for s in segs if s["world_label"]}
        self.assertEqual(len(distinct), 3)


class ClassifySegmentsTimeSkip(unittest.TestCase):
    """time_skip 不应起新 world(结构先验层不产生 time_skip,恒 continuous 沿用上段)。"""

    def test_time_skip_style_label_change_does_not_start_new_world(self):
        # 章标题/摘要含"三年后"等时间跳跃措辞,但卷名/标题不命中 new_world 词表
        # → 结构先验层判 continuous,沿用上一段 world(时间跳跃不是世界切换)。
        chapters = _infinite_flow_chapters()
        # 追加新一卷:卷名换了(不再是『副本三：镜中世界』)但不含任何 new_world 关键词,
        # 标题带"三年后"式时间跳跃措辞 —— 这正是 time_skip 该有的信号(时间变了,世界没变)。
        last_ci = chapters[-1]["chapter_index"]
        chapters.append({
            "chapter_index": last_ci + 1,
            "title": f"第{last_ci + 1}章 三年后",
            "volume_title": "日常生活篇",  # 新卷但不命中词表
            "summary": "三年后,主角回到了这里。",
        })
        segs = classify_segments(chapters)
        last_seg = segs[-1]
        self.assertEqual(last_seg["verdict"], "continuous")
        # 沿用上一段(『副本三：镜中世界』)的 world 标签,而不是 None 或新标签
        prev_seg = segs[-2]
        self.assertEqual(last_seg["world_label"], prev_seg["world_label"])
        self.assertIsNotNone(last_seg["world_label"])


class WorldLabelNormalization(unittest.TestCase):
    def test_truncated_to_24_chars_and_strips_punctuation(self):
        # 混入足够多不命中的普通卷,避免触发过切回退(§3 第三层),纯粹验证 label 归一化。
        long_vol = "序幕篇：一段很长很长很长很长很长很长很长很长的卷名标题!!!"
        chapters = [
            {"chapter_index": 1, "title": "第1章", "volume_title": long_vol, "summary": ""},
            {"chapter_index": 2, "title": "第2章", "volume_title": long_vol, "summary": ""},
        ]
        ci = 3
        for _ in range(8):  # 8 段平淡续卷,拉低 distinct-world/段数 比例到过切阈值以下
            for _ in range(5):
                chapters.append({"chapter_index": ci, "title": f"第{ci}章", "volume_title": f"卷{ci}", "summary": ""})
                ci += 1
        segs = classify_segments(chapters)
        self.assertFalse(segs[0]["overcut"], "本测试不应触发过切回退,否则不是在测归一化")
        label = segs[0]["world_label"]
        self.assertIsNotNone(label)
        self.assertLessEqual(len(label), 24)
        self.assertNotIn("！", label)
        self.assertNotIn("!", label)
        self.assertNotIn("：", label)

    def test_bracket_tag_change_is_a_candidate_signal(self):
        # 【】编号变化(不含关键词)作为候选信号;混入足够多不命中的普通章节
        # 避免触发过切回退,纯粹验证括号编号信号本身。
        chapters = []
        ci = 1
        for tag in ("Alpha", "Alpha", "Beta"):
            for _ in range(20):
                chapters.append({
                    "chapter_index": ci, "title": f"【{tag}】第{ci}节",
                    "volume_title": "", "summary": "",
                })
                ci += 1
        for _ in range(40):  # 稀释比例,避免过切
            chapters.append({"chapter_index": ci, "title": f"第{ci}章", "volume_title": "", "summary": ""})
            ci += 1

        segs = classify_segments(chapters)
        self.assertFalse(segs[0]["overcut"])
        by_range = {(s["ch_min"], s["ch_max"]): s for s in segs}
        # 第一段(Alpha)首段无 prev,不算候选;第二段仍是 Alpha(未变)→ continuous;
        # 第三段 Beta(变了)→ new_world。
        self.assertEqual(by_range[(1, 20)]["verdict"], "continuous")
        self.assertEqual(by_range[(21, 40)]["verdict"], "continuous")
        self.assertEqual(by_range[(41, 60)]["verdict"], "new_world")
        self.assertEqual(by_range[(41, 60)]["world_label"], "Beta")


class ClassifySegmentsIdempotent(unittest.TestCase):
    def test_same_input_same_output_twice(self):
        chapters = _infinite_flow_chapters()
        segs1 = classify_segments(chapters)
        segs2 = classify_segments(chapters)
        self.assertEqual(segs1, segs2)

    def test_idempotent_on_linear_book(self):
        chapters = _linear_chapters(45)
        self.assertEqual(classify_segments(chapters), classify_segments(chapters))


class ClassifySegmentsEmptyInput(unittest.TestCase):
    def test_empty_chapters_returns_empty_list(self):
        self.assertEqual(classify_segments([]), [])


class CleanInWorldTime(unittest.TestCase):
    def test_placeholder_label_cleaned_to_empty(self):
        self.assertEqual(clean_in_world_time("第3章"), "")
        self.assertEqual(clean_in_world_time("ch5 节点"), "")

    def test_real_label_passes_through(self):
        self.assertEqual(clean_in_world_time("三年后"), "三年后")

    def test_empty_input(self):
        self.assertEqual(clean_in_world_time(""), "")


class _FakeDB:
    """最小 psycopg 连接替身,支持 dry-run 读路径(script_chapters + chapter_facts)。"""

    def __init__(self, *, script_chapters_rows, chapter_facts_rows):
        self.script_chapters_rows = script_chapters_rows
        self.chapter_facts_rows = chapter_facts_rows
        self.calls: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        norm = " ".join(sql.split())
        return _FakeResult(self, norm)


class _FakeResult:
    def __init__(self, db: _FakeDB, sql: str):
        self._db = db
        self._sql = sql

    def fetchall(self):
        if "from script_chapters" in self._sql:
            return self._db.script_chapters_rows
        if "from chapter_facts" in self._sql:
            return self._db.chapter_facts_rows
        if "from script_timeline_anchors" in self._sql:
            return []
        return []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class BackfillWorldlinesDryRun(unittest.TestCase):
    """IO 层薄封装:只测 dry_run=True 一条路径,monkeypatch 假 rows,不连真 DB。"""

    def test_dry_run_reports_segments_without_writing(self):
        script_chapters_rows = [
            {"chapter_index": i, "title": f"第{i}章", "volume_title": vol}
            for i, vol in (
                [(i, "第一卷 觉醒") for i in range(1, 6)]
                + [(i, "副本一：医院惊魂") for i in range(6, 16)]
                + [(i, "副本二：孤岛求生") for i in range(16, 26)]
            )
        ]
        chapter_facts_rows = [
            {"chapter": i, "title": f"第{i}章", "summary": f"摘要{i}"}
            for i in range(1, 26)
        ]
        fake = _FakeDB(
            script_chapters_rows=script_chapters_rows,
            chapter_facts_rows=chapter_facts_rows,
        )
        with mock.patch("platform_app.db.connect", return_value=fake):
            result = backfill_worldlines(133, dry_run=True)

        self.assertIn("segments", result)
        self.assertFalse(result["overcut"])
        self.assertEqual(result["would_write"], 25)
        # dry-run 不应发出任何 update 语句
        for sql, _params in fake.calls:
            self.assertNotIn("update", sql.lower())
        distinct = {s["world_label"] for s in result["segments"] if s["world_label"]}
        self.assertGreaterEqual(len(distinct), 1)


if __name__ == "__main__":
    unittest.main()


def test_sparse_single_hit_degrades_to_null():
    """生产 dry-run 实证(script 133):无卷名书退化合成窗后词表孤立单命中 → 横跨
    几百章的错误伪世界。保守护栏:仅无卷名结构时,命中<2=信号不足,整书退单世界;
    有卷名结构的单次世界切换(穿越书)不受影响(见既有 volume 系测试)。"""
    chapters = []
    for i in range(1, 101):
        title = "恶魔轮回小队登场" if i == 81 else f"第{i}章 平平无奇"
        chapters.append({"chapter_index": i, "title": title, "volume_title": "", "summary": ""})
    segs = classify_segments(chapters)
    assert all(s["world_label"] is None for s in segs)
    assert any(s.get("sparse_signal") for s in segs)


def test_two_plus_hits_still_segment():
    """无卷名书 ≥2 处命中=真信号,正常切分不受护栏影响。"""
    chapters = []
    for i in range(1, 61):
        if i == 21:
            title = "进入副本:生化危机"
        elif i == 41:
            title = "进入副本:咒怨"
        else:
            title = f"第{i}章 战斗"
        chapters.append({"chapter_index": i, "title": title, "volume_title": "", "summary": ""})
    segs = classify_segments(chapters)
    labels = {s["world_label"] for s in segs if s["world_label"]}
    assert len(labels) >= 2
    assert not any(s.get("sparse_signal") for s in segs)
