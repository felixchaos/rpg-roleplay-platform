"""OSS issue #22 回归：换/删 APIKey 后游戏控制台模型列表不刷新。

根因：per-user 模型 overlay(user_model_entries)不随凭据变化清理 —
  · delete_credential 删了 key 却不清 overlay → 旧 key 同步来的模型残留；
  · set_credential 换 key 后若新 key 的 /models 列不出(失败/空)，旧 overlay 留着。

这里用真实 DB 验证两条修复路径 + happy-path 不回归。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("RPG_DEPLOYMENT_MODE", "local")  # 本地模式 → 加密走文件 fallback


class TestCredentialOverlaySync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                """
                insert into users(username, display_name, password_hash, email)
                values (%s, %s, %s, %s)
                returning id
                """,
                ("overlay_sync_uat", "Overlay UAT", "x", "overlay_sync_uat@example.com"),
            ).fetchone()
            cls.uid = int(row["id"])

    @classmethod
    def tearDownClass(cls):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from user_model_entries where user_id = %s", (cls.uid,))
            db.execute("delete from user_api_credentials where user_id = %s", (cls.uid,))
            db.execute("delete from users where id = %s", (cls.uid,))

    def setUp(self):
        # 每个用例前清干净 overlay + 凭据
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from user_model_entries where user_id = %s", (self.uid,))
            db.execute("delete from user_api_credentials where user_id = %s", (self.uid,))

    def _seed_overlay(self):
        """模拟「旧 key 拉取远程模型」写进 overlay 的状态。"""
        from platform_app.user_models import load_overlay, replace_synced_models
        replace_synced_models(self.uid, "deepseek", [
            {"id": "old-model-a", "real_name": "old-model-a", "display_name": "Old A"},
            {"id": "old-model-b", "real_name": "old-model-b", "display_name": "Old B"},
        ])
        overlay = load_overlay(self.uid)
        self.assertIn("deepseek", overlay)
        self.assertEqual(len(overlay["deepseek"]), 2)

    def test_delete_credential_clears_overlay(self):
        from platform_app import user_credentials
        from platform_app.user_models import load_overlay
        self._seed_overlay()
        user_credentials.delete_credential(self.uid, "deepseek")
        overlay = load_overlay(self.uid)
        self.assertNotIn(
            "deepseek", overlay,
            "删 key 后该 provider 的 overlay 应被清空，否则旧模型残留在模型列表",
        )

    def test_set_credential_clears_overlay_when_sync_fails(self):
        """换新 key 但新 key /models 列不出(ok=False)→ 必须清掉旧 overlay。"""
        import model_probe
        from platform_app import user_credentials
        from platform_app.user_models import load_overlay
        self._seed_overlay()
        orig = model_probe.list_remote_models
        model_probe.list_remote_models = lambda *a, **k: {"ok": False, "error": "no /models"}
        try:
            user_credentials.set_credential(self.uid, "deepseek", "sk-new-key-xyz")
        finally:
            model_probe.list_remote_models = orig
        overlay = load_overlay(self.uid)
        self.assertNotIn(
            "deepseek", overlay,
            "换 key 后新 key 列不出模型时，旧 key 的 overlay 应被清空(回退全局菜单)",
        )

    def test_set_credential_replaces_overlay_on_successful_sync(self):
        """happy path 不回归：新 key 能列出模型 → overlay 被新清单替换。"""
        import model_probe
        from platform_app import user_credentials
        from platform_app.user_models import load_overlay
        self._seed_overlay()
        orig = model_probe.list_remote_models
        model_probe.list_remote_models = lambda *a, **k: {
            "ok": True,
            "models": [{"id": "new-model-z", "real_name": "new-model-z", "display_name": "New Z"}],
        }
        try:
            user_credentials.set_credential(self.uid, "deepseek", "sk-new-key-xyz")
        finally:
            model_probe.list_remote_models = orig
        overlay = load_overlay(self.uid)
        self.assertIn("deepseek", overlay)
        names = {m.get("real_name") or m.get("id") for m in overlay["deepseek"]}
        self.assertEqual(names, {"new-model-z"}, "应只剩新 key 同步来的模型，旧模型被移除")

    # ── 深审新发现的两层(此前 10 次修复从未覆盖)─────────────────────────────
    def test_set_credential_forces_refresh_on_autosync(self):
        """换 key 自动同步必须 force_refresh=True。

        否则会命中改 key 前「校验连接/拉取模型」刚写满的旧 key 60s 缓存,
        把旧 key 的模型写进 overlay → 「换 key 后列表不刷新」(issue #22 后端根因)。
        """
        import model_probe
        from platform_app import user_credentials
        captured = {}
        orig = model_probe.list_remote_models

        def _spy(api_id, *a, **k):
            captured["force_refresh"] = k.get("force_refresh")
            return {"ok": True, "models": [{"id": "m1", "real_name": "m1"}]}

        model_probe.list_remote_models = _spy
        try:
            user_credentials.set_credential(self.uid, "deepseek", "sk-new-key-xyz")
        finally:
            model_probe.list_remote_models = orig
        self.assertIs(
            captured.get("force_refresh"), True,
            "set_credential 自动同步必须强制刷新,绝不能读旧 key 的缓存",
        )

    def test_set_credential_invalidates_stale_list_cache(self):
        """换 key 后旧 key 的 _LIST_CACHE 条目必须被清,否则 /api/models/remote 60s 内仍返旧。"""
        import model_probe
        from platform_app import user_credentials
        ck = f"{self.uid}::deepseek"
        model_probe._LIST_CACHE[ck] = (9e18, [{"id": "old", "real_name": "old"}])
        orig = model_probe.list_remote_models
        model_probe.list_remote_models = lambda *a, **k: {
            "ok": True, "models": [{"id": "new", "real_name": "new"}],
        }
        try:
            user_credentials.set_credential(self.uid, "deepseek", "sk-new-key-xyz")
        finally:
            model_probe.list_remote_models = orig
        self.assertNotIn(
            ck, model_probe._LIST_CACHE,
            "换 key 后旧 key 的远程模型缓存必须被失效",
        )

    def test_delete_credential_invalidates_list_cache(self):
        """删 key 后 _LIST_CACHE 条目必须被清,否则删后「拉取远程模型」仍返已删 key 清单。"""
        import model_probe
        from platform_app import user_credentials
        ck = f"{self.uid}::deepseek"
        model_probe._LIST_CACHE[ck] = (9e18, [{"id": "old", "real_name": "old"}])
        user_credentials.delete_credential(self.uid, "deepseek")
        self.assertNotIn(ck, model_probe._LIST_CACHE, "删 key 后远程模型缓存必须被失效")

    def test_disabled_resave_keeps_renamed_overlay(self):
        """模型页关掉供应商后在「编辑供应商」里重填 key(前端带 enabled:false):停用的凭据拉不到
        模型,不能因此走「新 key 列不出 → 清空」把用户改过名的模型整个清掉。"""
        import model_probe
        from platform_app import user_credentials
        from platform_app.user_models import load_overlay, set_overlay_model_display_name
        self._seed_overlay()
        self.assertEqual(set_overlay_model_display_name(self.uid, "deepseek", "old-model-a", "主力"), 1)
        orig = model_probe.list_remote_models
        # 与真实行为一致:拉取入口跳过停用的凭据 → 失败
        model_probe.list_remote_models = lambda *a, **k: {"ok": False, "error": "需要先配置", "models": []}
        try:
            user_credentials.set_credential(self.uid, "deepseek", "sk-new-key-xyz", enabled=False)
        finally:
            model_probe.list_remote_models = orig
        overlay = load_overlay(self.uid)
        self.assertIn("deepseek", overlay, "停用态重填 key 把用户的模型清单清空了")
        names = {(m.get("real_name") or m.get("id")): m.get("display_name") for m in overlay["deepseek"]}
        self.assertEqual(names.get("old-model-a"), "主力", "改过的显示名丢了")
        self.assertIn("old-model-b", names)


if __name__ == "__main__":
    unittest.main()
