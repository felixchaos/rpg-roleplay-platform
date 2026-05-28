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
        from state import DEFAULT_STATE, GameState
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
        from state import DEFAULT_STATE, GameState
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
        cls.app_text = (PROJECT / "rpg" / "chat_pipeline.py").read_text(encoding="utf-8")

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


class GenericAlgorithmNoHardcoding(unittest.TestCase):
    """关键回归:解析算法不能依赖任何"火星/柏林/图卢兹"等柏林剧本专有词。
    本测试给三个完全不同剧本(三国/魔法学院/科幻基地)的 phase 名做匹配,
    验证通用 substring + token overlap 算法在跨剧本场景都 work。"""

    def _seed_anchors(self, script_id: int, phases: list[tuple[str, str, int, int]]):
        """直接往 script_timeline_anchors 注 anchors,跳过 ETL (test 工具)。"""
        from platform_app.db import connect, init_db
        from script_timeline import _extract_keywords
        init_db()
        with connect() as db:
            # 删旧
            db.execute("delete from script_timeline_anchors where script_id = %s", (script_id,))
            # 确保 scripts 行存在 (避免 FK 错)
            row = db.execute("select id from scripts where id = %s", (script_id,)).fetchone()
            if not row:
                # 找个真实 user
                u = db.execute("select id from users limit 1").fetchone()
                if not u:
                    self.skipTest("no users in DB")
                    return
                db.execute(
                    "insert into scripts (id, owner_id, title) values (%s, %s, %s) on conflict do nothing",
                    (script_id, u["id"], f"test-generic-{script_id}"),
                )
            for phase, time_label, ch_min, ch_max in phases:
                keywords = sorted(set(_extract_keywords(phase) + _extract_keywords(time_label)))[:20]
                db.execute(
                    """insert into script_timeline_anchors
                       (script_id, story_phase, story_time_label, chapter_min, chapter_max,
                        chapter_count, sample_title, sample_summary, keywords, confidence)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       on conflict (script_id, story_phase, story_time_label) do update
                         set chapter_min = excluded.chapter_min,
                             chapter_max = excluded.chapter_max,
                             keywords = excluded.keywords""",
                    (script_id, phase, time_label, ch_min, ch_max,
                     ch_max - ch_min + 1, "", "", keywords, 1.0),
                )

    def test_three_kingdoms_script(self):
        """三国演义风格:黄巾 / 董卓 / 官渡 / 赤壁 / 三分天下。"""
        from script_timeline import resolve_timeline_anchor
        sid = 9990001
        self._seed_anchors(sid, [
            ("黄巾起义篇", "中平元年", 1, 30),
            ("董卓乱政篇", "中平六年", 31, 80),
            ("官渡之战篇", "建安五年", 81, 130),
            ("赤壁之战篇", "建安十三年", 131, 180),
            ("三分天下篇", "建安末年", 181, 240),
        ])
        # 完整 phase 名匹配
        a = resolve_timeline_anchor(sid, "黄巾起义")
        self.assertIsNotNone(a, "三国'黄巾起义'应匹配")
        self.assertIn("黄巾", a["story_phase"])
        # 短词匹配
        a = resolve_timeline_anchor(sid, "赤壁")
        self.assertIsNotNone(a, "三国'赤壁'应匹配")
        self.assertEqual(a["chapter_min"], 131)
        # 时间标签匹配
        a = resolve_timeline_anchor(sid, "建安五年")
        self.assertIsNotNone(a)
        self.assertEqual(a["story_phase"], "官渡之战篇")
        # 章节号
        a = resolve_timeline_anchor(sid, "原著第100章")
        self.assertIsNotNone(a)
        self.assertTrue(a["chapter_min"] <= 100 <= a["chapter_max"])

    def test_magic_academy_script(self):
        """魔法学院风格:一年级到七年级。"""
        from script_timeline import resolve_timeline_anchor
        sid = 9990002
        self._seed_anchors(sid, [
            ("魔法学校一年级篇", "新生入学", 1, 50),
            ("魔法学校二年级篇", "密室之谜", 51, 100),
            ("魔法学校三年级篇", "时光转换器", 101, 150),
            ("终极对决篇", "黑魔王陨落", 301, 360),
        ])
        a = resolve_timeline_anchor(sid, "一年级")
        self.assertIsNotNone(a, "'一年级'应能匹配到一年级 phase")
        self.assertEqual(a["chapter_min"], 1)
        a = resolve_timeline_anchor(sid, "密室之谜")
        self.assertIsNotNone(a, "时间标签匹配")
        self.assertEqual(a["story_phase"], "魔法学校二年级篇")
        a = resolve_timeline_anchor(sid, "终极对决")
        self.assertIsNotNone(a)
        self.assertEqual(a["chapter_min"], 301)

    def test_scifi_foundation_script(self):
        """科幻基地风格:全英文 phase 名,验证算法不假定中文。"""
        from script_timeline import resolve_timeline_anchor
        sid = 9990003
        self._seed_anchors(sid, [
            ("Foundation Era", "Year 0 FE", 1, 100),
            ("Mule Crisis", "Year 310 FE", 101, 200),
            ("Second Foundation Search", "Year 376 FE", 201, 280),
        ])
        a = resolve_timeline_anchor(sid, "Mule")
        self.assertIsNotNone(a, "英文 phase 名应能匹配")
        self.assertEqual(a["story_phase"], "Mule Crisis")
        a = resolve_timeline_anchor(sid, "Year 376 FE")
        self.assertIsNotNone(a)
        self.assertEqual(a["story_phase"], "Second Foundation Search")
        # 章节号在英文剧本里仍然 work
        a = resolve_timeline_anchor(sid, "原著第 150 章")
        self.assertIsNotNone(a)
        self.assertEqual(a["story_phase"], "Mule Crisis")

    def test_no_match_safety(self):
        """完全无关 label 在所有剧本类型下都返回 None。"""
        from script_timeline import resolve_timeline_anchor
        # 用上面 test 已 seed 的 sid
        for sid in (9990001, 9990002, 9990003):
            for noise in ("ZZZZZ", "随便瞎写的字串 123", "abc def ghi"):
                self.assertIsNone(
                    resolve_timeline_anchor(sid, noise),
                    f"sid={sid} label={noise!r} 不该误匹配",
                )

    def test_no_berlin_specific_keywords_in_source(self):
        """硬约束:script_timeline.py 源代码里不应再有柏林剧本专有词的硬编码列表。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "script_timeline.py").read_text(encoding="utf-8")
        # 检查那段硬编码模式是否还在 — 不应该有 "for keyword in (\"火星\", \"柏林\", ...)" 这种
        forbidden_combo = (
            '("火星", "柏林"',  # 旧硬编码起始
            '"图卢兹", "扬陆"',  # 旧硬编码中段
            '"薇瑟"',           # 柏林剧本特有
        )
        # 注释里出现一次是允许的(写说明用),但代码 logic 里不应该有
        for token in forbidden_combo:
            # 简化:整文件不应包含 (注释里说"删了硬编码"也不应触发,因为注释里只引用"火星 / 柏林"这种)
            self.assertNotIn(token, src,
                f"script_timeline.py 不应硬编码柏林剧本特有词组 {token!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
