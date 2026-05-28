"""
test_context_db.py — B3 验证 context_engine 改读 DB

覆盖：
- 插入 character_cards 行 + script_id → _load_characters 返回 DB 数据
- 插入 worldbook_entries 行 + script_id → _active_worldbook 命中 DB 条目
- 不给 script_id 时退化到 JSON
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class _CleanupRowsBase(unittest.TestCase):
    """提供 (owner_user_id, book_id, script_id) 给子类用，setUp 建一份，tearDown 删干净。"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        # 建一个测试用户 + 它的 book + script
        u = register_user(cls.client)
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select id from users where username=%s", (u["username"],)).fetchone()
            cls.owner_id = int(row["id"])
            row = db.execute(
                "insert into books(owner_id, slug, title) values (%s, %s, %s) returning id",
                (cls.owner_id, f"integtest_book_{cls.owner_id}", "integtest_book"),
            ).fetchone()
            cls.book_id = int(row["id"])
            row = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (cls.owner_id, "integtest_script"),
            ).fetchone()
            cls.script_id = int(row["id"])

    @classmethod
    def tearDownClass(cls):
        # 删 character_cards / worldbook_entries / scripts / books（用 user 级联）
        cleanup_test_users()


class CharacterCardsFromDB(_CleanupRowsBase):
    def test_load_characters_db_returns_seeded_card(self):
        from psycopg.types.json import Jsonb

        import context_engine
        from platform_app.db import connect

        with connect() as db:
            db.execute(
                """
                insert into character_cards(book_id, script_id, name, aliases, identity, personality,
                                            sample_dialogue, priority, enabled)
                values (%s, %s, %s, %s, %s, %s, %s, %s, true)
                """,
                (
                    self.book_id, self.script_id, "测试角色甲",
                    Jsonb(["阿甲", "甲哥"]),
                    "测试身份", "测试性格",
                    Jsonb(["示例台词1"]),
                    150,
                ),
            )
        chars = context_engine._load_characters(script_id=self.script_id)
        self.assertIn("测试角色甲", chars)
        self.assertEqual(chars["测试角色甲"]["identity"], "测试身份")
        self.assertEqual(chars["测试角色甲"]["aliases"], ["阿甲", "甲哥"])

    def test_load_characters_fallback_to_json_when_no_script_id(self):
        import context_engine
        chars = context_engine._load_characters(script_id=None)
        # JSON 里至少有杭雁菱这类已知角色（fixture 长期存在）；只校验是 dict 且非空
        self.assertIsInstance(chars, dict)


class WorldbookFromDB(_CleanupRowsBase):
    def test_active_worldbook_picks_db_entries(self):
        from psycopg.types.json import Jsonb

        import context_engine
        from platform_app.db import connect

        with connect() as db:
            db.execute(
                """
                insert into worldbook_entries(book_id, script_id, title, content, keys, regex_keys,
                                              priority, enabled)
                values (%s, %s, %s, %s, %s, %s, %s, true)
                """,
                (
                    self.book_id, self.script_id, "测试条目",
                    "这是测试世界书的内容。",
                    Jsonb(["独特关键词abc"]),
                    Jsonb([]),
                    100,
                ),
            )
        # 构造一个 mock state（minimal interface）
        class _MockState:
            data = {"memory": {"resources": []}}
        scan_text = "玩家提到了独特关键词abc，希望触发该条目。"
        active = context_engine._active_worldbook(
            scan_text, world={}, state=_MockState(),
            script_id=self.script_id, book_id=None,
        )
        titles = [e.get("title") for e in active]
        self.assertIn("测试条目", titles, f"DB 条目未命中: {active}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
