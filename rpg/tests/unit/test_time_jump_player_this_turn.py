"""
test_time_jump_player_this_turn.py — task 35 回归

复现：玩家本轮发自然语言『请把剧情时间推进到次日清晨，先让子代理检查冲突』。
apply_player_directives 已建好 pending_jump（pending.turn == state.turn）。然后
主 GM 流式输出，最终结构化标签里包含 【时间：次日清晨】 但 不包含 待确认/询问 等
语境信号。原代码（task 22/32 修复后仍然）只看 GM 文本侧是否 asking → 不 asking
就允许锁定，绕过 pending。

修复：在 apply_structured_updates 入口就检查 pending_jump.turn == state.turn —— 玩家
本轮 NL 触发的 pending，GM 同一轮一律视为 asking_for_confirm，禁止任何路径锁
（包括 【时间：X】 / prose extract / 【时间跳跃确认：X】）。/set 不走 pending（直接
update_time），不受影响。
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


def _state_with_player_pending(initial: str = "四日后的黄昏") -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.update_time(initial, source="player_set")
    s.update_location("雾港码头")
    # 玩家本轮自然语言请求时间推进 → pending_jump（pending.turn == state.turn）
    s.apply_player_directives(
        "请把剧情时间推进到次日清晨，地点仍在雾港码头；先让子代理检查时间线冲突，不要直接跳过确认。"
    )
    return s


class PlayerThisTurnPendingBlocksGmLock(unittest.TestCase):
    def test_player_this_turn_pending_blocks_clean_gm_time_lock(self):
        """核心回归：玩家本轮 NL 触发 pending；GM 输出干净的【时间：次日清晨】
        （无 pending 信号文本）→ 仍不能锁。"""
        s = _state_with_player_pending(initial="四日后的黄昏")
        original_time = s.data["world"]["time"]
        pending_before = s.data["world"]["timeline"].get("pending_jump")
        self.assertIsNotNone(pending_before)
        self.assertEqual(pending_before["turn"], s.data["turn"],
            "test 前置：pending_jump.turn 必须等于 state.turn（同 turn 创建）")

        # GM 没有 pending 文本，只是直接说"好的，时间推进到次日清晨"
        gm_response = "好的，时间推进到次日清晨。【时间：次日清晨】"
        updates = s.apply_structured_updates(gm_response)

        # 关键：world.time 必须保留原值
        self.assertEqual(s.data["world"]["time"], original_time,
            f"task 35：玩家本轮 NL 触发 pending 时 GM 不能锁定；"
            f"actual={s.data['world']['time']!r} updates={updates}")
        # pending_jump 应保留
        self.assertEqual(s.data["world"]["timeline"].get("anchor_state"), "pending_confirmation",
            f"anchor_state 应保持 pending_confirmation；实际 {s.data['world']['timeline'].get('anchor_state')!r}")
        self.assertIsNotNone(s.data["world"]["timeline"].get("pending_jump"))
        # last_transition 不应被改写
        last_trans = s.data["world"]["timeline"].get("last_transition")
        if last_trans:
            self.assertNotEqual(last_trans.get("source"), "gm",
                f"task 35：last_transition.source 不应是 gm（不允许锁）；actual={last_trans!r}")

    def test_player_this_turn_pending_blocks_jump_confirm_tag(self):
        """GM 输出【时间跳跃确认：次日清晨】（干净 confirm tag，无 pending 文本）→ 仍不能锁"""
        s = _state_with_player_pending(initial="四日后的黄昏")
        original_time = s.data["world"]["time"]
        s.apply_structured_updates("好的。【时间跳跃确认：次日清晨】")
        self.assertEqual(s.data["world"]["time"], original_time,
            f"task 35：玩家本轮 NL 触发 pending 时 GM 干净 confirm 也不能锁；"
            f"actual={s.data['world']['time']!r}")
        self.assertEqual(s.data["world"]["timeline"].get("anchor_state"), "pending_confirmation")

    def test_player_set_force_locks_even_with_pending(self):
        """对照：/set 直接走 update_time（user_set），不通过 pending，依旧应该锁；
        因为 /set 是『强制设定』，玩家明确要求覆盖。"""
        s = _state_with_player_pending(initial="四日后的黄昏")
        # 玩家用 /set 强制锁到次日清晨（这清空 pending）
        s.update_time("次日清晨", source="user_set")
        self.assertEqual(s.data["world"]["time"], "次日清晨")
        self.assertIsNone(s.data["world"]["timeline"].get("pending_jump"))

    def test_old_pending_from_previous_turn_does_not_block_gm(self):
        """对照：pending 是上一轮创建的（pending.turn < state.turn）→ GM 可正常锁。"""
        s = _state_with_player_pending(initial="四日后的黄昏")
        # 模拟 turn 已递增到下一轮（pending 是上一轮的）
        s.data["turn"] = int(s.data.get("turn", 0)) + 1
        # 现在 pending.turn != state.turn
        s.apply_structured_updates("好的，时间推进到次日清晨。【时间：次日清晨】")
        self.assertEqual(s.data["world"]["time"], "次日清晨",
            "对照：上一轮的 pending 不该长期阻挡 GM；只阻挡同 turn 内 NL → 立即 GM 锁")


if __name__ == "__main__":
    unittest.main(verbosity=2)
