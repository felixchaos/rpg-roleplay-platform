"""
test_rules_api.py — FastAPI 端到端 smoke test，验证 /api/rules/* 端点真正生效。
"""
from __future__ import annotations

import unittest

from tests.helpers import make_client, register_user


class RulesApiSmoke(unittest.TestCase):
    def setUp(self):
        self.client = make_client()
        u = register_user(self.client)
        self.cookies = u["cookies"]

    def test_list_modules_contains_ash_mine(self):
        r = self.client.get("/api/rules/modules", cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        ids = [m["id"] for m in body["modules"]]
        self.assertIn("ash_mine", ids)

    def test_start_module_and_scene(self):
        r = self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=self.cookies)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        rules = body["rules"]
        self.assertEqual(rules["scene"]["module_id"], "ash_mine")
        self.assertEqual(rules["scene"]["location_id"], "mine_entrance")
        self.assertGreater(rules["player_character"]["hp"], 0)
        self.assertIsInstance(rules["dice_log"], list)
        self.assertIn("opening", body)
        self.assertIn("灰烬", body["opening"])

    def test_skill_check_action(self):
        self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=self.cookies)
        # 移动到 minecart_track
        r = self.client.post("/api/rules/move", json={"to": "minecart_track"}, cookies=self.cookies)
        self.assertEqual(r.status_code, 200, r.text)
        # 执行 stealth 检定
        r = self.client.post("/api/rules/action", json={
            "kind": "skill_check", "skill": "stealth", "dc": 13, "seed": 7,
            "reason": "悄悄翻越矿车", "sets_flag": "sneak_pass",
        }, cookies=self.cookies)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["result"]["success"])
        self.assertEqual(len(body["rules"]["dice_log"]), 1)
        self.assertTrue(body["rules"]["scene"]["flags"].get("sneak_pass"))

    def test_state_payload_includes_rules_block(self):
        """/api/state 必须包含 ruleset / player_character / scene / encounter / dice_log。"""
        self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=self.cookies)
        r = self.client.get("/api/state", cookies=self.cookies)
        body = r.json()
        for key in ("ruleset", "player_character", "scene", "encounter", "dice_log"):
            self.assertIn(key, body, f"/api/state 缺少 {key}")

    def test_suggest_rule_actions(self):
        self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=self.cookies)
        self.client.post("/api/rules/move", json={"to": "minecart_track"}, cookies=self.cookies)
        r = self.client.post("/api/rules/suggest", json={"text": "我悄悄靠近矿车"}, cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        kinds = [a["kind"] for a in body["actions"]]
        self.assertIn("skill_check", kinds)
        stealth = next(a for a in body["actions"] if a.get("skill") == "stealth")
        self.assertEqual(stealth["dc"], 13)

    def test_suggest_stealth_can_target_adjacent_minecart_room(self):
        self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=self.cookies)
        r = self.client.post("/api/rules/suggest", json={"text": "我悄悄靠近矿车"}, cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        actions = r.json()["actions"]
        stealth = next(a for a in actions if a.get("skill") == "stealth")
        self.assertEqual(stealth["move_to"], "minecart_track")
        self.assertEqual(stealth["target"], "minecart_track")
        self.assertEqual(stealth["dc"], 13)

    def test_rules_action_clears_stale_gm_question(self):
        import app as ui_mod

        user = register_user(self.client)
        cookies = user["cookies"]
        api_user = user["body"]["user"]
        self.client.post("/api/rules/module/start", json={"module_id": "ash_mine"}, cookies=cookies)
        self.client.post("/api/rules/move", json={"to": "minecart_track"}, cookies=cookies)

        state = ui_mod._ensure_loaded(api_user)
        state.add_pending_question(
            "你的下一个动作是？",
            source="gm:json",
            options=["继续射击已经倒下的敌人", "转向另一个敌人"],
        )
        state.data.setdefault("memory", {})["last_structured_updates"] = [
            "append: world.known_events",
            "等待玩家回答",
        ]
        state.save()

        r = self.client.post("/api/rules/action", json={
            "kind": "skill_check",
            "skill": "investigation",
            "dc": 12,
            "seed": 9,
            "reason": "检查矿车把手的油污",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text)

        state_body = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(state_body["permissions"]["pending_questions"], [])
        self.assertNotIn("等待玩家回答", state_body["memory"]["last_structured_updates"])
        self.assertIn("append: world.known_events", state_body["memory"]["last_structured_updates"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
