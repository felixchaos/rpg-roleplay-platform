"""
test_sub_agent_separation.py — B4 验证子代理与主代理分离

覆盖：
- 无 override：子代理复用主 GM 实例（避免重复 init SDK），usage 仍按 kind=sub_agent 记
- 有 override：子代理建独立 GameMaster 实例
- _invalidate_user_cache 同时清掉子代理缓存
- 子代理 usage 写入 token_usage 表，metadata.kind='sub_agent'
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tests.helpers import cleanup_test_users, make_client, register_user


class SubAgentInstanceSeparation(unittest.TestCase):
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

    def _inject_fake_main(self):
        """绕开真实 GameMaster 构造（要连 Vertex/Anthropic 太慢）"""
        import app as ui
        ui._invalidate_user_cache(self.api_user)
        fake_main = MagicMock()
        fake_main.api_id = "vertex_ai"
        fake_main._backend = MagicMock()
        fake_main._backend.model_name = "gemini-3.5-flash"
        fake_main._backend.last_usage = {}
        ui._gm_by_user[ui._user_key(self.api_user)] = fake_main
        ui._state_by_user[ui._user_key(self.api_user)] = MagicMock()
        return ui, fake_main

    def test_no_override_reuses_main_gm(self):
        ui, fake_main = self._inject_fake_main()
        # 清掉 user_preferences 里的 override
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                "delete from user_preferences where user_id = %s",
                (self.api_user["id"],),
            )
        sub = ui._get_sub_gm(self.api_user)
        self.assertIs(sub, fake_main, "无 override 时应复用主 GM 实例")

    def test_with_override_creates_separate_instance(self):
        ui, fake_main = self._inject_fake_main()
        # 写一个 override 到 user_preferences
        from psycopg.types.json import Jsonb

        from platform_app.db import connect
        with connect() as db:
            db.execute(
                """
                insert into user_preferences(user_id, preferences) values (%s, %s)
                on conflict(user_id) do update set preferences = excluded.preferences
                """,
                (
                    self.api_user["id"],
                    Jsonb({"sub_agent_model_override": {"api_id": "anthropic", "model": "claude-haiku-4-5"}}),
                ),
            )
        # patch GameMaster 防止真的实例化
        with patch("app.GameMaster") as MockGM:
            mock_instance = MagicMock()
            mock_instance.api_id = "anthropic"
            mock_instance._backend = MagicMock()
            mock_instance._backend.model_name = "claude-haiku-4-5"
            MockGM.return_value = mock_instance
            sub = ui._get_sub_gm(self.api_user)
            self.assertIsNot(sub, fake_main, "有 override 时应建独立实例")
            self.assertEqual(sub.api_id, "anthropic")

    def test_invalidate_clears_sub_gm(self):
        import app as ui
        ui._gm_by_user[ui._user_key(self.api_user)] = MagicMock()
        ui._sub_gm_by_user[ui._user_key(self.api_user)] = MagicMock()
        ui._state_by_user[ui._user_key(self.api_user)] = MagicMock()
        ui._invalidate_user_cache(self.api_user)
        self.assertNotIn(ui._user_key(self.api_user), ui._sub_gm_by_user)


class SubAgentUsageRecording(unittest.TestCase):
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
        cls.user_id = int(row["id"])

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_record_usage_with_sub_agent_metadata(self):
        from platform_app.db import connect
        from platform_app.usage import record_usage
        row = record_usage(
            user_id=self.user_id,
            save_id=None,
            context_run_id=None,
            api_id="anthropic",
            model_real_name="claude-haiku-4-5",
            usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            metadata={"kind": "sub_agent", "phase": "context_curator"},
        )
        self.assertTrue(row.get("id"))
        with connect() as db:
            r = db.execute(
                "select metadata from token_usage where id = %s",
                (row["id"],),
            ).fetchone()
        md = r["metadata"] or {}
        self.assertEqual(md.get("kind"), "sub_agent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
