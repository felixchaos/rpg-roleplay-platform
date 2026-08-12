"""test_phase0_model_unify — Phase 0 模型层重构 DB 运行时验证

3 个针对 Phase 0 的新用例:
1. v67 别名规范化：存量 api_id='AgentPlatform' 经迁移 SQL 变成 'vertex_ai'
2. resolve 偏好回退：gm.model_real_name 存了不在 catalog 里的模型 → 自动回退到
   first_user_model 的真实模型（替代黑名单职责）
3. 配 key 自动 sync（mock 掉网络）：monkeypatch list_remote_models → set_credential
   自动写入 user_model_entries → load_catalog_for_user overlay 可见
"""
from __future__ import annotations

import os
import random
import string
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# 让 import 能找到顶层模块
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")


def _random_suffix(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _make_test_user(db) -> int:
    """插入一个最简 users 行，返回 id。"""
    uname = f"phase0test_{_random_suffix()}"
    row = db.execute(
        """
        insert into users(username, display_name, password_hash, email)
        values (%s, %s, 'x', %s)
        returning id
        """,
        (uname, uname, f"{uname}@example.test"),
    ).fetchone()
    return int(row["id"])


class TestV67AliasMigration(unittest.TestCase):
    """用例 1：v67 别名规范化迁移 SQL 正确性。

    策略：
    - 直接 SQL 插入 api_id='AgentPlatform' 的存量行（绕过 set_credential）
    - 跑 v67 的两条 UPDATE 语句（幂等，对已是 canonical 的行无影响）
    - 断言结果变为 'vertex_ai'
    同理验证 user_model_entries。
    """

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect

    def setUp(self):
        from platform_app.db import connect
        with connect() as db:
            self._uid = _make_test_user(db)

    def tearDown(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from users where id = %s", (self._uid,))

    def test_v67_normalizes_AgentPlatform_in_credentials(self):
        """user_api_credentials: api_id='AgentPlatform' → 'vertex_ai' 经迁移 SQL。"""
        from platform_app.db import connect
        # 直接插入存量别名行（绕过 set_credential 的规范化）
        with connect() as db:
            db.execute(
                """
                insert into user_api_credentials(user_id, api_id, encrypted_key)
                values (%s, 'AgentPlatform', '\\x')
                on conflict do nothing
                """,
                (self._uid,),
            )
            # 验证插入成功
            row = db.execute(
                "select api_id from user_api_credentials where user_id = %s",
                (self._uid,),
            ).fetchone()
        self.assertEqual(row["api_id"], "AgentPlatform", "预期存量行保持原始别名")

        # 运行 v67 的迁移 SQL
        v67_sql_creds = "update user_api_credentials set api_id = 'vertex_ai' where api_id in ('AgentPlatform', 'agent_platform', 'vertex')"
        with connect() as db:
            db.execute(v67_sql_creds)
            row = db.execute(
                "select api_id from user_api_credentials where user_id = %s",
                (self._uid,),
            ).fetchone()
        self.assertEqual(row["api_id"], "vertex_ai", "v67 应将 AgentPlatform 规范化为 vertex_ai")

    def test_v67_normalizes_AgentPlatform_in_model_entries(self):
        """user_model_entries: api_id='AgentPlatform' → 'vertex_ai' 经迁移 SQL。"""
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                """
                insert into user_model_entries(user_id, api_id, model_id, real_name, display_name)
                values (%s, 'AgentPlatform', 'gemini-test', 'gemini-test', 'Gemini Test')
                on conflict do nothing
                """,
                (self._uid,),
            )
            row = db.execute(
                "select api_id from user_model_entries where user_id = %s and model_id = 'gemini-test'",
                (self._uid,),
            ).fetchone()
        self.assertEqual(row["api_id"], "AgentPlatform", "预期存量行保持原始别名")

        v67_sql_entries = "update user_model_entries set api_id = 'vertex_ai' where api_id in ('AgentPlatform', 'agent_platform', 'vertex')"
        with connect() as db:
            db.execute(v67_sql_entries)
            row = db.execute(
                "select api_id from user_model_entries where user_id = %s and model_id = 'gemini-test'",
                (self._uid,),
            ).fetchone()
        self.assertEqual(row["api_id"], "vertex_ai", "v67 应将 AgentPlatform 规范化为 vertex_ai")

    def test_v67_is_idempotent_on_canonical(self):
        """v67 UPDATE 对已是 canonical 'vertex_ai' 的行无影响（幂等）。"""
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                """
                insert into user_api_credentials(user_id, api_id, encrypted_key)
                values (%s, 'vertex_ai', '\\x')
                on conflict do nothing
                """,
                (self._uid,),
            )
        v67_sql = "update user_api_credentials set api_id = 'vertex_ai' where api_id in ('AgentPlatform', 'agent_platform', 'vertex')"
        with connect() as db:
            db.execute(v67_sql)
            row = db.execute(
                "select api_id from user_api_credentials where user_id = %s",
                (self._uid,),
            ).fetchone()
        self.assertEqual(row["api_id"], "vertex_ai", "幂等：canonical 行不被误改")


class TestResolvePrefFallback(unittest.TestCase):
    """用例 2：resolve_preferred_model 偏好 catalog 存在性校验回退。

    构造:
    - 给 user 写 user_preferences: gm.model_real_name = 'nonexistent-xyz-9999'
    - 给 user 配置真实 provider 凭证（openai，用加密空 key 模拟）
    - 往 user_model_entries 写一个真实模型
    - 断言 resolve_preferred_model(user_id, 'gm.model_real_name') 回退到
      该真实模型而非 'nonexistent-xyz-9999'
    """

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect

    def setUp(self):
        from psycopg.types.json import Jsonb

        from platform_app.db import connect
        with connect() as db:
            self._uid = _make_test_user(db)
            # 写 user_preferences: gm.model_real_name = 不存在的模型
            db.execute(
                """
                insert into user_preferences(user_id, preferences)
                values (%s, %s)
                on conflict (user_id) do update set preferences = excluded.preferences
                """,
                (self._uid, Jsonb({"gm.model_real_name": "nonexistent-xyz-9999"})),
            )
            # 给 user 配一个 openai 凭证（empty key，模拟有凭证）
            db.execute(
                """
                insert into user_api_credentials(user_id, api_id, encrypted_key, enabled)
                values (%s, 'openai', '\\x01', true)
                on conflict do nothing
                """,
                (self._uid,),
            )
            # 往 user_model_entries 写一个真实可用模型
            db.execute(
                """
                insert into user_model_entries(user_id, api_id, model_id, real_name, display_name, enabled)
                values (%s, 'openai', 'gpt-4o-mini', 'gpt-4o-mini', 'GPT-4o mini', true)
                on conflict do nothing
                """,
                (self._uid,),
            )

    def tearDown(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from users where id = %s", (self._uid,))

    def test_resolve_preferred_model_falls_back_when_model_not_in_catalog(self):
        """偏好的模型不在 catalog 里 → resolve_preferred_model 回退到 first_user_model。"""
        from core.llm_backend import resolve_preferred_model

        result = resolve_preferred_model(self._uid, pref_key="gm.model_real_name")
        # 'nonexistent-xyz-9999' 不在 catalog → 应回退到 first_user_model 的真实模型
        # 而非返回 'nonexistent-xyz-9999'
        self.assertIsNotNone(result, "应有回退结果，不应返回 None")
        self.assertNotEqual(
            result, "nonexistent-xyz-9999",
            "回退后不应返回不在 catalog 里的偏好模型名"
        )

    def test_first_user_model_returns_real_model(self):
        """first_user_model 应返回 user_model_entries 里真实存在的模型。"""
        from core.llm_backend import first_user_model

        result = first_user_model(self._uid)
        self.assertIsNotNone(result, "用户有凭证和 model_entries，应能找到第一个模型")
        api_id, real_name = result
        self.assertEqual(api_id, "openai")
        self.assertEqual(real_name, "gpt-4o-mini")


class TestSetCredentialAutoSync(unittest.TestCase):
    """用例 3：配 key 自动 sync（mock 掉网络）。

    构造：
    - monkeypatch model_probe.list_remote_models 返回 fake-img-1（含 image_gen 能力）
    - 调 set_credential(user_id, 'openai', 'fake-key')
    - 断言 user_model_entries 有该模型写入
    - 断言 load_catalog_for_user overlay 里能看到它，且带 image_gen 能力
    """

    @classmethod
    def setUpClass(cls):
        # set_credential 需要加密，生产模式下要求 RPG_MASTER_KEY
        # 提供测试用的 32 字节 hex master key
        os.environ.setdefault("RPG_MASTER_KEY", "aa" * 32)
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect

    def setUp(self):
        from platform_app.db import connect
        with connect() as db:
            self._uid = _make_test_user(db)

    def tearDown(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from users where id = %s", (self._uid,))

    def test_set_credential_auto_syncs_models(self):
        """set_credential 配 key 后自动 sync 写入 user_model_entries + overlay 可见。"""
        fake_models = [
            {
                "id": "fake-img-1",
                "real_name": "fake-img-1",
                "display_name": "Fake Image Model",
                "capabilities": ["image_gen"],
            }
        ]
        fake_sync_result = {"ok": True, "models": fake_models}

        # set_credential 内部用 lazy import: from model_probe import list_remote_models
        # 所以要 patch model_probe 模块上的函数
        with patch("model_probe.list_remote_models", return_value=fake_sync_result):
            from platform_app.user_credentials import set_credential
            result = set_credential(self._uid, "openai", "fake-key-for-test")

        self.assertTrue(result.get("ok"), f"set_credential 应成功: {result}")

        # 验证 user_model_entries 写入了该模型
        from platform_app.db import connect
        with connect() as db:
            rows = db.execute(
                "select model_id, real_name, capabilities from user_model_entries "
                "where user_id = %s and api_id = 'openai'",
                (self._uid,),
            ).fetchall()
        model_ids = [r["model_id"] for r in rows]
        self.assertIn("fake-img-1", model_ids, "fake-img-1 应已写入 user_model_entries")

        # 验证 capabilities 包含 image_gen
        entry = next(r for r in rows if r["model_id"] == "fake-img-1")
        caps = list(entry.get("capabilities") or [])
        self.assertIn("image_gen", caps, "fake-img-1 应带 image_gen 能力")

    def test_overlay_visible_in_catalog_for_user(self):
        """同步写入后 load_catalog_for_user 的 overlay 里能看到该模型。"""
        fake_models = [
            {
                "id": "fake-img-2",
                "real_name": "fake-img-2",
                "display_name": "Fake Image Model 2",
                "capabilities": ["image_gen", "text"],
            }
        ]
        fake_sync_result = {"ok": True, "models": fake_models}

        with patch("model_probe.list_remote_models", return_value=fake_sync_result):
            from platform_app.user_credentials import set_credential
            set_credential(self._uid, "openai", "fake-key-for-overlay-test")

        from model_registry import load_catalog_for_user
        catalog = load_catalog_for_user(self._uid)

        # 找 openai provider 的 models 列表
        openai_api = next(
            (a for a in catalog.get("apis", []) if a.get("id") == "openai"),
            None,
        )
        self.assertIsNotNone(openai_api, "catalog 应包含 openai provider")
        model_ids = [m.get("real_name") or m.get("id") for m in (openai_api.get("models") or [])]
        self.assertIn(
            "fake-img-2", model_ids,
            f"fake-img-2 应出现在 load_catalog_for_user overlay 里; 当前 openai models: {model_ids}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
