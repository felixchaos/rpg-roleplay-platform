from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, random_suffix


class ScriptCharacterCardsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        username = f"integtest_{random_suffix()}@example.test"
        from platform_app.auth import _issue_session
        from platform_app.db import connect

        with connect() as db:
            owner = db.execute(
                """
                insert into users(
                  username, display_name, role, email,
                  email_verified, terms_accepted_at, age_confirmed
                )
                values (%s, 'integ', 'user', %s, true, now(), true)
                returning id
                """,
                (username, username),
            ).fetchone()
            cls.owner_id = int(owner["id"])
            cls.cookies = {"rpg_session": _issue_session(db, cls.owner_id)}
            script = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (cls.owner_id, "integtest_card_script"),
            ).fetchone()
            cls.script_id = int(script["id"])
            book = db.execute(
                "insert into books(owner_id, script_id, slug, title) values (%s, %s, %s, %s) returning id",
                (
                    cls.owner_id, cls.script_id,
                    f"integtest_card_book_{cls.owner_id}", "integtest_card_book",
                ),
            ).fetchone()
            cls.book_id = int(book["id"])

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_create_same_npc_name_updates_existing_card(self):
        first = self.client.post(
            f"/api/scripts/{self.script_id}/character-cards",
            cookies=self.cookies,
            json={"name": "露露", "identity": "初始身份", "importance": 20},
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_body = first.json()
        self.assertTrue(first_body["ok"], first_body)
        card_id = first_body["card"]["id"]

        second = self.client.post(
            f"/api/scripts/{self.script_id}/character-cards",
            cookies=self.cookies,
            json={"name": "露露", "identity": "更新后的身份", "importance": 80},
        )
        self.assertEqual(second.status_code, 200, second.text)
        second_body = second.json()
        self.assertTrue(second_body["ok"], second_body)
        self.assertEqual(second_body["card"]["id"], card_id)
        self.assertEqual(second_body["card"]["identity"], "更新后的身份")
        self.assertEqual(second_body["card"]["importance"], 80)

        from platform_app.db import connect

        with connect() as db:
            row = db.execute(
                """
                select count(*) as c from character_cards
                where script_id=%s and name=%s and card_type='npc'
                """,
                (self.script_id, "露露"),
            ).fetchone()
        self.assertEqual(int(row["c"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
