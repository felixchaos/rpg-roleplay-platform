"""
test_command_tools.py — task 86: 工具表执行单元测试。

不打 LLM,直接测每个工具是否正确改 state、错误参数是否产生错误描述。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import DEFAULT_STATE, GameState  # noqa: E402
from tools_dsl.command_tools import COMMAND_TOOLS, execute_tool  # noqa: E402


def _new_state(turn=3) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


class ToolTableShape(unittest.TestCase):
    def test_every_tool_has_required_fields(self):
        for t in COMMAND_TOOLS:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertIn("input_schema", t)
            schema = t["input_schema"]
            self.assertEqual(schema.get("type"), "object")
            self.assertIn("properties", schema)

    def test_no_dangerous_tools(self):
        """工具表不能含改 permissions/history/schema_version 的工具
        (这是 task 86 安全设计:没有工具就不能写)。"""
        forbidden_targets = {"permissions", "history", "schema_version", "created_at"}
        for t in COMMAND_TOOLS:
            for forbidden in forbidden_targets:
                self.assertNotIn(
                    forbidden, t["name"].lower(),
                    f"工具名不应直接涉及禁字段 {forbidden}: {t['name']}",
                )


class SetWorldTimeWritesUserSetJumpTurn(unittest.TestCase):
    """关键测试:set_world_time 必须走 update_time(source='user_set') →
    设置 user_set_jump_turn → narrative guard 才会生效。"""

    def test_writes_user_set_jump_turn(self):
        s = _new_state(turn=5)
        execute_tool(s, "set_world_time", {"target": "火星·扬陆城内"})
        self.assertEqual(s.data["world"]["time"], "火星·扬陆城内")
        self.assertEqual(
            s.data["world"]["timeline"].get("user_set_jump_turn"), 5,
            "set_world_time 必须设置 user_set_jump_turn (否则 guard 失效)",
        )
        self.assertEqual(
            s.data["world"]["timeline"]["last_transition"]["source"], "user_set",
        )

    def test_empty_target_fails(self):
        s = _new_state()
        result = execute_tool(s, "set_world_time", {"target": ""})
        self.assertIn("失败", result)
        # state 不变
        self.assertEqual(s.data["world"]["time"], "")

    def test_arbitrary_target_accepted(self):
        """工具不再被 looks_like_time_value 启发式过滤 ——
        '魔王城地下三层'/'第七纪元'/'XYZ星系' 等任意标签都被接受。"""
        _new_state()
        for target in ("魔王城地下三层", "第七纪元", "XYZ 星系", "宇宙战时"):
            s2 = _new_state()
            r = execute_tool(s2, "set_world_time", {"target": target})
            self.assertNotIn("失败", r)
            self.assertEqual(s2.data["world"]["time"], target)


class SetPlayerLocationTool(unittest.TestCase):
    def test_basic(self):
        s = _new_state()
        execute_tool(s, "set_player_location", {"location": "雾港码头"})
        self.assertEqual(s.data["player"]["current_location"], "雾港码头")

    def test_empty(self):
        s = _new_state()
        s.data["player"]["current_location"] = "原位置"
        result = execute_tool(s, "set_player_location", {"location": ""})
        self.assertIn("失败", result)
        self.assertEqual(s.data["player"]["current_location"], "原位置")


class SetRelationshipTool(unittest.TestCase):
    def test_creates_relationship(self):
        s = _new_state()
        execute_tool(s, "set_relationship", {"character": "斯雷因", "status": "警惕中立"})
        self.assertEqual(s.data["relationships"]["斯雷因"], "警惕中立")

    def test_overwrites(self):
        s = _new_state()
        s.data["relationships"]["斯雷因"] = "敌对"
        execute_tool(s, "set_relationship", {"character": "斯雷因", "status": "信任"})
        self.assertEqual(s.data["relationships"]["斯雷因"], "信任")

    def test_missing_args(self):
        s = _new_state()
        result = execute_tool(s, "set_relationship", {"character": "斯雷因"})
        self.assertIn("失败", result)
        self.assertEqual(s.data["relationships"], {})


class MemoryTools(unittest.TestCase):
    def test_add_memory_fact(self):
        s = _new_state()
        r1 = execute_tool(s, "add_memory_fact", {"text": "扎兹巴鲁姆有内部敌人"})
        self.assertIn("memory.facts", r1)
        self.assertIn("扎兹巴鲁姆有内部敌人", s.data["memory"]["facts"])
        # 去重
        r2 = execute_tool(s, "add_memory_fact", {"text": "扎兹巴鲁姆有内部敌人"})
        self.assertIn("去重", r2)
        self.assertEqual(s.data["memory"]["facts"].count("扎兹巴鲁姆有内部敌人"), 1)

    def test_pin_memory(self):
        s = _new_state()
        execute_tool(s, "pin_memory", {"text": "蕾穆丽娜的银十字坠饰"})
        self.assertIn("蕾穆丽娜的银十字坠饰", s.data["memory"]["pinned"])

    def test_set_memory_mode(self):
        s = _new_state()
        execute_tool(s, "set_memory_mode", {"mode": "deep"})
        self.assertEqual(s.data["memory"]["mode"], "deep")
        # 非法 mode
        result = execute_tool(s, "set_memory_mode", {"mode": "ultra"})
        self.assertIn("失败", result)
        self.assertEqual(s.data["memory"]["mode"], "deep")

    def test_set_main_quest(self):
        s = _new_state()
        execute_tool(s, "set_main_quest", {"text": "营救蕾穆丽娜出柏林"})
        self.assertEqual(s.data["memory"]["main_quest"], "营救蕾穆丽娜出柏林")

    def test_add_memory_dual_writes_items(self):
        """add_memory_fact 会触发 state.add_memory 的 dual-write 到 memory.items."""
        s = _new_state()
        prev = len(s.data["memory"]["items"])
        execute_tool(s, "add_memory_fact", {"text": "测试事实"})
        self.assertGreater(len(s.data["memory"]["items"]), prev,
                           "add_memory_fact 应同步写到 memory.items")


class HypothesisAndUserVariableTools(unittest.TestCase):
    def test_add_hypothesis(self):
        s = _new_state(turn=7)
        r = execute_tool(s, "add_hypothesis", {
            "text": "蛇信背后另有靠山", "characters": ["蛇信"]})
        self.assertIn("推测登记", r)
        items = s.data["memory"]["items"]
        hyp = [i for i in items if i.get("kind") == "hypothesis"]
        self.assertEqual(len(hyp), 1)
        self.assertEqual(hyp[0]["text"], "蛇信背后另有靠山")

    def test_set_user_variable(self):
        s = _new_state()
        execute_tool(s, "set_user_variable", {
            "key": "trust_slaine", "value": "信任下降"})
        vars_ = s.data["worldline"]["user_variables"]
        self.assertIn("trust_slaine", vars_)
        self.assertEqual(vars_["trust_slaine"]["value"], "信任下降")
        self.assertTrue(vars_["trust_slaine"]["locked"])


class UnknownToolDoesNotCrash(unittest.TestCase):
    def test_unknown_tool(self):
        s = _new_state()
        r = execute_tool(s, "delete_database", {"all": True})
        self.assertIn("unknown tool", r)
        # state 完全不变 — 危险工具没对应实现就是安全
        # turn/history/permissions 都是原默认值
        self.assertEqual(s.data["turn"], 3)


class ClarifyTool(unittest.TestCase):
    """clarify 工具不改 state,只返回澄清信息让前端展示。"""
    def test_clarify_does_not_mutate(self):
        s = _new_state()
        snapshot = copy.deepcopy(s.data)
        r = execute_tool(s, "clarify", {
            "question": "你想改什么字段?",
            "options": ["时间", "位置"],
        })
        self.assertIn("clarify", r)
        self.assertEqual(s.data, snapshot, "clarify 不应改 state")


if __name__ == "__main__":
    unittest.main(verbosity=2)
