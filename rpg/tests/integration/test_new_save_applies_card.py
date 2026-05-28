"""
test_new_save_applies_card.py — task 29 回归

复现：Platform.html#saves 「新建存档」UI 选「新建角色卡」→ 填姓名/身份/设定
→ POST /api/saves 返回 save_id；但 save_detail 拿到的 state_snapshot.player
仍是 {"name":"","role":"","background":"","current_location":"..."}，
分支 root snapshot 也同样空。

修复：
  - frontend NewGameModal 把 textarea「设定」绑到 newCardBg 状态，传到
    new_card.background；并把 character_kind 透传给后端
  - backend POST /api/saves 接 new_card / character_id+character_kind
  - workspace.create_save 用 setup_player 把 new_card 应用到初始 state，
    再 Jsonb 写入 game_saves.state_snapshot
  - branches.seed_tree（task 25 已修）信任 state_snapshot，root commit 同步
"""
from __future__ import annotations

import json
import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class NewSaveAppliesNewCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _create_script(self, uid: int, title: str) -> int:
        from platform_app.db import connect
        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, title),
            ).fetchone()
        return int(scr["id"])

    def _user_id(self, username: str) -> int:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select id from users where username = %s", (username,)).fetchone()
        return int(row["id"])

    def test_new_card_payload_writes_player_into_state_snapshot(self):
        """核心回归：UI payload {new_card: {name, role, background}} 必须真的应用到 state_snapshot.player"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._user_id(u["username"])
        script_id = self._create_script(uid, "integtest_card_apply")

        payload = {
            "title": "E2E_UI_完整游戏_test",
            "script_id": script_id,
            "character_id": None,
            "character_kind": None,
            "npc_id": None,
            "new_card": {
                "name": "测试旅人",
                "role": "时间线测试者",
                "background": "用于从导入剧本开始验证 /set、自然语言时间修改和按钮后端联动。",
            },
        }
        r = self.client.post("/api/v1/saves", json=payload, cookies=cookies)
        self.assertEqual(r.status_code, 200, f"POST /api/saves 应 200：{r.text[:300]}")
        body = r.json()
        self.assertTrue(body.get("ok"), f"应 ok=True：{body}")
        save = body.get("save") or {}
        save_id = int(save.get("id") or 0)
        self.assertGreater(save_id, 0)

        # GET /api/saves/{id} → detail 内含 state_snapshot
        r2 = self.client.get(f"/api/v1/saves/{save_id}", cookies=cookies)
        self.assertEqual(r2.status_code, 200, r2.text[:300])
        detail = (r2.json() or {}).get("save") or {}
        snap = detail.get("state_snapshot") or {}
        if isinstance(snap, str):
            snap = json.loads(snap)
        player = (snap.get("player") or {})
        self.assertEqual(player.get("name"), "测试旅人",
            f"task 29：state_snapshot.player.name 应=测试旅人；实际 {player.get('name')!r}（player={player}）")
        self.assertEqual(player.get("role"), "时间线测试者",
            f"task 29：state_snapshot.player.role 应=时间线测试者；实际 {player.get('role')!r}")
        self.assertIn("验证 /set", str(player.get("background") or ""),
            f"task 29：background 应反映用户输入；实际 {player.get('background')!r}")

        # 分支 root snapshot 也必须同步（task 25 修过 seed_tree 信任 snapshot）
        r3 = self.client.get(f"/api/v1/branches/{save_id}", cookies=cookies)
        self.assertEqual(r3.status_code, 200, r3.text[:300])
        nodes = (r3.json() or {}).get("nodes") or (r3.json() or {}).get("commits") or []
        self.assertGreaterEqual(len(nodes), 1, "应至少一个 root commit")
        root = nodes[0]
        rsnap = root.get("state_snapshot") or {}
        if isinstance(rsnap, str):
            rsnap = json.loads(rsnap)
        rplayer = (rsnap.get("player") or {})
        self.assertEqual(rplayer.get("name"), "测试旅人",
            f"task 29：branches root snapshot.player.name 应=测试旅人；实际 {rplayer.get('name')!r}")
        self.assertEqual(rplayer.get("role"), "时间线测试者",
            f"task 29：branches root snapshot.player.role 应=时间线测试者；实际 {rplayer.get('role')!r}")

    def test_create_save_without_card_falls_back_to_blank_player(self):
        """对照：不传 new_card 时退回到旧行为，不应抛"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._user_id(u["username"])
        script_id = self._create_script(uid, "integtest_card_blank")

        payload = {"title": "blank save", "script_id": script_id}
        r = self.client.post("/api/v1/saves", json=payload, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        self.assertGreater(save_id, 0)
        r2 = self.client.get(f"/api/v1/saves/{save_id}", cookies=cookies)
        snap = ((r2.json() or {}).get("save") or {}).get("state_snapshot") or {}
        if isinstance(snap, str):
            snap = json.loads(snap)
        player = (snap.get("player") or {})
        # 兼容老行为：name/role 可以是空字符串，但 turn=0/history=[] 必须成立
        self.assertEqual(snap.get("turn", 0), 0)
        self.assertEqual(snap.get("history") or [], [])
        # 玩家字段允许空，但 player dict 必须存在
        self.assertIsInstance(player, dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
