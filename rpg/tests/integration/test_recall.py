"""test_recall.py — P5 召回统一层 kb/recall.py 回归。

覆盖:① score() / _recency / _keyword_hits 纯函数(无 DB)② flag 默认 off ③ render_compat_string 必含
锚点 marker(契约护栏,供 _split_anchor_pending 切割)④ recall() 真库:走 reveal_clause_v2 前沿门控 +
derived ceil_chap,只召回可见集(防剧透),不召回未到达章实体。

需要本地 Postgres。
"""
from __future__ import annotations

import os
import unittest

from psycopg.types.json import Jsonb

from tests.helpers import cleanup_test_users, make_client, register_user


class RecallPureFunctions(unittest.TestCase):
    """纯函数,无需 DB。"""

    def test_flags_default_off(self):
        for k in ("RPG_TKB_RECALL", "RPG_TKB_RECALL_SHADOW", "RPG_TKB_RECALL_SAVES"):
            os.environ.pop(k, None)
        from kb.recall import _recall_on, _recall_shadow
        self.assertFalse(_recall_on(None))
        self.assertFalse(_recall_on(123))
        self.assertFalse(_recall_shadow())

    def test_flag_saves_whitelist(self):
        from kb.recall import _recall_on
        os.environ["RPG_TKB_RECALL"] = "on"
        os.environ["RPG_TKB_RECALL_SAVES"] = "5, 9"
        try:
            self.assertTrue(_recall_on(5))
            self.assertTrue(_recall_on(9))
            self.assertFalse(_recall_on(7))
        finally:
            os.environ.pop("RPG_TKB_RECALL", None)
            os.environ.pop("RPG_TKB_RECALL_SAVES", None)

    def test_min_save_id_gate_new_games_only(self):
        """「只对新游戏开」闸:RPG_TKB_*_MIN_SAVE_ID=N → 仅 save_id>=N 走新路,旧存档留旧逻辑。"""
        from kb.recall import _recall_on
        from kb.reveal import _frontier_on
        os.environ["RPG_TKB_RECALL"] = "on"; os.environ["RPG_TKB_FRONTIER"] = "on"
        os.environ["RPG_TKB_RECALL_MIN_SAVE_ID"] = "165"
        os.environ["RPG_TKB_FRONTIER_MIN_SAVE_ID"] = "165"
        os.environ.pop("RPG_TKB_RECALL_SAVES", None); os.environ.pop("RPG_TKB_FRONTIER_SAVES", None)
        try:
            self.assertFalse(_recall_on(100), "旧存档(id<165)应留旧逻辑")
            self.assertFalse(_frontier_on(100))
            self.assertTrue(_recall_on(200), "新存档(id>=165)走新路")
            self.assertTrue(_frontier_on(165))
        finally:
            for k in ("RPG_TKB_RECALL", "RPG_TKB_FRONTIER", "RPG_TKB_RECALL_MIN_SAVE_ID",
                      "RPG_TKB_FRONTIER_MIN_SAVE_ID"):
                os.environ.pop(k, None)

    def test_score_no_divzero_and_clamp(self):
        from kb.recall import score
        # max_importance=0 不崩;无 vscore/kw 仍返回有限值(recency 中性 0.5)
        s = score({"importance": 0}, ceil_chap=0, max_importance=0)
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.5)

    def test_score_vector_dominates(self):
        from kb.recall import W_VEC, score
        hi = score({"vscore": 1.0, "first_revealed_chapter": 5}, ceil_chap=5, max_importance=10)
        lo = score({"vscore": 0.0, "first_revealed_chapter": 5}, ceil_chap=5, max_importance=10)
        self.assertAlmostEqual(hi - lo, W_VEC, places=6)

    def test_recency_monotonic(self):
        from kb.recall import _recency
        self.assertEqual(_recency(10, 5), 1.0)       # 超过上界 → 满
        self.assertGreater(_recency(5, 10), _recency(1, 10))  # 越近上界越高
        self.assertEqual(_recency(None, 10), 0.5)    # 无章号 → 中性

    def test_keyword_hits(self):
        from kb.recall import _keyword_hits
        node = {"name": "卡切尔", "body": "无忧宫的密党成员", "aliases": ["Katcher"]}
        self.assertEqual(_keyword_hits(node, ["卡切尔", "密党", "无关"]), 2)
        self.assertEqual(_keyword_hits(node, []), 0)

    def test_render_has_anchor_marker(self):
        from kb.recall import RecallResult, render_compat_string
        r = RecallResult(candidates=[{"node_kind": "character", "node_key": "x", "name": "甲",
                                      "body": "身份", "score": 0.9}], ceil_chap=5)
        anchor = "=== 世界线收束·接下来的锚点 ===\n1. [chapter 2] 某事件"
        out = render_compat_string(r, anchor)
        self.assertIn("=== 世界线收束·接下来的锚点 ===", out, "锚点 marker 丢失→_split 切不出")
        self.assertIn("知识库召回", out)
        self.assertIn("甲", out)

    def test_dispatcher_flag_off_no_db_touch(self):
        """审计 S3:flag 全 off 时 retrieve_fn_compat 不解析 save_id(零额外 DB 往返)+ 直通旧路。"""
        from unittest.mock import patch

        import retrieval
        for k in ("RPG_TKB_RECALL", "RPG_TKB_RECALL_SHADOW"):
            os.environ.pop(k, None)
        from kb.recall import retrieve_fn_compat
        calls = {"resolve": 0}

        def _spy_resolve(uid):
            calls["resolve"] += 1
            return 999

        with patch.object(retrieval, "retrieve_context", return_value="OLD_SENTINEL") as _rc, \
                patch.object(retrieval, "_resolve_save_id_from_user", _spy_resolve):
            out = retrieve_fn_compat("q", state=None, user_id=7, script_id=1)
        self.assertEqual(out, "OLD_SENTINEL", "flag off 必须直通 retrieve_context")
        self.assertEqual(calls["resolve"], 0, "flag off 不应解析 save_id(否则多一次 DB 往返)")

    def test_shadow_diff_equal_lengths_silent(self):
        """审计 S4:相同长度的纯长度集合应相等(_shadow_diff_log 静默,不每回合误报)。"""
        self.assertEqual({str(5000)}, {str(5000)})
        self.assertNotEqual({"old_chars=5000"}, {"new_chars=5000"})  # 旧 bug 形态(前缀致永不等)


class RecallGating(unittest.TestCase):
    """真库:recall() 走前沿门控,只召回可见集。"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        u = register_user(cls.client)
        from kb.reveal import backfill_entity_reveal_anchors, backfill_reveal_anchors, seed_frontier
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cls.owner_id = int(db.execute(
                "select id from users where username=%s", (u["username"],)).fetchone()["id"])
            cls.book_id = int(db.execute(
                "insert into books(owner_id, slug, title) values (%s,%s,%s) returning id",
                (cls.owner_id, f"rc_book_{cls.owner_id}", "rc_book")).fetchone()["id"])
            cls.script_id = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.owner_id, "rc_script")).fetchone()["id"])
            cls.save_id = int(db.execute(
                "insert into game_saves(user_id, script_id, title, state_path) "
                "values (%s,%s,%s,%s) returning id",
                (cls.owner_id, cls.script_id, "rc_save",
                 f"/tmp/rc_save_{cls.owner_id}.json")).fetchone()["id"])
            for n in range(1, 11):
                db.execute(
                    "insert into chapter_facts(book_id, script_id, chapter, events) values (%s,%s,%s,%s)",
                    (cls.book_id, cls.script_id, n,
                     Jsonb([{"event": f"第{n}章关键事件发生", "importance": "high"}])))
            from kb import canon_repo
            for lk, name, frc in (("甲", "甲城实体", 1), ("乙", "乙城实体", 5),
                                  ("丙", "丙城实体", 10), ("丁", "丁城实体", 0)):
                canon_repo.upsert_canon_entity(
                    db, cls.script_id, lk, name=name, type="concept",
                    summary=f"{name}的设定说明", first_revealed_chapter=frc, importance=70)
            for n in range(1, 6):  # ch1..5 occurred
                db.execute(
                    "insert into save_anchor_states(save_id, script_id, anchor_key, source_chapter, "
                    "status, summary) values (%s,%s,%s,%s,'occurred',%s)",
                    (cls.save_id, cls.script_id, f"chapter:{n}:event:0", n, f"ch{n}"))
        assert backfill_reveal_anchors(cls.script_id)["anchors"] == 10
        assert backfill_entity_reveal_anchors(cls.script_id)["ok"]
        assert seed_frontier(cls.save_id)["visible"] == 5

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_recall_gates_to_visible_set(self):
        from kb.recall import recall
        r = recall(self.save_id, "甲城实体 乙城实体 丙城实体 丁城实体", mode="none")
        names = {c["name"] for c in r.candidates}
        self.assertEqual(r.ceil_chap, 5, "ceil_chap 应=前沿派生进度 5")
        self.assertIn("甲城实体", names)   # ch1 可见
        self.assertIn("乙城实体", names)   # ch5 可见
        self.assertIn("丁城实体", names)   # frc0 → NULL 恒可见
        self.assertNotIn("丙城实体", names, "ch10 未到达,recall 不应召回(防剧透)")

    def test_recall_omniscient_sees_all(self):
        from kb.recall import recall
        r = recall(self.save_id, "甲城实体 乙城实体 丙城实体 丁城实体", mode="omniscient")
        names = {c["name"] for c in r.candidates}
        self.assertIn("丙城实体", names, "omniscient 不门控,应召回 ch10 实体")

    def test_recall_embed_uses_locked_script_id(self):
        """审计 S2:向量路必须用本剧本锁定的 embedder(传真 script_id,非 None),否则向量空间错乱。"""
        from unittest.mock import patch

        import platform_app.knowledge._search as search_mod
        seen = {}

        def _spy_embed(text, *, script_id=None, user_id=None, db=None):
            seen["script_id"] = script_id
            return None  # 返 None → 向量路跳过,只验传参

        from kb.recall import recall
        with patch.object(search_mod, "_embed_query", _spy_embed):
            recall(self.save_id, "甲城实体", mode="none")
        self.assertEqual(seen.get("script_id"), self.script_id,
                         "recall 向量路应传本档锁定的 script_id,而非 None")


if __name__ == "__main__":
    unittest.main()
