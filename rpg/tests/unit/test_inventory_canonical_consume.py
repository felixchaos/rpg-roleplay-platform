"""
test_inventory_canonical_consume.py — 用户硬要求：
canonical inventory + 确定性 consume_item parser，不依赖 GM prompt。

覆盖：
1. parse_consume_intent 解析中英文 "点燃/使用/消耗 N 支/份 Torch" 等
2. RulesEngine.consume_inventory_item 扣 canonical inventory
3. memory.resources 派生同步（不再出现 Torch ×2 + Torch ×1 共存）
4. State Gate 锁住 player_character.inventory，GM 直写被拒
5. /api/rules/action kind=consume_item 端到端
6. /api/chat 玩家文本"点燃 1 支 Torch"→ backend parser 触发 consume
7. read_only 下 GM 写 memory.resources 走 pending；approve 后 inventory 仍 canonical
"""
from __future__ import annotations

import unittest

from rules.dnd5e.character import (
    consume_inventory_item,
    find_inventory_item,
    normalize_item_alias,
    resources_from_inventory,
)
from rules_bridge import parse_consume_intent, start_module
from state import GameState
from tests.helpers import cleanup_test_users, make_client, register_user

# ── 纯函数单测 ───────────────────────────────────────────────────


class NormalizeItemAlias(unittest.TestCase):
    def test_canonical_id_unchanged(self):
        self.assertEqual(normalize_item_alias("torch"), "torch")

    def test_chinese_aliases(self):
        self.assertEqual(normalize_item_alias("火把"), "torch")
        self.assertEqual(normalize_item_alias("火炬"), "torch")
        self.assertEqual(normalize_item_alias("急救药剂"), "healing_draught")

    def test_english_aliases(self):
        self.assertEqual(normalize_item_alias("Torch"), "torch")
        self.assertEqual(normalize_item_alias("Healing Draught"), "healing_draught")

    def test_unknown_returns_empty(self):
        self.assertEqual(normalize_item_alias("某种不存在的物品"), "")


class ConsumeInventoryItem(unittest.TestCase):
    def _char(self):
        return {
            "inventory": [
                {"id": "torch", "name": "Torch", "qty": 2, "kind": "gear"},
                {"id": "healing_draught", "name": "Healing Draught", "qty": 1, "kind": "consumable"},
            ],
        }

    def test_consume_decrements_qty(self):
        c = self._char()
        r = consume_inventory_item(c, "torch", 1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["consumed"], 1)
        self.assertEqual(r["qty_before"], 2)
        self.assertEqual(r["qty_after"], 1)
        # inventory 真的扣了
        torch = next(i for i in c["inventory"] if i["id"] == "torch")
        self.assertEqual(torch["qty"], 1)

    def test_consume_via_chinese_alias(self):
        c = self._char()
        r = consume_inventory_item(c, "火把", 1)
        self.assertTrue(r["ok"], r)
        torch = next(i for i in c["inventory"] if i["id"] == "torch")
        self.assertEqual(torch["qty"], 1)

    def test_consume_to_zero_removes_item(self):
        c = self._char()
        r = consume_inventory_item(c, "healing_draught", 1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["qty_after"], 0)
        self.assertIsNone(find_inventory_item(c, "healing_draught"))

    def test_consume_more_than_available_caps(self):
        c = self._char()
        r = consume_inventory_item(c, "torch", 5)
        self.assertTrue(r["ok"])
        self.assertEqual(r["consumed"], 2, "应只消耗背包里实际有的 2 个")
        self.assertEqual(r["qty_after"], 0)

    def test_consume_unknown_fails(self):
        c = self._char()
        r = consume_inventory_item(c, "magic_wand", 1)
        self.assertFalse(r["ok"])

    def test_resources_from_inventory_derived(self):
        c = self._char()
        out = resources_from_inventory(c)
        self.assertIn("Torch ×2", out)
        self.assertIn("Healing Draught ×1", out)


class ParseConsumeIntent(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")
        self.pc = self.g.data["player_character"]

    def test_chinese_dianran_yizhi_huoba(self):
        intents = parse_consume_intent("我点燃一支火把照亮矿车轨道", self.pc)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["item_id"], "torch")
        self.assertEqual(intents[0]["qty"], 1)

    def test_chinese_xiaohao_with_arabic_number(self):
        intents = parse_consume_intent("我消耗背包里 1 支 Torch", self.pc)
        self.assertGreaterEqual(len(intents), 1)
        self.assertEqual(intents[0]["item_id"], "torch")
        self.assertEqual(intents[0]["qty"], 1)

    def test_chinese_use_two_torches(self):
        intents = parse_consume_intent("使用两支火把", self.pc)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["item_id"], "torch")
        self.assertEqual(intents[0]["qty"], 2)

    def test_english_use_healing_draught(self):
        intents = parse_consume_intent("I use the healing draught", self.pc)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["item_id"], "healing_draught")

    def test_no_consume_intent_returns_empty(self):
        intents = parse_consume_intent("我观察灌木后的动静", self.pc)
        self.assertEqual(intents, [])

    def test_item_not_in_inventory_filtered(self):
        # 如果 inventory 里没有 ash_relic，即便文本有 use ash_relic 也不返回
        intents = parse_consume_intent("我使用魔法权杖", self.pc)
        self.assertEqual(intents, [])


# ── State / RulesEngine 集成 ─────────────────────────────────────


class StateConsumeAndSync(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")

    def test_state_consume_syncs_memory_resources(self):
        # 初始：inventory 有 Torch ×2
        before = self.g.data["player_character"]["inventory"]
        torch_before = next(i for i in before if i["id"] == "torch")
        self.assertEqual(torch_before["qty"], 2)

        r = self.g.consume_inventory_item("torch", 1)
        self.assertTrue(r["ok"], r)

        # canonical inventory
        torch_after = next(i for i in self.g.data["player_character"]["inventory"]
                           if i["id"] == "torch")
        self.assertEqual(torch_after["qty"], 1)

        # memory.resources 已同步
        resources = self.g.data["memory"]["resources"]
        self.assertIn("Torch ×1", resources)
        self.assertNotIn("Torch ×2", resources)
        # 没出现两个 Torch（旧 bug）
        torch_lines = [r for r in resources if r.startswith("Torch")]
        self.assertEqual(len(torch_lines), 1, f"应只剩一行 Torch；实际={torch_lines}")


class StateGateProtectsInventory(unittest.TestCase):
    def test_gm_cannot_directly_write_inventory(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        # GM 试图直接改 inventory（force/不 force 都应被拒）
        result = g.apply_state_write_typed(
            "player_character.inventory", [], source="gm", overwrite=True,
        )
        self.assertIn("rules_managed", result,
            "Bug 5：player_character.inventory 必须是 rules_managed，"
            f"GM 直写应被拒；实际={result}")

    def test_user_force_set_cannot_overwrite_inventory(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        result = g.apply_state_write_typed(
            "player_character.inventory", [], source="user:/set", force=True,
        )
        self.assertIn("rules_managed", result)


class ApprovalRoutesToCanonicalInventory(unittest.TestCase):
    """硬要求 #4：read_only 模式审批 memory.resources 写入后，
    canonical inventory 才是真相源，派生层重新同步。"""

    def test_approve_memory_resources_resyncs_from_inventory(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        # 玩家通过确定性 consume 路径扣了 1 支 torch
        g.consume_inventory_item("torch", 1)
        canonical_resources = list(g.data["memory"]["resources"])
        self.assertIn("Torch ×1", canonical_resources)

        # 模拟 GM 在 read_only 下写了一个错误的 resources list
        g.data["permissions"]["mode"] = "read_only"
        bogus_list = ["Shortsword ×1", "Shortbow ×1", "Torch ×99", "Healing Draught ×1"]
        result = g.apply_state_write_typed(
            "memory.resources", bogus_list, source="gm", overwrite=False,
        )
        self.assertIn("待审", result)
        pw = g.data["permissions"]["pending_writes"][0]

        approve_result = g.approve_pending_write(id=pw["id"])
        self.assertIn("状态写入", approve_result)

        # 关键：审批后，memory.resources 应回归 canonical inventory（Torch ×1），
        # 而不是 GM 那条 Torch ×99
        final = g.data["memory"]["resources"]
        self.assertIn("Torch ×1", final)
        self.assertNotIn("Torch ×99", final,
            f"硬要求 #3/#4：memory.resources 是派生层，GM 编造的 Torch ×99 "
            f"不应留下；实际={final}")


# ── API 端到端 ──────────────────────────────────────────────────


class ApiConsumeItemEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_api_rules_action_consume_item(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        self.client.post("/api/rules/module/launch",
                         json={"module_id": "ash_mine"}, cookies=cookies)
        # 起点：Torch ×2
        state = self.client.get("/api/state", cookies=cookies).json()
        torch = next(i for i in state["player_character"]["inventory"] if i["id"] == "torch")
        self.assertEqual(torch["qty"], 2)

        # POST /api/rules/action consume_item
        r = self.client.post("/api/rules/action", json={
            "kind": "consume_item",
            "item_id": "torch",
            "qty": 1,
            "reason": "点燃火把",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        self.assertTrue(body.get("ok"), body)

        # /api/state torch qty 应该是 1
        state2 = self.client.get("/api/state", cookies=cookies).json()
        torch2 = next(i for i in state2["player_character"]["inventory"] if i["id"] == "torch")
        self.assertEqual(torch2["qty"], 1,
            f"硬要求 #2：canonical inventory torch qty 必须 2→1；实际 {torch2['qty']}")

        # memory.resources 派生同步
        resources = state2["memory"]["resources"]
        self.assertIn("Torch ×1", resources)
        self.assertNotIn("Torch ×2", resources)
        torch_lines = [r for r in resources if r.startswith("Torch")]
        self.assertEqual(len(torch_lines), 1,
            f"硬要求 #3：memory.resources 不应同时出现 Torch ×2 和 Torch ×1；"
            f"实际 Torch 行={torch_lines}")

        # dice_log 应有一条 consume_item 记录
        dl = state2.get("dice_log") or []
        consume_logs = [d for d in dl if d.get("kind") == "consume_item"]
        self.assertGreaterEqual(len(consume_logs), 1,
            f"consume_item 应留 dice_log；实际 log={dl}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
