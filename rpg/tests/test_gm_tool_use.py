"""
test_gm_tool_use.py — task 87 Phase 5: GM 主响应工具化端到端

验证:
  · build_unified_tool_list 合并 MCP + dispatcher 工具 + 按 origin=llm_chat 过滤
  · build_tool_call_router 识别 dispatcher 工具 vs MCP 工具
  · GM 调 dispatcher 工具时 audit_log 记录 origin=llm_chat
  · GM 调 dispatcher destructive 工具被拒
  · GM trace 不污染 /set 的 trace
  · gm.respond_stream_with_tools 接受 tool_call_router 参数
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import GameState, DEFAULT_STATE  # noqa: E402
from tools_dsl.command_tools_register import force_reset_for_tests  # noqa: E402
from tools_dsl.command_dispatcher import get_registry  # noqa: E402
from tools_dsl.chat_tool_router import (  # noqa: E402
    DISPATCHER_SENTINEL, build_unified_tool_list, build_tool_call_router,
)


def _new_state(turn=3):
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


class UnifiedToolListBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_filters_by_origin_llm_chat(self):
        """build_unified_tool_list(origin='llm_chat') 应排除 destructive 工具。"""
        unified = build_unified_tool_list([], origin="llm_chat")
        names = {t["name"] for t in unified}
        # 不应含 destructive
        self.assertNotIn("delete_save", names)
        self.assertNotIn("delete_branch", names)
        self.assertNotIn("delete_persona", names)
        self.assertNotIn("set_player_name", names)
        self.assertNotIn("remove_memory_item", names)
        # 应含常用读写
        self.assertIn("set_world_time", names)
        self.assertIn("set_relationship", names)
        self.assertIn("query_memory", names)
        self.assertIn("get_game_state", names)

    def test_dispatcher_tools_carry_sentinel_server_id(self):
        unified = build_unified_tool_list([], origin="llm_chat")
        for t in unified:
            if t["name"] == "set_world_time":
                self.assertEqual(t["server_id"], DISPATCHER_SENTINEL)
                self.assertEqual(t["schema"]["type"], "object")
                self.assertIn("target", t["schema"]["properties"])
                break
        else:
            self.fail("set_world_time 应出现在 unified 列表里")

    def test_appends_mcp_tools(self):
        """MCP 工具(带真实 server_id) 应在 dispatcher 工具之前/之后保留。"""
        mcp = [{"server_id": "filesystem", "name": "read_file",
                "description": "...", "schema": {"type": "object"}}]
        unified = build_unified_tool_list(mcp, origin="llm_chat")
        mcp_ids = [t["server_id"] for t in unified]
        self.assertIn("filesystem", mcp_ids)
        self.assertIn(DISPATCHER_SENTINEL, mcp_ids)


class ToolCallRouterRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state(turn=5)

    def test_dispatcher_tool_routes_to_dispatcher(self):
        router = build_tool_call_router(
            user_id=1, save_id=100, script_id=None, trace_id="gm-x",
            state_provider=lambda env: self.state,
        )
        r = router(DISPATCHER_SENTINEL, "set_world_time",
                   {"target": "月球·谒见大厅"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.state.data["world"]["time"], "月球·谒见大厅")
        # audit_log 应记录 origin=llm_chat
        audit = self.state.data["permissions"]["audit_log"]
        tool_calls = [a for a in audit if a.get("kind") == "tool_call"]
        self.assertGreaterEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[-1]["origin"], "llm_chat")
        self.assertEqual(tool_calls[-1]["tool"], "set_world_time")

    def test_router_handles_destructive_block(self):
        """GM 调 destructive 工具应被拒。"""
        router = build_tool_call_router(
            user_id=1, save_id=100, script_id=None, trace_id="gm-d",
            state_provider=lambda env: self.state,
        )
        r = router(DISPATCHER_SENTINEL, "delete_save", {"save_id": 1})
        self.assertFalse(r["ok"])
        self.assertTrue("origin_forbidden" in (r["error"] or "")
                         or "destructive_blocked" in (r["error"] or ""))

    def test_router_falls_back_to_mcp_for_unknown_server(self):
        called = {}

        def _fake_mcp(server_id, tool_name, args):
            called["sid"] = server_id
            called["tool"] = tool_name
            called["args"] = args
            return {"ok": True, "result": "mcp result"}

        router = build_tool_call_router(
            user_id=1, save_id=100, script_id=None, trace_id="gm-m",
            state_provider=lambda env: self.state,
            fallback_mcp_call=_fake_mcp,
        )
        r = router("filesystem", "read_file", {"path": "/x"})
        self.assertEqual(called["sid"], "filesystem")
        self.assertEqual(called["tool"], "read_file")
        self.assertEqual(r["result"], "mcp result")

    def test_router_recognizes_dispatcher_tool_without_sentinel(self):
        """如果工具名是 dispatcher 注册的 (LLM 没填 server_id),也应走 dispatcher。"""
        router = build_tool_call_router(
            user_id=1, save_id=100, script_id=None, trace_id="gm-y",
            state_provider=lambda env: self.state,
        )
        # server_id 留空,name 是 dispatcher 工具
        r = router("", "query_memory", {"limit": 5})
        self.assertTrue(r["ok"], r)

    def test_router_carries_depth_for_recursion_safety(self):
        """GM 调用工具时 depth=1; 工具再调工具的话会被 dispatcher MAX_TRACE_DEPTH 挡。"""
        router = build_tool_call_router(
            user_id=1, save_id=100, script_id=None, trace_id="gm-z",
            state_provider=lambda env: self.state,
        )
        r = router(DISPATCHER_SENTINEL, "set_world_time",
                   {"target": "测试 depth"})
        self.assertTrue(r["ok"])
        # 检查 audit_log 的 depth
        audit = self.state.data["permissions"]["audit_log"]
        last = [a for a in audit if a.get("kind") == "tool_call"][-1]
        self.assertEqual(last["depth"], 1)


class GMRespondStreamSignature(unittest.TestCase):
    """gm.respond_stream_with_tools 接受 tool_call_router 参数。"""

    def test_signature_accepts_tool_call_router(self):
        import inspect
        from agents.gm import GameMaster
        sig = inspect.signature(GameMaster.respond_stream_with_tools)
        self.assertIn("tool_call_router", sig.parameters)
        self.assertIn("tools", sig.parameters)


class CrossOriginIsolation(unittest.TestCase):
    """同一 user/save 但来自 GM 的工具调用 (origin=llm_chat) 不会污染
    /set 命令 (origin=llm_set) 的 trace。"""

    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_distinct_trace_ids_isolated(self):
        state = _new_state(turn=5)
        router_gm = build_tool_call_router(
            user_id=1, save_id=100, script_id=None, trace_id="gm-trace",
            state_provider=lambda env: state,
        )
        # /set origin
        from tools_dsl.command_dispatcher import ToolCallEnvelope, ToolDispatcher
        d = ToolDispatcher(get_registry(), state_provider=lambda env: state)
        r1 = router_gm(DISPATCHER_SENTINEL, "set_world_time",
                       {"target": "时间A"})
        self.assertTrue(r1["ok"])
        # 同一工具 + 不同 trace_id (llm_set) — 应通过 (trace 内才去重)
        r2 = d.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=100, tool="set_world_time",
            args={"target": "时间A"}, origin="llm_set",
            trace_id="set-trace",
        ))
        self.assertTrue(r2.ok, r2.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
