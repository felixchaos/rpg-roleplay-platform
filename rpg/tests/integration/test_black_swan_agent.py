"""tests.integration.test_black_swan_agent — 黑天鹅子代理单元+集成测试。

测试覆盖:
  - reality_snapshot 字段提取
  - proposal_tool_schema 结构
  - validator_token_blacklist (跨 phase token 拦截 / 通过)
  - validator_hard_constraints (locked var 违反 / 通过)
  - validator_timeline_anchor (未在场 NPC / 通过)
  - run_validators 聚合
  - maybe_trigger no_op 短路
  - maybe_trigger max_retries 耗尽
  - maybe_trigger 通过 → dispatch (mock dispatcher)
  - maybe_trigger llm_caller=None 跳过 (test mode)
  - maybe_trigger llm_caller 抛异常
"""
from __future__ import annotations

import sys
import types
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

# ── 辅助: 构造一个最小 GameState-like 对象 ────────────────────────────────────

def _make_state(
    *,
    current_phase: str = "测试篇",
    current_location: str = "测试城市",
    locked_vars: dict | None = None,
    active_entities: list | None = None,
    known_events: list | None = None,
    turn: int = 1,
) -> Any:
    state = MagicMock()
    user_vars = {}
    if locked_vars:
        for k, v in locked_vars.items():
            user_vars[k] = {"locked": True, "value": v}
    state.data = {
        "world": {
            "timeline": {"current_phase": current_phase},
            "time": "夜",
            "known_events": known_events or [],
        },
        "player": {"current_location": current_location},
        "worldline": {"user_variables": user_vars},
        "active_entities": active_entities or [],
        "turn": turn,
    }
    return state


# ── reality_snapshot ──────────────────────────────────────────────────────────

class TestRealitySnapshot(unittest.TestCase):
    def test_basic_fields(self):
        from agents.black_swan_agent import reality_snapshot
        state = _make_state(
            current_phase="柏林暗流篇",
            current_location="柏林内城",
            turn=5,
        )
        snap = reality_snapshot(state)
        self.assertEqual(snap["current_phase"], "柏林暗流篇")
        self.assertEqual(snap["current_location"], "柏林内城")
        self.assertEqual(snap["turn"], 5)
        self.assertIn("active_npcs", snap)
        self.assertIn("locked_variables", snap)
        self.assertIn("recent_events", snap)

    def test_locked_vars_extracted(self):
        from agents.black_swan_agent import reality_snapshot
        state = _make_state(locked_vars={"主角身份": "斯雷因"})
        snap = reality_snapshot(state)
        self.assertIn("主角身份", snap["locked_variables"])
        self.assertEqual(snap["locked_variables"]["主角身份"], "斯雷因")

    def test_active_npcs_extracted(self):
        from agents.black_swan_agent import reality_snapshot
        entities = [
            {"id": "npc1", "name": "蕾穆丽娜", "disposition": "friendly", "kind": "npc"},
            {"id": "npc2", "name": "扎兹巴鲁姆", "disposition": "neutral", "kind": "npc"},
        ]
        state = _make_state(active_entities=entities)
        snap = reality_snapshot(state)
        ids = [n["id"] for n in snap["active_npcs"]]
        self.assertIn("npc1", ids)
        self.assertIn("npc2", ids)

    def test_recent_events_limit(self):
        from agents.black_swan_agent import reality_snapshot
        events = [f"事件{i}" for i in range(10)]
        state = _make_state(known_events=events)
        snap = reality_snapshot(state)
        self.assertLessEqual(len(snap["recent_events"]), 5)
        # 最后 5 个
        self.assertEqual(snap["recent_events"][-1], "事件9")


# ── proposal_tool_schema ─────────────────────────────────────────────────────

class TestProposalToolSchema(unittest.TestCase):
    def test_schema_structure(self):
        from agents.black_swan_agent import proposal_tool_schema
        snap = {
            "active_npcs": [{"id": "npc1", "name": "A", "disposition": "x", "kind": "npc"}],
        }
        schema = proposal_tool_schema(snap)
        self.assertEqual(schema["name"], "propose_black_swan_event")
        self.assertIn("input_schema", schema)
        props = schema["input_schema"]["properties"]
        self.assertIn("event_kind", props)
        self.assertIn("no_op", props["event_kind"]["enum"])

    def test_npc_enum_in_schema(self):
        from agents.black_swan_agent import proposal_tool_schema
        snap = {
            "active_npcs": [
                {"id": "id_abc", "name": "A", "disposition": "x", "kind": "npc"},
            ],
        }
        schema = proposal_tool_schema(snap)
        items_enum = schema["input_schema"]["properties"]["involved_npcs"]["items"]["enum"]
        self.assertIn("id_abc", items_enum)


# ── validator_token_blacklist ─────────────────────────────────────────────────

class TestValidatorTokenBlacklist(unittest.TestCase):
    def setUp(self):
        self.overrides = {
            "phase_inference": {
                "rules": [
                    {"phase": "柏林暗流篇", "or_text_needles": ["柏林", "图卢兹"]},
                    {"phase": "初期穿越与火星线", "or_text_needles": ["火星", "初期"]},
                ]
            }
        }

    def test_passes_current_phase_token(self):
        from agents.black_swan_agent import validator_token_blacklist
        proposal = {"summary": "柏林城内发现异动。"}
        snap = {"current_phase": "柏林暗流篇"}
        ok, reason = validator_token_blacklist(proposal, snap, self.overrides)
        self.assertTrue(ok)

    def test_rejects_cross_phase_token(self):
        from agents.black_swan_agent import validator_token_blacklist
        # current phase = 柏林暗流篇, summary 含 "火星" 属于 "初期穿越与火星线"
        proposal = {"summary": "火星上出现了奇异现象。"}
        snap = {"current_phase": "柏林暗流篇"}
        ok, reason = validator_token_blacklist(proposal, snap, self.overrides)
        self.assertFalse(ok)
        self.assertIn("火星", reason)

    def test_passes_no_overrides(self):
        from agents.black_swan_agent import validator_token_blacklist
        proposal = {"summary": "任意内容"}
        snap = {"current_phase": "柏林暗流篇"}
        ok, _ = validator_token_blacklist(proposal, snap, None)
        self.assertTrue(ok)

    def test_passes_empty_summary(self):
        from agents.black_swan_agent import validator_token_blacklist
        proposal = {"summary": ""}
        snap = {"current_phase": "柏林暗流篇"}
        ok, _ = validator_token_blacklist(proposal, snap, self.overrides)
        self.assertTrue(ok)


# ── validator_hard_constraints ────────────────────────────────────────────────

class TestValidatorHardConstraints(unittest.TestCase):
    def test_passes_no_locked_vars(self):
        from agents.black_swan_agent import validator_hard_constraints
        proposal = {"summary": "随便一段叙述。"}
        snap = {"locked_variables": {}}
        ok, _ = validator_hard_constraints(proposal, snap)
        self.assertTrue(ok)

    def test_passes_unrelated_summary(self):
        from agents.black_swan_agent import validator_hard_constraints
        proposal = {"summary": "城外发生了一场大火。"}
        snap = {"locked_variables": {"主角": "斯雷因"}}
        ok, _ = validator_hard_constraints(proposal, snap)
        self.assertTrue(ok)

    def test_rejects_negation_of_locked_value(self):
        from agents.black_swan_agent import validator_hard_constraints
        proposal = {"summary": "斯雷因不再是主角了。"}
        snap = {"locked_variables": {"主角": "斯雷因"}}
        ok, reason = validator_hard_constraints(proposal, snap)
        self.assertFalse(ok)
        self.assertIn("斯雷因", reason)


# ── validator_timeline_anchor ─────────────────────────────────────────────────

class TestValidatorTimelineAnchor(unittest.TestCase):
    def test_passes_empty_npcs(self):
        from agents.black_swan_agent import validator_timeline_anchor
        proposal = {"involved_npcs": []}
        snap = {"active_npcs": [{"id": "npc1", "name": "A"}]}
        ok, _ = validator_timeline_anchor(proposal, snap)
        self.assertTrue(ok)

    def test_passes_valid_npcs(self):
        from agents.black_swan_agent import validator_timeline_anchor
        proposal = {"involved_npcs": ["npc1"]}
        snap = {"active_npcs": [{"id": "npc1", "name": "A"}]}
        ok, _ = validator_timeline_anchor(proposal, snap)
        self.assertTrue(ok)

    def test_rejects_absent_npc(self):
        from agents.black_swan_agent import validator_timeline_anchor
        proposal = {"involved_npcs": ["npc99"]}
        snap = {"active_npcs": [{"id": "npc1", "name": "A"}]}
        ok, reason = validator_timeline_anchor(proposal, snap)
        self.assertFalse(ok)
        self.assertIn("npc99", reason)


# ── maybe_trigger 行为 ────────────────────────────────────────────────────────

class TestMaybeTrigger(unittest.TestCase):
    def _make_minimal_state(self):
        return _make_state()

    def test_no_llm_caller_skips(self):
        """harness 适配后:enable_llm=False 显式禁用,llm_caller=None 时跳过。"""
        from agents.black_swan_agent import maybe_trigger
        state = self._make_minimal_state()
        result = maybe_trigger(
            state, user_id=1, save_id=1,
            llm_caller=None, enable_llm=False,
        )
        self.assertFalse(result["triggered"])
        self.assertIn("harness disabled", result["reason"])

    def test_anonymous_user_disables_harness(self):
        """user_id=0 (匿名) 时即便 enable_llm=True 也不调 harness,避免外部依赖。"""
        from agents.black_swan_agent import maybe_trigger
        state = self._make_minimal_state()
        result = maybe_trigger(
            state, user_id=0, save_id=1,
            llm_caller=None, enable_llm=True,
        )
        self.assertFalse(result["triggered"])
        self.assertIn("harness disabled", result["reason"])

    def test_noop_proposal_short_circuits(self):
        from agents.black_swan_agent import maybe_trigger
        state = self._make_minimal_state()
        caller = MagicMock(return_value={"event_kind": "no_op", "summary": ""})
        result = maybe_trigger(state, user_id=1, save_id=1, llm_caller=caller)
        self.assertFalse(result["triggered"])
        self.assertIn("no_op", result["reason"])
        caller.assert_called_once()

    def test_llm_caller_exception_returns_error(self):
        from agents.black_swan_agent import maybe_trigger
        state = self._make_minimal_state()
        caller = MagicMock(side_effect=RuntimeError("network error"))
        result = maybe_trigger(state, user_id=1, save_id=1, llm_caller=caller)
        self.assertFalse(result["triggered"])
        self.assertIn("network error", result["reason"])

    def test_max_retries_exhausted(self):
        from agents.black_swan_agent import maybe_trigger
        state = _make_state(current_phase="柏林暗流篇")

        # LLM always returns a proposal that fails 3d_timeline_anchor
        bad_proposal = {
            "event_kind": "npc_action",
            "summary": "某人行动了。",
            "involved_npcs": ["ghost_npc"],  # not in active_npcs → fails 3d
        }
        caller = MagicMock(return_value=bad_proposal)
        result = maybe_trigger(
            state, user_id=1, save_id=1, llm_caller=caller, max_retries=2
        )
        self.assertFalse(result["triggered"])
        self.assertEqual(caller.call_count, 3)  # initial + 2 retries
        self.assertIn("rejected", result["reason"])

    def test_passes_validators_and_dispatches(self):
        """All validators pass → dispatch_event is called (mocked)."""
        from agents.black_swan_agent import maybe_trigger
        entities = [{"id": "npc1", "name": "A", "disposition": "x", "kind": "npc"}]
        state = _make_state(active_entities=entities)

        good_proposal = {
            "event_kind": "new_event",
            "summary": "城中出现了风暴。",
            "involved_npcs": ["npc1"],
            "tools_to_call": [],
        }
        caller = MagicMock(return_value=good_proposal)

        # patch dispatch_event to avoid real dispatcher dependency
        with patch("agents.black_swan_agent.dispatch_event", return_value=[]) as mock_dispatch:
            result = maybe_trigger(state, user_id=1, save_id=1, llm_caller=caller)

        self.assertTrue(result["triggered"])
        self.assertEqual(result["retries"], 0)
        mock_dispatch.assert_called_once()
        # all 5 validators present
        self.assertEqual(len(result["validator_results"]), 5)
        self.assertTrue(all(v[1] for v in result["validator_results"]))


# ── run_validators 聚合 ───────────────────────────────────────────────────────

class TestRunValidators(unittest.TestCase):
    def test_returns_five_entries(self):
        from agents.black_swan_agent import run_validators
        proposal = {"event_kind": "new_event", "summary": "普通事件。"}
        snap = {"current_phase": "X", "active_npcs": [], "locked_variables": {}}
        results = run_validators(proposal, snap, script_id=None, script_overrides=None)
        self.assertEqual(len(results), 5)
        names = [r[0] for r in results]
        self.assertIn("3a_token_blacklist", names)
        self.assertIn("3b_npc_presence", names)
        self.assertIn("3c_hard_constraints", names)
        self.assertIn("3d_timeline_anchor", names)
        self.assertIn("3e_independent_critic", names)


if __name__ == "__main__":
    unittest.main()
