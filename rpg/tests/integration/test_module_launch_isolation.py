"""
test_module_launch_isolation.py — Bug 2 回归

人工 QA 报告：从 Platform 冒险模组页点「开始模组」后，/api/saves 仍只有
两个存档，当前 save_id 未变，但 player_name 变成 Cinder、world_time
变成灰烬矿坑——模组数据写进了当前小说存档，污染了用户的剧本进度。

修复要求：模组启动必须创建并激活独立 save，绝不修改当前 save 的状态。
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class ModuleLaunchIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _user_id(self, username: str) -> int:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select id from users where username = %s", (username,)).fetchone()
        return int(row["id"])

    def _create_script(self, uid: int, title: str) -> int:
        from platform_app.db import connect
        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, title),
            ).fetchone()
        return int(scr["id"])

    def test_launch_creates_new_save_and_keeps_original_save_unchanged(self):
        """核心：launch 后原小说存档的 player.name 不变；新 save 是独立的。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._user_id(u["username"])
        script_id = self._create_script(uid, "qa_novel_script")

        # 1. 建小说存档，模拟用户的「QA 角色卡新游戏 0847」
        r = self.client.post("/api/saves", json={
            "title": "QA 角色卡新游戏",
            "script_id": script_id,
            "new_card": {"name": "林晚舟QA", "role": "QA 探险者", "background": "测试背景。"},
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        novel_save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)

        # 激活这个小说存档，模拟"现在用户正在玩这个剧本"
        self.client.post(f"/api/saves/{novel_save_id}/activate", cookies=cookies)
        state_before = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual((state_before.get("player") or {}).get("name"), "林晚舟QA")

        # 2. launch ash_mine
        r = self.client.post("/api/rules/module/launch",
                             json={"module_id": "ash_mine"}, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:500])
        body = r.json()
        self.assertTrue(body.get("ok"), body)
        module_save_id = int(body.get("save_id") or 0)
        self.assertGreater(module_save_id, 0)
        # 必须是新的 save_id，不能复用小说 save
        self.assertNotEqual(
            module_save_id, novel_save_id,
            f"Bug 2：launch 应建独立 save，但 save_id={module_save_id} 等于小说 save"
        )

        # 3. /api/saves 列表应该多了一个
        r = self.client.get("/api/saves", cookies=cookies)
        saves = r.json().get("items") or []
        save_ids = [int(s.get("id")) for s in saves]
        self.assertIn(novel_save_id, save_ids, "小说存档应仍在列表")
        self.assertIn(module_save_id, save_ids, "模组存档应已在列表")

        # 4. 当前 /api/state 应该是模组 save（已被激活）
        state_after = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(
            (state_after.get("scene") or {}).get("module_id"), "ash_mine",
            f"launch 后当前 state 应在 ash_mine：{state_after.get('scene')}"
        )
        self.assertEqual(
            (state_after.get("content_pack") or {}).get("kind"), "module_adventure",
            f"launch 后 content_pack.kind 应是 module_adventure：{state_after.get('content_pack')}"
        )

        # 5. 切回小说存档，玩家档案必须完整保留
        self.client.post(f"/api/saves/{novel_save_id}/activate", cookies=cookies)
        state_novel = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(
            (state_novel.get("player") or {}).get("name"), "林晚舟QA",
            f"Bug 2：切回小说存档后 player.name 应仍是 林晚舟QA；"
            f"实际={state_novel.get('player')} — 模组启动污染了小说存档"
        )
        self.assertEqual(
            (state_novel.get("scene") or {}).get("module_id"), "",
            f"Bug 2：小说存档不应被注入 module_id；实际 scene={state_novel.get('scene')}"
        )
        # content_pack 也应回到非模组
        self.assertNotEqual(
            (state_novel.get("content_pack") or {}).get("kind"), "module_adventure",
            "小说存档 content_pack 不应是 module_adventure"
        )

    def test_launch_uses_module_title_in_save(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        r = self.client.post("/api/rules/module/launch",
                             json={"module_id": "ash_mine"}, cookies=cookies)
        body = r.json()
        self.assertEqual(body.get("save_title"), "灰烬矿坑",
            f"模组 save 默认 title 应是模组中文名；实际 {body.get('save_title')!r}")

    def test_launch_unknown_module_returns_404(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        r = self.client.post("/api/rules/module/launch",
                             json={"module_id": "definitely_not_a_module"}, cookies=cookies)
        self.assertEqual(r.status_code, 404, r.text[:200])


if __name__ == "__main__":
    unittest.main(verbosity=2)
