"""
test_time_jump_pending.py — UI 审计任务 22 后端契约

GM 自然语言时间跳跃在 pending_confirmation 期间不应被询问文本里的目标时间
误锁。即便 GM 正文/结构化标签里出现目标时间，只要存在 pending_jump 且 GM
是「请确认/是否/等待玩家回答」语境，world.time 应保持原锚点。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# 让本测试也可独立运行
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

import copy  # noqa: E402

from state import DEFAULT_STATE, GameState  # noqa: E402


def _make_state(initial_time: str = "三日后的子夜") -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    # 初始化基础结构
    s.update_time(initial_time, source="player_set")
    s.update_location("雾港灯塔")
    return s


class TimeJumpPendingNotLocked(unittest.TestCase):
    def test_helper_recognizes_asking_phrases(self):
        from state import _gm_is_asking_for_time_confirm
        for phrase in [
            "请玩家确认是否把剧情时间推进到次日清晨",
            "是否要跳到次日清晨？",
            "等玩家回答后再决定",
            "请确认 awaiting confirm",
            "先让子代理检查时间线冲突，不要直接跳过确认",
        ]:
            self.assertTrue(_gm_is_asking_for_time_confirm(phrase, []), f"未识别询问语境：{phrase!r}")
        # 反面：明确锁定不算询问
        self.assertFalse(_gm_is_asking_for_time_confirm("时间正式推进到次日清晨。", []))

    def test_helper_recognizes_tag_intents(self):
        from state import _gm_is_asking_for_time_confirm
        # 结构化标签明确标了待确认 / 询问 / 提案
        self.assertTrue(_gm_is_asking_for_time_confirm("", ["时间跳跃待确认：次日清晨"]))
        self.assertTrue(_gm_is_asking_for_time_confirm("", ["询问玩家：是否要跳？"]))
        self.assertTrue(_gm_is_asking_for_time_confirm("", ["时间提案：次日清晨"]))
        # 显式确认 tag → 不算询问
        self.assertFalse(_gm_is_asking_for_time_confirm("", ["时间跳跃确认：次日清晨"]))

    def test_natural_language_request_followed_by_gm_question_keeps_pending(self):
        """复现用户场景：玩家自然语言提请时间跳跃 → GM 回复询问 → 时间不应被锁"""
        s = _make_state(initial_time="三日后的子夜")
        original_time = s.data["world"]["time"]

        # 1. 玩家自然语言请求 → 后端 request_time_jump 设 pending
        player_text = "请把剧情时间推进到次日清晨，地点仍在雾港码头；先让子代理检查时间线冲突，不要直接跳过确认。"
        s.apply_player_directives(player_text)
        timeline = s.data["world"]["timeline"]
        self.assertEqual(timeline.get("anchor_state"), "pending_confirmation",
                         "玩家自然语言时间跳跃应进入 pending_confirmation")
        pending = timeline.get("pending_jump") or {}
        self.assertEqual(pending.get("to"), "次日清晨")
        self.assertEqual(pending.get("status"), "awaiting_gm_confirmation")

        # 2. GM 回复，包含目标时间但本质是「请确认」的询问语境
        gm_response = (
            "我注意到玩家希望把剧情时间推进到次日清晨，"
            "但当前世界状态仍锁定在三日后的子夜。"
            "请玩家确认是否要跳到次日清晨？我会先让子代理检查时间线冲突，不要直接改写。"
            "【询问玩家：是否确认时间推进到次日清晨？】"
        )
        updates = s.apply_structured_updates(gm_response)

        # 关键断言：时间没被锁；pending_jump 保留
        self.assertEqual(s.data["world"]["time"], original_time,
                         f"GM 询问语境下 world.time 不应被改写为目标值；"
                         f"actual={s.data['world']['time']!r} updates={updates}")
        timeline = s.data["world"]["timeline"]
        self.assertEqual(timeline.get("anchor_state"), "pending_confirmation",
                         "应保持 pending_confirmation")
        pending2 = timeline.get("pending_jump") or {}
        self.assertEqual(pending2.get("to"), "次日清晨", "pending_jump.to 应保留目标")
        # 不应该出现『时间线锁定：次日清晨』
        for u in updates:
            self.assertNotIn("时间线锁定", u,
                f"询问语境不应产生『时间线锁定』update：{u!r}（all={updates}）")

    def test_explicit_gm_confirm_still_locks(self):
        """对照：GM 明确确认（不带询问语境，且有【时间跳跃确认】tag）仍可锁定。
        task 35 之后：玩家本轮 NL 触发的 pending 不允许 GM 同轮锁；要测 GM 显式
        confirm 仍能锁，必须把 GM 放到下一 turn（pending.turn < state.turn）。"""
        s = _make_state(initial_time="三日后的子夜")
        s.apply_player_directives("时间推进到次日清晨")
        # 现在 pending
        self.assertEqual(s.data["world"]["timeline"]["anchor_state"], "pending_confirmation")
        # task 35：模拟 GM 在下一 turn 才回应（pending 不再 same-turn）
        s.data["turn"] = int(s.data["turn"]) + 1
        # GM 显式确认
        gm = "好的，时间正式推进到次日清晨。【时间跳跃确认：次日清晨】"
        s.apply_structured_updates(gm)
        self.assertEqual(s.data["world"]["time"], "次日清晨", "GM 显式确认应锁定")
        self.assertEqual(s.data["world"]["timeline"]["anchor_state"], "locked")
        self.assertIsNone(s.data["world"]["timeline"].get("pending_jump"))

    def test_normal_gm_lock_when_no_pending(self):
        """对照：没有 pending 时，GM 的『时间线锁定』标签照常锁；不影响正常流程"""
        s = _make_state(initial_time="三日后的子夜")
        # 没有 pending_jump
        gm = "时间推进到午后。【时间：午后】"
        s.apply_structured_updates(gm)
        self.assertEqual(s.data["world"]["time"], "午后",
                         "无 pending 时 GM 标签应正常锁定")


class PendingQuestionOptions(unittest.TestCase):
    def test_json_question_keeps_option_text_with_commas(self):
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        updates = s.apply_structured_updates(
            """```json
[
  {
    "op": "question",
    "question": "接下来你打算怎么做？",
    "options": [
      "躲在矿车后，仔细观察周围并倾听声音（察觉）",
      "伸手搜寻矿车内部，看看里面留下了什么（调查）",
      "顺着铁轨继续向东面探索"
    ]
  }
]
```"""
        )

        self.assertIn("等待玩家回答", updates)
        pending = s.data["permissions"]["pending_questions"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["options"], [
            "躲在矿车后，仔细观察周围并倾听声音（察觉）",
            "伸手搜寻矿车内部，看看里面留下了什么（调查）",
            "顺着铁轨继续向东面探索",
        ])

    def test_markdown_option_labels_do_not_become_facts(self):
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        updates = s.apply_structured_updates(
            """你可以： - **【借助掩体】** 躲在矿车后，仔细聆听风中传来的低语
- **【搜寻车厢】** 拨开风化的碎石
```json
[
  {
    "op": "question",
    "question": "接下来你打算怎么做？",
    "options": ["借助掩体聆听（察觉）", "搜寻车厢异物（调查）"]
  }
]
```"""
        )

        self.assertIn("等待玩家回答", updates)
        self.assertNotIn("事实：借助掩体", updates)
        self.assertNotIn("事实：搜寻车厢", updates)
        self.assertNotIn("借助掩体", s.data["memory"]["facts"])
        self.assertNotIn("搜寻车厢", s.data["memory"]["facts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
