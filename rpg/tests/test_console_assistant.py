"""
test_console_assistant.py — task 48: 侧栏控制台助手基础架构

测试覆盖:
  Layer A — dispatcher origins 包含 console_assistant 且过滤正确
  Layer B — create_save 工具注册并能调 (mock workspace.create_save)
  Layer C — SSE 协议: meta / token / tool_call / tool_result / done 事件序列
  Layer D — destructive 流: 工具触发 confirmation_required → /confirm approve → 执行
  Layer E — destructive reject 不执行
  Layer F — origin 隔离: llm_chat 仍不能调 console_assistant 专属工具
  Layer G — 跨用户 conversation 隔离
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import GameState, DEFAULT_STATE  # noqa: E402
from command_tools_register import force_reset_for_tests  # noqa: E402
from command_dispatcher import (  # noqa: E402
    ToolCallEnvelope, ToolDispatcher, get_registry,
)


def _new_state(turn=3) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


# ────────────────────────────────────────────────────────────
# Layer A: dispatcher origins
# ────────────────────────────────────────────────────────────


class DispatcherOriginsHaveConsoleAssistant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_origin_literal_includes_console_assistant(self):
        """Origin literal type should include console_assistant."""
        from command_dispatcher import Origin
        # 用 typing 拿 Literal args
        import typing
        args = typing.get_args(Origin)
        self.assertIn("console_assistant", args)

    def test_user_read_tools_visible_to_console_assistant(self):
        names = {s.name for s in get_registry().list_for_origin("console_assistant")}
        for expected in [
            "list_my_saves", "list_branches", "list_scripts", "list_my_personas",
            "list_my_character_cards", "get_save_detail", "get_my_stats",
            "list_my_credentials_meta", "list_my_import_jobs", "get_import_status",
        ]:
            self.assertIn(expected, names, f"console_assistant 应能看到 {expected}")

    def test_user_mutate_tools_visible(self):
        names = {s.name for s in get_registry().list_for_origin("console_assistant")}
        for expected in [
            "activate_save", "rename_save", "create_persona", "create_character_card",
            "set_preference", "select_model", "continue_branch", "activate_branch",
            "start_script_import", "cancel_import_job",
            "mcp_server_enable", "mcp_server_start", "mcp_server_stop",
        ]:
            self.assertIn(expected, names)

    def test_destructive_tools_visible_but_marked(self):
        """destructive 工具加入 console_assistant origin, 但 spec.destructive=True
        端点层会做二次确认。"""
        reg = get_registry()
        names = {s.name for s in reg.list_for_origin("console_assistant")}
        for dest in [
            "delete_save", "delete_persona", "delete_character_card",
            "delete_script", "delete_branch", "resplit_script",
            "set_player_name",
        ]:
            self.assertIn(dest, names, f"console_assistant 应能看到 destructive {dest}")
            self.assertTrue(reg.get(dest).destructive)

    def test_ui_only_tools_not_visible(self):
        """inject_pending_question / set_permission_mode / approve_pending_write
        应仍只对 UI/API 开放, console_assistant 看不到。"""
        names = {s.name for s in get_registry().list_for_origin("console_assistant")}
        for blocked in [
            "inject_pending_question", "set_permission_mode",
            "approve_pending_write", "reject_pending_write",
            "stop_current_chat",
        ]:
            self.assertNotIn(blocked, names, f"{blocked} 不应对 console_assistant 开放")


# ────────────────────────────────────────────────────────────
# Layer B: create_save 工具
# ────────────────────────────────────────────────────────────


class CreateSaveTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_registered(self):
        reg = get_registry()
        self.assertTrue(reg.has("create_save"))
        spec = reg.get("create_save")
        self.assertEqual(spec.scope, "user")
        self.assertFalse(spec.destructive)
        self.assertIn("console_assistant", spec.origins)
        self.assertIn("ui_button", spec.origins)
        self.assertNotIn("llm_chat", spec.origins)
        self.assertNotIn("llm_set", spec.origins)

    def test_invokes_workspace_create_save(self):
        captured = {}
        fake_save = {"id": 777, "title": "测试存档", "script_id": 12}

        def _fake_create_save(*, user_id, script_id, title,
                              new_card=None, character=None):
            captured.update(dict(
                user_id=user_id, script_id=script_id, title=title,
                new_card=new_card, character=character,
            ))
            return fake_save

        with mock.patch("platform_app.workspace.create_save", side_effect=_fake_create_save):
            dispatcher = ToolDispatcher(registry=get_registry())
            env = ToolCallEnvelope(
                user_id=42, save_id=None, script_id=None,
                tool="create_save",
                args={"script_id": 12, "title": "测试存档", "persona_id": 5},
                origin="console_assistant", trace_id="t-cs",
            )
            r = dispatcher.dispatch_sync(env)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(captured["user_id"], 42)
        self.assertEqual(captured["script_id"], 12)
        self.assertEqual(captured["title"], "测试存档")
        self.assertEqual(captured["character"], {"kind": "persona", "id": 5})
        self.assertIn("777", r.result)

    def test_rejects_llm_chat_origin(self):
        """create_save 不允许 llm_chat origin (跨 save 操作)。"""
        dispatcher = ToolDispatcher(registry=get_registry())
        env = ToolCallEnvelope(
            user_id=1, save_id=None, tool="create_save",
            args={"script_id": 12},
            origin="llm_chat", trace_id="t-cs-llmblock",
        )
        r = dispatcher.dispatch_sync(env)
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", (r.error or ""))


# ────────────────────────────────────────────────────────────
# Layer C/D/E: console_assistant module 行为 (SSE / 二次确认)
# ────────────────────────────────────────────────────────────


class FakeBackend:
    """模拟 backend.stream_with_mcp_loop:
    按 scripted_events 顺序 yield, 但 tool_call 会真正调 mcp_call 闭包。
    每次 iteration 完整跑一次脚本。
    """

    def __init__(self, scripted_events: list[dict], call_call_back: bool = True):
        self.scripted_events = scripted_events
        self.call_back = call_call_back

    def stream_with_mcp_loop(self, *, system, messages, mcp_tools,
                              max_iterations, max_tokens, mcp_call):
        for ev in self.scripted_events:
            yield ev
            if ev.get("type") == "tool_call" and self.call_back:
                server_id = ev.get("server_id") or "dispatcher"
                tname = ev.get("tool")
                args = ev.get("arguments") or {}
                # 真正调 mcp_call (router), 把结果 yield 出去
                r = mcp_call(server_id, tname, args)
                yield {"type": "tool_result",
                       "ok": bool(r.get("ok")), "result": r.get("result"),
                       "error": r.get("error"), "_call_id": r.get("_call_id")}


def _consume_sse(generator) -> list[dict]:
    """把 SSE 字符串生成器解析回 [{event, data}, ...]"""
    out: list[dict] = []
    current = {"event": None, "data": ""}
    for chunk in generator:
        for line in chunk.split("\n"):
            if not line:
                if current["event"]:
                    try:
                        current["data"] = json.loads(current["data"]) if current["data"] else None
                    except json.JSONDecodeError:
                        pass
                    out.append(dict(current))
                current = {"event": None, "data": ""}
                continue
            if line.startswith("event:"):
                current["event"] = line[6:].strip()
            elif line.startswith("data:"):
                current["data"] += line[5:].strip()
    if current["event"]:
        out.append(current)
    return out


class StreamChatSSEProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        from console_assistant import reset_all_conversations
        reset_all_conversations()

    def test_meta_token_done_sequence_no_tools(self):
        from console_assistant import stream_chat
        backend = FakeBackend([
            {"type": "text", "text": "你好,"},
            {"type": "text", "text": "需要我做什么?"},
        ])
        events = _consume_sse(stream_chat(
            user_id=1, message="hi", conversation_id=None,
            page_context={"tab": "platform.scripts"},
            backend=backend,
        ))
        types = [e["event"] for e in events]
        self.assertEqual(types[0], "meta")
        self.assertIn("token", types)
        self.assertEqual(types[-1], "done")
        # meta 含 conversation_id 与 trace_id
        meta = events[0]["data"]
        self.assertTrue(meta["conversation_id"].startswith("conv-"))
        self.assertTrue(meta["trace_id"].startswith("console-"))

    def test_tool_call_non_destructive_round_trip(self):
        """非 destructive 工具: stream_chat 应 yield tool_call + tool_result, 真正 dispatch。"""
        from console_assistant import stream_chat

        class FakeDB:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
            def execute(self_inner, sql, params=None):
                m = mock.MagicMock()
                m.fetchall.return_value = []
                m.fetchone.return_value = None
                return m

        # 模拟 LLM 调 list_my_saves
        backend = FakeBackend([
            {"type": "text", "text": "好的, 我先列存档。"},
            {"type": "tool_call", "server_id": "dispatcher",
             "tool": "list_my_saves", "arguments": {}},
            {"type": "text", "text": "完成。"},
        ])
        # mock 实际 DB 调用 — connect 在工具函数内 from platform_app.db import connect,
        # 所以要打 platform_app.db.connect 本身
        with mock.patch("platform_app.db.connect", return_value=FakeDB()):
            with mock.patch("platform_app.db.init_db"):
                events = _consume_sse(stream_chat(
                    user_id=1, message="列存档", conversation_id=None,
                    page_context=None, backend=backend,
                ))
        types = [e["event"] for e in events]
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        # tool_call 在 tool_result 之前
        idx_call = types.index("tool_call")
        idx_result = types.index("tool_result")
        self.assertLess(idx_call, idx_result)
        # tool_result.ok 应为 True (list 即使空也算 ok)
        tool_results = [e["data"] for e in events if e["event"] == "tool_result"]
        self.assertTrue(tool_results[0]["ok"])

    def test_destructive_yields_confirmation_required_and_pauses(self):
        """destructive 工具: stream_chat 应 yield confirmation_required, 不 dispatch, 中止本轮。"""
        from console_assistant import stream_chat, get_conversation_state
        backend = FakeBackend([
            {"type": "text", "text": "我要删存档了。"},
            {"type": "tool_call", "server_id": "dispatcher",
             "tool": "delete_save", "arguments": {"save_id": 100}},
            # 后面 LLM 不该被调用 (因为 destructive 中断), 但 fake backend 不知道,
            # 所以这条不会被消费 - stream_chat 在 confirmation_required 后 break
            {"type": "text", "text": "(本句不该出现)"},
        ])
        events = _consume_sse(stream_chat(
            user_id=1, message="删存档 100", conversation_id=None,
            page_context={"save_id": 100}, backend=backend,
        ))
        types = [e["event"] for e in events]
        self.assertIn("confirmation_required", types)
        # tool_result 不应在 events 中 (或者即便有, 也是 DESTRUCTIVE 错误,不会真删)
        # 也不应该看到 "(本句不该出现)" 的 token
        tokens = [e["data"]["text"] for e in events if e["event"] == "token"]
        self.assertNotIn("(本句不该出现)", tokens)
        # confirmation_required 内容
        confirms = [e["data"] for e in events if e["event"] == "confirmation_required"]
        self.assertEqual(confirms[0]["tool"], "delete_save")
        self.assertEqual(confirms[0]["args"], {"save_id": 100})
        self.assertTrue(confirms[0]["destructive"])
        # conversation state 中存有 pending
        # 注意:fake backend 不返回 conversation_id, 但 stream_chat 给我们的 meta 里有
        meta = next(e["data"] for e in events if e["event"] == "meta")
        cid = meta["conversation_id"]
        conv_state = get_conversation_state(1)
        self.assertIn(cid, conv_state)
        self.assertEqual(len(conv_state[cid]["pending_confirmations"]), 1)


class ConfirmationApply(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        from console_assistant import reset_all_conversations
        reset_all_conversations()

    def _create_pending(self, user_id=1):
        """跑一次 stream_chat 让 destructive 工具创建一个 pending。返回 (conv_id, call_id)。"""
        from console_assistant import stream_chat
        backend = FakeBackend([
            {"type": "tool_call", "server_id": "dispatcher",
             "tool": "delete_save", "arguments": {"save_id": 7}},
        ])
        events = _consume_sse(stream_chat(
            user_id=user_id, message="x", conversation_id=None,
            page_context={"save_id": 7}, backend=backend,
        ))
        meta = next(e["data"] for e in events if e["event"] == "meta")
        confirm = next(e["data"] for e in events if e["event"] == "confirmation_required")
        return meta["conversation_id"], confirm["call_id"]

    def test_approve_executes(self):
        from console_assistant import apply_confirmation
        cid, call_id = self._create_pending(user_id=10)

        # mock 实际 DB 删档
        called = {"deleted": False}

        class FakeDB:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False
            def execute(self_inner, sql, params=None):
                if "delete" in sql.lower() and "game_saves" in sql.lower():
                    called["deleted"] = True
                m = mock.MagicMock()
                m.fetchone.return_value = {"1": 1} if "select 1" in sql.lower() else None
                m.fetchall.return_value = []
                return m

        with mock.patch("platform_app.db.connect", return_value=FakeDB()):
            with mock.patch("platform_app.db.init_db"):
                result = apply_confirmation(
                    user_id=10, conversation_id=cid, call_id=call_id,
                    decision="approve",
                )
        self.assertEqual(result["decision"], "approve")
        self.assertTrue(called["deleted"], "approve 应真的调到 delete_save executor")
        # pending 已消费
        from console_assistant import get_conversation_state
        conv = get_conversation_state(10)[cid]
        self.assertEqual(conv["pending_confirmations"], {})

    def test_reject_does_not_execute(self):
        from console_assistant import apply_confirmation
        cid, call_id = self._create_pending(user_id=11)

        # 如果 dispatch_sync 被调到说明 reject 没生效, 抛异常
        with mock.patch("platform_app.db.connect",
                         side_effect=AssertionError("不应执行 DB 删档")):
            result = apply_confirmation(
                user_id=11, conversation_id=cid, call_id=call_id,
                decision="reject",
            )
        self.assertEqual(result["decision"], "reject")
        self.assertTrue(result["ok"])
        from console_assistant import get_conversation_state
        conv = get_conversation_state(11)[cid]
        self.assertEqual(conv["pending_confirmations"], {})

    def test_reject_invalid_call_id(self):
        from console_assistant import apply_confirmation
        cid, _ = self._create_pending(user_id=12)
        result = apply_confirmation(
            user_id=12, conversation_id=cid, call_id="cc-bogus",
            decision="approve",
        )
        self.assertFalse(result["ok"])
        self.assertIn("没有 pending 记录", (result.get("error") or ""))

    def test_cross_user_cannot_see_pending(self):
        from console_assistant import apply_confirmation
        cid, call_id = self._create_pending(user_id=20)
        # 别的 user 试图 approve user 20 的 pending → 应找不到 conv
        result = apply_confirmation(
            user_id=21, conversation_id=cid, call_id=call_id,
            decision="approve",
        )
        self.assertFalse(result["ok"])
        self.assertIn("conversation", (result.get("error") or "").lower())


# ────────────────────────────────────────────────────────────
# Layer F: origin 隔离 - llm_chat 仍不能调 console_assistant 专属工具
# ────────────────────────────────────────────────────────────


class OriginIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_llm_chat_cannot_call_create_save(self):
        d = ToolDispatcher(registry=get_registry())
        r = d.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=None, tool="create_save",
            args={"script_id": 1}, origin="llm_chat", trace_id="iso-1",
        ))
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", (r.error or ""))

    def test_llm_chat_cannot_call_activate_save(self):
        d = ToolDispatcher(registry=get_registry())
        r = d.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=None, tool="activate_save",
            args={"save_id": 1}, origin="llm_chat", trace_id="iso-2",
        ))
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", (r.error or ""))

    def test_llm_chat_cannot_call_delete_save(self):
        d = ToolDispatcher(registry=get_registry())
        r = d.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=None, tool="delete_save",
            args={"save_id": 1}, origin="llm_chat", trace_id="iso-3",
        ))
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", (r.error or ""))

    def test_llm_chat_cannot_call_select_model(self):
        d = ToolDispatcher(registry=get_registry())
        r = d.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=None, tool="select_model",
            args={"api_id": "x", "model": "y"}, origin="llm_chat", trace_id="iso-4",
        ))
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", (r.error or ""))

    def test_llm_set_cannot_call_create_save(self):
        d = ToolDispatcher(registry=get_registry())
        r = d.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=None, tool="create_save",
            args={"script_id": 1}, origin="llm_set", trace_id="iso-5",
        ))
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", (r.error or ""))


# ────────────────────────────────────────────────────────────
# Layer G: 跨用户 conversation 隔离
# ────────────────────────────────────────────────────────────


class CrossUserConversationIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        from console_assistant import reset_all_conversations
        reset_all_conversations()

    def test_two_users_have_separate_buckets(self):
        from console_assistant import stream_chat, get_conversation_state
        backend = FakeBackend([{"type": "text", "text": "hi"}])
        events_a = _consume_sse(stream_chat(
            user_id=100, message="msg-A", conversation_id=None,
            page_context=None, backend=backend,
        ))
        events_b = _consume_sse(stream_chat(
            user_id=200, message="msg-B", conversation_id=None,
            page_context=None, backend=FakeBackend([{"type": "text", "text": "ho"}]),
        ))
        cid_a = next(e["data"] for e in events_a if e["event"] == "meta")["conversation_id"]
        cid_b = next(e["data"] for e in events_b if e["event"] == "meta")["conversation_id"]
        # user 100 看不到 user 200 的 conv, 反之亦然
        user_100 = get_conversation_state(100)
        user_200 = get_conversation_state(200)
        self.assertIn(cid_a, user_100)
        self.assertNotIn(cid_b, user_100)
        self.assertIn(cid_b, user_200)
        self.assertNotIn(cid_a, user_200)
        # messages 内容互不污染
        self.assertEqual(user_100[cid_a]["messages"][0]["content"], "msg-A")
        self.assertEqual(user_200[cid_b]["messages"][0]["content"], "msg-B")

    def test_same_conversation_id_does_not_leak_across_users(self):
        """如果两个用户都传一样的 conversation_id, 应当成两个独立的 conv (按 user_id 分桶)。"""
        from console_assistant import stream_chat, get_conversation_state
        cid = "shared-conv-id"
        _consume_sse(stream_chat(
            user_id=300, message="A", conversation_id=cid,
            page_context=None, backend=FakeBackend([{"type": "text", "text": "a"}]),
        ))
        _consume_sse(stream_chat(
            user_id=301, message="B", conversation_id=cid,
            page_context=None, backend=FakeBackend([{"type": "text", "text": "b"}]),
        ))
        u300 = get_conversation_state(300)
        u301 = get_conversation_state(301)
        self.assertIn(cid, u300)
        self.assertIn(cid, u301)
        # 两条独立, 各自只有自己的消息
        self.assertEqual(u300[cid]["messages"][0]["content"], "A")
        self.assertEqual(u301[cid]["messages"][0]["content"], "B")


# ────────────────────────────────────────────────────────────
# Layer H: 模块基本属性
# ────────────────────────────────────────────────────────────


class ConsoleAssistantModuleBasics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_list_assistant_tools_returns_known_subset(self):
        from console_assistant import list_assistant_tools
        tools = list_assistant_tools()
        names = {t["name"] for t in tools}
        # 关键能力
        self.assertIn("create_save", names)
        self.assertIn("list_my_saves", names)
        self.assertIn("activate_save", names)
        self.assertIn("delete_save", names)
        self.assertIn("create_persona", names)
        self.assertIn("get_game_state", names)
        # 不应含 UI-only
        self.assertNotIn("inject_pending_question", names)
        self.assertNotIn("set_permission_mode", names)
        # destructive 标志
        for t in tools:
            if t["name"] == "delete_save":
                self.assertTrue(t["destructive"])
            if t["name"] == "list_my_saves":
                self.assertFalse(t["destructive"])

    def test_build_system_prompt_includes_page_context(self):
        from console_assistant import build_system_prompt
        sp = build_system_prompt({
            "tab": "platform.scripts", "save_id": 7, "script_id": 12,
        })
        self.assertIn("platform.scripts", sp)
        self.assertIn("7", sp)
        self.assertIn("12", sp)
        self.assertIn("控制台助手", sp)

    def test_build_system_prompt_handles_none(self):
        from console_assistant import build_system_prompt
        sp = build_system_prompt(None)
        self.assertIn("控制台助手", sp)
        self.assertIn("未知", sp)


if __name__ == "__main__":
    unittest.main()
