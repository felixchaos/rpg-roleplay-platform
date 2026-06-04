"""test_coc_engine.py — CoC 7E RulesEngine 单元测试。"""
from __future__ import annotations

import unittest
import sys
sys.path.insert(0, ".")

from rules import get_engine
from rules.coc.combat import apply_sanity_loss


class CoCEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = get_engine("coc")
        self.char = self.engine.make_default_character("Harvey Walters")

    def test_engine_info(self):
        info = self.engine.info()
        self.assertEqual(info["id"], "coc")
        self.assertIn("Cthulhu", info["label"])

    def test_character_creation(self):
        c = self.char
        self.assertEqual(c["name"], "Harvey Walters")
        self.assertIn("记者", c["occupation"])
        self.assertEqual(c["hp"], 15)
        self.assertEqual(c["max_hp"], 15)
        self.assertEqual(c["san"], 45)
        self.assertGreater(len(c["skills"]), 10)
        self.assertIn("str", c["characteristics"])
        self.assertIn("app", c["characteristics"])
        self.assertEqual(c["proficiency_bonus"], 0)
        self.assertEqual(c["ac"], 0)

    def test_skill_check_success(self):
        r = self.engine.skill_check(self.char, "library_use", 0, seed=42)
        self.assertEqual(r.kind, "skill_check")
        self.assertIn(r.extra["level"], ("failure", "regular_success", "hard_success",
                                          "extreme_success", "critical_success", "fumble"))

    def test_skill_check_critical(self):
        r = self.engine.skill_check(self.char, "library_use", 60, seed=1)
        if r.roll["total"] == 1:
            self.assertEqual(r.extra["level"], "critical_success")

    def test_characteristic_roll(self):
        r = self.engine.saving_throw(self.char, "pow", 5, seed=42)
        self.assertEqual(r.kind, "saving_throw")
        self.assertIn(r.success, (True, False))

    def test_characteristic_roll_difficulty_scaling(self):
        # Regular (dc=5) should have higher threshold than extreme (dc=1)
        r_reg = self.engine.saving_throw(self.char, "pow", 5, seed=1)
        r_ext = self.engine.saving_throw(self.char, "pow", 1, seed=1)
        # Higher dc = easier (higher threshold)
        self.assertGreater(r_reg.dc, r_ext.dc)

    def test_initiative(self):
        c1 = {"name": "Alice", "characteristics": {"dex": 60}}
        c2 = {"name": "Bob", "characteristics": {"dex": 45}}
        order = self.engine.initiative([c1, c2], seed=42)
        self.assertEqual(order[0]["name"], "Alice")

    def test_encounter_start(self):
        enemy = self.engine.build_combatant("deep_one")
        enc = self.engine.start_encounter([self.char], [enemy], seed=42)
        self.assertTrue(enc["active"])
        self.assertEqual(enc["round"], 1)

    def test_encounter_next_turn(self):
        enemy = self.engine.build_combatant("deep_one")
        enc = self.engine.start_encounter([self.char], [enemy], seed=42)
        prev_index = enc["turn_index"]
        prev_round = enc["round"]
        self.engine.next_turn(enc)
        self.assertTrue(enc["turn_index"] != prev_index or enc["round"] != prev_round)

    def test_attack_roll(self):
        r = self.engine.attack_roll(
            self.char, {"name": "Target", "hp": 10, "armor": 0},
            35, "1d6", seed=42, attacker_name="Test", weapon_name="拳"
        )
        self.assertEqual(r.kind, "attack")
        self.assertIn(r.success, (True, False))

    def test_apply_damage(self):
        target = {"name": "Enemy", "hp": 10, "max_hp": 10, "armor": 2}
        dmg = self.engine.apply_damage(target, 7)
        self.assertEqual(target["hp"], 5)
        self.assertTrue(dmg.extra["is_major_wound"])

    def test_first_aid_success(self):
        char = {"name": "Wounded", "hp": 1, "max_hp": 15, "skills": {"first_aid": 90}}
        r = self.engine.short_rest(char, seed=42)
        self.assertEqual(r.kind, "short_rest")
        if r.success:
            self.assertGreaterEqual(char["hp"], 1)

    def test_sanity_loss(self):
        char = {"name": "Victim", "san": 50, "max_san": 99}
        result = apply_sanity_loss(char, "1/1d10", success=False, seed=42)
        self.assertGreater(result["san_lost"], 0)
        self.assertLessEqual(char["san"], 50)

    def test_sanity_no_loss_on_success(self):
        char = {"name": "Victim", "san": 50, "max_san": 99}
        result = apply_sanity_loss(char, "0/1d6", success=True, seed=42)
        self.assertEqual(result["san_lost"], 0)
        self.assertEqual(char["san"], 50)

    def test_sanity_indefinite(self):
        char = {"name": "Victim", "san": 19, "max_san": 20}
        result = apply_sanity_loss(char, "5/5", success=False, seed=42)
        self.assertTrue(result["indefinite"])
        self.assertTrue(result["temporary"])

    def test_monster_stat_block(self):
        block = self.engine.get_stat_block("deep_one")
        self.assertEqual(block["name"], "深潜者（Deep One）")
        self.assertGreater(block["hp"], 0)
        self.assertGreater(len(block["attacks"]), 0)
        self.assertIn("深潜者", block["notes"])

    def test_build_combatant(self):
        comb = self.engine.build_combatant("deep_one")
        self.assertEqual(comb["side"], "enemy")
        self.assertEqual(comb["hp"], comb["max_hp"])
        self.assertFalse(comb["defeated"])

    def test_list_stat_blocks(self):
        blocks = self.engine.list_stat_blocks()
        self.assertIn("deep_one", blocks)

    def test_encounter_resolved(self):
        enemy = self.engine.build_combatant("deep_one")
        enc = self.engine.start_encounter([self.char], [enemy], seed=42)
        resolved, outcome = self.engine.is_encounter_resolved(enc)
        self.assertFalse(resolved)
        # Defeat all enemies
        for c in enc["combatants"]:
            if c.get("side") == "enemy":
                c["defeated"] = True
        resolved, outcome = self.engine.is_encounter_resolved(enc)
        self.assertTrue(resolved)
        self.assertEqual(outcome, "party_victory")

    def test_ability_modifier_zero(self):
        self.assertEqual(self.engine.ability_modifier(18), 0)

    def test_proficiency_bonus_zero(self):
        self.assertEqual(self.engine.proficiency_bonus(5), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
