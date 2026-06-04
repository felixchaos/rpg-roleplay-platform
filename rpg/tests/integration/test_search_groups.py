"""
test_search_groups.py — /api/search 新分组集成测试

覆盖：
- worldbook 分组：worldbook_entries 命中 title / content
- memories 分组：memories 表命中 content
- npc_cards 分组：character_cards (card_type='npc') 命中 name / identity
- scope=worldbook 只返回 worldbook 分组，不含其它
- scope=memories 只返回 memories 分组
- scope=npc_cards 只返回 npc_cards 分组
- 无结果时 groups 列表为空而非报错
- 未登录访问返回 401/403
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


# ---------------------------------------------------------------------------
#  Shared fixture: one user, one book, one script
# ---------------------------------------------------------------------------
class _SearchBase(unittest.TestCase):
    """建一个 owner + book + script，子类在上面插自己的数据。"""

    UNIQUE_TOKEN = "xyzUniqueSearchToken42"  # 搜索用的唯一关键词，避免干扰其它数据

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        u = register_user(cls.client)
        cls.cookies = u["cookies"]
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select id from users where username=%s", (u["username"],)
            ).fetchone()
            cls.owner_id = int(row["id"])
            row = db.execute(
                "insert into books(owner_id, slug, title) values (%s, %s, %s) returning id",
                (cls.owner_id, f"integtest_srch_book_{cls.owner_id}", "integtest_srch_book"),
            ).fetchone()
            cls.book_id = int(row["id"])
            row = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (cls.owner_id, "integtest_srch_script"),
            ).fetchone()
            cls.script_id = int(row["id"])

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _get(self, q: str, scope: str = "all") -> dict:
        resp = self.client.get(
            "/api/search",
            params={"q": q, "scope": scope},
            cookies=self.cookies,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["ok"], body)
        return body

    def _groups_by_kind(self, body: dict) -> dict:
        return {g["kind"]: g["items"] for g in body["groups"]}


# ---------------------------------------------------------------------------
#  worldbook 分组
# ---------------------------------------------------------------------------
class WorldbookSearchGroup(_SearchBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                """
                insert into worldbook_entries(book_id, script_id, title, content, priority)
                values (%s, %s, %s, %s, 80)
                """,
                (
                    cls.book_id,
                    cls.script_id,
                    f"WB_Title_{cls.UNIQUE_TOKEN}",
                    f"Some world content mentioning {cls.UNIQUE_TOKEN}.",
                ),
            )

    def test_worldbook_found_by_title(self):
        body = self._get(self.UNIQUE_TOKEN)
        groups = self._groups_by_kind(body)
        self.assertIn("worldbook", groups, f"worldbook 分组未出现: {body}")
        labels = [it["label"] for it in groups["worldbook"]]
        self.assertTrue(
            any(self.UNIQUE_TOKEN in lbl for lbl in labels),
            f"worldbook 结果未含唯一 token: {labels}",
        )

    def test_worldbook_item_has_expected_fields(self):
        body = self._get(self.UNIQUE_TOKEN)
        groups = self._groups_by_kind(body)
        self.assertIn("worldbook", groups)
        item = groups["worldbook"][0]
        self.assertIn("id", item)
        self.assertIn("label", item)
        self.assertIn("sub", item)   # snippet

    def test_scope_worldbook_only_returns_worldbook(self):
        body = self._get(self.UNIQUE_TOKEN, scope="worldbook")
        self.assertEqual(body["scope"], "worldbook")
        kinds = {g["kind"] for g in body["groups"]}
        self.assertIn("worldbook", kinds)
        # 其他分组不应出现
        for other in ("scripts", "saves", "cards", "memories", "npc_cards"):
            self.assertNotIn(other, kinds, f"scope=worldbook 不应返回 {other}")


# ---------------------------------------------------------------------------
#  memories 分组
# ---------------------------------------------------------------------------
class MemoriesSearchGroup(_SearchBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                """
                insert into memories(user_id, bucket, content, importance)
                values (%s, 'facts', %s, 80)
                """,
                (
                    cls.owner_id,
                    f"A fact containing {cls.UNIQUE_TOKEN} for memory search.",
                ),
            )

    def test_memories_found(self):
        body = self._get(self.UNIQUE_TOKEN)
        groups = self._groups_by_kind(body)
        self.assertIn("memories", groups, f"memories 分组未出现: {body}")
        subs = [it["sub"] for it in groups["memories"]]
        self.assertTrue(
            any(self.UNIQUE_TOKEN in (s or "") for s in subs),
            f"memories 结果未含唯一 token: {subs}",
        )

    def test_memories_item_has_expected_fields(self):
        body = self._get(self.UNIQUE_TOKEN)
        groups = self._groups_by_kind(body)
        self.assertIn("memories", groups)
        item = groups["memories"][0]
        self.assertIn("id", item)
        self.assertIn("label", item)   # bucket 名
        self.assertIn("sub", item)     # content snippet

    def test_scope_memories_only(self):
        body = self._get(self.UNIQUE_TOKEN, scope="memories")
        self.assertEqual(body["scope"], "memories")
        kinds = {g["kind"] for g in body["groups"]}
        self.assertIn("memories", kinds)
        for other in ("scripts", "saves", "cards", "worldbook", "npc_cards"):
            self.assertNotIn(other, kinds, f"scope=memories 不应返回 {other}")


# ---------------------------------------------------------------------------
#  npc_cards 分组
# ---------------------------------------------------------------------------
class NpcCardsSearchGroup(_SearchBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from psycopg.types.json import Jsonb

        from platform_app.db import connect
        with connect() as db:
            db.execute(
                """
                insert into character_cards(
                    book_id, script_id, name, aliases, identity, personality,
                    sample_dialogue, priority, enabled, card_type
                ) values (%s, %s, %s, %s, %s, %s, %s, 50, true, 'npc')
                """,
                (
                    cls.book_id,
                    cls.script_id,
                    f"NPC_{cls.UNIQUE_TOKEN}",
                    Jsonb([]),
                    f"A mysterious identity {cls.UNIQUE_TOKEN}",
                    "taciturn",
                    Jsonb([]),
                ),
            )

    def test_npc_card_found_by_name(self):
        body = self._get(self.UNIQUE_TOKEN)
        groups = self._groups_by_kind(body)
        self.assertIn("npc_cards", groups, f"npc_cards 分组未出现: {body}")
        labels = [it["label"] for it in groups["npc_cards"]]
        self.assertTrue(
            any(self.UNIQUE_TOKEN in lbl for lbl in labels),
            f"npc_cards 结果未含唯一 token: {labels}",
        )

    def test_npc_card_item_has_expected_fields(self):
        body = self._get(self.UNIQUE_TOKEN)
        groups = self._groups_by_kind(body)
        self.assertIn("npc_cards", groups)
        item = groups["npc_cards"][0]
        self.assertIn("id", item)
        self.assertIn("label", item)

    def test_scope_npc_cards_only(self):
        body = self._get(self.UNIQUE_TOKEN, scope="npc_cards")
        self.assertEqual(body["scope"], "npc_cards")
        kinds = {g["kind"] for g in body["groups"]}
        self.assertIn("npc_cards", kinds)
        for other in ("scripts", "saves", "cards", "worldbook", "memories"):
            self.assertNotIn(other, kinds, f"scope=npc_cards 不应返回 {other}")


# ---------------------------------------------------------------------------
#  边界：无结果 + 未登录
# ---------------------------------------------------------------------------
class SearchEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        u = register_user(cls.client)
        cls.cookies = u["cookies"]

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_no_results_returns_empty_groups(self):
        resp = self.client.get(
            "/api/search",
            params={"q": "zzz_no_such_thing_xyzzy_99999"},
            cookies=self.cookies,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["groups"], [])

    def test_unauthenticated_returns_error(self):
        resp = self.client.get("/api/search", params={"q": "test"})
        self.assertIn(resp.status_code, (400, 401, 403), f"未登录应被拒绝: {resp.status_code}")

    def test_invalid_scope_falls_back_to_all(self):
        resp = self.client.get(
            "/api/search",
            params={"q": "zzz_no_such_thing_xyzzy_99999", "scope": "invalid_scope"},
            cookies=self.cookies,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["scope"], "all")

    def test_response_has_query_field(self):
        resp = self.client.get(
            "/api/search",
            params={"q": "hello"},
            cookies=self.cookies,
        )
        body = resp.json()
        self.assertEqual(body["query"], "hello")


if __name__ == "__main__":
    unittest.main(verbosity=2)
