"""模组房间移动两处 fail-closed 修复:
- 当前 location_id 不在模组房间图时,不得瞬移到任意房间(只能回锚起点)。
- 未识别的 requires 前缀(非 flag:)的上锁出口必须 fail-closed,不静默放行。
"""
import copy
import re
import unittest
from pathlib import Path

from rules_bridge import enter_room, start_module
from state import DEFAULT_STATE, GameState

MOD_SRC = (Path(__file__).resolve().parents[2] / "rules_bridge" / "module_ops.py").read_text(encoding="utf-8")


class EnterRoomCorruptCurrentFailClosed(unittest.TestCase):
    def setUp(self):
        self.g = GameState(copy.deepcopy(DEFAULT_STATE))
        res = start_module(self.g, "ash_mine")
        self.assertTrue(res.get("ok", True) is not False, res)

    def test_corrupt_location_cannot_teleport(self):
        # 损坏当前位置(指向不存在的房间)
        self.g.data["scene"]["location_id"] = "__bogus_room__"
        # 试图瞬移到非起点房间 → 必须被拒(否则穿锁+瞬移)
        res = enter_room(self.g, "mine_heart_altar")
        self.assertFalse(res.get("ok"), "损坏当前位置时瞬移到任意房间被允许(穿锁漏洞)")

    def test_corrupt_location_can_reanchor_to_start(self):
        self.g.data["scene"]["location_id"] = "__bogus_room__"
        res = enter_room(self.g, "mine_entrance")  # 起点
        self.assertTrue(res.get("ok"), "损坏位置时回锚起点应被允许")
        self.assertEqual(self.g.data["scene"]["location_id"], "mine_entrance")


class UnrecognizedRequiresFailClosed(unittest.TestCase):
    def test_non_flag_requires_rejected_in_source(self):
        i = MOD_SRC.find("def enter_room(")
        end = MOD_SRC.find("\ndef ", i + 1)
        body = MOD_SRC[i:end]
        # flag: 分支后必须有 else 拒绝未识别前缀
        self.assertIn('req.startswith("flag:")', body)
        self.assertTrue(re.search(r"暂不支持|通行被拒|fail-closed", body),
                        "未识别 requires 前缀未 fail-closed(锁失效穿门)")

    def test_corrupt_cur_room_handled(self):
        i = MOD_SRC.find("def enter_room(")
        end = MOD_SRC.find("\ndef ", i + 1)
        body = MOD_SRC[i:end]
        self.assertIn("cur_room is None", body, "cur_room=None 未单独 fail-closed 处理")


if __name__ == "__main__":
    unittest.main()
