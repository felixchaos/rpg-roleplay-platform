"""test_model_overlay_rename_db.py — 真库验证:用户改名 / 供应商开关只动自己的数据,且改名扛得住同步。

设置 → 模型页一打开就会自动「拉取远程模型」(replace_synced_models 覆盖语义)。用户给同步来的
模型改了显示名,如果同步把远端名字写回去,改名在下一次打开设置页时就没了 —— 等于没改。
这里用真实 PostgreSQL 跑一遍:改名 → 同步 → 名字还在;远端下线 → 行照常被清掉;
手填模型的显示名不被远端同名模型覆盖;供应商开关只翻用户自己凭据的 enabled。

需要 DATABASE_URL 指向可写的测试库(用户名 integtest_ 前缀,用完删除)。
"""
from __future__ import annotations

import unittest

from tests.helpers import integtest_username


def _db_ok() -> bool:
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            db.execute("select 1").fetchone()
        return True
    except Exception:
        return False


@unittest.skipUnless(_db_ok(), "需要可用的 PostgreSQL(DATABASE_URL)")
class OverlayRenameAndCredentialToggle(unittest.TestCase):
    def setUp(self):
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "insert into users(username, display_name) values (%s, 'integ') returning id",
                (integtest_username(),),
            ).fetchone()
        self.uid = int(row["id"])

    def tearDown(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from users where id = %s", (self.uid,))

    def _rows(self, api_id="deepseek"):
        from platform_app.db import connect
        with connect() as db:
            return {r["model_id"]: dict(r) for r in db.execute(
                "select model_id, display_name, enabled, source from user_model_entries "
                "where user_id = %s and api_id = %s", (self.uid, api_id)).fetchall()}

    def test_rename_survives_resync_and_follows_remote_lifecycle(self):
        from platform_app.user_models import replace_synced_models, set_overlay_model_display_name
        replace_synced_models(self.uid, "deepseek", [
            {"real_name": "ds-chat", "display_name": "ds-chat"},
            {"real_name": "ds-reasoner", "display_name": "ds-reasoner"},
        ])
        self.assertEqual(set_overlay_model_display_name(self.uid, "deepseek", "ds-chat", "主力"), 1)
        # 设置页再打开:自动同步一次
        replace_synced_models(self.uid, "deepseek", [
            {"real_name": "ds-chat", "display_name": "DeepSeek Chat"},
            {"real_name": "ds-reasoner", "display_name": "DeepSeek Reasoner"},
        ])
        rows = self._rows()
        self.assertEqual(rows["ds-chat"]["display_name"], "主力", "同步把用户改的名字冲掉了")
        self.assertEqual(rows["ds-chat"]["source"], "renamed")
        self.assertEqual(rows["ds-reasoner"]["display_name"], "DeepSeek Reasoner")
        # 远端下线 ds-chat:改过名的同步模型照常跟随远端被清掉(它不是手填的)
        replace_synced_models(self.uid, "deepseek", [{"real_name": "ds-reasoner"}])
        self.assertNotIn("ds-chat", self._rows())

    def test_manual_model_display_name_not_overwritten_by_remote(self):
        from platform_app.user_models import replace_synced_models, upsert_manual_model
        upsert_manual_model(self.uid, "deepseek", {"real_name": "ds-pro", "display_name": "我的 Pro"})
        replace_synced_models(self.uid, "deepseek", [{"real_name": "ds-pro", "display_name": "ds-pro"}])
        rows = self._rows()
        self.assertEqual(rows["ds-pro"]["display_name"], "我的 Pro")
        self.assertEqual(rows["ds-pro"]["source"], "manual")

    def test_rename_unknown_model_is_noop(self):
        from platform_app.user_models import set_overlay_model_display_name
        self.assertEqual(set_overlay_model_display_name(self.uid, "deepseek", "nope", "x"), 0)

    def test_credential_toggle_only_flips_enabled(self):
        from platform_app import user_credentials as uc
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                "insert into user_api_credentials(user_id, api_id, encrypted_key, base_url_override, metadata) "
                "values (%s, 'deepseek', %s, 'https://relay.example.test/v1', '{\"proxy\": \"http://127.0.0.1:7890\"}')",
                (self.uid, b"cipher"),
            )
        out = uc.set_credential_enabled(self.uid, "deepseek", False)
        self.assertTrue(out["ok"])
        with connect() as db:
            row = db.execute(
                "select enabled, encrypted_key, base_url_override, metadata from user_api_credentials "
                "where user_id = %s and api_id = 'deepseek'", (self.uid,)).fetchone()
        self.assertFalse(row["enabled"])
        self.assertEqual(bytes(row["encrypted_key"]), b"cipher")
        self.assertEqual(row["base_url_override"], "https://relay.example.test/v1")
        self.assertEqual(row["metadata"].get("proxy"), "http://127.0.0.1:7890")
        with self.assertRaises(ValueError):
            uc.set_credential_enabled(self.uid, "openai", True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
