"""
test_set_explicit_phase_wins.py — task 28 回归

复现：用户在一条 /set 里同时写「时间改为...」和 world.timeline.current_phase=...
显式值，原代码顺序是：
  1. apply_state_write("world.timeline.current_phase=雾港灯塔测试")
  2. update_time("三日后的子夜", source="user_set")
     → _phase_for_time("三日后的子夜") = "玩家分支"
     → 把第 1 步写好的 current_phase 冲掉
结果：用户显式指定的 current_phase 被自动派生的 phase 覆盖。

修复：apply_set_directive 顺序改为
  时间 → 位置 → 显式 path=value（兜底覆盖）
让用户显式赋值始终最后赢。
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

from state import DEFAULT_STATE, GameState  # noqa: E402


def _make_state() -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.update_time("开局时刻", source="bootstrap")
    s.update_location("起始地")
    return s


class ExplicitAssignmentWinsOverAutoPhase(unittest.TestCase):
    def test_explicit_current_phase_survives_update_time(self):
        """核心回归：同一条 /set 含「时间改为...」和 current_phase=显式，最终 phase
        应是显式值，而不是 _phase_for_time 推断的『玩家分支』。"""
        s = _make_state()
        updates = s.apply_player_directives(
            "/set 时间改为三日后的子夜，"
            "地点改为雾港灯塔，"
            "player.name=测试旅人，"
            "memory.current_objective=验证 /set 自然语言改参，"
            "world.timeline.current_phase=雾港灯塔测试"
        )

        # 时间/位置/name/objective 应都生效（原始 bug 之外的 baseline 还在）
        self.assertEqual(s.data["world"]["time"], "三日后的子夜",
            f"world.time 应是 /set 指定值；实际 {s.data['world']['time']!r} updates={updates}")
        self.assertEqual(s.data["player"]["current_location"], "雾港灯塔",
            f"current_location 应被 /set 写为雾港灯塔；实际 {s.data['player']['current_location']!r}")
        self.assertEqual(s.data["player"]["name"], "测试旅人",
            f"player.name 应被显式 path=value 写为测试旅人；实际 {s.data['player']['name']!r}")
        self.assertEqual(s.data["memory"]["current_objective"], "验证 /set 自然语言改参",
            f"memory.current_objective 应被显式覆盖；实际 {s.data['memory']['current_objective']!r}")

        # 关键断言：current_phase 必须是用户显式值，不是 _phase_for_time 推断
        timeline = s.data["world"]["timeline"]
        self.assertEqual(timeline["current_phase"], "雾港灯塔测试",
            f"task 28：world.timeline.current_phase 应等于用户显式值；"
            f"实际 {timeline['current_phase']!r}（_phase_for_time 又把它冲了）")

        # 同时 anchor_state=locked, pending_jump=None（update_time 的副作用应保留）
        self.assertEqual(timeline.get("anchor_state"), "locked",
            f"anchor_state 应 locked；实际 {timeline.get('anchor_state')!r}")
        self.assertIsNone(timeline.get("pending_jump"),
            f"pending_jump 应为 None；实际 {timeline.get('pending_jump')!r}")

    def test_set_without_explicit_phase_falls_back_to_auto(self):
        """对照：如果用户没显式给 current_phase，则保留 _phase_for_time 的自动推断（不破坏老行为）"""
        s = _make_state()
        s.apply_player_directives(
            "/set 时间改为三日后的子夜，地点改为雾港灯塔"
        )
        # "三日后的子夜" 不含 _phase_for_time 关键字 → 自动落『玩家分支』
        self.assertEqual(s.data["world"]["timeline"]["current_phase"], "玩家分支",
            "无显式 current_phase 时仍应走 _phase_for_time 自动推断")

    def test_set_explicit_phase_with_柏林_keyword_still_explicit_wins(self):
        """对照：即便时间含『柏林』等会被 _phase_for_time 识别成『柏林暗流篇』
        的关键字，显式值仍要赢。"""
        s = _make_state()
        s.apply_player_directives(
            "/set 时间改为柏林夜战之后，world.timeline.current_phase=我设的阶段"
        )
        self.assertEqual(s.data["world"]["timeline"]["current_phase"], "我设的阶段",
            "显式 current_phase 应永远赢，包括 _phase_for_time 会识别的关键字时间")

    def test_assignment_order_does_not_corrupt_other_paths(self):
        """对照：重排序不应该破坏不相关的写入（worldline.user_variables.X 等）"""
        s = _make_state()
        # 用『；』分隔（task 28 修复只动顺序，不动分词；逗号 + 时间裸语句的分词限制
        # 是 _extract_set_assignments 的既有行为，不在本次修复范围）
        s.apply_player_directives(
            "/set worldline.user_variables.灯塔状态=未点燃；"
            "时间改为次日清晨；"
            "memory.current_objective=保护灯塔"
        )
        self.assertEqual(s.data["world"]["time"], "次日清晨")
        self.assertEqual(s.data["memory"]["current_objective"], "保护灯塔")
        uvars = (s.data.get("worldline") or {}).get("user_variables") or {}
        self.assertIn("灯塔状态", uvars,
            f"user_variables 应写入；keys={list(uvars.keys())}")
        v = uvars["灯塔状态"]
        v_value = v.get("value") if isinstance(v, dict) else v
        self.assertEqual(v_value, "未点燃")


if __name__ == "__main__":
    unittest.main(verbosity=2)
