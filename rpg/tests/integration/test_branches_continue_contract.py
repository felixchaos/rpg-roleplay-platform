"""
test_branches_continue_contract.py — task 38 回归

复现：Game Console hover 消息后点"从这里新建分支"，前端只发 {label}（没 commit_id/
message_index/save_id），后端 int(body.get("node_id")) → int(None) → TypeError → 500。

修复：
  1. /api/branches/continue 接受两种 body：
     A) {node_id: <int>}                  老路径
     B) {save_id, message_index, label}   Game Console 用，后端通过
        branches.resolve_commit_id_by_message 把 message_index → turn_index → commit
  2. 任何缺/坏字段 → 清晰 400（不再 500）
  3. 前端 MsgActions 拿 saveId + msgIndex 后才会发请求；缺信息时按钮 disabled
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class BranchesContinueAcceptsMessageIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _uid(self, username: str) -> int:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select id from users where username = %s", (username,)).fetchone()
        return int(row["id"])

    def _mk_save_with_chapters(self, uid: int) -> int:
        """建一个 script + save + 几个 branch_commits（模拟跑过几轮）"""
        from platform_app import workspace
        from platform_app.db import connect
        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "br_contract_test"),
            ).fetchone()
            script_id = int(scr["id"])
        save = workspace.create_save(uid, script_id, "br save", new_card={
            "name": "p", "role": "r", "background": "b",
        })
        save_id = int(save["id"])
        # 模拟 3 轮：turn 0,1,2 各产生 player+gm commit
        with connect() as db:
            # 先找 root commit
            root = db.execute(
                "select * from branch_commits where save_id = %s order by turn_index asc limit 1",
                (save_id,),
            ).fetchone()
            parent_id = int(root["id"]) if root else None
            state_path = str(root["state_path"]) if root else ""
            import secrets as _secrets
            for turn in range(3):
                for kind in ("player", "gm"):
                    obj_hash = _secrets.token_hex(8)
                    new = db.execute(
                        """
                        insert into branch_commits(save_id, parent_id, turn_index, kind,
                                                   object_hash, tree_hash, title, message,
                                                   summary, content_preview,
                                                   player_input, gm_output, state_path)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        returning id
                        """,
                        (save_id, parent_id, turn, kind,
                         obj_hash, obj_hash,
                         f"turn {turn} {kind}", f"{kind} msg @ turn {turn}",
                         f"{kind} turn {turn} summary",
                         f"{kind} turn {turn} preview",
                         f"player input {turn}" if kind == "player" else "",
                         f"gm output {turn}" if kind == "gm" else "",
                         state_path),
                    ).fetchone()
                    parent_id = int(new["id"])
        return save_id

    def _mk_save_with_turn_one_messages(self, uid: int) -> int:
        """建一个真实 messages 表从 turn=1 开始的存档，覆盖线上删除/分支映射。"""
        import secrets as _secrets

        from psycopg.types.json import Jsonb

        from platform_app import workspace
        from platform_app.db import connect

        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "br_messages_test"),
            ).fetchone()
            script_id = int(scr["id"])
        save = workspace.create_save(uid, script_id, "br messages save", new_card={
            "name": "p", "role": "r", "background": "b",
        })
        save_id = int(save["id"])
        with connect() as db:
            root = db.execute(
                "select * from branch_commits where save_id = %s order by id asc limit 1",
                (save_id,),
            ).fetchone()
            parent_id = int(root["id"])
            state_path = str(root["state_path"] or "")
            commits = []
            for turn in (1, 2):
                obj_hash = _secrets.token_hex(8)
                row = db.execute(
                    """
                    insert into branch_commits(save_id, parent_id, turn_index, kind,
                                               object_hash, tree_hash, title, message,
                                               summary, content_preview, state_path, state_snapshot)
                    values (%s, %s, %s, 'round', %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        save_id, parent_id, turn, obj_hash, obj_hash,
                        f"turn {turn}", f"round {turn}", f"summary {turn}",
                        f"preview {turn}", state_path,
                        Jsonb({"history": [], "turn": turn}),
                    ),
                ).fetchone()
                parent_id = int(row["id"])
                commits.append(parent_id)
            session = db.execute(
                """
                insert into game_sessions(save_id, script_id, user_id, title, state)
                values (%s, %s, %s, %s, %s)
                on conflict(save_id) do update set state = excluded.state
                returning id
                """,
                (save_id, script_id, uid, "chat", Jsonb({"history": [], "turn": 2})),
            ).fetchone()
            session_id = int(session["id"])
            for turn, user_text, gm_text in (
                (1, "u1", "a1"),
                (2, "u2", "a2"),
            ):
                db.execute(
                    """
                    insert into messages(session_id, save_id, turn, role, content, metadata)
                    values (%s, %s, %s, 'user', %s, '{}'::jsonb),
                           (%s, %s, %s, 'assistant', %s, '{}'::jsonb)
                    """,
                    (session_id, save_id, turn, user_text, session_id, save_id, turn, gm_text),
                )
            db.execute(
                "update game_saves set active_commit_id = %s, active_branch_node_id = %s where id = %s",
                (commits[-1], commits[-1], save_id),
            )
        return save_id

    def test_continue_with_save_id_and_message_index_resolves_commit(self):
        """核心：发 {save_id, message_index} → 后端把它映射到正确的 branch_commit"""
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_chapters(uid)

        # message_index=3 → turn=1, kind=gm（msg=3 是 turn1 的 gm）
        r = self.client.post("/api/v1/branches/continue", json={
            "save_id": save_id,
            "message_index": 3,
            "label": "从消息分支",
        }, cookies=u["cookies"])
        self.assertEqual(r.status_code, 200, f"应 200；实际 {r.status_code}: {r.text[:300]}")
        body = r.json()
        self.assertTrue(body.get("ok"), f"应 ok=True：{body}")
        self.assertIn("active_commit_id", body)
        self.assertGreater(int(body["active_commit_id"] or 0), 0)

    def test_continue_with_node_id_still_works(self):
        """对照：传统 node_id 路径不破坏"""
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_chapters(uid)
        # 拿一个真实 commit id
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select id from branch_commits where save_id = %s and turn_index = 1 and kind = 'gm' limit 1",
                (save_id,),
            ).fetchone()
        node_id = int(row["id"])
        r = self.client.post("/api/v1/branches/continue", json={
            "node_id": node_id,
            "label": "old path",
        }, cookies=u["cookies"])
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertTrue((r.json() or {}).get("ok"))

    def test_continue_with_no_fields_returns_400_not_500(self):
        """关键回归：原 bug——空 body 让后端 int(None) 崩；现在必须 400 + 清晰 message"""
        u = register_user(self.client)
        r = self.client.post("/api/v1/branches/continue", json={"label": "从消息分支"}, cookies=u["cookies"])
        self.assertEqual(r.status_code, 400,
            f"task 38：缺 node_id/save_id+message_index 应回 400，不是 500；实际 {r.status_code}: {r.text[:200]}")
        body = r.json()
        self.assertFalse(body.get("ok"))
        self.assertIn("缺字段", str(body.get("error", "")),
            f"error message 应说明缺字段；实际 {body.get('error')!r}")

    def test_continue_with_bad_node_id_returns_400(self):
        """对照：node_id 不是整数 → 400 而不是 500"""
        u = register_user(self.client)
        r = self.client.post("/api/v1/branches/continue", json={"node_id": "not-a-number"}, cookies=u["cookies"])
        self.assertEqual(r.status_code, 400, r.text[:200])
        self.assertIn("不是整数", str((r.json() or {}).get("error", "")))

    def test_continue_with_unresolvable_message_index_returns_400(self):
        """对照：save 存在但 message_index 超出范围 → 400 而不是 500"""
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_chapters(uid)
        # 该 save 只有 3 turn (0,1,2)，msg=10 → turn=5 不存在
        r = self.client.post("/api/v1/branches/continue", json={
            "save_id": save_id,
            "message_index": 100,
            "label": "x",
        }, cookies=u["cookies"])
        self.assertEqual(r.status_code, 400, r.text[:200])
        self.assertIn("无法在 save", str((r.json() or {}).get("error", "")))

    def test_resolve_commit_id_unit(self):
        """单元：resolve_commit_id_by_message 行为锚"""
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_chapters(uid)
        from platform_app import branches as br
        # msg=0 → turn 0 player
        cid_player_0 = br.resolve_commit_id_by_message(uid, save_id, 0)
        # msg=1 → turn 0 gm
        cid_gm_0 = br.resolve_commit_id_by_message(uid, save_id, 1)
        # msg=4 → turn 2 player
        cid_player_2 = br.resolve_commit_id_by_message(uid, save_id, 4)
        self.assertIsNotNone(cid_player_0)
        self.assertIsNotNone(cid_gm_0)
        self.assertIsNotNone(cid_player_2)
        self.assertNotEqual(cid_player_0, cid_gm_0)
        # 跨用户隔离
        u2 = register_user(self.client)
        uid2 = self._uid(u2["username"])
        cid_cross = br.resolve_commit_id_by_message(uid2, save_id, 1)
        self.assertIsNone(cid_cross, "其它用户不应能 resolve 不属于自己的 save")

    def test_resolve_uses_actual_message_turns_when_available(self):
        """线上 messages.turn 从 1 开始时，不能再用 message_index // 2 映射错 turn。"""
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_turn_one_messages(uid)

        from platform_app import branches as br
        cid_msg0 = br.resolve_commit_id_by_message(uid, save_id, 0)
        cid_msg2 = br.resolve_commit_id_by_message(uid, save_id, 2)

        from platform_app.db import connect
        with connect() as db:
            turn1 = db.execute(
                "select id from branch_commits where save_id = %s and turn_index = 1 order by id desc limit 1",
                (save_id,),
            ).fetchone()
            turn2 = db.execute(
                "select id from branch_commits where save_id = %s and turn_index = 2 order by id desc limit 1",
                (save_id,),
            ).fetchone()
        self.assertEqual(cid_msg0, int(turn1["id"]))
        self.assertEqual(cid_msg2, int(turn2["id"]))

    def test_rollback_assistant_message_preserves_previous_player_line(self):
        """反馈 #15：删除 GM 回复时不应把上一条玩家输入也一起删掉。"""
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_turn_one_messages(uid)

        r = self.client.post("/api/v1/branches/rollback", json={
            "save_id": save_id,
            "message_index": 1,
        }, cookies=u["cookies"])
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        self.assertTrue(body.get("ok"), body)
        self.assertEqual((body.get("deleted") or {}).get("messages"), 3)

        from platform_app.db import connect
        with connect() as db:
            rows = db.execute(
                "select turn, role, content from messages where save_id = %s order by id",
                (save_id,),
            ).fetchall()
        self.assertEqual(
            [(int(r["turn"]), r["role"], r["content"]) for r in rows],
            [(1, "user", "u1")],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
