"""
test_clarify_threshold.py — task 85 验证 _clarify_threshold helper

覆盖：
- api_user = None / 缺 id    → 0.5
- 数据库无 preferences 行     → 0.5
- preferences 不含相关 key    → 0.5
- 合法 float 值              → 原值返回
- 字符串数字（"0.3"）         → float(value)
- 非法值（"abc"、None）       → 0.5
- 越界值                     → clamp 到 [0.0, 1.0]
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class ClarifyThresholdHelper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        cls.u = register_user(cls.client)
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select id from users where username = %s",
                (cls.u["username"],),
            ).fetchone()
        cls.api_user = {"id": int(row["id"]), "username": cls.u["username"], "role": "user"}

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _clear_prefs(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                "delete from user_preferences where user_id = %s",
                (self.api_user["id"],),
            )

    def _write_pref(self, value):
        from platform_app.db import connect
        from psycopg.types.json import Jsonb
        with connect() as db:
            db.execute(
                """
                insert into user_preferences(user_id, preferences) values (%s, %s)
                on conflict(user_id) do update set preferences = excluded.preferences
                """,
                (
                    self.api_user["id"],
                    Jsonb({"curator.confidence_threshold": value}),
                ),
            )

    def test_none_user_returns_default(self):
        import app as ui
        self.assertEqual(ui._clarify_threshold(None), 0.5)

    def test_user_without_id_returns_default(self):
        import app as ui
        self.assertEqual(ui._clarify_threshold({"username": "x"}), 0.5)

    def test_missing_preference_returns_default(self):
        import app as ui
        self._clear_prefs()
        self.assertEqual(ui._clarify_threshold(self.api_user), 0.5)

    def test_valid_float_returned(self):
        import app as ui
        self._write_pref(0.3)
        self.assertAlmostEqual(ui._clarify_threshold(self.api_user), 0.3)

    def test_valid_zero(self):
        import app as ui
        self._write_pref(0.0)
        self.assertEqual(ui._clarify_threshold(self.api_user), 0.0)

    def test_valid_one(self):
        import app as ui
        self._write_pref(1.0)
        self.assertEqual(ui._clarify_threshold(self.api_user), 1.0)

    def test_numeric_string_parsed(self):
        import app as ui
        self._write_pref("0.75")
        self.assertAlmostEqual(ui._clarify_threshold(self.api_user), 0.75)

    def test_garbage_string_falls_back_to_default(self):
        import app as ui
        self._write_pref("not-a-number")
        self.assertEqual(ui._clarify_threshold(self.api_user), 0.5)

    def test_null_value_falls_back_to_default(self):
        import app as ui
        self._write_pref(None)
        self.assertEqual(ui._clarify_threshold(self.api_user), 0.5)

    def test_negative_clamped_to_zero(self):
        import app as ui
        self._write_pref(-0.2)
        self.assertEqual(ui._clarify_threshold(self.api_user), 0.0)

    def test_above_one_clamped(self):
        import app as ui
        self._write_pref(1.5)
        self.assertEqual(ui._clarify_threshold(self.api_user), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
