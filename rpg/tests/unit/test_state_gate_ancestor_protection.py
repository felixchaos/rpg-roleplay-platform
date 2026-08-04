"""State Gate 祖先保护:写「含受保护子树的裸父节点」必须被拦,防 full_access 下 GM
整体覆盖父对象绕过叶子保护(改 HP/清战斗/清审计/自我提权)。"""
import unittest

from state.path_ops import (
    _write_path_hard_forbidden as hf,
)
from state.path_ops import (
    _write_path_rules_managed as rm,
)


class StateGateAncestorProtection(unittest.TestCase):
    def test_bare_parent_of_hard_forbidden_blocked(self):
        # 裸 permissions/history 父覆盖会整体替换受保护子树
        self.assertTrue(hf("permissions"), "permissions 裸父覆盖未拦 → 自我提权 + 清审计")
        self.assertTrue(hf("history"), "history 裸父覆盖未拦")

    def test_bare_parent_of_rules_managed_blocked(self):
        # 裸 player_character/encounter 父覆盖会把 hp/combatants 一并改掉
        self.assertTrue(rm("player_character"), "player_character 裸父覆盖未拦 → GM 凭空改 HP")
        self.assertTrue(rm("encounter"), "encounter 裸父覆盖未拦 → GM 清空战斗")
        self.assertTrue(rm("encounter.combatants"), "encounter.combatants 父未拦")

    def test_original_leaf_protections_intact(self):
        self.assertTrue(rm("player_character.hp"))
        self.assertTrue(rm("player_character.inventory"))
        self.assertTrue(hf("permissions.mode"))
        self.assertTrue(hf("history.0"))

    def test_legitimate_writes_not_overblocked(self):
        # 非受保护叶子/字段不应被祖先逻辑误拦
        self.assertFalse(rm("player_character.name"), "player_character.name 被误拦")
        self.assertFalse(rm("player"))
        self.assertFalse(rm("world.time"))
        self.assertFalse(hf("relationships.斯雷因"))
        self.assertFalse(hf("memory.notes"))


if __name__ == "__main__":
    unittest.main()
