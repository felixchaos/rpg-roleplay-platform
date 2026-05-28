"""
test_composer_live_data.py — Composer 的 ContextUsage 与 Model 下拉
必须接真后端，不能再是 hardcoded mock。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import make_client, register_user


class StatePayloadIncludesContextWindow(unittest.TestCase):
    """/api/v1/state.app.context_window 必须存在，给 FE ContextUsage 圆环做分母。"""

    def test_app_context_window_is_present_and_int(self):
        client = make_client()
        u = register_user(client)
        state = client.get("/api/v1/state", cookies=u["cookies"]).json()
        app_block = state.get("app") or {}
        self.assertIn("context_window", app_block,
            "/api/v1/state.app 必须含 context_window；否则 Composer 圆环只能用 mock 1M")
        ctx = app_block["context_window"]
        self.assertIsInstance(ctx, int)
        self.assertGreater(ctx, 0,
            "context_window 应 > 0；后端 platform_app.usage.context_window_for 应识别当前 model")


class StatePayloadIncludesModelCatalog(unittest.TestCase):
    """/api/v1/state.models.apis 必须存在 + .selected 指向当前模型。"""

    def test_models_catalog_present(self):
        client = make_client()
        u = register_user(client)
        state = client.get("/api/v1/state", cookies=u["cookies"]).json()
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
        state = client.get("/api/v1/state", cookies=u["cookies"]).json()
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
        cls.composer = (Path(__file__).resolve().parents[3]
                        / "frontend" / "src" / "game-composer.jsx").read_text(encoding="utf-8")
        cls.html = (Path(__file__).resolve().parents[3]
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

    def test_no_mock_model_options_constant(self):
        # task 39 收尾：完全删掉 MODEL_OPTIONS 常量，不让任何 fallback 路径还能命中 mock 标签。
        # 现场 bug：用户截图显示 "GPT-4o · RPG / 主流 · 较快" 5 项 — 那是 MODEL_OPTIONS literal。
        # 注释里出现 MODEL_OPTIONS 这个词没事（写解释/历史），只要不再有真正的 const 声明 + 业务读它。
        import re
        # 找 `const MODEL_OPTIONS` / `let MODEL_OPTIONS` / `var MODEL_OPTIONS` —— 任何形式的真正声明
        decl = re.search(r"^\s*(?:const|let|var)\s+MODEL_OPTIONS\b", self.composer, re.MULTILINE)
        self.assertIsNone(decl,
            "MODEL_OPTIONS 常量应已删除；仍存在会作为 mock fallback 把用户带回 mock 列表")
        # 业务代码不应再读取这个标识符（注释/字符串里出现没问题，避免误判）
        # 思路：把所有单行注释和块注释剥掉后再 grep。
        nocmt = re.sub(r"/\*[\s\S]*?\*/", "", self.composer)
        nocmt = re.sub(r"^\s*//.*$", "", nocmt, flags=re.MULTILINE)
        # 字串里 MODEL_OPTIONS 仍可能出现（不影响），但既然没声明，任何"读 MODEL_OPTIONS.find/.map"
        # 都会让 JS runtime 直接 ReferenceError。grep 所有这类访问。
        for pat in (r"\bMODEL_OPTIONS\.find\b", r"\bMODEL_OPTIONS\.map\b",
                    r"\bMODEL_OPTIONS\.forEach\b", r"\bMODEL_OPTIONS\.filter\b",
                    r"\bMODEL_OPTIONS\s*\["):
            self.assertIsNone(re.search(pat, nocmt),
                f"代码（剥注释后）不应再访问 {pat}，会 ReferenceError")

    def test_no_hardcoded_mock_model_labels_in_composer(self):
        # 截图取证里出现的 5 个 mock 字串绝不应作为代码常量留在 jsx 里
        # （注释里写一次说明历史可以；这里只查"真正还在被渲染的 5 项 literal 整块"）。
        mock_strings = [
            '"GPT-4o · RPG"',
            '"Claude Opus 4.1"',
            '"Gemini 3 Flash"',
            '"通义千问 Max"',
            '"DeepSeek R1"',
        ]
        # 用 dict 文字面声明特征 — `id: "...", label: "..."` 这种 — 来定位 literal 数据。
        for s in mock_strings:
            # 业务代码不应出现这种 `label: "GPT-4o · RPG"` 的对象 literal 写法。
            # 注释里出现整个字串没事；只要不是 `label: "..."` 这种 prop 赋值。
            import re
            pat = r'label\s*:\s*' + re.escape(s)
            m = re.search(pat, self.composer)
            self.assertIsNone(m,
                f"composer 还有 `label: {s}` 对象字面量 — 这就是 MODEL_OPTIONS mock 残留")

    def test_game_console_initial_model_not_mock_id(self):
        # Game Console.html 之前 useState("gpt-4o-mini-rpg") — 用户截图底部
        # "+ GPT-4o · RPG" 标签就是这个 id 走 MODEL_OPTIONS.find 得到的。
        # 现在应改成 useState(null) 之类，让真值完全由 gameState.app.model 决定。
        self.assertNotIn('useState("gpt-4o-mini-rpg")', self.html,
            "Game Console 初始 model state 不应再用 mock id 'gpt-4o-mini-rpg'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
