"""
test_user_set_jump_survives_gm_overwrite.py — task 86 回归

用户报告（实际玩存档复盘）：

  turn 7 玩家 `/set 设置时间为书籍剧情开始，此时在火星而不是柏林`
  turn 7 GM 输出："冷,刺骨的冷。当你再次睁开眼睛时...
                  时间被一双看不见的手生生拨回了最初的起点..."
  → 显然命中 timeline_narrative_guard 的多个禁词模板,
    但 audit_log 里**没有** time_jump_narrative_violation。

根因:
  · apply_set_directive → update_time(source="user_set")
    设置 last_transition.source="user_set"
  · 但 GM 响应中又通过 JSON op / extractor 调 update_time(source="gm")
    把 last_transition.source 覆盖成 "gm"
  · detect_time_jump_violations 看 source!=user_set → return [],guard 失效

修复 (task 86):
  · state.update_time(source="user_set") 同时写
    world.timeline.user_set_jump_turn = state.turn
  · 后续非 user_set 的 update_time 不会清掉 user_set_jump_turn
  · detect_time_jump_violations 优先看 user_set_jump_turn == current_turn,
    回退看 last_transition.source (兼容旧字段)

本测试 3 层:
  Layer A — update_time(source="user_set") 设置 user_set_jump_turn
  Layer B — 后续 update_time(source="gm") 不清掉 user_set_jump_turn
  Layer C — detect_time_jump_violations 在 GM 覆盖 source 后仍能命中禁词
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import GameState, DEFAULT_STATE  # noqa: E402
from agents.timeline_narrative_guard import detect_time_jump_violations  # noqa: E402


class UserSetJumpTurnSetOnUserSet(unittest.TestCase):
    """Layer A: update_time(source='user_set') 设置 timeline.user_set_jump_turn。"""

    def test_user_set_sets_jump_turn(self):
        g = GameState(copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = 7
        g.update_time("火星·扬陆城", source="user_set")
        self.assertEqual(
            g.data["world"]["timeline"].get("user_set_jump_turn"), 7,
            "user_set update_time 必须设置 user_set_jump_turn=当前 turn",
        )

    def test_other_sources_do_not_set_jump_turn(self):
        """非 user_set 的 update_time 不该写 user_set_jump_turn。"""
        for source in ("system", "gm", "gm_confirmed", "script_opening"):
            g = GameState(copy.deepcopy(DEFAULT_STATE))
            g.data["turn"] = 3
            g.update_time("X", source=source)
            self.assertIsNone(
                g.data["world"]["timeline"].get("user_set_jump_turn"),
                f"source={source} update_time 不应设置 user_set_jump_turn",
            )


class UserSetJumpTurnSurvivesGMOverwrite(unittest.TestCase):
    """Layer B: GM 后续调 update_time(source='gm') 不应清掉 user_set_jump_turn。"""

    def test_gm_overwrite_keeps_user_set_jump_turn(self):
        g = GameState(copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = 7
        # 玩家 /set 触发 user_set 跳跃
        g.update_time("火星·扬陆城", source="user_set")
        self.assertEqual(
            g.data["world"]["timeline"]["user_set_jump_turn"], 7,
        )
        # GM 在同回合的响应里通过 JSON op 又 update_time(source="gm")
        g.update_time("火星·扬陆城·谒见大厅", source="gm")
        # last_transition.source 已被 gm 覆盖
        self.assertEqual(
            g.data["world"]["timeline"]["last_transition"]["source"], "gm",
        )
        # 但 user_set_jump_turn 仍然保留
        self.assertEqual(
            g.data["world"]["timeline"]["user_set_jump_turn"], 7,
            "GM 后续 update_time 不应清掉 user_set_jump_turn(guard 依靠它生效)",
        )


class DetectViolationsAfterGMOverwrite(unittest.TestCase):
    """Layer C: 经历 GM source 覆盖后, detect_time_jump_violations 仍生效。"""

    def _state_user_set_then_gm_overwrite(self, turn=7):
        g = GameState(copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = turn
        g.update_time("火星·扬陆城", source="user_set")
        # 模拟 GM 在响应中改写 last_transition.source
        g.update_time("火星·扬陆城·谒见大厅", source="gm")
        return g

    def test_detects_cold_opening_after_gm_overwrite(self):
        """实际玩存档触发的核心场景:
        last_transition.source="gm" 但 user_set_jump_turn=cur_turn → 仍检测。"""
        g = self._state_user_set_then_gm_overwrite()
        text = "冷,刺骨的冷。当你再次睁开眼睛时,四周已经不是柏林。"
        violations = detect_time_jump_violations(text, g)
        self.assertGreater(
            len(violations), 0,
            f"GM 覆盖 source 后,guard 必须仍能命中禁词;实际: {violations}",
        )
        labels = " ".join(v["pattern_label"] for v in violations)
        self.assertIn("刺骨", labels)
        self.assertTrue(
            "睁开眼" in labels or "再次X" in labels,
            f"应命中睁开眼相关,实际: {labels}",
        )

    def test_detects_bo_hui_after_gm_overwrite(self):
        """同上,但用'时间被拨回'禁词。"""
        g = self._state_user_set_then_gm_overwrite()
        text = "时间被一双看不见的手生生拨回了最初的起点。"
        violations = detect_time_jump_violations(text, g)
        self.assertGreater(len(violations), 0)
        self.assertTrue(
            any("拨回" in v["pattern_label"] for v in violations),
            f"应命中'拨回'禁词,实际: {violations}",
        )

    def test_clean_text_not_falsely_flagged(self):
        """干净叙事不应被误报,即使在 user_set_jump_turn 当回合。"""
        g = self._state_user_set_then_gm_overwrite()
        text = "薇瑟帝国扬陆城的大厅笼罩在猩红的日光下,蕾穆丽娜坐在轮椅上看着你。"
        violations = detect_time_jump_violations(text, g)
        self.assertEqual(violations, [])

    def test_next_turn_no_longer_checks(self):
        """玩家跳跃后下一个回合(turn 推进了),不再检测,允许 GM 自由叙事。"""
        g = self._state_user_set_then_gm_overwrite(turn=7)
        g.data["turn"] = 8  # 模拟下一回合
        text = "冷,刺骨的冷。"
        violations = detect_time_jump_violations(text, g)
        self.assertEqual(
            violations, [],
            "turn 已经推进,不应再检测上一回合的 user_set 跳跃",
        )


class BackwardCompatLastTransitionPath(unittest.TestCase):
    """旧存档没有 user_set_jump_turn 字段,但 last_transition.source="user_set",
    guard 走兼容路径仍应工作。"""

    def test_old_save_with_only_last_transition(self):
        g = GameState(copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = 5
        g.data["world"]["timeline"]["last_transition"] = {
            "source": "user_set", "turn": 5, "from": "X", "to": "Y",
        }
        # 故意不设 user_set_jump_turn,模拟旧存档
        g.data["world"]["timeline"].pop("user_set_jump_turn", None)
        text = "冷,刺骨的冷。"
        violations = detect_time_jump_violations(text, g)
        self.assertGreater(
            len(violations), 0,
            "旧存档(无 user_set_jump_turn)仍应通过 last_transition.source 兼容路径触发",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
