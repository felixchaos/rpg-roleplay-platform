"""玩家卡字段(外貌/性格/语气/背景)可维护后端。

覆盖三条维护通道 + npc_agendas 暴露 + 史官可写清单:
  a) registry 执行器锁语义:ui_button 写 → 锁定 → llm_chat_json_op 被拒 → ui_button 再写成功
  b) apply_state_write_typed 兜底锁闸:锁定后 gm 写被拒;user /set force=True 放行
  c) map_op_to_tool: player.appearance/personality/speech_style → set_player_* 工具
  d) status_payload 暴露 npc_agendas 键
  e) 史官 ops system prompt 列出 player.appearance/personality/speech_style
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state.core import GameState  # noqa: E402
from tools_dsl.command_dispatcher import (  # noqa: E402
    ToolCallEnvelope,
    ToolDispatcher,
    get_registry,
)
from tools_dsl.command_tools_register import force_reset_for_tests  # noqa: E402


class PlayerProfileRegistryExecutor(unittest.TestCase):
    """(a) 通过 dispatcher(registry 执行器)验证 _origin 驱动的锁语义。"""

    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = GameState.new()
        self.state.data["player"]["name"] = "测试玩家"
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _dispatch(self, origin, appearance, trace_id):
        return self.dispatcher.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=100, tool="set_player_appearance",
            args={"appearance": appearance}, origin=origin, trace_id=trace_id,
        ))

    def test_ui_lock_then_llm_rejected_then_ui_ok(self):
        # ui_button 写 → 成功 + 路径进 user_locked
        r1 = self._dispatch("ui_button", "银发赤瞳", "ppm-a-1")
        self.assertTrue(r1.ok, r1.error)
        self.assertEqual(self.state.data["player"]["appearance"], "银发赤瞳")
        self.assertTrue(self.state._is_user_locked("player.appearance"))

        # llm_chat_json_op(史官 ops 路由,非用户意图)改写 → 被锁闸拒
        r2 = self._dispatch("llm_chat_json_op", "史官想改的外貌", "ppm-a-2")
        self.assertFalse(r2.ok)
        self.assertIn("锁定", str(r2.result))
        self.assertEqual(self.state.data["player"]["appearance"], "银发赤瞳")

        # 玩家再从 UI 改 → 仍成功(用户意图不受锁)
        r3 = self._dispatch("ui_button", "金发碧眼", "ppm-a-3")
        self.assertTrue(r3.ok, r3.error)
        self.assertEqual(self.state.data["player"]["appearance"], "金发碧眼")


class PlayerProfileApplyOpsGuard(unittest.TestCase):
    """(b) apply_state_write_typed 纵深锁闸(路由 fall-through 老路径)。"""

    def test_gm_write_rejected_when_locked(self):
        state = GameState.new()
        state.mark_user_locked("player.appearance")
        r = state.apply_state_write_typed("player.appearance", "GM 想改", source="gm")
        self.assertIn("状态写入拒绝", r)
        self.assertNotEqual(state.data["player"].get("appearance"), "GM 想改")

    def test_user_force_write_allowed_when_locked(self):
        state = GameState.new()
        state.mark_user_locked("player.appearance")
        r = state.apply_state_write_typed(
            "player.appearance", "玩家自己改", source="user:/set", force=True,
        )
        self.assertNotIn("拒绝", r)
        self.assertEqual(state.data["player"]["appearance"], "玩家自己改")


class MapOpToToolPlayerProfile(unittest.TestCase):
    """(c) GM JSON op path → dispatcher 工具映射。"""

    def test_profile_fields_map_to_tools(self):
        from state_op_tool_map import map_op_to_tool
        self.assertEqual(
            map_op_to_tool("player.appearance", "银发"),
            ("set_player_appearance", {"appearance": "银发"}),
        )
        self.assertEqual(
            map_op_to_tool("player.personality", "冷淡好奇"),
            ("set_player_personality", {"personality": "冷淡好奇"}),
        )
        self.assertEqual(
            map_op_to_tool("player.speech_style", "简短直接"),
            ("set_player_speech_style", {"speech_style": "简短直接"}),
        )


class StatusPayloadNpcAgendas(unittest.TestCase):
    """(d) status_payload 暴露 npc_agendas。"""

    def test_status_payload_has_npc_agendas(self):
        state = GameState.new()
        payload = state.status_payload()
        self.assertIn("npc_agendas", payload)
        self.assertIsInstance(payload["npc_agendas"], dict)


class RecorderSystemPromptWritableFields(unittest.TestCase):
    """(e) 史官 ops system prompt 列出玩家人设卡字段。"""

    def test_ops_prompt_lists_profile_fields(self):
        from agents.recorder import _build_system_prompt
        text = _build_system_prompt(frozenset({"ops"}))
        self.assertIn("player.appearance", text)
        self.assertIn("player.personality", text)
        self.assertIn("player.speech_style", text)


if __name__ == "__main__":
    unittest.main()
