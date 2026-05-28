"""
test_user_set_time_jump_guard.py
================================

用户报告:用 `/set 设置时间为火星·薇瑟帝国扬陆城内` 切换时间线后,
GM 把它叙事成"穿越/醒来发现/拨回时钟/时间被拉回最初"等过渡剧情,而不是
直接在新时间点开场。

修复 (双层):
  · 主防线: context_engine._timeline_layer 检测 last_transition.source=='user_set'
    且 turn==当前 turn 时,给 GM prompt 明示禁止过渡叙事
  · belt-and-suspenders: timeline_narrative_guard.detect_time_jump_violations
    扫 GM 文本,命中禁词则写 audit_log + 前端 SSE 警告(不强 strip)

本测试 4 层:
  Layer A — _timeline_layer prompt 在 user_set 当回合给出禁令
  Layer B — _timeline_layer 在非 user_set / 非当回合 时不给禁令
  Layer C — detect_time_jump_violations 纯函数单元测试
  Layer D — record_violations_to_audit 写 audit_log 结构正确
"""
from __future__ import annotations

import copy as _copy
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


# ────────────────────────────────────────────────────────────
# Layer A + B: _timeline_layer 行为
# ────────────────────────────────────────────────────────────


class TimelineLayerUserSetWarning(unittest.TestCase):
    """user_set 时间跳跃当回合,_timeline_layer 必须给 GM 明示禁令。"""

    def _state_with(self, *, source="user_set", turn=5, last_turn=5,
                    from_label="柏林", to_label="火星·薇瑟帝国扬陆城内"):
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = turn
        g.data["world"]["timeline"]["last_transition"] = {
            "from": from_label,
            "to": to_label,
            "source": source,
            "turn": last_turn,
        }
        g.data["world"]["timeline"]["pending_jump"] = None
        g.data["world"]["timeline"]["current_label"] = to_label
        g.data["world"]["time"] = to_label
        return g

    def _layer_text(self, state) -> str:
        from context_engine import _timeline_layer
        return _timeline_layer(state).get("text", "")

    def test_user_set_same_turn_emits_restrictions(self):
        state = self._state_with(source="user_set", turn=5, last_turn=5)
        text = self._layer_text(state)
        self.assertIn("覆盖式跳跃", text,
            "user_set 当回合应明示是'覆盖式跳跃'")
        # 禁词清单
        for kw in ("穿越", "醒来", "再次睁开眼", "拨回时钟", "重启世界",
                   "重置场景", "刺骨的冷", "失忆", "无意识"):
            self.assertIn(kw, text,
                f"禁词清单必须含 {kw}")
        # 必须含明示"既定事实"指令
        self.assertIn("既定事实", text)
        self.assertIn("镜头切到新时间点", text)

    def test_user_set_old_turn_does_not_emit_restrictions(self):
        """user_set 跳跃发生在过去 turn,不再约束当前 GM (玩家已经接受了新时间线)。"""
        state = self._state_with(source="user_set", turn=10, last_turn=5)
        text = self._layer_text(state)
        self.assertNotIn("覆盖式跳跃", text,
            "user_set 不是当回合时,不应再发禁令")
        # 应该走"没有待确认时间跳跃"普通分支
        self.assertIn("没有待确认时间跳跃", text)

    def test_other_source_does_not_emit_restrictions(self):
        """source!=user_set (system / initial / gm_confirmed) 不触发禁令。"""
        state = self._state_with(source="initial", turn=5, last_turn=5)
        text = self._layer_text(state)
        self.assertNotIn("覆盖式跳跃", text)

    def test_pending_jump_still_takes_priority(self):
        """pending_jump 存在时仍走 pending 分支 (不被 user_set 检测覆盖)。"""
        state = self._state_with(source="user_set", turn=5, last_turn=5)
        state.data["world"]["timeline"]["pending_jump"] = {
            "from": "X", "to": "Y", "status": "awaiting_gm_confirmation",
        }
        text = self._layer_text(state)
        # pending 分支会输出"pending 状态"
        self.assertIn("pending 状态", text)


# ────────────────────────────────────────────────────────────
# Layer C: detect_time_jump_violations 单元
# ────────────────────────────────────────────────────────────


class DetectViolationsUnit(unittest.TestCase):
    def _state_user_set_now(self, turn=5):
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = turn
        g.data["world"]["timeline"]["last_transition"] = {
            "source": "user_set", "turn": turn,
            "from": "X", "to": "Y",
        }
        return g

    def test_detects_chuanyue_in_text(self):
        from agents.timeline_narrative_guard import detect_time_jump_violations
        state = self._state_user_set_now()
        text = "时间被一双看不见的手生生拨回了最初的起点。"
        violations = detect_time_jump_violations(text, state)
        self.assertGreater(len(violations), 0)
        labels = [v["pattern_label"] for v in violations]
        # 应该命中 "时间被拨回" 或 "拨回原点"
        self.assertTrue(
            any("拨回" in lb for lb in labels),
            f"应命中拨回相关禁词,实际: {labels}",
        )

    def test_detects_user_zaici_kaiyan(self):
        from agents.timeline_narrative_guard import detect_time_jump_violations
        state = self._state_user_set_now()
        text = "当你再次睁开眼睛时,四周已经不是柏林。"
        violations = detect_time_jump_violations(text, state)
        self.assertGreater(len(violations), 0)
        labels = " ".join(v["pattern_label"] for v in violations)
        self.assertTrue("睁开眼" in labels or "再次X" in labels,
            f"应命中 '再次睁开眼' 或 '当你再次X时',实际: {labels}")

    def test_detects_cold_opening(self):
        from agents.timeline_narrative_guard import detect_time_jump_violations
        state = self._state_user_set_now()
        text = "冷,刺骨的冷。"
        violations = detect_time_jump_violations(text, state)
        self.assertGreater(len(violations), 0)
        self.assertTrue(
            any("刺骨" in v["pattern_label"] for v in violations),
            "应命中'刺骨的冷开场'",
        )

    def test_clean_text_no_violation(self):
        from agents.timeline_narrative_guard import detect_time_jump_violations
        state = self._state_user_set_now()
        text = "薇瑟帝国扬陆城的大厅笼罩在猩红的日光下,蕾穆丽娜坐在精致的轮椅上看着你。"
        violations = detect_time_jump_violations(text, state)
        self.assertEqual(violations, [],
            "正常的新时间点叙事不应被误报")

    def test_no_user_set_jump_skips_check(self):
        """如果 last_transition 不是 user_set,不应检测 (避免误伤普通叙事)。"""
        from agents.timeline_narrative_guard import detect_time_jump_violations
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = 5
        g.data["world"]["timeline"]["last_transition"] = {
            "source": "initial", "turn": 5, "from": "", "to": "X",
        }
        text = "冷,刺骨的冷。时间被拨回最初。"  # 满天禁词
        violations = detect_time_jump_violations(text, g)
        self.assertEqual(violations, [],
            "非 user_set 跳跃不应触发检测")

    def test_old_jump_skips_check(self):
        """user_set 跳跃但在过去 turn,不应再检测。"""
        from agents.timeline_narrative_guard import detect_time_jump_violations
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = 10
        g.data["world"]["timeline"]["last_transition"] = {
            "source": "user_set", "turn": 5, "from": "X", "to": "Y",
        }
        text = "冷,刺骨的冷。时间被拨回。"
        violations = detect_time_jump_violations(text, g)
        self.assertEqual(violations, [],
            "user_set 跳跃发生在过去 turn,本回合不应再检测")


# ────────────────────────────────────────────────────────────
# Layer D: record_violations_to_audit 写 audit_log
# ────────────────────────────────────────────────────────────


class RecordViolationsAudit(unittest.TestCase):
    def test_audit_log_entry_structure(self):
        from agents.timeline_narrative_guard import record_violations_to_audit
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = 5
        violations = [
            {"pattern_label": "穿越叙事", "match": "穿越事件"},
            {"pattern_label": "刺骨的冷开场", "match": "冷,刺骨的冷"},
        ]
        record_violations_to_audit(g, violations)
        audit = g.data["permissions"]["audit_log"]
        self.assertEqual(len(audit), 1)
        entry = audit[0]
        self.assertEqual(entry["kind"], "time_jump_narrative_violation")
        self.assertEqual(entry["source"], "timeline_narrative_guard")
        self.assertEqual(entry["turn"], 5)
        self.assertEqual(len(entry["violations"]), 2)
        self.assertIn("hint", entry)
        self.assertIn("retry", entry["hint"])  # 提示用户可以 /retry

    def test_empty_violations_no_op(self):
        from agents.timeline_narrative_guard import record_violations_to_audit
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        record_violations_to_audit(g, [])
        self.assertEqual(g.data["permissions"].get("audit_log", []), [])


# ────────────────────────────────────────────────────────────
# Layer E: chat 流程接入 — 静态扫源验证
# ────────────────────────────────────────────────────────────


class ChatFlowIntegratesGuard(unittest.TestCase):
    """app.py chat 主流程必须 import + 调用 detect_time_jump_violations。"""

    @classmethod
    def setUpClass(cls):
        cls.app_text = (PROJECT / "rpg" / "app.py").read_text(encoding="utf-8")

    def test_imports_guard(self):
        self.assertIn("from timeline_narrative_guard import", self.app_text,
            "app.py 必须 import timeline_narrative_guard")
        self.assertIn("detect_time_jump_violations", self.app_text)
        self.assertIn("record_violations_to_audit", self.app_text)

    def test_emits_sse_warning_event(self):
        self.assertIn('"phase": "timeline_guard"', self.app_text,
            "chat handler 应 yield SSE agent 事件 phase=timeline_guard 给前端")


if __name__ == "__main__":
    unittest.main(verbosity=2)
