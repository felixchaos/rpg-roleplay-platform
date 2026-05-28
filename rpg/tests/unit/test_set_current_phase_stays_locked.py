"""
test_set_current_phase_stays_locked.py — task 36 回归

复现：玩家用 /set 设过 world.timeline.current_phase=港口黄昏测试 后，主 GM 任何
【时间：Y】 tag 会触发 update_time(Y) → 内部 _phase_for_time(Y) 把 current_phase
推回『玩家分支』或『柏林暗流篇』，覆盖用户显式值。

修复：apply_state_write 在 force=True / source 以 'user' 开头时，把 path 加入
worldline.user_locked_fields；update_time 先检查 world.timeline.current_phase
是否 locked，是 → 不再覆盖。/set 走 force=True，所以自动登记。
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


def _state() -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.update_time("开局时刻", source="bootstrap")
    s.update_location("起始地")
    return s


class UserSetCurrentPhaseSurvivesGmTimeWrites(unittest.TestCase):
    def test_set_marks_user_locked_fields(self):
        """单元：/set world.timeline.current_phase=X → user_locked_fields 含该路径"""
        s = _state()
        s.apply_player_directives("/set world.timeline.current_phase=港口黄昏测试")
        locked = (s.data.get("worldline") or {}).get("user_locked_fields") or []
        self.assertIn("world.timeline.current_phase", locked,
            f"task 36：/set 应把 world.timeline.current_phase 登记到 user_locked_fields；"
            f"实际 {locked!r}")

    def test_explicit_current_phase_survives_subsequent_gm_time_lock(self):
        """核心回归：先 /set 显式 current_phase=X，后续 GM 写 时间 tag 触发
        update_time → 不得覆盖 current_phase。"""
        s = _state()
        # /set 同时含时间和显式 current_phase（模拟用户真实 UI 输入）
        s.apply_player_directives(
            "/set 时间改为四日后的黄昏，"
            "地点改为雾港码头，"
            "memory.current_objective=验证 UI /set 回归，"
            "world.timeline.current_phase=港口黄昏测试"
        )
        # task 28 保证 /set 后 current_phase 已是用户显式值
        self.assertEqual(s.data["world"]["timeline"]["current_phase"], "港口黄昏测试")
        # 模拟下一轮 GM 输出，重申时间（任意能触发 update_time 的 GM 写法）。
        # 用 prose："时间推进到次日清晨" 命中 _extract_explicit_time_updates 正则；
        # apply_structured_updates 会调 update_time。
        s.data["turn"] = int(s.data["turn"]) + 1
        gm = "好的。时间推进到次日清晨。"
        updates = s.apply_structured_updates(gm)
        # 关键断言：current_phase 仍是用户显式值，不能被 _phase_for_time 覆盖
        self.assertEqual(
            s.data["world"]["timeline"]["current_phase"],
            "港口黄昏测试",
            f"task 36：/set 后 GM 时间 tag 不应覆盖 current_phase；"
            f"actual={s.data['world']['timeline']['current_phase']!r} updates={updates}",
        )
        # 但 world.time 应该正常更新（用户没锁 time）
        self.assertEqual(s.data["world"]["time"], "次日清晨",
            f"world.time 未被显式锁定，GM 时间 tag 应正常更新；"
            f"actual={s.data['world']['time']!r} updates={updates}")

    def test_gm_only_write_does_not_lock(self):
        """对照：GM 自己（source=gm）的 apply_state_write 不应触发 user lock"""
        s = _state()
        # 直接模拟 GM 通过结构化标签触发的 apply_state_write（source 默认 gm，无 force）
        # 注意：apply_state_write 要先放行，把 permissions 设全访问
        s.set_permission_mode("full_access")
        s.apply_state_write("world.timeline.current_phase=GM设的", source="gm", force=False)
        locked = (s.data.get("worldline") or {}).get("user_locked_fields") or []
        self.assertNotIn("world.timeline.current_phase", locked,
            f"task 36：GM 自写不应被登记为 user-locked；实际 {locked!r}")
        # 此后 update_time 仍允许覆盖 current_phase（因为没 lock）
        s.update_time("夜晚", source="gm")
        # current_phase 应已被 _phase_for_time 推算（不是用户值）
        # 不强断言具体值，只断"GM 写没被作为用户锁阻挡未来自动派生"
        self.assertNotIn("world.timeline.current_phase",
            (s.data.get("worldline") or {}).get("user_locked_fields") or [])

    def test_locked_field_persists_across_many_turns(self):
        """显式锁不应该被『turn 递增』或『多轮 GM 更新』自然解除（必须用户再次主动覆盖）。
        用 prose 时间推进确保真触发 update_time（bare【时间：】tag 不匹配 is_time_key）。"""
        s = _state()
        s.apply_player_directives("/set world.timeline.current_phase=用户阶段A")
        # 注意：looks_like_time_value 要求含 日/天/夜/晨/早/午/晚 等关键字；"黄昏"单独不匹配
        targets = ["午后", "深夜", "次日清晨", "次日午后", "三日后"]
        for t in targets:
            s.data["turn"] = int(s.data["turn"]) + 1
            s.apply_structured_updates(f"时间推进到{t}。")
            self.assertEqual(s.data["world"]["timeline"]["current_phase"], "用户阶段A",
                f"task 36：经过多轮 GM 时间写后 current_phase 仍应保持用户锁；"
                f"actual={s.data['world']['timeline']['current_phase']!r} at target={t}")
            # 同时确认 world.time 真的被 GM 更新了（证明 update_time 真的跑了）
            self.assertEqual(s.data["world"]["time"], t,
                f"world.time 应被 GM 时间推进更新；actual={s.data['world']['time']!r} target={t}")

    def test_user_can_overwrite_their_own_lock(self):
        """用户用第二次 /set 覆盖锁住的字段 → 新值生效，锁仍在"""
        s = _state()
        s.apply_player_directives("/set world.timeline.current_phase=阶段一")
        self.assertEqual(s.data["world"]["timeline"]["current_phase"], "阶段一")
        s.apply_player_directives("/set world.timeline.current_phase=阶段二")
        self.assertEqual(s.data["world"]["timeline"]["current_phase"], "阶段二",
            "用户可以用第二次 /set 修改自己锁住的字段")
        locked = (s.data.get("worldline") or {}).get("user_locked_fields") or []
        self.assertIn("world.timeline.current_phase", locked,
            "锁应该仍存在（用户没主动撤销）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
