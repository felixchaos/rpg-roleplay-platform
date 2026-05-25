"""
test_rules_engine.py — 5E-compatible RulesEngine 单元测试。

覆盖：
- 掷骰表达式解析与确定性
- 优势/劣势
- 属性修正与熟练加值
- 技能检定 & 豁免
- 攻击命中/未命中/暴击/伤害
- 先攻顺序与战斗推进
- 短休
"""
from __future__ import annotations

import unittest

from rules.dice import parse_expression, roll
from rules.engine import RulesEngine
from rules.dnd5e.ruleset import ability_modifier, proficiency_bonus
from rules.dnd5e.monsters import get_stat_block, build_combatant


class DiceTests(unittest.TestCase):
    def test_parse_simple(self):
        self.assertEqual(parse_expression("1d20+3"), (1, 20, 3))
        self.assertEqual(parse_expression("2d6"), (2, 6, 0))
        self.assertEqual(parse_expression("d20-1"), (1, 20, -1))
        self.assertEqual(parse_expression(" 4d8 + 5 "), (4, 8, 5))

    def test_parse_invalid(self):
        with self.assertRaises(ValueError):
            parse_expression("hello")
        with self.assertRaises(ValueError):
            parse_expression("0d6")
        with self.assertRaises(ValueError):
            parse_expression("1d0")

    def test_deterministic_with_seed(self):
        r1 = roll("1d20+3", seed=42)
        r2 = roll("1d20+3", seed=42)
        self.assertEqual(r1.total, r2.total)
        self.assertEqual(r1.rolls, r2.rolls)

    def test_advantage_chooses_higher(self):
        # 同 seed 下不带优势 vs 优势：优势的总值 >= 普通
        for seed in (1, 2, 3, 4, 5):
            r_norm = roll("1d20", seed=seed)
            r_adv = roll("1d20", seed=seed, advantage=True)
            self.assertGreaterEqual(r_adv.total, r_norm.total,
                                    f"seed={seed} adv={r_adv.total} norm={r_norm.total}")

    def test_disadvantage_chooses_lower(self):
        for seed in (1, 2, 3, 4, 5):
            r_norm = roll("1d20", seed=seed)
            r_dis = roll("1d20", seed=seed, disadvantage=True)
            self.assertLessEqual(r_dis.total, r_norm.total)

    def test_modifier_applied(self):
        r = roll("2d6+5", seed=1)
        self.assertEqual(r.total, sum(r.rolls) + 5)
        self.assertEqual(r.modifier, 5)


class RulesetMathTests(unittest.TestCase):
    def test_ability_modifier(self):
        self.assertEqual(ability_modifier(10), 0)
        self.assertEqual(ability_modifier(14), 2)
        self.assertEqual(ability_modifier(8), -1)
        self.assertEqual(ability_modifier(20), 5)
        self.assertEqual(ability_modifier(3), -4)

    def test_proficiency_bonus(self):
        self.assertEqual(proficiency_bonus(1), 2)
        self.assertEqual(proficiency_bonus(4), 2)
        self.assertEqual(proficiency_bonus(5), 3)
        self.assertEqual(proficiency_bonus(9), 4)
        self.assertEqual(proficiency_bonus(20), 6)


class SkillCheckTests(unittest.TestCase):
    def setUp(self):
        self.engine = RulesEngine()
        self.char = self.engine.make_default_character("Cinder", level=1)

    def test_skill_check_returns_structured_result(self):
        # 强制成功（DC 1）和失败（DC 30）来检查 success 字段语义
        success_res = self.engine.skill_check(self.char, "stealth", dc=1, seed=10)
        self.assertTrue(success_res.success)
        self.assertIsNotNone(success_res.dc)
        self.assertEqual(success_res.kind, "skill_check")
        self.assertGreater(len(success_res.gm_facts), 0)

        fail_res = self.engine.skill_check(self.char, "stealth", dc=30, seed=10)
        self.assertFalse(fail_res.success)

    def test_skill_modifier_includes_proficiency(self):
        # stealth 用 DEX 14 (mod +2)，熟练加 +2 → 总 +4
        res = self.engine.skill_check(self.char, "stealth", dc=13, seed=99)
        self.assertEqual(res.extra["modifier"], 4)
        self.assertEqual(res.roll["modifier"], 4)

    def test_saving_throw(self):
        res = self.engine.saving_throw(self.char, "con", dc=12, seed=5)
        self.assertEqual(res.kind, "saving_throw")
        self.assertIn(res.success, (True, False))


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.engine = RulesEngine()
        self.player = self.engine.make_default_character("Cinder", level=1)
        self.player["id"] = "player"

    def test_initiative_sorts_descending(self):
        enemies = [build_combatant("ash_skulker", instance_id="g1"),
                   build_combatant("ash_skulker", instance_id="g2")]
        combatants = [{"id": "player", "name": "Cinder", "side": "party",
                       "abilities": self.player["abilities"]}, *enemies]
        order = self.engine.initiative(combatants, seed=7)
        inits = [o["init"] for o in order]
        self.assertEqual(inits, sorted(inits, reverse=True))

    def test_attack_hit_reduces_target_hp(self):
        target = build_combatant("ash_skulker")
        result = self.engine.attack_roll(
            attacker=self.player, target=target,
            attack_bonus=10, damage_expr="1d6+2",  # 高 bonus 保证命中
            seed=1,
        )
        self.assertTrue(result.success)
        self.assertIsNotNone(result.damage)
        # 验证 state_op 正确生成
        ops = [op.to_dict() for op in result.state_ops]
        self.assertTrue(any(op["op"] == "subtract" and "hp" in op["path"] for op in ops))

    def test_attack_miss_no_damage(self):
        target = {"id": "x", "name": "Iron Wall", "ac": 30, "hp": 10, "max_hp": 10}
        result = self.engine.attack_roll(
            attacker=self.player, target=target,
            attack_bonus=0, damage_expr="1d6",
            seed=2,
        )
        # 在大多数 seed 下，AC 30 无法命中（natural 20 除外）。
        # 至少检查 damage 与 success 字段语义对齐：未命中时 damage 应为 None。
        if not result.success:
            self.assertIsNone(result.damage)
        else:
            # 必为 nat20 暴击
            self.assertEqual(result.roll["rolls"][0], 20)

    def test_encounter_full_flow(self):
        enemies = [build_combatant("ash_skulker", instance_id="g1", name="Skulker")]
        encounter = self.engine.start_encounter([self.player], enemies, seed=11)
        self.assertTrue(encounter["active"])
        self.assertEqual(len(encounter["initiative_order"]), 2)
        # 推进回合
        before_round = encounter["round"]
        self.engine.next_turn(encounter)
        # 至少 turn_index 或 round 之一被推进
        changed = (encounter["round"] > before_round) or (encounter["turn_index"] != 0)
        self.assertTrue(changed)

        # 模拟连续命中直到敌人倒下
        for _ in range(10):
            target = next((c for c in encounter["combatants"] if c["side"] == "enemy" and not c.get("defeated")), None)
            if not target:
                break
            res = self.engine.attack_roll(
                attacker=self.player, target=target,
                attack_bonus=15, damage_expr="2d6+3", seed=42,
            )
            # 应用 state_ops 到 encounter combatants
            for op in res.state_ops:
                op_dict = op.to_dict()
                if op_dict["op"] == "subtract":
                    target["hp"] = max(0, target["hp"] - int(op_dict["value"]))
        self.engine.mark_defeated_by_hp(encounter)
        resolved, outcome = self.engine.is_encounter_resolved(encounter)
        self.assertTrue(resolved)
        self.assertEqual(outcome, "victory")


class ShortRestTests(unittest.TestCase):
    def test_short_rest_heals_within_max_hp(self):
        engine = RulesEngine()
        char = engine.make_default_character("Cinder", level=1)
        char["hp"] = 1
        res = engine.short_rest(char, hit_die="1d8", seed=3)
        self.assertGreaterEqual(char["hp"], 1)
        self.assertLessEqual(char["hp"], char["max_hp"])
        self.assertEqual(res.kind, "short_rest")

    def test_short_rest_caps_at_max_hp(self):
        engine = RulesEngine()
        char = engine.make_default_character("Cinder", level=1)
        char["max_hp"] = 10
        char["hp"] = 9
        engine.short_rest(char, hit_die="1d8", seed=4)
        self.assertLessEqual(char["hp"], char["max_hp"])


class MonsterStatBlockTests(unittest.TestCase):
    def test_all_blocks_have_required_fields(self):
        from rules.dnd5e.monsters import STAT_BLOCKS
        required = {"name", "max_hp", "hp", "ac", "abilities", "attacks"}
        for blk_id, blk in STAT_BLOCKS.items():
            for field in required:
                self.assertIn(field, blk, f"{blk_id} 缺少字段 {field}")
            # 每个怪物至少有一个攻击
            self.assertGreater(len(blk["attacks"]), 0, f"{blk_id} 没有攻击动作")
            # 不引用官方 IP：英文名禁止包含官方品牌词
            blacklist = ["beholder", "mind flayer", "githyanki", "githzerai", "drow", "strahd",
                         "drizzt", "elminster", "tiamat", "bahamut", "vecna", "forgotten realms"]
            name_lower = (blk.get("name", "") + " " + blk.get("name_cn", "")).lower()
            for word in blacklist:
                self.assertNotIn(word, name_lower, f"{blk_id} 名称含官方 IP 关键词: {word}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
