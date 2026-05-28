"""
test_state_save_id.py — UI 审计任务 10 后端契约：/api/state 顶层暴露 save_id / save_title

前端 Game Console 不再回退到 hard-coded mock id=11；它读 data.save_id 来识别当前
存档。如果后端把这个字段拿掉，前端就只能退化到 /api/saves 第一条 —— 测试要锚住。
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class StateExposesSaveContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_state_contains_save_id_after_save_active(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        # 新建一个 save → 让 user_runtime 指向它
        r_new = self.client.post("/api/v1/new", json={
            "name": "integtest_player",
            "role": "tester",
            "background": "测试 player",
        }, cookies=cookies)
        self.assertEqual(r_new.status_code, 200, f"/api/v1/new failed: {r_new.text[:200]}")

        # /api/state 现在应该带 save_id + save_title
        r_state = self.client.get("/api/v1/state", cookies=cookies)
        self.assertEqual(r_state.status_code, 200)
        body = r_state.json()
        self.assertIn("save_id", body, f"/api/v1/state 必须暴露 save_id（task 10 契约锚）：{list(body)[:20]}")
        self.assertTrue(body.get("save_id"), f"save_id 不能空：{body.get('save_id')}")
        self.assertIn("save_title", body, "save_title 必须存在")
        self.assertIsInstance(body.get("save_title", ""), str)

    def test_state_save_id_matches_saves_list_first(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        self.client.post("/api/v1/new", json={"name": "p2", "role": "r", "background": ""}, cookies=cookies)
        r_state = self.client.get("/api/v1/state", cookies=cookies)
        r_saves = self.client.get("/api/v1/saves", cookies=cookies)
        body = r_state.json()
        saves = r_saves.json().get("items") or r_saves.json().get("saves") or []
        if saves:
            # save_id 必须出现在 /api/saves 列表里（不能是别的用户的）
            ids = {int(s["id"]) for s in saves if s.get("id") is not None}
            self.assertIn(int(body.get("save_id") or 0), ids,
                          f"state.save_id={body.get('save_id')} 不在自己的 saves 列表 {ids}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
