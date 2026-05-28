"""
test_retest_qa_5_bugs.py — 第二轮人工 QA 5 个问题的回归测试。

复测路径（账号 qa_retest_0526_093552）：
1. Bug 1 仍失败：普通新游戏选 existing user_card 后 runtime player 仍空
2. 新位置回归：minecart_track 输入"观察灌木"被错误移回 mine_entrance
3. Bug 5 未修完：GM 全量 list set 应覆盖（不再 append）
4. acceptance_unmet 否定语义被误判
5. 中文逗号拆完整事件句
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class Retest_Bug1_ExistingCardAfterPriorActiveSave(unittest.TestCase):
    """复测 Bug 1：用户已有一个活跃 module save 时，
    新建带 user_card 的普通存档 → activate → /api/state 必须读到 player.name。"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _uid(self, username):
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select id from users where username = %s", (username,)).fetchone()
        return int(row["id"])

    def _script(self, uid, title):
        from platform_app.db import connect
        with connect() as db:
            scr = db.execute("insert into scripts(owner_id, title) values (%s, %s) returning id",
                             (uid, title)).fetchone()
        return int(scr["id"])

    def test_user_card_save_player_survives_prior_module_save(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])

        # 1. 先 launch 一个模组（模拟 QA 之前测 Bug 2 留下的活跃 save）
        r = self.client.post("/api/rules/module/launch",
                             json={"module_id": "ash_mine"}, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        module_save_id = int(r.json().get("save_id") or 0)
        self.assertGreater(module_save_id, 0)

        # 2. 建 user_card
        r = self.client.post("/api/me/character-cards", json={
            "name": "林晚舟复测",
            "identity": "QA 探险者",
            "appearance": "黑发披风",
            "personality": "冷静细致",
        }, cookies=cookies)
        card_id = int(((r.json() or {}).get("card") or {}).get("id") or 0)
        self.assertGreater(card_id, 0)

        # 3. 建普通新存档（选择 user_card）
        script_id = self._script(uid, "qa_retest_existing_card_script")
        r = self.client.post("/api/saves", json={
            "title": "QA existing card retest 0938",
            "script_id": script_id,
            "character_id": card_id,
            "character_kind": "user_card",
            "new_card": None,
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        novel_save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        self.assertGreater(novel_save_id, 0)
        self.assertNotEqual(novel_save_id, module_save_id, "新 save 应是独立 id")

        # 4. 走 FE ContinuePicker.confirm 同样的路径：/api/saves/{id}/activate → /api/state
        r = self.client.post(f"/api/saves/{novel_save_id}/activate", cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        self.assertEqual(int(body.get("active_save_id") or 0), novel_save_id)

        # 5. /api/state 必须读到 player.name = 林晚舟复测
        state = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(int(state.get("save_id") or 0), novel_save_id,
            f"/api/state.save_id 应=novel save；实际={state.get('save_id')}")
        player = state.get("player") or {}
        self.assertEqual(
            player.get("name"), "林晚舟复测",
            f"Bug 1 retest：activate 后 /api/state.player.name 应=林晚舟复测；"
            f"实际={player}（这是用户报告的失败现象）"
        )
        self.assertIn("QA 探险者", str(player.get("role") or ""))


class Retest_NewBug_ObserveDoesNotTriggerCrossRoomMove(unittest.TestCase):
    """复测：在 minecart_track 输入「观察灌木」不应触发移回 mine_entrance。"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_observation_intent_does_not_move_player(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        # 1. launch ash_mine + 移动到 minecart_track
        r = self.client.post("/api/rules/module/launch",
                             json={"module_id": "ash_mine"}, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        r = self.client.post("/api/rules/move",
                             json={"to": "minecart_track"}, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        state = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual((state.get("scene") or {}).get("location_id"), "minecart_track")

        # 2. suggest 观察意图 → 不应给 move_to
        r = self.client.post("/api/rules/suggest", json={
            "text": "我点燃一支火把照亮矿车轨道，消耗背包里 1 支 Torch，然后保持戒备观察灌木后的动静。",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        actions = (r.json() or {}).get("actions") or []
        # 任何 action 都不应有 move_to / move kind
        for a in actions:
            self.assertFalse(
                a.get("move_to"),
                f"观察意图不应触发跨房移动；action={a}"
            )
            self.assertNotEqual(
                a.get("kind"), "move",
                f"无明确移动动词时不应建议 move action；action={a}"
            )


class Retest_Bug5_FullListSetOverwrites(unittest.TestCase):
    """复测 Bug 5：GM op={"op":"set","value":[完整列表]} 应覆盖，不要 append。"""

    def test_set_with_list_value_replaces_not_appends(self):
        from state import GameState
        g = GameState.new()
        g.data["memory"]["resources"] = ["Shortsword ×1", "Shortbow ×1",
                                          "Torch ×2", "Healing Draught ×1"]
        # 模拟 read_only 模式下 GM op→ pending → approve
        g.data["permissions"]["mode"] = "read_only"
        new_list = ["Shortsword ×1", "Shortbow ×1", "Torch ×1", "Healing Draught ×1"]
        # GM op kind="set" 走 _gm_write_via_gate → apply_state_write_typed
        # 不带 append=True / overwrite=True flag。
        result = g.apply_state_write_typed("memory.resources", new_list,
                                            source="gm", overwrite=False, append=False)
        self.assertIn("待审", result)
        pw = g.data["permissions"]["pending_writes"][0]
        # 审批
        approve_result = g.approve_pending_write(id=pw["id"])
        self.assertIn("状态写入", approve_result)
        final = g.data["memory"]["resources"]
        self.assertEqual(final, new_list,
            f"Bug 5 retest：set 全量 list 应覆盖；实际={final}\n"
            f"（之前 dedupe-append 会把 Torch ×2 和 Torch ×1 并存）")
        # 不应出现 Torch ×2 残留
        self.assertNotIn("Torch ×2", final, "旧 Torch ×2 应被替换")


class Retest_AcceptanceNegationSemantics(unittest.TestCase):
    """复测：「没有直接修改玩家的 HP 或 AC」这种 success state 不应被误报 unmet。"""

    def test_no_modification_acceptance_passes_when_response_does_not_mention_subject(self):
        from app import _verify_acceptance_rule
        # 条款本身是"成功状态"描述："没有发生 HP/AC 修改"
        acceptance = ["没有直接修改玩家的 HP 或 AC"]
        # 响应没有提到 HP/AC（普通叙事）
        response = "你蹲在矿道入口前，仔细辨认泥土里那串脚印。东侧的轨道上锈迹斑驳。"
        updates: list[str] = []
        unmet = _verify_acceptance_rule(acceptance, response, updates)
        self.assertEqual(unmet, [],
            f"retest #4：成功 success state '没有 X 发生' 不应被报 unmet；"
            f"实际 unmet={unmet}")


class Retest_SplitItemsKeepsSentenceWithCommas(unittest.TestCase):
    """复测 #5：完整事件句不应被中文逗号拆成两条 known_events。"""

    def test_sentence_with_internal_comma_stays_one_item(self):
        from state import _split_items
        sentence = "Cinder在东侧轨道调查时触发巨响，惊动了不明生物"
        items = _split_items(sentence)
        self.assertEqual(len(items), 1,
            f"含内嵌中文逗号的完整事件句应保持单条；实际拆出 {len(items)} 条：{items}")
        self.assertEqual(items[0], sentence)

    def test_short_comma_separated_resources_still_split(self):
        # 对照：短词列表（每项 ≤ 12 字）仍应按逗号切
        from state import _split_items
        items = _split_items("Torch ×1，Shortsword，Shortbow")
        self.assertEqual(len(items), 3,
            f"短词列表（≤12 字）应按逗号切；实际={items}")

    def test_dunhao_split_short_items(self):
        from state import _split_items
        items = _split_items("Torch、Shortsword、Healing Draught")
        self.assertEqual(len(items), 3,
            f"顿号短项强切分；实际={items}")

    def test_long_sentence_with_dunhao_falls_back_to_split(self):
        # 顿号 + 分号 永远切分（不像逗号那样依赖长度启发式）。
        # 这是 known 取舍 — 顿号/分号是强列表分隔符。
        from state import _split_items
        items = _split_items("发生了 A；接着又发生 B 让人措手不及")
        self.assertEqual(len(items), 2,
            f"分号应总是切；实际={items}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
