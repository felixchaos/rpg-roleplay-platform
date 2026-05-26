"""
test_composer_live_data.py — Composer 的 ContextUsage 与 Model 下拉
必须接真后端，不能再是 hardcoded mock。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import make_client, register_user


class StatePayloadIncludesContextWindow(unittest.TestCase):
    """/api/state.app.context_window 必须存在，给 FE ContextUsage 圆环做分母。"""

    def test_app_context_window_is_present_and_int(self):
        client = make_client()
        u = register_user(client)
        state = client.get("/api/state", cookies=u["cookies"]).json()
        app_block = state.get("app") or {}
        self.assertIn("context_window", app_block,
            "/api/state.app 必须含 context_window；否则 Composer 圆环只能用 mock 1M")
        ctx = app_block["context_window"]
        self.assertIsInstance(ctx, int)
        self.assertGreater(ctx, 0,
            "context_window 应 > 0；后端 platform_app.usage.context_window_for 应识别当前 model")


class StatePayloadIncludesModelCatalog(unittest.TestCase):
    """/api/state.models.apis 必须存在 + .selected 指向当前模型。"""

    def test_models_catalog_present(self):
        client = make_client()
        u = register_user(client)
        state = client.get("/api/state", cookies=u["cookies"]).json()
        models = state.get("models") or {}
        self.assertIsInstance(models.get("apis"), list)
        self.assertGreater(len(models["apis"]), 0,
            "至少应有一个 API/模型，否则 Composer 模型下拉为空")
        # selected 必须能映射回真实 model
        sel = models.get("selected") or {}
        self.assertIn("api_id", sel)
        self.assertIn("model_id", sel)

    def test_at_least_one_model_in_first_enabled_api(self):
        client = make_client()
        u = register_user(client)
        state = client.get("/api/state", cookies=u["cookies"]).json()
        apis = (state.get("models") or {}).get("apis") or []
        enabled_apis = [a for a in apis if a.get("enabled") is not False]
        self.assertGreater(len(enabled_apis), 0, "需要至少一个 enabled API")
        first = enabled_apis[0]
        self.assertIn("models", first)
        self.assertGreater(len(first.get("models") or []), 0)


class FrontendComposerWiresLiveData(unittest.TestCase):
    """game-composer.jsx 不再使用 hardcoded ContextUsage 数值；ModelPopover 接真目录 + 真 select API。"""

    @classmethod
    def setUpClass(cls):
        cls.composer = (Path(__file__).resolve().parents[2]
                        / "frontend" / "src" / "game-composer.jsx").read_text(encoding="utf-8")
        cls.html = (Path(__file__).resolve().parents[2]
                    / "frontend" / "Game Console.html").read_text(encoding="utf-8")

    def test_context_usage_no_longer_hardcoded(self):
        # 旧 mock：<ContextUsage used={624300} cap={1_048_576} plan={28} />
        self.assertNotIn("used={624300}", self.composer,
            "ContextUsage 不应再 hardcoded used=624300")
        self.assertNotIn("cap={1_048_576}", self.composer,
            "ContextUsage 不应再 hardcoded cap=1_048_576")
        self.assertNotIn("plan={28}", self.composer,
            "ContextUsage 不应再 hardcoded plan=28")

    def test_context_usage_reads_gameState(self):
        self.assertIn("<ContextUsage gameState={gameState}", self.composer,
            "ContextUsage 应从 gameState 拿数据")
        self.assertIn("memory.last_context.estimated_tokens", self.composer,
            "ContextUsage used 应读 gameState.memory.last_context.estimated_tokens")
        self.assertIn("app.context_window", self.composer,
            "ContextUsage cap 应读 gameState.app.context_window")
        self.assertIn("window.api.account.usage", self.composer,
            "ContextUsage 应拉 /api/me/usage 接月度数据")

    def test_model_popover_uses_catalog_not_hardcoded(self):
        # 旧 MODEL_OPTIONS.map(...) 在 ModelPopover 内 — 现在应用 catalog.apis.flatMap
        # 通过查找 ModelPopover 上下文里没有 MODEL_OPTIONS.map 即可
        idx = self.composer.find("function ModelPopover")
        self.assertGreater(idx, 0)
        end = self.composer.find("function ", idx + 1)
        if end < 0:
            end = len(self.composer)
        popover_body = self.composer[idx:end]
        self.assertNotIn("MODEL_OPTIONS.map", popover_body,
            "ModelPopover 不应再迭代 hardcoded MODEL_OPTIONS")
        self.assertIn("window.api.models.select", popover_body,
            "ModelPopover 选中后必须调真后端 /api/models/select")
        self.assertIn("apis", popover_body,
            "ModelPopover 应从 catalog.apis 派生选项")

    def test_game_console_picks_app_and_models_into_state(self):
        self.assertIn('"app"', self.html,
            "Game Console PICK_STATE_KEYS 应含 app，否则 ContextUsage 拿不到 context_window")
        self.assertIn('"models"', self.html,
            "Game Console PICK_STATE_KEYS 应含 models，否则 ModelPopover 拿不到 catalog")

    def test_composer_label_reads_live_app_model(self):
        # 当前模型标签应优先用 gameState.app.model，而不是 MODEL_OPTIONS 的 mock label
        self.assertIn("_currentModelLabel", self.composer)
        self.assertIn("gameState.app.model", self.composer,
            "_currentModelLabel 必须读 gameState.app.model 才反映真实切换结果")


if __name__ == "__main__":
    unittest.main(verbosity=2)
