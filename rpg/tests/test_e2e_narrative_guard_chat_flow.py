"""
test_e2e_narrative_guard_chat_flow.py — task 86 端到端验证

不只是单元测试。这里走真实 chat handler:
  1. 用 TestClient 调 POST /api/chat
  2. monkeypatch GM backend 让它返回禁词文本 (模拟 LLM 失控生成"穿越/醒来/拨回时钟")
  3. monkeypatch run_context_agent 返回 stub (避免真打 LLM)
  4. 同回合先 /set 时间跳跃,GM 写禁词,验证 audit_log 收到 violation

这个测试关键证明:
  · apply_player_directives → update_time(source="user_set") → 设 user_set_jump_turn
  · GM 响应路径上,即便 GM 通过 JSON op 又 update_time(source="gm") 把 last_transition
    覆盖,user_set_jump_turn 仍保留
  · timeline_narrative_guard.detect_time_jump_violations 在生产 chat 流程里
    真的写到 state.permissions.audit_log
  · SSE 'agent' phase='timeline_guard' 事件被 yield 出去
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

from tests.helpers import (  # noqa: E402
    cleanup_test_users,
    make_client,
    register_user,
)


def _consume_sse(resp) -> list[dict]:
    """读 SSE stream,返回 [{event, data}, ...]"""
    events = []
    current = {"event": None, "data": ""}
    for raw_line in resp.iter_lines():
        line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
        if not line:
            if current["event"]:
                try:
                    current["data"] = json.loads(current["data"]) if current["data"] else None
                except json.JSONDecodeError:
                    pass
                events.append(dict(current))
            current = {"event": None, "data": ""}
            continue
        if line.startswith("event:"):
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current["data"] += line[5:].strip()
    if current["event"]:
        events.append(current)
    return events


class FakeContextAgent:
    """run_context_agent 替身,只 yield 必要的事件让 chat handler 走完。"""
    def __init__(self, retrieved_context: str = ""):
        self.retrieved_context = retrieved_context

    def __call__(self, *args, **kwargs):
        yield {"type": "step",
               "step": {"phase": "stub", "message": "stub", "status": "running"}}
        yield {
            "type": "result",
            "retrieved_context": self.retrieved_context,
            "bundle": {"debug": {"cache_plan": {}}, "prompt": "stub prompt"},
            "steps": [],
            "agent_prompt": "stub",
            "curator_plan": {},
        }


class FakeGM:
    """GM 替身,respond_stream_with_tools yield 预设的 GM 文本(含禁词)。"""
    api_id = "stub"

    class _B:
        model_name = "stub"
        last_usage = {}

    _backend = _B()

    def __init__(self, scripted_response: str):
        self.scripted_response = scripted_response

    def respond_stream_with_tools(self, *args, **kwargs):
        # 模拟流式输出:把整段 GM 文本作为 text event 一次性 yield
        yield {"type": "text", "text": self.scripted_response}

    def curate_context(self, *args, **kwargs):
        return ""


# ────────────────────────────────────────────────────────────
# 核心 e2e 测试
# ────────────────────────────────────────────────────────────


class NarrativeGuardChatFlowE2E(unittest.TestCase):
    """task 86: chat handler 完整流程下,user_set + GM 禁词 → audit_log 必须记录。"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _send_chat(self, message: str, cookies: dict, *, gm_response: str = "",
                   retrieved: str = "",
                   command_tool_calls: list[dict] | None = None) -> tuple[list[dict], dict]:
        """发一条 chat 消息,返回 (sse_events, final_state)。

        task 86: command_tool_calls 给定时,monkeypatch command_agent.parse_set_command
        返回这些工具调用 (模拟 LLM 解析结果),避免真打 LLM。
        """
        import app as ui_mod
        original_rca = ui_mod.run_context_agent
        original_get_gm = ui_mod._get_gm

        ui_mod.run_context_agent = FakeContextAgent(retrieved_context=retrieved)
        fake_gm = FakeGM(gm_response)

        def _fake_get_gm(api_user):
            return fake_gm

        ui_mod._get_gm = _fake_get_gm

        # task 86: monkeypatch command_agent.parse_set_command (LLM 命令工具调用解析)
        import agents.command_agent as _cmd_agent
        original_parse = _cmd_agent.parse_set_command
        if command_tool_calls is not None:
            def _fake_parse(set_text, state_data, **kwargs):
                return list(command_tool_calls)
            _cmd_agent.parse_set_command = _fake_parse

        try:
            with self.client.stream("POST", "/api/chat",
                                     json={"message": message, "attachments": []},
                                     cookies=cookies) as resp:
                self.assertEqual(resp.status_code, 200,
                                 f"chat 应 200;实际 {resp.status_code}")
                events = _consume_sse(resp)
            state_resp = self.client.get("/api/state", cookies=cookies)
            return events, state_resp.json()
        finally:
            ui_mod.run_context_agent = original_rca
            ui_mod._get_gm = original_get_gm
            _cmd_agent.parse_set_command = original_parse

    def test_user_set_jump_with_forbidden_narrative_triggers_audit(self):
        """同回合 /set 时间跳跃 + GM 写禁词 → audit_log 必须含 time_jump_narrative_violation。

        task 86 主路径: LLM 工具调用 (command_agent → set_world_time) → update_time(source='user_set')
        → 设 user_set_jump_turn → guard 在 GM 响应后检测到禁词 → 写 audit_log。
        """
        u = register_user(self.client)
        cookies = u["cookies"]

        gm_text = (
            "冷,刺骨的冷。当你再次睁开眼睛时,四周已经不是柏林。"
            "时间被一双看不见的手生生拨回了最初的起点。"
        )
        # task 86 monkeypatch: 模拟 LLM 解析"/set 设置时间为火星·扬陆城内" → set_world_time 工具调用
        events, state = self._send_chat(
            "/set 设置时间为火星·扬陆城内",
            cookies, gm_response=gm_text,
            command_tool_calls=[
                {"name": "set_world_time", "input": {"target": "火星·扬陆城内"}}
            ],
        )

        # 1) audit_log 必须有 time_jump_narrative_violation
        audit = state.get("permissions", {}).get("audit_log", [])
        violations = [a for a in audit if a.get("kind") == "time_jump_narrative_violation"]
        self.assertEqual(
            len(violations), 1,
            f"应有 1 条 time_jump_narrative_violation;实际 {len(violations)}\n"
            f"全部 audit_log kinds: {[a.get('kind') for a in audit]}",
        )
        violation = violations[0]
        # 2) violation 含具体禁词标签
        labels = [v.get("label") for v in violation.get("violations", [])]
        self.assertTrue(
            any("刺骨" in (lb or "") for lb in labels),
            f"应命中刺骨开场;实际 labels={labels}",
        )
        self.assertTrue(
            any(("睁开眼" in (lb or "")) or ("再次X" in (lb or "")) for lb in labels),
            f"应命中睁开眼相关;实际 labels={labels}",
        )
        self.assertTrue(
            any("拨回" in (lb or "") for lb in labels),
            f"应命中拨回相关;实际 labels={labels}",
        )

        # 3) SSE 'agent' phase='timeline_guard' 事件被 yield
        agent_events = [e for e in events if e.get("event") == "agent"]
        guard_events = [
            e for e in agent_events
            if isinstance(e.get("data"), dict) and e["data"].get("phase") == "timeline_guard"
        ]
        self.assertGreaterEqual(
            len(guard_events), 1,
            f"应至少有一个 timeline_guard SSE 事件;"
            f"实际 agent phases: {[e.get('data',{}).get('phase') for e in agent_events]}",
        )

        # 4) user_set_jump_turn 被设上,且和 record_turn 后的 turn-1 相等
        # (apply_player_directives 在 turn=N 时调,record_turn 后 turn=N+1;
        # 所以 user_set_jump_turn 仍等于触发时的 N)
        timeline = state.get("world", {}).get("timeline", {})
        self.assertIsNotNone(
            timeline.get("user_set_jump_turn"),
            f"user_set_jump_turn 必须被设上;实际 timeline={timeline}",
        )

    def test_normal_input_no_user_set_no_guard(self):
        """对照:玩家正常输入,GM 即便写禁词也不触发 guard
        (因为没有 user_set 跳跃,GM 自由叙事不应被禁)。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        gm_text = "你从沉睡中醒来。"  # 禁词,但不在 user_set 当回合
        events, state = self._send_chat(
            "看看周围", cookies, gm_response=gm_text,
        )
        audit = state.get("permissions", {}).get("audit_log", [])
        violations = [a for a in audit if a.get("kind") == "time_jump_narrative_violation"]
        self.assertEqual(
            len(violations), 0,
            f"非 user_set 跳跃下,不应触发 guard;实际 {len(violations)} 条违规",
        )

    def test_user_set_clean_narrative_no_false_positive(self):
        """对照:/set 时间跳跃 + GM 干净叙事 → 不触发 guard。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        gm_text = "薇瑟帝国扬陆城的猩红日光斜射进谒见大厅,你站在长廊尽头。"
        events, state = self._send_chat(
            "/set 设置时间为火星·扬陆城内",
            cookies, gm_response=gm_text,
            command_tool_calls=[
                {"name": "set_world_time", "input": {"target": "火星·扬陆城内"}}
            ],
        )
        audit = state.get("permissions", {}).get("audit_log", [])
        violations = [a for a in audit if a.get("kind") == "time_jump_narrative_violation"]
        self.assertEqual(
            len(violations), 0,
            f"干净叙事不应被误报;实际 {len(violations)} 条违规",
        )

    def test_multi_tool_calls_in_one_set(self):
        """一条 /set 含多项操作 → LLM 拆成多个工具调用 → 全部落地。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        events, state = self._send_chat(
            "/set 设置时间为月球基地,关系蕾穆丽娜=极度依赖,主线=守护蕾穆丽娜",
            cookies, gm_response="月球的低重力让你白发轻盈飘起。",
            command_tool_calls=[
                {"name": "set_world_time", "input": {"target": "月球基地"}},
                {"name": "set_relationship",
                 "input": {"character": "蕾穆丽娜", "status": "极度依赖"}},
                {"name": "set_main_quest", "input": {"text": "守护蕾穆丽娜"}},
            ],
        )
        # 三个工具调用全部生效
        self.assertEqual(state["world"]["time"], "月球基地")
        self.assertEqual(state["relationships"]["蕾穆丽娜"], "极度依赖")
        self.assertEqual(state["memory"]["main_quest"], "守护蕾穆丽娜")
        # user_set_jump_turn 被设上 (set_world_time 通过 update_time(source='user_set'))
        self.assertIsNotNone(
            state["world"]["timeline"].get("user_set_jump_turn"),
            "set_world_time 工具必须触发 user_set_jump_turn",
        )

    def test_command_tools_skip_when_llm_returns_empty(self):
        """LLM 工具调用返回空 → fallback 到正则路径,旧 apply_player_directives 仍生效。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        events, state = self._send_chat(
            "/set 当前位置=雾港码头",
            cookies, gm_response="雾港的码头潮湿且寒冷。",
            command_tool_calls=[],  # LLM 没产工具调用 → fallback
        )
        # 正则 fallback 应仍把 location 改对
        self.assertEqual(state["player"]["current_location"], "雾港码头")

    def test_command_agent_exception_falls_back_to_regex(self):
        """LLM 抛异常 → fallback 到正则路径,/set 仍正确落地。"""
        u = register_user(self.client)
        cookies = u["cookies"]

        # monkeypatch 让 parse_set_command 抛异常
        import agents.command_agent as _cmd_agent
        original = _cmd_agent.parse_set_command

        def _raise(*a, **kw):
            raise RuntimeError("LLM 不可达 (模拟)")

        _cmd_agent.parse_set_command = _raise
        try:
            events, state = self._send_chat(
                "/set 当前位置=雾港码头",
                cookies, gm_response="到达雾港。",
                command_tool_calls=None,  # None = 不要 monkeypatch (走真路径,但已经被外层 patch)
            )
            self.assertEqual(state["player"]["current_location"], "雾港码头",
                             "LLM 异常时,正则 fallback 应让 /set 仍生效")
        finally:
            _cmd_agent.parse_set_command = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
