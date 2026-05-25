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


if __name__ == "__main__":
    unittest.main(verbosity=2)
