"""
test_time_jump_mixed_tags.py — task 32 回归

用户实测：/set 完成后发"请把剧情时间推进到次日清晨，先让子代理检查冲突"。
pending_jump 已建好，但主 GM 结束后输出混合标签：
  【时间跳跃确认：待确认（当前处于 pending_confirmation 状态）】
  【询问玩家：是否确认？】
  【设定校验：冲突】
原 _gm_is_asking_for_time_confirm 看到任何含"时间跳跃确认"的标签就立刻 return False，
导致 apply_structured_updates 的 "时间跳跃确认" 分支直接调 confirm_time_jump 锁时间。

修复：
  - _gm_is_asking_for_time_confirm 先扫所有 tag 把 pending/explicit 信号分类，pending 优先
  - apply_structured_updates "时间跳跃确认" 分支检查 value 是否含 pending markers 或
    asking_for_confirm 为 True；任一成立就保留 pending、不 confirm
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

from state import DEFAULT_STATE, GameState, _gm_is_asking_for_time_confirm  # noqa: E402


def _state_with_pending(initial: str = "三日后的子夜") -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.update_time(initial, source="player_set")
    s.update_location("雾港灯塔")
    # 玩家自然语言请求 → 建 pending_jump
    s.apply_player_directives("请把剧情时间推进到次日清晨，地点仍在雾港码头；先让子代理检查时间线冲突，不要直接跳过确认。")
    return s


class MixedConfirmAndPendingTags(unittest.TestCase):
    def test_helper_pending_signal_overrides_explicit_confirm(self):
        """task 32 helper：value 含『待确认』的时间跳跃确认 tag → 应识别为 asking"""
        # value 含 "待确认"
        self.assertTrue(_gm_is_asking_for_time_confirm(
            "",
            ["时间跳跃确认：待确认（当前处于 pending_confirmation 状态）"],
        ), "value=待确认 应被识别为 asking")
        # value pending
        self.assertTrue(_gm_is_asking_for_time_confirm(
            "",
            ["时间跳跃确认：pending（waiting for user）"],
        ), "value=pending 应被识别为 asking")
        # 混合：一个明确同意 + 一个询问 → 应保守为 asking
        self.assertTrue(_gm_is_asking_for_time_confirm(
            "",
            ["时间跳跃确认：次日清晨", "询问玩家：是否确认？"],
        ), "明确确认 + 询问 同时存在时应保守为 asking")
        # 设定冲突也算 pending 信号
        self.assertTrue(_gm_is_asking_for_time_confirm(
            "",
            ["时间跳跃确认：次日清晨", "设定校验：冲突"],
        ), "时间跳跃确认 + 设定校验冲突 → 应识别为 asking")
        # 真·确认（无任何 pending 信号）→ 应 return False
        self.assertFalse(_gm_is_asking_for_time_confirm(
            "时间正式推进到次日清晨。",
            ["时间跳跃确认：次日清晨"],
        ), "干净的时间跳跃确认应识别为真 confirm")

    def test_mixed_tag_does_not_lock_time(self):
        """核心回归：GM 输出『时间跳跃确认：待确认』+ 询问 + 冲突 → world.time 不应被锁"""
        s = _state_with_pending(initial="三日后的子夜")
        original_time = s.data["world"]["time"]
        original_anchor = s.data["world"]["timeline"]["anchor_state"]
        original_pending = s.data["world"]["timeline"].get("pending_jump")
        self.assertEqual(original_anchor, "pending_confirmation")
        self.assertIsNotNone(original_pending)

        gm_response = (
            "我注意到玩家希望推进时间，先让子代理检查冲突。"
            "【时间跳跃确认：待确认（当前处于 pending_confirmation 状态）】"
            "【询问玩家：是否确认时间推进到次日清晨？】"
            "【设定校验：冲突】"
        )
        updates = s.apply_structured_updates(gm_response)

        # 关键断言：world.time 不应被改写
        self.assertEqual(s.data["world"]["time"], original_time,
            f"task 32：混合标签下 world.time 不应被锁；"
            f"actual={s.data['world']['time']!r} updates={updates}")
        # pending_jump 应保留
        self.assertEqual(s.data["world"]["timeline"].get("anchor_state"), "pending_confirmation",
            f"anchor_state 应保持 pending_confirmation；实际 {s.data['world']['timeline'].get('anchor_state')!r}")
        self.assertIsNotNone(s.data["world"]["timeline"].get("pending_jump"),
            f"pending_jump 应保留；实际 {s.data['world']['timeline'].get('pending_jump')!r}")
        # updates 里不应出现 "时间线锁定" 或 "时间跳跃确认：次日清晨"
        for u in updates:
            self.assertNotIn("时间线锁定", u,
                f"混合标签下不应产生『时间线锁定』update：{u!r}（all={updates}）")
            # 确认 update 的内容必须是『保留待确认』而不是已锁定的时间字符串
            if u.startswith("时间跳跃确认：") and not u.startswith("时间跳跃确认保留待确认"):
                self.fail(f"task 32：不应出现『时间跳跃确认：<真实时间>』update：{u!r}")

    def test_explicit_confirm_alone_still_locks(self):
        """对照：只有干净的『时间跳跃确认：次日清晨』，没有任何 pending 信号 → 仍可正常锁。
        task 35 之后：玩家本轮 NL 触发的 pending 不许 GM 同轮锁；下一 turn 才行。"""
        s = _state_with_pending(initial="三日后的子夜")
        # task 35：GM 必须在 pending 之后的下一 turn 才能 confirm
        s.data["turn"] = int(s.data["turn"]) + 1
        gm = "好的，时间正式推进到次日清晨。【时间跳跃确认：次日清晨】"
        s.apply_structured_updates(gm)
        self.assertEqual(s.data["world"]["time"], "次日清晨",
            "干净的 confirm 应正常生效")
        self.assertEqual(s.data["world"]["timeline"]["anchor_state"], "locked")
        self.assertIsNone(s.data["world"]["timeline"].get("pending_jump"))

    def test_time_key_lock_blocked_when_value_pending(self):
        """同样回归：GM 输出【时间：次日清晨】但同时有等待信号 → 不锁（task 22 已覆盖部分，这里再压一遍）"""
        s = _state_with_pending(initial="三日后的子夜")
        gm = "请玩家确认。【时间：次日清晨】【询问玩家：是否确认？】"
        s.apply_structured_updates(gm)
        self.assertEqual(s.data["world"]["time"], "三日后的子夜",
            "有询问 + 时间 tag 时 world.time 仍不应被锁")
        self.assertEqual(s.data["world"]["timeline"]["anchor_state"], "pending_confirmation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
