"""
test_existing_card_applied.py — Bug 1 回归

人工 QA 报告：UI 新建存档选「林晚舟QA」(user_card) →
POST /api/saves 用 character_id+character_kind=user_card →
后端创建 save 后 /api/state 的 player.name/role/background 仍是空。

修复要求：
- create_save 必须把 user_card / persona / script_card 应用到初始 snapshot
- save activate → /api/state 必须拿到带 player.name 的快照
"""
from __future__ import annotations

import json
import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class ExistingCardAppliedToRuntime(unittest.TestCase):
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

    def _activate_and_state(self, cookies, save_id: int) -> dict:
        r = self.client.post(f"/api/saves/{save_id}/activate", cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        r2 = self.client.get("/api/state", cookies=cookies)
        self.assertEqual(r2.status_code, 200, r2.text[:300])
        return r2.json()

    def test_user_card_writes_player_into_runtime_state(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._user_id(u["username"])
        script_id = self._create_script(uid, "card_apply_user_card")

        # 1. 建 user_card「林晚舟QA」
        r = self.client.post("/api/me/character-cards", json={
            "name": "林晚舟QA",
            "identity": "QA 探险者",
            "appearance": "黑发披风，背着旧皮包。",
            "personality": "冷静细致。",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        card = r.json().get("card") or {}
        card_id = int(card.get("id") or 0)
        self.assertGreater(card_id, 0)

        # 2. POST /api/saves 用 character_id+character_kind=user_card
        r = self.client.post("/api/saves", json={
            "title": "QA 角色卡新游戏",
            "script_id": script_id,
            "character_id": card_id,
            "character_kind": "user_card",
            "npc_id": None,
            "new_card": None,
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        save = r.json().get("save") or {}
        save_id = int(save.get("id") or 0)
        self.assertGreater(save_id, 0)

        # 3. 验证 save 的 state_snapshot 含 player name
        snap = save.get("state_snapshot") or {}
        if isinstance(snap, str):
            snap = json.loads(snap)
        player = (snap.get("player") or {})
        self.assertEqual(
            player.get("name"), "林晚舟QA",
            f"Bug 1：save.state_snapshot.player.name 应=林晚舟QA；"
            f"实际={player.get('name')!r} (player={player})",
        )

        # 4. 激活后 /api/state 也必须含 player（Bug 1 真正报告的现象）
        state = self._activate_and_state(cookies, save_id)
        self.assertEqual(
            (state.get("player") or {}).get("name"), "林晚舟QA",
            f"Bug 1：activate 后 /api/state.player.name 应=林晚舟QA；"
            f"实际={state.get('player')}"
        )

    def test_persona_writes_player_into_runtime_state(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._user_id(u["username"])
        script_id = self._create_script(uid, "card_apply_persona")

        # 建 persona
        r = self.client.post("/api/me/personas", json={
            "name": "测试 persona",
            "role": "侦探",
            "background": "一名退役军人。",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        persona = r.json().get("persona") or {}
        persona_id = int(persona.get("id") or 0)
        self.assertGreater(persona_id, 0)

        r = self.client.post("/api/saves", json={
            "title": "persona save",
            "script_id": script_id,
            "character_id": persona_id,
            "character_kind": "persona",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        save = r.json().get("save") or {}
        save_id = int(save.get("id") or 0)
        state = self._activate_and_state(cookies, save_id)
        self.assertEqual((state.get("player") or {}).get("name"), "测试 persona",
            f"persona 应用失败：player={state.get('player')}")

    def test_user_card_survives_branches_continue_path(self):
        """FE 实际流程：创建后弹 Continue Picker → 点 root 节点 →
        POST /api/branches/continue → 进入游戏。/api/state 必须保留 player。
        这条路径与直接 /api/saves/{id}/activate 不同，单独覆盖。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._user_id(u["username"])
        script_id = self._create_script(uid, "card_apply_continue")

        r = self.client.post("/api/me/character-cards", json={
            "name": "继续路径测试", "identity": "测试者",
            "appearance": "—", "personality": "—",
        }, cookies=cookies)
        card_id = int(((r.json() or {}).get("card") or {}).get("id") or 0)

        r = self.client.post("/api/saves", json={
            "title": "branches continue path",
            "script_id": script_id,
            "character_id": card_id,
            "character_kind": "user_card",
        }, cookies=cookies)
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)

        # 拉根 commit
        r = self.client.get(f"/api/branches/{save_id}", cookies=cookies)
        body = r.json() or {}
        commits = body.get("nodes") or body.get("commits") or []
        self.assertGreater(len(commits), 0, "无 root commit")
        root = commits[0]
        root_id = int(root.get("id") or 0)
        self.assertGreater(root_id, 0)

        # POST /api/branches/continue
        r = self.client.post("/api/branches/continue",
                             json={"node_id": root_id}, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])

        # /api/state 必须含 player.name
        r = self.client.get("/api/state", cookies=cookies)
        state = r.json()
        self.assertEqual(
            (state.get("player") or {}).get("name"), "继续路径测试",
            f"Bug 1：continue_from 走完 /api/state.player.name 应=继续路径测试；"
            f"实际={state.get('player')}"
        )

    def test_create_save_falls_back_to_blank_when_card_missing(self):
        """无 new_card 也无 character_id 时退回空白快照，不要 500。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._user_id(u["username"])
        script_id = self._create_script(uid, "card_apply_blank")
        r = self.client.post("/api/saves", json={"title": "blank", "script_id": script_id}, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])


if __name__ == "__main__":
    unittest.main(verbosity=2)
