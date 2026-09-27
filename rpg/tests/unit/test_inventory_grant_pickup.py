"""
test_inventory_grant_pickup.py — 库存"授予"机制（弥补"有消耗无授予"结构缺口）。

覆盖 4 个游戏内场景 + Bug 3 匹配修复：
  ① GM 叙事获得物品 → resources 与 canonical 一致 → 能 consume
  ② 捡 ash_mine 的 mine_core / healing_draught → 进背包 → 可用 / 可通关
  ③ 多把同类武器共存时"用长剑"消耗正确那把（Bug 3）
  ④ full_access 与 pending 审批两路径物品一致不丢
"""
from __future__ import annotations

import unittest

from rules.dnd5e.character import (
    consume_inventory_item,
    find_inventory_item,
    grant_inventory_item,
    normalize_item_alias,
)
from rules_bridge import (
    enter_room,
    grant_item_action,
    parse_pickup_intent,
    pickup_loot_action,
    start_module,
)
from state import GameState


# ── canonical grant 纯函数 ────────────────────────────────────────


class GrantInventoryItem(unittest.TestCase):
    def _char(self):
        return {"inventory": [
            {"id": "torch", "name": "Torch", "qty": 2, "kind": "gear"},
        ]}

    def test_grant_new_item_creates_entry(self):
        c = self._char()
        r = grant_inventory_item(c, "antidote", name="解毒剂", qty=2, kind="consumable")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["created"])
        self.assertEqual(r["qty_after"], 2)
        item = find_inventory_item(c, "antidote")
        self.assertIsNotNone(item)
        self.assertEqual(item["qty"], 2)
        self.assertEqual(item["kind"], "consumable")

    def test_grant_existing_accumulates_by_id(self):
        c = self._char()
        r = grant_inventory_item(c, "torch", name="火把", qty=3)
        self.assertTrue(r["ok"])
        self.assertFalse(r["created"])
        self.assertEqual(r["qty_before"], 2)
        self.assertEqual(r["qty_after"], 5)
        # 仍只有一条 torch（按 id 判重，不重复建条目）
        torches = [i for i in c["inventory"] if i["id"] == "torch"]
        self.assertEqual(len(torches), 1)
        self.assertEqual(torches[0]["qty"], 5)

    def test_grant_qty_zero_rejected(self):
        c = self._char()
        self.assertFalse(grant_inventory_item(c, "torch", qty=0)["ok"])
        self.assertFalse(grant_inventory_item(c, "torch", qty=-1)["ok"])

    def test_grant_empty_id_rejected(self):
        self.assertFalse(grant_inventory_item({}, "", qty=1)["ok"])


# ── Bug 3：双向子串误命中修复 ─────────────────────────────────────


class Bug3FuzzyMatch(unittest.TestCase):
    def test_single_char_alias_no_substring_match(self):
        # "长剑" 不应被单字别名 "剑" 吞掉错配到 shortsword
        self.assertEqual(normalize_item_alias("长剑"), "longsword")
        self.assertEqual(normalize_item_alias("剑"), "shortsword")  # 精确仍可

    def test_longsword_not_misrouted_to_shortsword(self):
        c = {"inventory": [{"id": "shortsword", "name": "Shortsword", "qty": 1, "kind": "weapon"}]}
        # 只有 shortsword 时，"长剑" 找不到（不再误命中 shortsword）
        self.assertIsNone(find_inventory_item(c, "长剑"))

    def test_multiple_weapons_consume_correct_one(self):
        """③ 多把同类武器共存：'用长剑' 消耗 longsword，不动 shortsword。"""
        c = {"inventory": [
            {"id": "shortsword", "name": "Shortsword", "qty": 1, "kind": "weapon"},
            {"id": "longsword", "name": "长剑", "qty": 1, "kind": "weapon"},
        ]}
        r = consume_inventory_item(c, "长剑", 1)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["item_id"], "longsword")
        # shortsword 原封不动
        ss = find_inventory_item(c, "shortsword")
        self.assertIsNotNone(ss)
        self.assertEqual(ss["qty"], 1)

    def test_short_alias_does_not_swallow_other_item(self):
        # 单字不参与子串：背包同时有 healing_draught 和 antidote，
        # "解毒剂" 必须命中 antidote 而非 healing_draught
        c = {"inventory": [
            {"id": "healing_draught", "name": "Healing Draught", "qty": 1, "kind": "consumable"},
            {"id": "antidote", "name": "解毒剂", "qty": 1, "kind": "consumable"},
        ]}
        self.assertEqual(find_inventory_item(c, "解毒剂")["id"], "antidote")
        self.assertEqual(find_inventory_item(c, "药剂")["id"], "healing_draught")


# ── ① GM 叙事获得 → 一致 → 可消耗 ────────────────────────────────


class GmGrantThenConsume(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")

    def test_grant_syncs_resources_and_consumable(self):
        res = grant_item_action(self.g, "antidote", name="解毒剂", qty=2,
                                kind="consumable", reason="GM：你拾得两瓶解毒剂")
        self.assertTrue(res["ok"], res)

        # canonical inventory 有
        inv = self.g.data["player_character"]["inventory"]
        anti = next((i for i in inv if i["id"] == "antidote"), None)
        self.assertIsNotNone(anti)
        self.assertEqual(anti["qty"], 2)

        # memory.resources 派生一致
        self.assertIn("解毒剂 ×2", self.g.data["memory"]["resources"])

        # dice_log 留痕
        dl = [d for d in self.g.data.get("dice_log", []) if d.get("kind") == "grant_item"]
        self.assertGreaterEqual(len(dl), 1)

        # 玩家能 consume（看得到也用得了）
        c = self.g.consume_inventory_item("解毒剂", 1)
        self.assertTrue(c["ok"], c)
        anti2 = next((i for i in self.g.data["player_character"]["inventory"]
                      if i["id"] == "antidote"), None)
        self.assertEqual(anti2["qty"], 1)
        self.assertIn("解毒剂 ×1", self.g.data["memory"]["resources"])


# ── ② 捡 loot → 进背包 → 可用 / 可通关 ──────────────────────────


class PickupLoot(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")

    def _goto(self, *room_ids):
        for rid in room_ids:
            res = enter_room(self.g, rid)
            self.assertTrue(res.get("ok"), res)

    def test_pickup_healing_draught_usable(self):
        # rest_cavern 有 healing_draught loot
        self._goto("shaft_lift", "rest_cavern")
        before = next((i for i in self.g.data["player_character"]["inventory"]
                       if i["id"] == "healing_draught"), {"qty": 0})
        before_qty = before.get("qty", 0)

        res = pickup_loot_action(self.g, "healing_draught")
        self.assertTrue(res["ok"], res)

        after = next(i for i in self.g.data["player_character"]["inventory"]
                     if i["id"] == "healing_draught")
        self.assertEqual(after["qty"], before_qty + 1)
        # 可用（consume）
        self.assertTrue(self.g.consume_inventory_item("healing_draught", 1)["ok"])

    def test_pickup_mine_core_into_backpack(self):
        # mine_heart_altar 有 mine_core（通关核心物品）
        self.g.set_scene_flag("bypass_fissure")  # 不影响主路径
        self._goto("shaft_lift", "rest_cavern", "fissure", "ash_camp",
                   "deep_hall", "altar_approach", "mine_heart_altar")
        res = pickup_loot_action(self.g, "mine_core")
        self.assertTrue(res["ok"], res)
        core = find_inventory_item(self.g.data["player_character"], "mine_core")
        self.assertIsNotNone(core, "mine_core 必须进背包")
        self.assertEqual(core["kind"], "artifact")  # 从 loot.json 目录补全 kind

    def test_pickup_marks_taken_no_respawn(self):
        self._goto("shaft_lift", "rest_cavern")
        self.assertTrue(pickup_loot_action(self.g, "healing_draught")["ok"])
        self.assertIn("healing_draught", self.g.data["scene"]["taken_loot"])
        # 离开再回来：loot 不应刷新
        self._goto("minecart_track", "rest_cavern")
        loot_ids = [l.get("item_id") for l in self.g.data["scene"]["current_room"]["loot"]]
        self.assertNotIn("healing_draught", loot_ids)
        # 重复拾取被拒
        self.assertFalse(pickup_loot_action(self.g, "healing_draught")["ok"])

    def test_pickup_nonexistent_in_room_fails(self):
        self._goto("shaft_lift", "rest_cavern")
        # mine_core 不在 rest_cavern
        self.assertFalse(pickup_loot_action(self.g, "mine_core")["ok"])

    def test_parse_pickup_intent_room_scoped(self):
        self._goto("shaft_lift", "rest_cavern")
        intents = parse_pickup_intent("我捡起矿工日志", self.g)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["item_id"], "miner_journal")
        # 房间里没有的东西不会被解析出来
        self.assertEqual(parse_pickup_intent("我捡起暗红矿核", self.g), [])


# ── ④ full_access vs pending 两路径物品一致不丢 ───────────────────


class GrantConsistentAcrossPermissionModes(unittest.TestCase):
    def test_grant_canonical_not_phantom_in_full_access(self):
        """full_access：grant 写 canonical inventory（非 resources 幽灵物品）。"""
        g = GameState.new()
        start_module(g, "ash_mine")
        g.data["permissions"]["mode"] = "full_access"
        grant_item_action(g, "silver_key", name="银钥匙", qty=1, kind="key_item")
        # canonical 有
        self.assertIsNotNone(find_inventory_item(g.data["player_character"], "silver_key"))
        # resources 是 canonical 的派生，能找到对应行
        self.assertTrue(any("银钥匙" in r for r in g.data["memory"]["resources"]))

    def test_pending_approval_does_not_erase_granted_item(self):
        """pending：read_only 下审批一个 memory.resources 写入后，
        sync_resources_from_inventory 从 canonical 重派生，
        刚 grant 的物品不被抹掉（Bug 3：pending 用 canonical 覆盖 resources）。"""
        g = GameState.new()
        start_module(g, "ash_mine")

        # GM 先 grant 一个新物品（canonical）
        grant_item_action(g, "silver_key", name="银钥匙", qty=1, kind="key_item")
        self.assertIsNotNone(find_inventory_item(g.data["player_character"], "silver_key"))

        # 切 read_only，GM 试图写一个不含银钥匙的 resources list → 入 pending
        g.data["permissions"]["mode"] = "read_only"
        bogus = ["Shortsword ×1", "Shortbow ×1"]
        out = g.apply_state_write_typed("memory.resources", bogus, source="gm")
        self.assertIn("待审", out)
        pw = g.data["permissions"]["pending_writes"][0]

        # 审批通过 → 触发 sync_resources_from_inventory 从 canonical 重派生
        g.approve_pending_write(id=pw["id"])

        # 关键：银钥匙仍在 resources（canonical 派生），未被 GM 的 bogus list 抹掉
        final = g.data["memory"]["resources"]
        self.assertTrue(any("银钥匙" in r for r in final),
                        f"审批后 grant 的银钥匙不应丢失；实际={final}")
        # canonical inventory 也仍在
        self.assertIsNotNone(find_inventory_item(g.data["player_character"], "silver_key"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
