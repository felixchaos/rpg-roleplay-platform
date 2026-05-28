"""
test_branch_seed_isolation.py — task 25 回归

修复前：seed_tree 在 state_snapshot 为空时 fallback 到读 game_saves.state_path
（多个 save 共享 rpg/saves/game_state.json），导致新建 save 的 root snapshot 含
上一个激活 save 的运行态（player.name、user_variables、pending_questions 等）。

修复后：seed_tree 只信任 game_saves.state_snapshot（create_save 保证写入清白种子）；
只有 snapshot 完全为空才允许 fallback（仅历史数据兼容）。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.helpers import cleanup_test_users, make_client, register_user


class SeedTreeUsesOwnSnapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_new_save_seed_root_does_not_leak_other_saves_runtime(self):
        """
        模拟用户报告：
          1) 创建 script + save A，在共享 state_path 写入"已玩过"的 state（含 user_variables、
             player.name 等）—— 模拟 A 玩到一半的样子。
          2) 创建 save B（同 user 同 script）。
          3) GET /api/branches/B → seed_tree(B) 触发。
          4) B 的 root snapshot 必须是清白的（turn=0/history=[]，无 A 的 player.name）。
        """
        u = register_user(self.client)
        cookies = u["cookies"]
        from platform_app import branches as br
        from platform_app import workspace
        from platform_app.db import connect

        # 0) 找 user_id + 创 script
        with connect() as db:
            uid_row = db.execute(
                "select id from users where username = %s", (u["username"],),
            ).fetchone()
            uid = int(uid_row["id"])
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "integtest_seed_iso"),
            ).fetchone()
            script_id = int(scr["id"])

        # 1) 创 save A
        save_a = workspace.create_save(uid, script_id, "save A")
        save_a_id = int(save_a["id"])

        # 2) 在共享 state_path 写入「A 玩过的状态」(模拟运行时残留)
        state_path = Path(save_a["state_path"])
        polluted = {
            "turn": 7,
            "history": [{"role": "user", "content": "旧聊天 from A"}],
            "player": {"name": "测试旅人A", "current_location": "雾港"},
            "world": {"time": "次日清晨", "timeline": {"current_label": "次日清晨"}},
            "worldline": {"user_variables": {"A_var": "A_val"}},
            "memory": {"current_objective": "A 的目标"},
            "permissions": {"mode": "full_access", "pending_writes": [], "pending_questions": []},
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(polluted, ensure_ascii=False), encoding="utf-8")
        try:
            # 3) 创 save B
            save_b = workspace.create_save(uid, script_id, "save B")
            save_b_id = int(save_b["id"])
            self.assertNotEqual(save_a_id, save_b_id)

            # 4) GET /api/branches/B 触发 seed
            r = self.client.get(f"/api/branches/{save_b_id}", cookies=cookies)
            self.assertEqual(r.status_code, 200, f"branches GET 200: {r.text[:200]}")
            body = r.json()
            nodes = body.get("nodes") or body.get("commits") or []
            self.assertGreaterEqual(len(nodes), 1, "应至少有 root commit")
            root = nodes[0]
            snap = root.get("state_snapshot") or {}

            # 关键断言：B 的 root 不带 A 的 player / history / user_variables
            self.assertEqual(snap.get("turn", 0), 0,
                f"task 25：B 的 root turn 应为 0；实际 {snap.get('turn')!r}（root snap={snap}）")
            self.assertEqual(snap.get("history") or [], [],
                f"task 25：B 的 root history 应为 []；实际 {snap.get('history')!r}")
            player_name = (snap.get("player") or {}).get("name", "")
            self.assertNotEqual(player_name, "测试旅人A",
                f"task 25：B 的 root player.name 不应是 A 的 '测试旅人A'；实际 {player_name!r}")
            wl = (snap.get("worldline") or {}).get("user_variables") or {}
            self.assertNotIn("A_var", wl,
                f"task 25：B 的 root user_variables 不应含 A 的 'A_var'；实际 {wl!r}")
            # current_label 也不应是 A 的「次日清晨」
            current_label = ((snap.get("world") or {}).get("timeline") or {}).get("current_label", "")
            self.assertNotEqual(current_label, "次日清晨",
                f"task 25：B 的 timeline.current_label 不应是 A 的运行时态；实际 {current_label!r}")
        finally:
            # 清理污染文件，避免影响其他测试
            try:
                state_path.unlink()
            except Exception:
                pass

    def test_seed_tree_still_works_with_empty_snapshot_and_fallback(self):
        """兼容历史数据：snapshot 完全为空时仍允许走 state_path（不能破坏老用户）"""
        u = register_user(self.client)
        from platform_app import branches as br
        from platform_app.db import connect

        with connect() as db:
            uid_row = db.execute(
                "select id from users where username = %s", (u["username"],),
            ).fetchone()
            uid = int(uid_row["id"])
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "integtest_seed_empty"),
            ).fetchone()
            script_id = int(scr["id"])
            # 直插 game_saves 行：snapshot=NULL，state_path 指向一个真实文件
            tmp_state = Path("/tmp/_test_seed_empty.json")
            tmp_state.write_text(json.dumps({
                "turn": 3, "history": [{"role":"user","content":"legacy"}],
                "player": {"name": "legacy player"},
            }, ensure_ascii=False), encoding="utf-8")
            sv = db.execute(
                """
                insert into game_saves(user_id, script_id, title, state_path)
                values (%s, %s, %s, %s) returning id
                """,
                (uid, script_id, "empty-snap save", str(tmp_state)),
            ).fetchone()
            save_id = int(sv["id"])
        try:
            # seed
            br.seed_tree(save_id, str(tmp_state))
            with connect() as db:
                root = db.execute(
                    "select state_snapshot from branch_commits where save_id = %s order by turn_index asc limit 1",
                    (save_id,),
                ).fetchone()
            # 空 snapshot 时应该从 state_path 读到 legacy player
            self.assertIsNotNone(root)
            snap = root["state_snapshot"]
            if isinstance(snap, str):
                snap = json.loads(snap)
            self.assertEqual((snap.get("player") or {}).get("name"), "legacy player",
                f"空 snapshot 兼容路径未生效；snap={snap!r}")
        finally:
            try:
                tmp_state.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
