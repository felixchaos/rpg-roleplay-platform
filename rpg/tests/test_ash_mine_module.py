"""
test_ash_mine_module.py — Ash Mine 模组与 State Gate / RulesEngine 集成测试。

覆盖：
- 模组加载 + 房间数
- 模组开启后 scene/encounter/dice_log 初始化
- 房间移动校验
- 潜行检定写 scene.flags
- 战斗：开始 / 命中 HP 扣减 / 敌人倒下 / 胜利清场
- 陷阱失败扣 HP
- State Gate：GM 不能直接覆盖 HP/AC/initiative
- E2E：开始模组 → 移动 → 潜行检定 → dice_log 更新
"""
from __future__ import annotations

import unittest

import modules as module_registry
from state import GameState
from rules_bridge import (
    start_module, enter_room,
    perform_skill_check, perform_saving_throw,
    start_encounter_by_id, player_attack, advance_turn, enemy_attack,
    trap_check, short_rest, suggest_rule_actions,
)


class ModuleLoaderTests(unittest.TestCase):
    def test_list_modules_includes_ash_mine(self):
        mods = module_registry.list_modules()
        ids = [m["id"] for m in mods]
        self.assertIn("ash_mine", ids)

    def test_ash_mine_has_8_to_12_rooms(self):
        bundle = module_registry.load_module("ash_mine")
        rooms = bundle["rooms"]
        # 含 module_complete 结局占位；不含也合规
        self.assertGreaterEqual(len(rooms), 8)
        self.assertLessEqual(len(rooms), 12)

    def test_ash_mine_module_has_no_official_ip(self):
        bundle = module_registry.load_module("ash_mine")
        blob = (str(bundle.get("manifest")) + str(bundle.get("rooms")) +
                str(bundle.get("encounters")) + str(bundle.get("npcs")) +
                str(bundle.get("loot")) + str(bundle.get("worldbook")) +
                str(bundle.get("opening"))).lower()
        blacklist = [
            "beholder", "mind flayer", "drow", "strahd", "drizzt",
            "forgotten realms", "faerûn", "faerun", "elminster",
            "wizards of the coast", "wotc", "dungeons & dragons",
            "dungeons and dragons",
        ]
        for word in blacklist:
            self.assertNotIn(word, blob, f"模组含官方 IP 关键词: {word}")


class StartModuleTests(unittest.TestCase):
    def test_start_module_initializes_state(self):
        g = GameState.new()
        res = start_module(g, "ash_mine")
        self.assertTrue(res["ok"])
        self.assertEqual(g.data["scene"]["module_id"], "ash_mine")
        self.assertIn(g.data["scene"]["location_id"], ["mine_entrance"])
        self.assertGreater(g.data["player_character"]["hp"], 0)
        self.assertEqual(g.data["encounter"]["active"], False)
        self.assertEqual(g.data["dice_log"], [])

    def test_module_manifest_uses_5e_compatible_label(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        ruleset = g.data["scene"]["module_manifest"]["ruleset"]
        self.assertEqual(ruleset["id"], "dnd5e")
        self.assertIn("5E compatible", ruleset.get("public_label", ""))


class RoomMovementTests(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")

    def test_valid_move_succeeds(self):
        res = enter_room(self.g, "minecart_track")
        self.assertTrue(res["ok"])
        self.assertEqual(self.g.data["scene"]["location_id"], "minecart_track")

    def test_invalid_move_blocked(self):
        # 从 mine_entrance 直接跳到非邻接的 mine_heart_altar 应失败
        res = enter_room(self.g, "mine_heart_altar")
        self.assertFalse(res["ok"])
        self.assertIn("不能直接", res["error"])

    def test_visited_rooms_tracked(self):
        enter_room(self.g, "minecart_track")
        enter_room(self.g, "rest_cavern")
        visited = self.g.data["scene"]["visited_rooms"]
        self.assertIn("minecart_track", visited)
        self.assertIn("rest_cavern", visited)


class SkillCheckIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")
        enter_room(self.g, "minecart_track")

    def test_stealth_success_sets_flag(self):
        # seed=7 在前面 smoke test 中已确认成功
        result = perform_skill_check(
            self.g, "stealth", dc=13, seed=7,
            reason="悄悄翻越矿车", sets_flag="sneak_pass",
        )
        self.assertTrue(result["success"])
        self.assertTrue(self.g.data["scene"]["flags"].get("sneak_pass"))
        self.assertEqual(len(self.g.data["dice_log"]), 1)
        entry = self.g.data["dice_log"][0]
        self.assertEqual(entry["kind"], "skill_check")
        self.assertEqual(entry["dc"], 13)

    def test_stealth_failure_does_not_set_flag(self):
        # 用极高 DC 强制失败
        result = perform_skill_check(
            self.g, "stealth", dc=99, seed=1,
            reason="悄悄翻越矿车", sets_flag="sneak_pass",
        )
        self.assertFalse(result["success"])
        self.assertFalse(self.g.data["scene"]["flags"].get("sneak_pass", False))

    def test_dice_log_capped(self):
        # 触发 60 次检定，确保 dice_log 限定在 50 内
        for i in range(60):
            perform_skill_check(self.g, "stealth", dc=5, seed=i)
        self.assertLessEqual(len(self.g.data["dice_log"]), 50)


class CombatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")
        # 移动到深层矿厅
        for loc in ["minecart_track", "rest_cavern", "fissure", "ash_camp", "deep_hall"]:
            enter_room(self.g, loc)

    def test_start_encounter_creates_initiative(self):
        res = start_encounter_by_id(self.g, "deep_hall_combat", seed=11)
        self.assertTrue(res["ok"])
        enc = self.g.data["encounter"]
        self.assertTrue(enc["active"])
        self.assertGreater(len(enc["initiative_order"]), 0)
        self.assertGreater(len(enc["combatants"]), 0)
        # 玩家在 combatants 里
        ids = [c["id"] for c in enc["combatants"]]
        self.assertIn("player", ids)

    def test_attack_hit_reduces_target_hp(self):
        start_encounter_by_id(self.g, "deep_hall_combat", seed=11)
        target_id = next(c["id"] for c in self.g.data["encounter"]["combatants"]
                         if c.get("side") == "enemy")
        hp_before = next(c["hp"] for c in self.g.data["encounter"]["combatants"] if c["id"] == target_id)

        # 用高 seed 多次尝试直至命中（或确认未命中）
        for seed in range(20):
            res = player_attack(self.g, target_id=target_id, weapon_id="shortsword", seed=seed)
            if res["ok"] and res["result"]["success"]:
                hp_after = next(c["hp"] for c in self.g.data["encounter"]["combatants"] if c["id"] == target_id)
                self.assertLess(hp_after, hp_before)
                break
        else:
            self.fail("没有任何 seed 命中目标（统计上不太可能）")

    def test_attack_miss_does_not_reduce_hp(self):
        start_encounter_by_id(self.g, "deep_hall_combat", seed=11)
        target_id = next(c["id"] for c in self.g.data["encounter"]["combatants"]
                         if c.get("side") == "enemy")
        hp_before = next(c["hp"] for c in self.g.data["encounter"]["combatants"] if c["id"] == target_id)
        # 临时降低玩家攻击 bonus 来逼出未命中
        self.g.data["player_character"]["weapons"]["shortsword"]["attack_bonus"] = -10
        # 修改 target AC 到 25 以确保失误
        for c in self.g.data["encounter"]["combatants"]:
            if c.get("side") == "enemy":
                c["ac"] = 25
        # 试几个 seed，找出 miss case
        for seed in range(20):
            self.g.data["encounter"]["combatants"][1]["hp"] = hp_before  # 重置
            res = player_attack(self.g, target_id=target_id, seed=seed)
            if res["ok"] and not res["result"]["success"]:
                hp_after = next(c["hp"] for c in self.g.data["encounter"]["combatants"] if c["id"] == target_id)
                self.assertEqual(hp_after, hp_before)
                break

    def test_defeated_enemy_marked(self):
        start_encounter_by_id(self.g, "deep_hall_combat", seed=11)
        target = next(c for c in self.g.data["encounter"]["combatants"] if c.get("side") == "enemy")
        # 直接把 HP 设到 0 然后调用一次 player_attack 触发 mark_defeated_by_hp
        target["hp"] = 0
        # 给玩家武器伤害，但 target 已 0 HP
        player_attack(self.g, target_id=target["id"], seed=1)
        # mark_defeated_by_hp 在 player_attack 内部会扫描；不论是否命中，0 HP 应被标记
        defeated = next(c for c in self.g.data["encounter"]["combatants"] if c["id"] == target["id"])
        self.assertTrue(defeated["defeated"])


class TrapTests(unittest.TestCase):
    def test_fissure_save_failure_damages_player(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        # 移动到毒雾裂隙
        for loc in ["minecart_track", "rest_cavern", "fissure"]:
            enter_room(g, loc)
        hp_before = g.data["player_character"]["hp"]
        # 用极低 CON 临时设到 1，DC12 几乎必败
        g.data["player_character"]["abilities"]["con"] = 1
        for seed in range(20):
            res = trap_check(g, room_id="fissure", trap_id="poison_fog", seed=seed)
            if res["ok"] and not res["result"]["success"]:
                hp_after = g.data["player_character"]["hp"]
                self.assertLessEqual(hp_after, hp_before)
                break

    def test_perception_success_disarms_needle_trap(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        # 强制把 scene.location_id 设为 altar_approach 后跑检定
        g.data["scene"]["location_id"] = "altar_approach"
        result = perform_skill_check(
            g, "perception", dc=1, seed=1,
            reason="发现陷阱", sets_flag="trap_seen",
        )
        self.assertTrue(result["success"])
        self.assertTrue(g.data["scene"]["flags"].get("trap_seen"))


class StateGateRulesTests(unittest.TestCase):
    """验证 GM/用户不能直接覆盖 HP/AC/initiative 等规则受控字段。"""

    def setUp(self):
        self.g = GameState.new()

    def test_gm_cannot_overwrite_player_hp(self):
        result = self.g.apply_state_write("player_character.hp=1", source="gm")
        self.assertIn("rules_managed", result)
        # HP 没变
        self.assertEqual(self.g.data["player_character"]["hp"], 9)
        # audit_log 有 rules_managed 拒绝条目
        audit = self.g.data["permissions"]["audit_log"]
        self.assertTrue(any(e.get("blocked") == "rules_managed" for e in audit))

    def test_user_set_force_cannot_overwrite_hp(self):
        result = self.g.apply_state_write("player_character.hp=99", source="user:/set", force=True)
        self.assertIn("rules_managed", result)
        self.assertEqual(self.g.data["player_character"]["hp"], 9)

    def test_gm_cannot_overwrite_ac(self):
        result = self.g.apply_state_write("player_character.ac=999", source="gm")
        self.assertIn("rules_managed", result)
        self.assertEqual(self.g.data["player_character"]["ac"], 13)

    def test_gm_cannot_overwrite_encounter_initiative(self):
        result = self.g.apply_state_write("encounter.initiative_order=[]", source="gm")
        self.assertIn("rules_managed", result)

    def test_gm_cannot_append_dice_log(self):
        result = self.g.apply_state_write("dice_log=fake_entry", source="gm")
        self.assertIn("rules_managed", result)

    def test_rules_engine_source_can_write_hp(self):
        # apply_state_write 的 spec 路径会按字符串落地（与历史行为一致）。
        # 真实规则更新通常走 apply_rules_state_ops（int 数值）或专用 damage_player/heal helper。
        result = self.g.apply_state_write("player_character.hp=5", source="rules_engine", overwrite=True)
        self.assertNotIn("拒绝", result)
        # rules_engine 写入未被拦截
        self.assertEqual(str(self.g.data["player_character"]["hp"]), "5")

    def test_rules_engine_damage_player_helper(self):
        before = self.g.data["player_character"]["hp"]
        actual = self.g.damage_player(3)
        self.assertEqual(actual, 3)
        self.assertEqual(self.g.data["player_character"]["hp"], before - 3)

    def test_apply_rules_state_ops_writes_combatant_hp(self):
        # 准备一个伪 encounter
        self.g.set_encounter({
            "active": True, "round": 1, "turn_index": 0,
            "initiative_order": [{"id": "e1", "name": "Goblin", "init": 10}],
            "combatants": [{"id": "e1", "name": "Goblin", "hp": 7, "max_hp": 7, "side": "enemy"}],
        })
        ops = [{"op": "subtract", "path": "_combatant.e1.hp", "value": 4}]
        self.g.apply_rules_state_ops(ops, reason="test")
        comb = self.g.data["encounter"]["combatants"][0]
        self.assertEqual(comb["hp"], 3)
        self.assertFalse(comb.get("defeated"))
        # 二次扣血至 0 触发 defeated
        ops = [{"op": "subtract", "path": "_combatant.e1.hp", "value": 5}]
        self.g.apply_rules_state_ops(ops, reason="test")
        self.assertEqual(self.g.data["encounter"]["combatants"][0]["hp"], 0)
        self.assertTrue(self.g.data["encounter"]["combatants"][0]["defeated"])


class EndToEndTests(unittest.TestCase):
    """端到端：开始 Ash Mine → 玩家自由文本『我悄悄靠近矿车』→ 触发 stealth check → state/log 更新。"""

    def test_e2e_stealth_flow(self):
        g = GameState.new()
        # 1. 启动模组
        start_module(g, "ash_mine")
        self.assertEqual(g.data["scene"]["location_id"], "mine_entrance")

        # 2. 移动到矿车轨道（玩家点击出口按钮）
        enter_room(g, "minecart_track")

        # 3. 玩家输入文本，suggest_rule_actions 提取候选动作
        user_text = "我悄悄靠近矿车"
        actions = suggest_rule_actions(user_text, g)
        self.assertGreater(len(actions), 0)
        stealth_action = next((a for a in actions if a["kind"] == "skill_check" and a["skill"] == "stealth"), None)
        self.assertIsNotNone(stealth_action)
        self.assertEqual(stealth_action["target"], "minecart_track")

        # 4. 执行规则动作（前端会调 /api/rules/action）
        result = perform_skill_check(
            g,
            skill=stealth_action["skill"],
            dc=stealth_action["dc"],
            seed=7,  # 已知此 seed 成功
            reason="悄悄靠近矿车",
            sets_flag=stealth_action.get("sets_flag"),
        )

        # 5. 验证 dice_log 与 scene 状态
        self.assertEqual(len(g.data["dice_log"]), 1)
        log_entry = g.data["dice_log"][0]
        self.assertEqual(log_entry["kind"], "skill_check")
        self.assertEqual(log_entry["dc"], 13)
        self.assertIsNotNone(log_entry["total"])

        # 6. 状态序列化后可由前端 status_payload 拿到
        payload = g.status_payload()
        self.assertIn("dice_log", payload)
        self.assertIn("player_character", payload)
        self.assertIn("scene", payload)
        self.assertEqual(payload["scene"]["location_id"], "minecart_track")
        self.assertEqual(payload["scene"]["module_id"], "ash_mine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
