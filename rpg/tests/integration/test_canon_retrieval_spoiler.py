"""test_canon_retrieval_spoiler.py — canon 实体检索 3 大系统性问题的防剧透回归。

覆盖:
  BUG-1  platform_app.knowledge._search._search_entities:
         - 改读 first_revealed_chapter(旧引用从不存在的 first_chapter 列 → 静默返空);
         - 第 1 章玩家不召回后期(ch400)角色/词条 = 防剧透;
         - 进度推进后才召回;
         - NULL 收紧:chapter_max 给定时绝不放行后期实体。
  BUG-2  规范进度过滤(retrieval.py 层级图复用 canon_repo._reveal_clause):
         - read_canon_entities(progress=1) 排除后期 faction,含早期 faction。
  BUG-3  gm_serving.settings.advance_progress 真正推进 progress_chapter(取 max 只增不减)。

需要本地 Postgres(与其它 integration 测试一致)。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from psycopg.types.json import Jsonb

from tests.helpers import cleanup_test_users, make_client, register_user


def _vec768(fill: float = 0.1) -> str:
    return "[" + ",".join(f"{fill:.6f}" for _ in range(768)) + "]"


class _CanonBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        u = register_user(cls.client)
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute("select id from users where username=%s", (u["username"],)).fetchone()
            cls.owner_id = int(row["id"])
            row = db.execute(
                "insert into books(owner_id, slug, title) values (%s,%s,%s) returning id",
                (cls.owner_id, f"canon_spoiler_book_{cls.owner_id}", "canon_spoiler_book"),
            ).fetchone()
            cls.book_id = int(row["id"])
            row = db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.owner_id, "canon_spoiler_script"),
            ).fetchone()
            cls.script_id = int(row["id"])
            # 一个属于该 user 的存档(BUG-3 用)
            row = db.execute(
                "insert into game_saves(user_id, script_id, title, state_path) "
                "values (%s,%s,%s,%s) returning id",
                (cls.owner_id, cls.script_id, "canon_spoiler_save",
                 f"/tmp/canon_spoiler_save_{cls.owner_id}.json"),
            ).fetchone()
            cls.save_id = int(row["id"])

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()


class SearchEntitiesChapterFilter(_CanonBase):
    """BUG-1: _search_entities 按 first_revealed_chapter 硬过滤,防剧透。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from platform_app.db import connect
        vec = _vec768(0.1)
        with connect() as db:
            # 两张 NPC 卡:相同向量(检索得分相同),仅 first_revealed_chapter 不同
            for name, frc in (("早期角色_林有德", 1), ("后期角色_莉莉丝", 400)):
                db.execute(
                    "insert into character_cards "
                    "(script_id, book_id, name, card_type, enabled, first_revealed_chapter, embedding_vec) "
                    "values (%s,%s,%s,'npc',true,%s,%s::vector)",
                    (cls.script_id, cls.book_id, name, frc, vec),
                )
            # 两条世界书:同理
            for title, frc in (("早期设定_火星基地", 1), ("后期设定_柏林暗流", 400)):
                db.execute(
                    "insert into worldbook_entries "
                    "(script_id, book_id, title, content, enabled, first_revealed_chapter, embedding_vec) "
                    "values (%s,%s,%s,%s,true,%s,%s::vector)",
                    (cls.script_id, cls.book_id, title, "设定内容", frc, vec),
                )
            # 另一个零向量剧本(同一属主,不多注册用户):测关键词兜底
            row = db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.owner_id, "canon_spoiler_kw_script"),
            ).fetchone()
            cls.kw_script_id = int(row["id"])
            for name, frc in (("林有德", 1), ("莉莉丝", 400), ("甲", 1)):
                db.execute(
                    "insert into character_cards "
                    "(script_id, book_id, name, card_type, enabled, first_revealed_chapter) "
                    "values (%s,%s,%s,'npc',true,%s)",
                    (cls.kw_script_id, cls.book_id, name, frc),
                )
            for title, frc in (("火星基地", 1), ("柏林暗流", 400)):
                db.execute(
                    "insert into worldbook_entries "
                    "(script_id, book_id, title, content, enabled, first_revealed_chapter) "
                    "values (%s,%s,%s,%s,true,%s)",
                    (cls.kw_script_id, cls.book_id, title, "设定内容", frc),
                )

    def _call(self, chapter_max):
        from platform_app.knowledge import _search as search_mod
        vec = _vec768(0.1)
        with patch.object(search_mod, "_embed_query", return_value=vec):
            from platform_app.db import connect
            with connect() as db:
                return search_mod._search_entities(
                    db, self.script_id, "林有德 莉莉丝 火星 柏林",
                    chapter_max=chapter_max, top_k_cards=8, top_k_wb=8,
                )

    def test_chapter1_player_excludes_late_entities(self):
        out = self._call(chapter_max=1)
        card_names = {c["name"] for c in out["cards"]}
        wb_titles = {w["title"] for w in out["worldbook"]}
        self.assertIn("早期角色_林有德", card_names)
        self.assertNotIn("后期角色_莉莉丝", card_names, "第1章玩家被召回 ch400 角色 = 剧透")
        self.assertIn("早期设定_火星基地", wb_titles)
        self.assertNotIn("后期设定_柏林暗流", wb_titles, "第1章玩家被召回 ch400 世界书 = 剧透")

    def test_progressed_player_recalls_late_entities(self):
        out = self._call(chapter_max=400)
        card_names = {c["name"] for c in out["cards"]}
        wb_titles = {w["title"] for w in out["worldbook"]}
        self.assertIn("后期角色_莉莉丝", card_names)
        self.assertIn("后期设定_柏林暗流", wb_titles)

    def test_admin_view_none_chapter_returns_all(self):
        # chapter_max=None 仅管理/编辑器视角(线上回合一定带 progress 钳定的天花板)
        out = self._call(chapter_max=None)
        card_names = {c["name"] for c in out["cards"]}
        self.assertIn("后期角色_莉莉丝", card_names)

    # ── 零向量剧本的关键词兜底:不嵌入查询,按「玩家原句里提到了这个名字」召回,仍过进度闸 ──
    def _call_kw(self, chapter_max):
        from platform_app.knowledge import _search as search_mod
        calls = []

        def _spy(*a, **k):
            calls.append(a)
            return None

        with patch.object(search_mod, "_embed_query", _spy):
            from platform_app.db import connect
            with connect() as db:
                out = search_mod._search_entities(
                    db, self.kw_script_id, "我去火星基地找林有德问问莉莉丝和柏林暗流的事",
                    chapter_max=chapter_max, top_k_cards=8, top_k_wb=8,
                )
        self.assertEqual(calls, [], "零向量剧本不应嵌入查询")
        return {c["name"] for c in out["cards"]}, {w["title"] for w in out["worldbook"]}

    def test_zero_vector_script_sentence_mentions_within_progress(self):
        cards, wb = self._call_kw(chapter_max=1)
        self.assertEqual(cards, {"林有德"}, "单字名不算提到;ch400 角色不能召回")
        self.assertEqual(wb, {"火星基地"})

    def test_zero_vector_script_progressed_player_sees_late_mentions(self):
        cards, wb = self._call_kw(chapter_max=400)
        self.assertEqual(cards, {"林有德", "莉莉丝"})
        self.assertEqual(wb, {"火星基地", "柏林暗流"})


class CanonEntityRevealFilter(_CanonBase):
    """BUG-2: 规范层进度过滤(层级图复用)——后期 faction 不进早章玩家上下文。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from kb import canon_repo
        from platform_app.db import connect
        with connect() as db:
            canon_repo.upsert_canon_entity(
                db, cls.script_id, "faction_early", name="德军", type="faction",
                first_revealed_chapter=1, importance=90, entity_subtype="军事势力",
            )
            canon_repo.upsert_canon_entity(
                db, cls.script_id, "faction_late", name="无忧宫密党", type="faction",
                first_revealed_chapter=400, importance=95, entity_subtype="政治势力",
            )

    def test_progress1_excludes_late_faction(self):
        from kb import canon_repo
        from platform_app.db import connect
        with connect() as db:
            rows = canon_repo.read_canon_entities(
                db, self.script_id, progress_chapter=1, mode="none", entity_type="faction",
            )
        names = {r["name"] for r in rows}
        self.assertIn("德军", names)
        self.assertNotIn("无忧宫密党", names, "progress=1 召回 ch400 势力 = 层级图剧透")

    def test_progress500_includes_late_faction(self):
        from kb import canon_repo
        from platform_app.db import connect
        with connect() as db:
            rows = canon_repo.read_canon_entities(
                db, self.script_id, progress_chapter=500, mode="none", entity_type="faction",
            )
        names = {r["name"] for r in rows}
        self.assertIn("无忧宫密党", names)


class WorldbookVisibilityFilter(_CanonBase):
    """群反馈(行者无疆):剧本设定里【关闭】的世界书条目照样被向量召回进 GM 记忆。

    向量召回是唯一没过「enabled」闸的世界书读路径(常驻/激活/关键词层都过),
    且关闭不脏化向量 → 条目关了向量还在,一召一个准。retirement(本档停用)
    是同一语义的存档级孪生,一并守住。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from platform_app.db import connect
        vec = _vec768(0.1)
        with connect() as db:
            for title, enabled in (("启用设定_食材与天财地宝", True),
                                   ("关闭设定_主神原材料", False),
                                   ("本档停用设定_光柱轮回", True)):
                row = db.execute(
                    "insert into worldbook_entries "
                    "(script_id, book_id, title, content, enabled, first_revealed_chapter, embedding_vec) "
                    "values (%s,%s,%s,%s,%s,1,%s::vector) returning id",
                    (cls.script_id, cls.book_id, title, "设定内容", enabled, vec),
                ).fetchone()
                if title.startswith("本档停用"):
                    cls.retired_id = int(row["id"])
            db.execute(
                "insert into save_worldbook_overlays "
                "(save_id, kind, retired_entry_id, retired_reason, introduced_turn) "
                "values (%s,'retirement',%s,'剧情已废止',1)",
                (cls.save_id, cls.retired_id),
            )

    def _call(self, *, save_id=None, embedded=True):
        from platform_app.knowledge import _search as search_mod
        vec = _vec768(0.1) if embedded else None
        with patch.object(search_mod, "_embed_query", return_value=vec):
            from platform_app.db import connect
            with connect() as db:
                out = search_mod._search_entities(
                    db, self.script_id, "启用设定_食材与天财地宝 关闭设定_主神原材料 本档停用设定_光柱轮回",
                    chapter_max=None, top_k_cards=8, top_k_wb=8, save_id=save_id,
                )
        return {w["title"] for w in out["worldbook"]}

    def test_disabled_entry_not_recalled(self):
        titles = self._call(save_id=self.save_id)
        self.assertIn("启用设定_食材与天财地宝", titles)
        self.assertNotIn("关闭设定_主神原材料", titles, "剧本里已关闭的世界书仍被向量召回")

    def test_retired_entry_not_recalled(self):
        titles = self._call(save_id=self.save_id)
        self.assertNotIn("本档停用设定_光柱轮回", titles, "本档已 retire 的世界书仍被向量召回")

    def test_retirement_is_save_scoped(self):
        # 没有 save 上下文(管理/编辑器视角)时只过 enabled;retirement 是存档级语义,不该外溢。
        titles = self._call(save_id=None)
        self.assertIn("本档停用设定_光柱轮回", titles)
        self.assertNotIn("关闭设定_主神原材料", titles)

    def test_ilike_fallback_filters_and_returns_rows(self):
        # 无向量兜底路径:旧代码参数序错位(patterns 排在 script_id 前)→ 恒抛恒空,
        # 顺带修正;修好之后同样必须过 enabled / retirement 闸。
        titles = self._call(save_id=self.save_id, embedded=False)
        self.assertIn("启用设定_食材与天财地宝", titles, "ILIKE 兜底恒返空(参数序错位)")
        self.assertNotIn("关闭设定_主神原材料", titles)
        self.assertNotIn("本档停用设定_光柱轮回", titles)


class AdvanceProgress(_CanonBase):
    """BUG-3: advance_progress 真正推进 progress_chapter(取 max 只增不减)。"""

    def _read_progress(self):
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select worldline from game_sessions where save_id=%s", (self.save_id,)
            ).fetchone()
        wl = (row or {}).get("worldline") if row else None
        return (wl or {}).get("progress_chapter") if isinstance(wl, dict) else None

    def test_advance_is_monotonic_max(self):
        from gm_serving.settings import advance_progress
        from platform_app.db import connect
        with connect() as db:
            advance_progress(db, self.save_id, 50)
        self.assertEqual(self._read_progress(), 50)
        with connect() as db:
            advance_progress(db, self.save_id, 30)  # 倒退请求被忽略
        self.assertEqual(self._read_progress(), 50)
        with connect() as db:
            advance_progress(db, self.save_id, 120)
        self.assertEqual(self._read_progress(), 120)


if __name__ == "__main__":
    unittest.main()
