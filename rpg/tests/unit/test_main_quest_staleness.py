"""test_main_quest_staleness.py — 主线长期不更新(08-01 群反馈)。

反馈:「蓝框有起码五十个回合以上没变过了,只有下面的会变」。蓝框=主线
(memory.main_quest),下面=当前目标(memory.current_objective)。

先排除了「写不进去」:涉事存档没有待审批写入、权限完全放行、审计日志零 blocked
—— 没有任何东西在拒绝。真相是**从来没人写**:工具审计里 set_current_objective
被反复调用,set_main_quest **一次都没有**。

根因:当前目标每轮都在议程上,主线是长程字段,写完一次就没有任何机制再提起它,
全靠 LLM 自己想起来 —— 它不会。修法遵循「确定性代码缝」:**触发**做成确定性的
(按回合数),**内容**仍归史官判断(主线该写什么是叙事判断,代码代写只会写废话)。

阈值按真实对局的更新节奏校准:正常间隔在个位数回合量级,超过 25 属于长尾 —— 取 25
只打长尾。

本测试锁四件:
  · 陈旧判定的边界(含「没主线不催」「没戳视为陈旧」两个方向)
  · **两条写入路径都打戳** —— 漏一条就会假性陈旧、天天催 GM 改主线
  · 提醒真的进了史官提示词,且给了「仍然准确就别动」的出口
  · 提醒只在陈旧时出现,不打扰正常节奏
"""
from __future__ import annotations

import ast
import copy
import os
import pathlib
import sys
import unittest

_RPG = pathlib.Path(__file__).resolve().parents[2]
REPO = _RPG.parent
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import DEFAULT_STATE, GameState  # noqa: E402
from state.quest_staleness import (  # noqa: E402
    MAIN_QUEST_STALE_TURNS,
    main_quest_age,
    main_quest_is_stale,
    stamp_main_quest,
)

_QUEST = "前往死神来了2世界寻找验尸官"


def _data(turn=100, quest=_QUEST, stamped=None):
    d = {"turn": turn, "memory": {"main_quest": quest} if quest else {"main_quest": ""}}
    if stamped is not None:
        d["memory"]["main_quest_turn"] = stamped
    return d


class TestStaleDetection(unittest.TestCase):
    def test_fresh_quest_is_not_stale(self):
        self.assertFalse(main_quest_is_stale(_data(turn=100, stamped=100 - MAIN_QUEST_STALE_TURNS + 1)))

    def test_old_quest_is_stale(self):
        self.assertTrue(main_quest_is_stale(_data(turn=100, stamped=100 - MAIN_QUEST_STALE_TURNS)))

    def test_reported_case_is_caught(self):
        """反馈现场的量级:主线冻了上百个回合。"""
        d = _data(turn=1000, stamped=885)
        self.assertTrue(main_quest_is_stale(d))
        self.assertEqual(main_quest_age(d), 115)

    def test_missing_stamp_counts_as_stale(self):
        """存量存档没有戳 —— 必须先催一次,否则老档永远等不到提醒。"""
        d = _data(stamped=None)
        self.assertIsNone(main_quest_age(d))
        self.assertTrue(main_quest_is_stale(d))

    def test_empty_quest_is_never_stale(self):
        """空主线是「还没定」,不是「过时」。催它只会逼出编造。"""
        self.assertFalse(main_quest_is_stale(_data(quest="")))
        self.assertFalse(main_quest_is_stale(_data(quest="   ")))

    def test_age_never_negative_and_survives_garbage(self):
        self.assertEqual(main_quest_age(_data(turn=10, stamped=50)), 0)   # 回滚后戳比当前回合大
        self.assertIsNone(main_quest_age(_data(stamped="坏数据")))
        self.assertIsNone(main_quest_age({}))

    def test_stamp_is_idempotent_and_uses_current_turn(self):
        d = _data(turn=77, stamped=1)
        stamp_main_quest(d)
        self.assertEqual(d["memory"]["main_quest_turn"], 77)
        self.assertEqual(main_quest_age(d), 0)
        self.assertFalse(main_quest_is_stale(d))


class TestBothWritePathsStamp(unittest.TestCase):
    """两条路:工具执行器 + apply_ops 老路径(dispatcher 路由失败时的 fall-through)。
    漏一条 → 主线明明刚改过却被判陈旧 → 每轮催,反而逼 GM 反复改写。"""

    def _state(self, turn=100):
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        s.data["turn"] = turn
        return s

    def test_tool_executor_stamps(self):
        from tools_dsl.command_tools import execute_tool
        s = self._state(turn=100)
        execute_tool(s, "set_main_quest", {"text": _QUEST})
        self.assertEqual(s.data["memory"]["main_quest"], _QUEST)
        self.assertEqual(main_quest_age(s.data), 0)
        self.assertFalse(main_quest_is_stale(s.data))

    def test_old_scalar_path_stamps(self):
        s = self._state(turn=100)
        s.apply_state_write_typed("memory.main_quest", _QUEST, source="gm")
        self.assertEqual(s.data["memory"]["main_quest"], _QUEST)
        self.assertEqual(main_quest_age(s.data), 0)

    def test_current_objective_does_not_stamp_main_quest(self):
        """别把「当前目标变了」当成「主线更新过」—— 那正是这次 bug 的伪装色。"""
        s = self._state(turn=100)
        s.data["memory"]["main_quest"] = _QUEST
        from tools_dsl.command_tools import execute_tool
        execute_tool(s, "set_current_objective", {"text": "等待下一部恐怖片"})
        self.assertIsNone(main_quest_age(s.data))

    def test_every_main_quest_writer_stamps(self):
        """AST 横扫:哪天多出第三条写 memory.main_quest 的路,不打戳就红。"""
        seams = {
            "rpg/tools_dsl/command_tools.py": "stamp_main_quest",
            "rpg/state/_mixins/apply_ops.py": "stamp_main_quest",
        }
        for rel, needle in seams.items():
            src = (REPO / rel).read_text(encoding="utf-8")
            self.assertIn("memory.main_quest" if "apply_ops" in rel else "set_main_quest", src)
            self.assertIn(needle, src, f"{rel} 写主线但没打戳")


class TestRecorderSeesTheReminder(unittest.TestCase):
    def _prompt(self, data):
        import inspect

        from agents.recorder import _build_user_prompt
        sig = inspect.signature(_build_user_prompt)
        kwargs = {}
        for name, p in sig.parameters.items():
            if name in ("state_data",):
                kwargs[name] = data
            elif p.default is not inspect.Parameter.empty:
                continue
            elif name == "tasks":
                kwargs[name] = frozenset({"ops"})
            elif name == "gm_prose":
                kwargs[name] = "正文"
            else:
                kwargs[name] = None
        return _build_user_prompt(**kwargs)

    def test_stale_quest_produces_a_reminder(self):
        text = self._prompt(_data(turn=1000, stamped=885))
        self.assertIn("主线", text)
        self.assertIn("115 回合", text)
        self.assertIn("memory.main_quest", text)

    def test_reminder_offers_a_self_limiting_confirm_path(self):
        """两个出口都要有,且「仍准确」那条必须是**原样重写以确认**而不是「别动」——
        「别动」会让戳永远不更新 → 每回合都催,反而逼出交差式改写。"""
        text = self._prompt(_data(turn=1000, stamped=885))
        self.assertIn("仍然准确", text)
        self.assertIn("原样重写", text)
        self.assertIn("重置计时", text)

    def test_confirming_with_the_same_value_resets_the_clock(self):
        """出口 (b) 必须真的有效:重写同一条值也要打戳,否则那条指示是空头支票。"""
        from tools_dsl.command_tools import execute_tool
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        s.data["turn"] = 1000
        s.data["memory"]["main_quest"] = _QUEST
        s.data["memory"]["main_quest_turn"] = 885
        self.assertTrue(main_quest_is_stale(s.data))
        execute_tool(s, "set_main_quest", {"text": _QUEST})   # 原样重写
        self.assertEqual(s.data["memory"]["main_quest"], _QUEST)
        self.assertFalse(main_quest_is_stale(s.data))

    def test_fresh_quest_has_no_reminder(self):
        text = self._prompt(_data(turn=100, stamped=99))
        self.assertNotIn("没有更新过", text)

    def test_reminder_is_wrapped_so_it_can_never_break_the_recorder(self):
        """史官是每回合热路径 —— 提醒逻辑抛异常绝不能带崩它。"""
        src = (REPO / "rpg/agents/recorder.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_build_user_prompt")
        guarded = any(
            isinstance(n, ast.Try) and "main_quest_is_stale" in ast.unparse(n)
            for n in ast.walk(fn))
        self.assertTrue(guarded, "陈旧提醒没有包在 try 里")


class TestThresholdIsCalibrated(unittest.TestCase):
    def test_threshold_is_well_above_normal_cadence(self):
        """正常间隔在个位数回合量级;阈值必须远高于它,否则每隔几轮就催一次。"""
        self.assertGreaterEqual(MAIN_QUEST_STALE_TURNS, 20)
        self.assertLessEqual(MAIN_QUEST_STALE_TURNS, 60)


if __name__ == "__main__":
    unittest.main()
