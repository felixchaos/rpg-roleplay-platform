"""
test_pending_jump_forbids_narrative.py — task 44 回归

复现：玩家先 /set 设置初始时间/地点，再发自然语言『请把剧情时间推进到第二天上午十点，
地点换到娅赛兰临时住处门外；先让子代理检查时间线冲突，不要直接跳过确认。』
- state 正确进入 pending_confirmation（task 22/32/35 OK）
- 但主 GM prompt 仍鼓励『默认尊重玩家意图、写出过渡/落点 + 输出【时间跳跃确认】+【当前时间线：目标】』
  → GM 正文直接叙事到目标时间，输出已跳转 tag，让用户看到的故事和后端 state 矛盾。

修复：context_engine._timeline_layer 在 pending.status=awaiting_gm_confirmation 时，
prompt 改为禁止任何把目标时间/地点当已发生事实的输出，只能给冲突检查/风险/询问确认。
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
from context_engine import _timeline_layer, build_context_bundle  # noqa: E402


def _state_with_pending() -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.update_time("柏林宴会后半夜", source="player_set")
    s.update_location("哈布斯堡宴会大厅")
    # 玩家本轮自然语言请求跳跃 + 明确要求"不要直接跳过确认"
    s.apply_player_directives(
        "请把剧情时间推进到第二天上午十点，地点换到娅赛兰临时住处门外；"
        "先让子代理检查时间线冲突，不要直接跳过确认。"
    )
    return s


class TimelineLayerForbidsNarrativeWhenPending(unittest.TestCase):

    def test_pending_branch_prompt_forbids_jump_confirm_and_narrative(self):
        """核心：_timeline_layer 在 pending awaiting_gm_confirmation 状态下产生的 prompt 文本
        必须包含禁止性指令，且不应再含『默认尊重玩家意图、写出过渡/落点 + 输出【时间跳跃确认】』"""
        s = _state_with_pending()
        layer = _timeline_layer(s)
        text = layer.get("text") or ""
        # 必须显式禁止 narrative + tags
        for required in (
            "禁止把玩家请求的未来时间",
            "禁止输出标签",
            "时间跳跃确认",   # 出现在『禁止输出』的负面列表里
            "询问玩家",       # 强制要求输出询问 tag
        ):
            self.assertIn(required, text,
                f"task 44：pending 分支 prompt 应含『{required}』；layer={text!r}")
        # 旧的『默认尊重玩家意图...输出【时间跳跃确认：目标时间】』必须不出现
        # （否则 GM 仍会被旧 instruction 引导直接 confirm）
        self.assertNotIn("默认尊重玩家的跳转/改线意图", text,
            f"task 44：pending awaiting 时不应再带『默认尊重...输出【时间跳跃确认】』旧 instruction；"
            f"layer={text!r}")
        # debug 字段保留 pending_jump 状态供观测
        debug = layer.get("debug") or {}
        self.assertEqual(debug.get("anchor_state"), "pending_confirmation")
        self.assertIsNotNone(debug.get("pending_jump"))

    def test_no_pending_branch_still_allows_normal_lock(self):
        """对照：没有 pending 时仍走原『没有待确认时间跳跃；保持锚点』分支"""
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        s.update_time("申时三刻", source="player_set")
        layer = _timeline_layer(s)
        text = layer.get("text") or ""
        self.assertIn("没有待确认时间跳跃", text,
            f"无 pending 时应走原分支；layer={text!r}")
        self.assertNotIn("禁止把玩家请求的未来时间", text)

    def test_pending_with_unknown_status_falls_back_to_safe(self):
        """对照：pending 存在但 status 字段缺失/不识别 → 走旧『默认尊重』分支（兼容性）"""
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        s.update_time("柏林宴会后半夜", source="player_set")
        s.update_location("哈布斯堡宴会大厅")
        # 手动构造一个无 status 字段的 pending
        s.data["world"]["timeline"]["anchor_state"] = "pending_confirmation"
        s.data["world"]["timeline"]["pending_jump"] = {
            "from": "柏林宴会后半夜", "to": "第二天上午十点",
            "raw": "test", "turn": 0,
            # 注意：故意没有 status 字段
        }
        layer = _timeline_layer(s)
        text = layer.get("text") or ""
        # 没识别为 awaiting → 走老 default 分支保留兼容
        self.assertIn("默认尊重玩家的跳转/改线意图", text,
            f"未识别的 pending status 应走兼容 default；layer={text!r}")

    def test_build_context_bundle_includes_pending_restriction(self):
        """端到端：build_context_bundle 拼出来的最终 prompt 必须含 pending 限制段（不只是 layer 单测）"""
        s = _state_with_pending()
        bundle = build_context_bundle(s, "请把剧情时间推进到第二天上午十点", "")
        prompt = bundle.get("prompt") or ""
        # 三类禁止指令至少有一条出现在最终 prompt 中
        markers = [
            "禁止把玩家请求的未来时间",
            "禁止输出标签",
            "本轮 anchor_state=pending_confirmation",
        ]
        hits = [m for m in markers if m in prompt]
        self.assertTrue(hits,
            f"task 44：build_context_bundle 最终 prompt 应至少含 pending 禁止指令之一 "
            f"({markers!r})；prompt 头 600 字={prompt[:600]!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
