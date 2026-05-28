"""
test_gm_json_op_via_dispatcher.py — task 87 Phase 6: GM JSON op 走 dispatcher

验证:
  · state_op_tool_map 把常见 path 正确映射到 dispatcher 工具
  · state.apply_state_write_typed 在 chat write context 下,GM source 的 op 走 dispatcher
  · 没有 context 时回退到老路径(向后兼容)
  · destructive op (改 player.name) 在 GM origin 下被 dispatcher 拒
  · 无对应工具的 op (encounter.* / dice_log) fall through 到老路径
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
from state_op_tool_map import map_op_to_tool  # noqa: E402
from state_write_context import (  # noqa: E402
    ChatWriteContext,
    clear_context,
    set_context,
)
from tools_dsl.command_tools_register import force_reset_for_tests  # noqa: E402


def _new_state(turn=3):
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


class PathToolMapping(unittest.TestCase):
    def test_world_time(self):
        m = map_op_to_tool("world.time", "火星·扬陆城")
        self.assertEqual(m, ("set_world_time", {"target": "火星·扬陆城"}))

    def test_world_weather(self):
        m = map_op_to_tool("world.weather", "酸雨")
        self.assertEqual(m, ("set_world_attribute", {"key": "weather", "value": "酸雨"}))

    def test_player_location(self):
        m = map_op_to_tool("player.current_location", "雾港码头")
        self.assertEqual(m, ("set_player_location", {"location": "雾港码头"}))

    def test_player_name_maps_to_destructive_tool(self):
        m = map_op_to_tool("player.name", "新名字")
        self.assertEqual(m, ("set_player_name", {"name": "新名字"}))

    def test_relationships(self):
        m = map_op_to_tool("relationships.斯雷因", "信任")
        self.assertEqual(m, ("set_relationship", {"character": "斯雷因", "status": "信任"}))

    def test_memory_main_quest(self):
        m = map_op_to_tool("memory.main_quest", "X")
        self.assertEqual(m, ("set_main_quest", {"text": "X"}))

    def test_memory_facts_append(self):
        m = map_op_to_tool("memory.facts", "事实A", op_kind="append")
        self.assertEqual(m, ("add_memory_fact", {"text": "事实A"}))

    def test_worldline_user_variable(self):
        m = map_op_to_tool("worldline.user_variables.trust_X", "高")
        self.assertEqual(m, ("set_user_variable", {"key": "trust_X", "value": "高"}))

    def test_unmapped_paths_return_none(self):
        for p in ("permissions.mode", "history.0", "schema_version",
                  "encounter.combatants", "dice_log", "world.timeline.current_label"):
            self.assertIsNone(map_op_to_tool(p, "x"),
                              f"{p} 不该有映射;返回 {map_op_to_tool(p, 'x')!r}")


class GMJsonOpRoutesToDispatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state(turn=5)
        # 设 chat context
        self._token = set_context(ChatWriteContext(
            user_id=1, save_id=100, script_id=None,
            trace_id="t-jsop-1",
        ))

    def tearDown(self):
        clear_context(self._token)

    def test_world_time_routes_via_dispatcher(self):
        """GM 写 op {path:world.time, value:X} 应通过 dispatcher 调 set_world_time,
        从而触发 user_set_jump_turn... 实际上 source='gm' 不会触发 user_set 跳跃,
        但 audit_log 应记 tool_call origin=llm_chat_json_op。"""
        result = self.state.apply_state_write_typed("world.time", "月球基地", source="gm")
        self.assertIn("set_world_time", result, f"返回应含工具名;实际 {result}")
        self.assertEqual(self.state.data["world"]["time"], "月球基地")
        # audit_log 应有 tool_call 记录,origin=llm_chat_json_op
        audit = self.state.data["permissions"]["audit_log"]
        tool_calls = [a for a in audit if a.get("kind") == "tool_call"]
        self.assertGreaterEqual(len(tool_calls), 1)
        last = tool_calls[-1]
        self.assertEqual(last["tool"], "set_world_time")
        self.assertEqual(last["origin"], "llm_chat")

    def test_destructive_op_blocked(self):
        """GM 写 op {path:player.name, value:X} 应被 dispatcher 拒绝
        (set_player_name 是 destructive,llm_chat_json_op 不在 origins 里)。"""
        result = self.state.apply_state_write_typed("player.name", "新名", source="gm")
        self.assertIn("拒绝", result, f"应被拒绝;实际 {result}")
        # state 不变
        self.assertNotEqual(self.state.data["player"]["name"], "新名")

    def test_unmapped_path_falls_through_to_legacy(self):
        """无对应工具的 path 应 fall through 老路径(也就是 apply_state_write_typed 继续执行)。
        encounter.* 受 rules_managed 保护,会被老路径拒绝,但说明 fall through 工作了。"""
        result = self.state.apply_state_write_typed(
            "encounter.combatants", [], source="gm",
        )
        # 老路径会拒 (rules_managed)
        self.assertIn("rules_managed", result, f"应被老路径 rules_managed 拒;实际 {result}")

    def test_no_context_uses_legacy_path(self):
        """没有 chat context (CLI / 单测) 不走 dispatcher 路由,直接老路径。"""
        clear_context(self._token)
        # 老路径在 source='gm' + full_access 模式应能直写
        result = self.state.apply_state_write_typed(
            "memory.main_quest", "测试主线", source="gm",
        )
        # 老路径返回 "状态写入：memory.main_quest"
        self.assertIn("状态写入", result)
        self.assertEqual(self.state.data["memory"]["main_quest"], "测试主线")
        # 重设 context for tearDown
        self._token = set_context(None)


class NonGMSourceUnchanged(unittest.TestCase):
    """非 GM source (user / rules_engine) 不应被 dispatcher 路由拦截。"""

    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state()
        self._token = set_context(ChatWriteContext(
            user_id=1, save_id=100, script_id=None, trace_id="t-x",
        ))

    def tearDown(self):
        clear_context(self._token)

    def test_user_source_uses_legacy(self):
        self.state.apply_state_write_typed(
            "world.time", "用户设定时间", source="user:/set", force=True,
        )
        # force=True 也跳过 dispatcher 路由,直接老路径
        self.assertEqual(self.state.data["world"]["time"], "用户设定时间")


if __name__ == "__main__":
    unittest.main(verbosity=2)
