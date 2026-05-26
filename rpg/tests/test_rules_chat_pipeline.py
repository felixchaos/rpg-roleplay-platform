"""
test_rules_chat_pipeline.py — free-form player input must hit RulesEngine before GM narration.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.helpers import cleanup_test_users, make_client, register_user


class RulesChatPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _consume(self, resp) -> list[dict]:
        events: list[dict] = []
        ev = "message"
        data_lines: list[str] = []
        for raw_line in resp.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if line == "":
                if data_lines:
                    try:
                        data = json.loads("\n".join(data_lines))
                    except Exception:
                        data = "\n".join(data_lines)
                    events.append({"event": ev, "data": data})
                ev = "message"
                data_lines = []
                continue
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        return events

    def test_free_text_stealth_runs_rules_engine_before_gm(self):
        import app as ui_mod

        user = register_user(self.client)
        cookies = user["cookies"]
        self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=cookies)

        def fake_context_agent(*args, **kwargs):
            yield {
                "type": "result",
                "retrieved_context": "",
                "bundle": {"debug": {"cache_plan": {}}, "prompt": "stub"},
                "steps": [],
                "agent_prompt": "stub",
                "curator_plan": {
                    "rule_candidate_actions": [{
                        "kind": "skill_check",
                        "skill": "stealth",
                        "dc": 13,
                        "move_to": "minecart_track",
                        "reason": "悄悄靠近矿车",
                        "seed": 7,
                    }],
                },
            }

        class StubGM:
            api_id = "stub"

            class Backend:
                model_name = "stub"
                last_usage = {}

            _backend = Backend()

            def curate_context(self, *args, **kwargs):
                return ""

            def respond_stream_with_tools(self, *args, **kwargs):
                yield {"type": "text", "text": "你压低脚步，矿车阴影挡住了你的轮廓。"}

        orig_rca = ui_mod.run_context_agent
        orig_get_gm = ui_mod._get_gm
        orig_get_sub_gm = ui_mod._get_sub_gm
        ui_mod.run_context_agent = fake_context_agent
        ui_mod._get_gm = lambda u: StubGM()
        ui_mod._get_sub_gm = lambda u: StubGM()
        try:
            with self.client.stream(
                "POST",
                "/api/chat",
                json={"message": "我悄悄靠近矿车", "attachments": []},
                cookies=cookies,
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                events = self._consume(resp)
        finally:
            ui_mod.run_context_agent = orig_rca
            ui_mod._get_gm = orig_get_gm
            ui_mod._get_sub_gm = orig_get_sub_gm

        self.assertNotIn("error", [e["event"] for e in events], events)
        rule_updates = [
            e for e in events
            if e["event"] == "updates" and isinstance(e["data"], dict)
            and e["data"].get("stage") == "rules_engine"
        ]
        self.assertTrue(rule_updates, events)

        state = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(state["scene"]["location_id"], "minecart_track")
        self.assertEqual(len(state["dice_log"]), 1)
        self.assertEqual(state["dice_log"][0]["kind"], "skill_check")
        self.assertEqual(state["dice_log"][0]["dc"], 13)

    def test_module_rule_suggestion_overrides_generic_llm_candidate(self):
        import app as ui_mod

        user = register_user(self.client)
        cookies = user["cookies"]
        self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=cookies)

        def fake_context_agent(*args, **kwargs):
            yield {
                "type": "result",
                "retrieved_context": "",
                "bundle": {"debug": {"cache_plan": {}}, "prompt": "stub"},
                "steps": [],
                "agent_prompt": "stub",
                "curator_plan": {
                    "rule_candidate_actions": [{
                        "kind": "skill_check",
                        "skill": "stealth",
                        "dc": 12,
                        "reason": "generic stealth",
                    }],
                },
            }

        class StubGM:
            api_id = "stub"

            class Backend:
                model_name = "stub"
                last_usage = {}

            _backend = Backend()

            def curate_context(self, *args, **kwargs):
                return ""

            def respond_stream_with_tools(self, *args, **kwargs):
                yield {"type": "text", "text": "你贴着岩壁靠近废弃矿车。"}

        orig_rca = ui_mod.run_context_agent
        orig_get_gm = ui_mod._get_gm
        orig_get_sub_gm = ui_mod._get_sub_gm
        ui_mod.run_context_agent = fake_context_agent
        ui_mod._get_gm = lambda u: StubGM()
        ui_mod._get_sub_gm = lambda u: StubGM()
        try:
            with self.client.stream(
                "POST",
                "/api/chat",
                json={"message": "我悄悄靠近矿车", "attachments": []},
                cookies=cookies,
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                events = self._consume(resp)
        finally:
            ui_mod.run_context_agent = orig_rca
            ui_mod._get_gm = orig_get_gm
            ui_mod._get_sub_gm = orig_get_sub_gm

        self.assertNotIn("error", [e["event"] for e in events], events)
        state = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(state["scene"]["location_id"], "minecart_track")
        self.assertEqual(state["dice_log"][0]["dc"], 13)

    def test_gm_json_ops_are_not_saved_as_player_visible_text(self):
        import app as ui_mod

        user = register_user(self.client)
        cookies = user["cookies"]
        self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=cookies)

        def fake_context_agent(*args, **kwargs):
            yield {
                "type": "result",
                "retrieved_context": "",
                "bundle": {"debug": {"cache_plan": {}}, "prompt": "stub"},
                "steps": [],
                "agent_prompt": "stub",
                "curator_plan": {},
            }

        class StubGM:
            api_id = "stub"

            class Backend:
                model_name = "stub"
                last_usage = {}

            _backend = Backend()

            def curate_context(self, *args, **kwargs):
                return ""

            def respond_stream_with_tools(self, *args, **kwargs):
                yield {
                    "type": "text",
                    "text": (
                        "你在矿车旁找到一枚裂纹徽章。\n\n"
                        "```json\n"
                        "[{\"op\":\"question\",\"question\":\"下一步？\",\"options\":[\"继续搜索\",\"返回入口\"]}]\n"
                        "```"
                    ),
                }

        orig_rca = ui_mod.run_context_agent
        orig_get_gm = ui_mod._get_gm
        orig_get_sub_gm = ui_mod._get_sub_gm
        ui_mod.run_context_agent = fake_context_agent
        ui_mod._get_gm = lambda u: StubGM()
        ui_mod._get_sub_gm = lambda u: StubGM()
        try:
            with self.client.stream(
                "POST",
                "/api/chat",
                json={"message": "搜查矿车", "attachments": []},
                cookies=cookies,
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                events = self._consume(resp)
        finally:
            ui_mod.run_context_agent = orig_rca
            ui_mod._get_gm = orig_get_gm
            ui_mod._get_sub_gm = orig_get_sub_gm

        self.assertNotIn("error", [e["event"] for e in events], events)
        state = self.client.get("/api/state", cookies=cookies).json()
        last = state["history"][-1]["content"]
        self.assertIn("裂纹徽章", last)
        self.assertNotIn("```json", last)
        self.assertNotIn('"op"', last)
        self.assertEqual(state["permissions"]["pending_questions"][0]["options"], ["继续搜索", "返回入口"])

    def test_game_console_keeps_rules_state_in_react_state(self):
        html = Path(__file__).resolve().parents[2] / "frontend" / "Game Console.html"
        text = html.read_text(encoding="utf-8")
        for key in ("ruleset", "player_character", "scene", "encounter", "dice_log"):
            self.assertIn(f'"{key}"', text)
        self.assertIn('"game-state-refresh"', text)
        self.assertIn("getRightTabForLocation", text)
        self.assertIn("location.hash", text)
        self.assertIn("if (nextAction) startRun(nextAction)", text)

    def test_game_panel_does_NOT_promote_relationship_to_user_card(self):
        """三层人物系统重构(test_active_entities_three_tier 落地)后,
        游戏面板**不应**再有"转为用户角色卡"按钮 / saveAsUserCard 函数。
        创建 / 提升用户角色卡只能在平台『角色卡』页操作。
        此测试反过来确认旧 promote 路径已彻底从 game-panels.jsx 移除。"""
        panel = Path(__file__).resolve().parents[2] / "frontend" / "src" / "game-panels.jsx"
        text = panel.read_text(encoding="utf-8")
        self.assertNotIn('data-tip="转为用户角色卡"', text,
            "game-panels.jsx 不应再有 data-tip='转为用户角色卡' 按钮")
        self.assertNotIn("saveAsUserCard", text,
            "saveAsUserCard 函数应已删除 — 游戏内不创建用户卡")
        self.assertNotIn("window.api.cards.myUpsert", text,
            "game-panels.jsx 不应再调 cards.myUpsert — 那是平台职责")
        # 平台一侧应仍保留 promote 路径(合法关注点)
        platform = Path(__file__).resolve().parents[2] / "frontend" / "src" / "platform-app.jsx"
        platform_text = platform.read_text(encoding="utf-8")
        self.assertIn("promoteNpcToUserCard", platform_text,
            "平台『角色卡』页应保留 promoteNpcToUserCard")

    def test_game_ui_strips_state_ops_from_gm_messages(self):
        app_js = Path(__file__).resolve().parents[2] / "frontend" / "src" / "game-app.jsx"
        text = app_js.read_text(encoding="utf-8")
        self.assertIn("stripStateOpsForDisplay", text)
        self.assertIn("json|state-ops|state", text)
        self.assertIn("const displayText = stripStateOpsForDisplay(text)", text)

    def test_gm_system_prompt_keeps_literal_json_examples(self):
        import gm as gm_mod

        old_world = gm_mod._WORLD
        gm_mod._WORLD = {
            "setting": "测试世界",
            "current_situation": "测试局势",
            "current_berlin": {
                "atmosphere": "测试氛围",
                "risk_level": "低",
                "power_presence": ["测试势力"],
            },
        }
        try:
            built = gm_mod.GameMaster.__new__(gm_mod.GameMaster)._build_system()
        finally:
            gm_mod._WORLD = old_world

        self.assertIn('"op": "set"', built)
        self.assertIn("测试世界", built)
        self.assertIn("测试局势", built)


if __name__ == "__main__":
    unittest.main(verbosity=2)
