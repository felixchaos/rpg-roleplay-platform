"""test_rollback_opening_offset — rollback_to_message 开场感知回归(2026-07-17 根修)。

群反馈(行者无疆/晓卡/星之游):「删除此消息及以后 会多回退一个回合」。第二根因=旧代码
`msg_index//2` + 「偶数再退一格」把 opening_offset 恒写死为 1;无开场对话(空起手 / 角色卡
无 first_mes)整体反相 → 玩家消息落偶数位 → 误退一轮、上上轮被删。

本测试用真库建两类档(有开场 / 无开场)跑真实 /api/v1/branches/rollback,断言:
  1. 有开场 3 轮,重生第 2 轮  → 恢复 turn1、只删 [2,3](守旧不回归)
  2. 无开场 3 轮,重生第 2 轮  → 恢复 turn1、只删 [2,3](本 bug 核心;旧代码会误退到 root)
  3. 无开场 3 轮,重生第 3 轮  → 恢复 turn2、只删 [3]
  4. 无开场 点 GM 回复(奇数位)→ 连整轮删、不越删上一轮
  5. 重生第 1 轮:有开场保开场;无开场恢复到 root

存档构造:workspace.create_save 建 save,清掉自动 seed 的 commits,把 game_saves.state_snapshot
覆盖为手工 history blob,再 seed_tree 重建(seed_tree 信任 state_snapshot,turn_index 用
history_index//2 与全系统对齐,并逐 commit 写全量快照 → 活跃叶子 history[0] 可靠 = 前端 msg_index 源)。
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


def _opening_blob() -> dict:
    """有开场:history[0]=GM 开场(assistant),其后 [玩家,GM] 交替 3 轮。"""
    return {
        "history": [
            {"role": "assistant", "content": "OPENING"},          # idx0 开场 → turn0
            {"role": "user", "content": "P1"}, {"role": "assistant", "content": "G1"},  # idx1,2 turn1
            {"role": "user", "content": "P2"}, {"role": "assistant", "content": "G2"},  # idx3,4 turn2
            {"role": "user", "content": "P3"}, {"role": "assistant", "content": "G3"},  # idx5,6 turn3
        ],
        "turn": 3,
    }


def _no_opening_blob() -> dict:
    """无开场:history[0] 直接是玩家输入(空起手 / 无 first_mes),[玩家,GM] 交替 3 轮。"""
    return {
        "history": [
            {"role": "user", "content": "P1"}, {"role": "assistant", "content": "G1"},  # idx0,1 turn1
            {"role": "user", "content": "P2"}, {"role": "assistant", "content": "G2"},  # idx2,3 turn2
            {"role": "user", "content": "P3"}, {"role": "assistant", "content": "G3"},  # idx4,5 turn3
        ],
        "turn": 3,
    }


class RollbackOpeningOffsetE2E(unittest.TestCase):
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

    def _mk_save_with_history(self, uid: int, blob: dict, title: str) -> int:
        """建 save,清 auto-seed 的 commits,把 state_snapshot 覆盖为 blob 再 seed_tree 重建。"""
        from psycopg.types.json import Jsonb

        from platform_app import workspace
        from platform_app.branches import seed_tree
        from platform_app.db import connect

        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, f"rb_offset_{title}"),
            ).fetchone()
            script_id = int(scr["id"])
        save = workspace.create_save(uid, script_id, f"rb {title}", new_card={
            "name": "p", "role": "r", "background": "b",
        })
        save_id = int(save["id"])
        with connect() as db:
            state_path = str((db.execute(
                "select state_path from game_saves where id = %s", (save_id,),
            ).fetchone() or {}).get("state_path") or "")
            db.execute("delete from branch_refs where save_id = %s", (save_id,))
            db.execute("delete from branch_commits where save_id = %s", (save_id,))
            db.execute(
                "update game_saves set state_snapshot = %s where id = %s",
                (Jsonb(blob), save_id),
            )
        seed_tree(save_id, state_path)
        return save_id

    def _rollback(self, cookies, save_id: int, message_index: int) -> dict:
        r = self.client.post("/api/v1/branches/rollback", json={
            "save_id": save_id,
            "message_index": message_index,
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:400])
        body = r.json()
        self.assertTrue(body.get("ok"), body)
        return body

    def _active_history(self, save_id: int) -> list:
        """回滚后活跃 commit 的 hydrated history(权威截断源)。"""
        from platform_app.branches.history_elide import hydrate_commit_state
        from platform_app.db import connect
        with connect() as db:
            cid = int((db.execute(
                "select coalesce(active_commit_id, active_branch_node_id) as cid from game_saves where id = %s",
                (save_id,),
            ).fetchone() or {}).get("cid") or 0)
            row = db.execute("select * from branch_commits where id = %s", (cid,)).fetchone()
            hist = hydrate_commit_state(db, save_id, row).get("history") or []
        return [(m.get("role"), m.get("content")) for m in hist]

    # ── 场景 1:有开场 3 轮,重生第 2 轮 → 恢复 turn1、只删 [2,3](守旧不回归)─────
    def test_opening_regen_round2_restores_turn1(self):
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_history(uid, _opening_blob(), "open_r2")
        # 有开场:玩家 turn2 输入 = idx3。
        body = self._rollback(u["cookies"], save_id, 3)
        self.assertEqual(body["restored_turn"], 1, "有开场重生第2轮应恢复到 turn1")
        self.assertEqual(
            self._active_history(save_id),
            [("assistant", "OPENING"), ("user", "P1"), ("assistant", "G1")],
            "只应保留开场 + turn1(删 turn2/turn3)",
        )

    # ── 场景 2:无开场 3 轮,重生第 2 轮 → 恢复 turn1、只删 [2,3](本 bug 核心)──────
    def test_no_opening_regen_round2_restores_turn1(self):
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_history(uid, _no_opening_blob(), "noopen_r2")
        # 无开场:玩家 turn2 输入 = idx2(旧代码把它当偶数=GM 再退一格 → 误退 root、多删 turn1)。
        body = self._rollback(u["cookies"], save_id, 2)
        self.assertEqual(body["restored_turn"], 1, "无开场重生第2轮必须恢复到 turn1(不得多退一轮到 root)")
        self.assertEqual(
            self._active_history(save_id),
            [("user", "P1"), ("assistant", "G1")],
            "只应保留 turn1(删 turn2/turn3);多删 turn1 即本 bug",
        )

    # ── 场景 3:无开场 3 轮,重生第 3 轮 → 恢复 turn2、只删 [3]─────────────────────
    def test_no_opening_regen_round3_restores_turn2(self):
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_history(uid, _no_opening_blob(), "noopen_r3")
        # 无开场:玩家 turn3 输入 = idx4。
        body = self._rollback(u["cookies"], save_id, 4)
        self.assertEqual(body["restored_turn"], 2, "无开场重生第3轮应恢复到 turn2")
        self.assertEqual(
            self._active_history(save_id),
            [("user", "P1"), ("assistant", "G1"), ("user", "P2"), ("assistant", "G2")],
            "只应保留 turn1+turn2(删 turn3)",
        )

    # ── 场景 4:无开场 点 GM 回复(奇数位)→ 连整轮删、不越删上一轮 ─────────────────
    def test_no_opening_delete_gm_reply_drops_whole_turn(self):
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_history(uid, _no_opening_blob(), "noopen_gm")
        # 无开场:GM turn2 回复 = idx3(奇数位)。点「删除此 GM 回复」应连 turn2 整轮删、不越删 turn1。
        body = self._rollback(u["cookies"], save_id, 3)
        self.assertEqual(body["restored_turn"], 1, "删 GM turn2 回复应退到 turn1(整回合删、不越删)")
        self.assertEqual(
            self._active_history(save_id),
            [("user", "P1"), ("assistant", "G1")],
            "turn2 整轮(含 GM 回复)被删、turn1 保留",
        )

    # ── 场景 5:重生第 1 轮 —— 有开场保开场;无开场恢复到 root ────────────────────
    def test_regen_round1_opening_preserves_opening(self):
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_history(uid, _opening_blob(), "open_r1")
        # 有开场:玩家 turn1 输入 = idx1 → 退到 turn0(=开场,保留开场,删其后)。
        body = self._rollback(u["cookies"], save_id, 1)
        self.assertEqual(body["restored_turn"], 0, "有开场重生第1轮应退到 turn0(开场)")
        self.assertEqual(
            self._active_history(save_id),
            [("assistant", "OPENING")],
            "开场必须保留(只删 turn1..3)",
        )

    def test_regen_round1_no_opening_restores_root(self):
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_history(uid, _no_opening_blob(), "noopen_r1")
        # 无开场:玩家 turn1 输入 = idx0 → 退到 turn0(=root,空历史)。
        body = self._rollback(u["cookies"], save_id, 0)
        self.assertEqual(body["restored_turn"], 0, "无开场重生第1轮应恢复到 root(turn0)")
        self.assertEqual(self._active_history(save_id), [], "root 无历史(全删)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
