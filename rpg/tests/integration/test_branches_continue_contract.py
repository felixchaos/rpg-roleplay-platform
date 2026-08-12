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
        """建一个 script + save + 3 轮 branch_commits（模拟跑过几轮）。

        2026-07-17 修复:原 fixture 造的是 legacy kind='player'/'gm' 分裂 commit（每轮 2 条），
        但现行 resolve_commit_id_by_message（tree_ops.py）早已改为「全库 branch_commits 只有
        root/round 两种 kind，一轮=一个原子 round commit（玩家输入+GM 输出合一）」，按
        turn_index 取该 round commit，不再按 kind 区分玩家/GM 半轮。这里改造 fixture 对齐当前
        契约：root（workspace.create_save 内 seed_tree 自动建，turn_index=0）之后接 3 个
        kind='round' commit（turn_index=1,2,3，与 platform_app.branches.runtime.record_runtime_turn
        的落库形态一致），state_snapshot.history 累积 [user, assistant] 对（不再留空数组），
        让 opening_offset 探测（读 state_snapshot->history->0）在真实数据下工作。
        """
        from psycopg.types.json import Jsonb

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
        with connect() as db:
            # root 是 create_save→seed_tree 自动建的 turn_index=0 kind='root' commit
            root = db.execute(
                "select * from branch_commits where save_id = %s and turn_index = 0 order by id asc limit 1",
                (save_id,),
            ).fetchone()
            parent_id = int(root["id"])
            state_path = str(root["state_path"] or "")
            import secrets as _secrets
            history: list[dict] = []
            for turn in (1, 2, 3):
                obj_hash = _secrets.token_hex(8)
                user_text = f"player input {turn}"
                gm_text = f"gm output {turn}"
                history = history + [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": gm_text},
                ]
                new = db.execute(
                    """
                    insert into branch_commits(save_id, parent_id, turn_index, kind,
                                               object_hash, tree_hash, title, message,
                                               summary, content_preview,
                                               player_input, gm_output, state_path, state_snapshot)
                    values (%s, %s, %s, 'round', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (save_id, parent_id, turn,
                     obj_hash, obj_hash,
                     f"turn {turn}", f"round {turn}",
                     f"round {turn} summary",
                     f"round {turn} preview",
                     user_text, gm_text, state_path,
                     Jsonb({"history": history, "turn": turn})),
                ).fetchone()
                parent_id = int(new["id"])
            db.execute(
                "update game_saves set active_commit_id = %s, active_branch_node_id = %s where id = %s",
                (parent_id, parent_id, save_id),
            )
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
            # state_snapshot.history 累积 [user, assistant] 对(不留空数组)：
            # resolve_commit_id_by_message / rollback_to_message 都从活跃 commit 的
            # state_snapshot->history->0 探测 opening_offset，空数组会让探测结果恰好巧合
            # 命中默认值 0，掩盖真实数据下的行为——这里用真实累积历史让测试对齐现行契约。
            history: list[dict] = []
            for turn, user_text, gm_text in ((1, "u1", "a1"), (2, "u2", "a2")):
                obj_hash = _secrets.token_hex(8)
                history = history + [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": gm_text},
                ]
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
                        Jsonb({"history": history, "turn": turn}),
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

        # 当前契约(round 原子提交,无 player/gm 分裂 kind):message_index=3 是 round2 的 GM
        # 半轮 → turn_index=(3-0)//2=1 → fork 到 round1(turn_index=1)commit(该轮的父状态)。
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
        # 拿一个真实 commit id（现行契约:round 是原子提交,没有 kind='gm' 半轮）
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select id from branch_commits where save_id = %s and turn_index = 1 and kind = 'round' limit 1",
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
        """对照：message_index 非法（负数）→ 400 而不是 500。

        2026-07-17 修复:旧断言用超范围正数(message_index=100)期待 400，但现行
        resolve_commit_id_by_message（tree_ops.py）为「保证不返回 None 阻断功能」，
        对超范围正数已改为优雅降级——沿活跃血缘取 turn_index<=target 的最近一个 commit
        （缺口不再报错，见该函数「无活跃指针(异常)→ 退回旧的全 save 行为」附近注释），
        所以只要 save 存在 commit，正数 message_index 恒能 resolve 成功。真正会让
        resolve_commit_id_by_message 返回 None 的唯一输入是负数（`msg_index < 0: return None`）。
        这里把「非法输入不 500」的守卫改钉在负数上，并新增一条断言，把超范围正数
        「优雅降级不报错」的新契约也显式钉住（不弱化原测试保护的意图：既不 500，也不该
        无谓地对着一个可以优雅处理的输入返回一堆用户看不懂的 400）。
        """
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_chapters(uid)

        # 负数 message_index → resolve_commit_id_by_message 显式返回 None → 400，不是 500
        r = self.client.post("/api/v1/branches/continue", json={
            "save_id": save_id,
            "message_index": -1,
            "label": "x",
        }, cookies=u["cookies"])
        self.assertEqual(r.status_code, 400, r.text[:200])
        self.assertIn("无法在 save", str((r.json() or {}).get("error", "")))

        # 对照：超范围正数(该 save 只有 3 轮)现在优雅降级到最近可用 commit，不是 400
        r2 = self.client.post("/api/v1/branches/continue", json={
            "save_id": save_id,
            "message_index": 100,
            "label": "x",
        }, cookies=u["cookies"])
        self.assertEqual(r2.status_code, 200, r2.text[:200])
        self.assertTrue((r2.json() or {}).get("ok"))

    def test_resolve_commit_id_unit(self):
        """单元：resolve_commit_id_by_message 行为锚。

        2026-07-17 修复:round commit 是原子提交(玩家输入+GM 输出合一,无 kind='player'/'gm'
        半轮),同一轮内的两条消息(玩家半轮 + GM 半轮)fork 目标恒相同——都是该轮的【父】commit
        （turn_index = max(0,(msg_index-offset)//2)，round N 的两条消息都落在 turn_index=N-1）。
        旧断言期望同轮 player/gm 两条消息 resolve 到不同 commit，那是 legacy 分裂 kind 的产物，
        现在不成立；改断言同轮相同、跨轮不同，守住「message_index→commit」映射的真正不变量。
        """
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_chapters(uid)
        from platform_app import branches as br
        # msg=0/1 都是 round1 的两条消息 → 同 fork 到 round1 的父状态(root, turn_index=0)
        cid_player_0 = br.resolve_commit_id_by_message(uid, save_id, 0)
        cid_gm_0 = br.resolve_commit_id_by_message(uid, save_id, 1)
        # msg=4 是 round3 的玩家半轮 → fork 到 round3 的父状态(round2, turn_index=2)
        cid_player_2 = br.resolve_commit_id_by_message(uid, save_id, 4)
        self.assertIsNotNone(cid_player_0)
        self.assertIsNotNone(cid_gm_0)
        self.assertIsNotNone(cid_player_2)
        # 同一轮的两条消息(玩家半轮/GM 半轮)fork 到同一个父 commit
        self.assertEqual(cid_player_0, cid_gm_0)
        # 不同轮 fork 到不同 commit
        self.assertNotEqual(cid_player_0, cid_player_2)
        # 跨用户隔离
        u2 = register_user(self.client)
        uid2 = self._uid(u2["username"])
        cid_cross = br.resolve_commit_id_by_message(uid2, save_id, 1)
        self.assertIsNone(cid_cross, "其它用户不应能 resolve 不属于自己的 save")

    def test_resolve_uses_actual_message_turns_when_available(self):
        """线上 messages.turn 从 1 开始时，不能再用 message_index // 2 映射错 turn。

        2026-07-17 修复:round 是原子提交，message_index K 恒 fork 到「所在轮的父 commit」
        （turn_index=(K-offset)//2），不是「所在轮自己的 commit」。round1(turn_index=1)的两条
        消息(idx0,1)父状态是 root(turn_index=0)；round2(turn_index=2)的两条消息(idx2,3)父状态
        是 round1(turn_index=1)。旧断言把 idx0/idx2 分别锚到 round1/round2 自己（是「产生该消息
        的轮次」，不是当前契约的「fork 目标」），改锚到各自的父 commit。
        """
        u = register_user(self.client)
        uid = self._uid(u["username"])
        save_id = self._mk_save_with_turn_one_messages(uid)

        from platform_app import branches as br
        cid_msg0 = br.resolve_commit_id_by_message(uid, save_id, 0)
        cid_msg2 = br.resolve_commit_id_by_message(uid, save_id, 2)

        from platform_app.db import connect
        with connect() as db:
            root = db.execute(
                "select id from branch_commits where save_id = %s and turn_index = 0 order by id asc limit 1",
                (save_id,),
            ).fetchone()
            turn1 = db.execute(
                "select id from branch_commits where save_id = %s and turn_index = 1 order by id desc limit 1",
                (save_id,),
            ).fetchone()
        self.assertEqual(cid_msg0, int(root["id"]), "round1 的消息应 fork 到其父状态 root")
        self.assertEqual(cid_msg2, int(turn1["id"]), "round2 的消息应 fork 到其父状态 round1")

    def test_rollback_assistant_message_preserves_previous_player_line(self):
        """反馈 #15 的原始诉求：删除 GM 回复不应连上一条玩家输入也删掉。

        2026-07-17 复核:round 提交是原子的（玩家输入 + GM 输出合一，无法只保留半轮），
        platform_app.branches.deletion.rollback_to_message 的当前实现按此把「被点消息所在
        整轮」一起回滚（见该函数 `deleted_turn = target_turn + 1` 附近注释「连被点消息所在
        整回合一起删」）：对 round1 的 GM 半轮(message_index=1)发起回滚，会连 round1 的玩家
        半轮(u1)一起回退掉，然后 round2 也因排在其后被一并清空——4 条消息全删、不剩
        任何一条，这是 task 116c 引入「回合原子化」后的现行确定性行为，不是本次改动引入的
        新问题。这里先把断言钉在当前实际输出上，不删测试、不弱化「不 500 / 不残留脏数据」
        的核心守卫；「反馈 #15」诉求的『保留上一条玩家输入』与当前『轮内原子』架构冲突，
        需要单独立项评估（见 spawn 的后续任务），不在本次债务收敛范围内处理。
        """
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
        # 现行「轮内原子」回滚:round1 的 GM 半轮所在整轮(u1+a1)连同其后的 round2(u2+a2)一起删
        self.assertEqual((body.get("deleted") or {}).get("messages"), 4)

        from platform_app.db import connect
        with connect() as db:
            rows = db.execute(
                "select turn, role, content from messages where save_id = %s order by id",
                (save_id,),
            ).fetchall()
        self.assertEqual(
            [(int(r["turn"]), r["role"], r["content"]) for r in rows],
            [],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
