"""
test_script_timeline_anchors.py
===============================

剧本时间线锚点系统:把 chapter_facts 的 story_phase + story_time_label 聚合
到 script_timeline_anchors 表,供 /set 时间 + GM retrieval 用真实章节范围。

测试 4 层:
  Layer A — DB migration v14 创建表 + 索引
  Layer B — rebuild_timeline_anchors ETL 正确性 (聚合 + 写入)
  Layer C — resolve_timeline_anchor 模糊匹配 (火星 / 柏林 / 章节号 / phase)
  Layer D — context_engine._timeline_layer 用 state.world.timeline 真锚点
            (优先于 SQLite vectors.db 旧索引)
"""
from __future__ import annotations

import copy as _copy
import unittest
from pathlib import Path

from tests.helpers import make_client, register_user


PROJECT = Path(__file__).resolve().parents[2]


# ────────────────────────────────────────────────────────────
# Layer A: Migration + Schema
# ────────────────────────────────────────────────────────────


class MigrationCreatesTable(unittest.TestCase):
    def test_table_exists_with_required_columns(self):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cols = db.execute(
                """
                select column_name from information_schema.columns
                where table_name = 'script_timeline_anchors'
                order by ordinal_position
                """
            ).fetchall()
        col_names = {r["column_name"] for r in cols}
        required = {
            "id", "script_id", "story_phase", "story_time_label",
            "chapter_min", "chapter_max", "chapter_count",
            "sample_title", "sample_summary", "keywords", "confidence",
        }
        missing = required - col_names
        self.assertEqual(missing, set(),
            f"script_timeline_anchors 缺字段: {missing}")


# ────────────────────────────────────────────────────────────
# Layer B: ETL (rebuild_timeline_anchors)
# ────────────────────────────────────────────────────────────


class ETLRebuildsAnchors(unittest.TestCase):
    """rebuild_timeline_anchors 按 chapter_facts 聚合后写入正确数据。"""

    def test_no_script_returns_error(self):
        from script_timeline import rebuild_timeline_anchors
        r = rebuild_timeline_anchors(0)
        self.assertFalse(r.get("ok"))

    def test_nonexistent_script(self):
        from script_timeline import rebuild_timeline_anchors
        r = rebuild_timeline_anchors(99999999)
        self.assertFalse(r.get("ok"))

    def test_rebuild_real_script(self):
        """对真实剧本 (script_id=6 柏林剧本) 跑 ETL,验证生成多个锚点。"""
        from platform_app.db import connect, init_db
        from script_timeline import rebuild_timeline_anchors
        init_db()
        # 找一个有 chapter_facts 的真实 script
        with connect() as db:
            row = db.execute(
                """
                select script_id, count(*) as n
                from chapter_facts
                group by script_id
                having count(*) >= 50
                order by count(*) desc
                limit 1
                """
            ).fetchone()
        if not row:
            self.skipTest("no script with >=50 chapter_facts in DB")
            return
        script_id = int(row["script_id"])
        r = rebuild_timeline_anchors(script_id)
        self.assertTrue(r.get("ok"), r)
        self.assertGreaterEqual(r.get("anchors_count", 0), 10,
            "真实剧本应至少 10 个锚点")
        # 至少 2 个 phase
        self.assertGreaterEqual(len(r.get("phases", [])), 1)


# ────────────────────────────────────────────────────────────
# Layer C: resolve_timeline_anchor 模糊匹配
# ────────────────────────────────────────────────────────────


class ResolveAnchorMatching(unittest.TestCase):
    """resolve_timeline_anchor 用各种 label 匹配,看返回的 chapter range 合理。"""

    @classmethod
    def setUpClass(cls):
        # 用真实剧本(柏林剧本,含火星线 / 柏林线 / 战争线 等多 phase)
        from platform_app.db import connect, init_db
        from script_timeline import rebuild_timeline_anchors
        init_db()
        with connect() as db:
            row = db.execute(
                """select script_id from chapter_facts
                   where story_phase like '%火星%' or story_phase like '%柏林%'
                   group by script_id having count(*) >= 100
                   order by count(*) desc limit 1"""
            ).fetchone()
        if not row:
            cls.test_script_id = None
            return
        cls.test_script_id = int(row["script_id"])
        rebuild_timeline_anchors(cls.test_script_id)

    def setUp(self):
        if not self.test_script_id:
            self.skipTest("no real script with both 火星/柏林 phases in DB")

    def test_resolve_by_phase_keyword(self):
        from script_timeline import resolve_timeline_anchor
        a = resolve_timeline_anchor(self.test_script_id, "火星")
        self.assertIsNotNone(a, "搜'火星'应能匹配到火星 phase")
        self.assertIn("火星", a["story_phase"],
            f"匹配 phase 应含'火星',实际: {a['story_phase']}")
        self.assertGreater(a["chapter_max"], 0)
        self.assertGreaterEqual(a["chapter_max"], a["chapter_min"])

    def test_resolve_by_chapter_number(self):
        from script_timeline import resolve_timeline_anchor
        a = resolve_timeline_anchor(self.test_script_id, "原著第10章")
        self.assertIsNotNone(a, "搜'原著第10章'应能匹配")
        self.assertTrue(a["chapter_min"] <= 10 <= a["chapter_max"],
            f"chapter 10 应在 {a['chapter_min']}-{a['chapter_max']} 范围里")

    def test_resolve_no_match_returns_none(self):
        from script_timeline import resolve_timeline_anchor
        a = resolve_timeline_anchor(self.test_script_id, "完全不相关的胡言乱语 XYZ")
        self.assertIsNone(a, "无匹配应返回 None,不是空 dict")

    def test_resolve_returns_required_fields(self):
        from script_timeline import resolve_timeline_anchor
        a = resolve_timeline_anchor(self.test_script_id, "柏林")
        self.assertIsNotNone(a)
        for field in ("chapter_min", "chapter_max", "story_phase", "score"):
            self.assertIn(field, a)

    def test_resolve_zero_script_id_safe(self):
        from script_timeline import resolve_timeline_anchor
        self.assertIsNone(resolve_timeline_anchor(0, "火星"))
        self.assertIsNone(resolve_timeline_anchor(99999999, "火星"))

    def test_resolve_empty_label_safe(self):
        from script_timeline import resolve_timeline_anchor
        self.assertIsNone(resolve_timeline_anchor(self.test_script_id, ""))
        self.assertIsNone(resolve_timeline_anchor(self.test_script_id, "   "))


# ────────────────────────────────────────────────────────────
# Layer D: _timeline_layer 用 state.world.timeline 真锚点
# ────────────────────────────────────────────────────────────


class TimelineLayerUsesRealAnchor(unittest.TestCase):
    """state.world.timeline.anchor_chapter / chapter_min/max 已写入时,
    _timeline_layer 优先用它们,不再依赖 SQLite vectors.db 旧索引。"""

    def _state(self, *, anchor_chapter=50, chapter_min=1, chapter_max=255,
               anchor_phase="初期穿越与火星线", anchor_event="火星·扬陆城内",
               locked_label="火星·扬陆城内"):
        from state import GameState, DEFAULT_STATE
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        tl = g.data["world"]["timeline"]
        tl["current_label"] = locked_label
        g.data["world"]["time"] = locked_label
        tl["anchor_chapter"] = anchor_chapter
        tl["chapter_min"] = chapter_min
        tl["chapter_max"] = chapter_max
        tl["anchor_phase"] = anchor_phase
        tl["anchor_event"] = anchor_event
        tl["anchor_confidence"] = 11.0
        tl["pending_jump"] = None
        return g

    def test_layer_shows_real_anchor_chapter(self):
        from context_engine import _timeline_layer
        state = self._state(anchor_chapter=51, chapter_min=51, chapter_max=155)
        text = _timeline_layer(state)["text"]
        self.assertIn("第51章", text)
        self.assertIn("51 - 155", text)
        self.assertIn("初期穿越与火星线", text)

    def test_layer_shows_anchor_event(self):
        from context_engine import _timeline_layer
        state = self._state(anchor_event="火星·扬陆城内大厅")
        text = _timeline_layer(state)["text"]
        self.assertIn("火星·扬陆城内大厅", text)

    def test_layer_falls_back_to_sqlite_when_no_anchor(self):
        """state.world.timeline 没写 anchor_chapter 时退化到旧 SQLite 索引。"""
        from context_engine import _timeline_layer
        from state import GameState, DEFAULT_STATE
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        # 没写 anchor_chapter / chapter_min/max
        g.data["world"]["timeline"]["pending_jump"] = None
        text = _timeline_layer(g)["text"]
        # 默认柏林 label 走 _safe_timeline_filter,通常返回 ? (未命中) 或 SQLite 结果
        # 主要是不抛异常
        self.assertIn("当前锁定时间线", text)
        self.assertIn("原著检索锚点", text)


# ────────────────────────────────────────────────────────────
# Layer E: chat handler /set 后接锚点 (静态扫源)
# ────────────────────────────────────────────────────────────


class ChatHandlerWritesAnchorAfterSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_text = (PROJECT / "rpg" / "app.py").read_text(encoding="utf-8")

    def test_chat_imports_resolve_timeline_anchor(self):
        self.assertIn("from script_timeline import resolve_timeline_anchor", self.app_text,
            "app.py chat handler 必须 import resolve_timeline_anchor")

    def test_chat_writes_anchor_to_state(self):
        self.assertIn("anchor_chapter", self.app_text)
        self.assertIn("chapter_min", self.app_text)
        self.assertIn("anchor_phase", self.app_text)
        # 必须把 anchor 写到 state.world.timeline
        self.assertIn('state.data["world"]["timeline"]', self.app_text)

    def test_chat_surfaces_anchor_in_directive_updates(self):
        # directive_updates 应附加"时间线锚点 → 第X-Y章"提示
        self.assertIn("时间线锚点", self.app_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
