"""战斗 lifecycle 三处修复:
- victory_flag 在 enemy_attack 路径也写(共用 _finalize_encounter)。
- enemy_attack 不能攻击已 defeated 目标。
- 重复 combat_start 进行中的战斗被拒(不重置敌人血量/复活)。
"""
import copy
import unittest
from pathlib import Path

from rules_bridge import start_encounter_by_id, start_module
from state import DEFAULT_STATE, GameState

CB = (Path(__file__).resolve().parents[2] / "rules_bridge" / "combat.py").read_text(encoding="utf-8")


class CombatStartGuard(unittest.TestCase):
    def test_repeated_start_rejected(self):
        g = GameState(copy.deepcopy(DEFAULT_STATE))
        start_module(g, "ash_mine")
        r1 = start_encounter_by_id(g, "boss_altar_combat", seed=1)
        self.assertTrue(r1.get("ok"), r1)
        # 进行中再次 start 同一战斗 → 必须被拒(否则 boss 满血复活、进度重置)
        r2 = start_encounter_by_id(g, "boss_altar_combat", seed=1)
        self.assertFalse(r2.get("ok"), "重复 combat_start 未被拒,会重置进行中的战斗")


class FinalizeSharedAndDefeatedGuard(unittest.TestCase):
    def test_victory_flag_written_in_both_paths(self):
        # _finalize_encounter 共用,player_attack 与 enemy_attack 都调用它
        self.assertIn("def _finalize_encounter(", CB)
        # enemy_attack 结算调 _finalize_encounter(原来漏写 victory_flag)
        i = CB.find("def enemy_attack(")
        end = CB.find("\ndef ", i + 1)
        ea = CB[i:end]
        self.assertIn("_finalize_encounter(state, encounter, outcome)", ea,
                      "enemy_attack 未用共用 finalize → victory_flag 漏写")
        # player_attack 也用
        i2 = CB.find("def player_attack(")
        end2 = CB.find("\ndef ", i2 + 1)
        pa = CB[i2:end2]
        self.assertIn("_finalize_encounter(state, encounter, outcome)", pa)

    def test_enemy_attack_rejects_defeated_target(self):
        i = CB.find("def enemy_attack(")
        end = CB.find("\ndef ", i + 1)
        ea = CB[i:end]
        self.assertIn('target.get("defeated")', ea,
                      "enemy_attack 缺已倒下目标守卫")

    def test_finalize_writes_victory_flag(self):
        i = CB.find("def _finalize_encounter(")
        end = CB.find("\n\ndef ", i + 1)
        fn = CB[i:end]
        self.assertIn("set_scene_flag", fn)
        self.assertIn('outcome == "victory"', fn)


if __name__ == "__main__":
    unittest.main()
