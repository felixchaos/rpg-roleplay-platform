"""test_context_bundle_budget_wiring.py — 装配层的预算与截断可观测(v1.82.0)。

锁三件事:
  ① 每层都报出 original_chars / budget_chars / truncated / budget_registered ——
     在此之前「这层被砍了」只以正文里一句中文标记存在,统计不了也告警不了,
     而 context_runs 表存的正是这份 debug。
  ② 未登记的层 id 会被标出来(budget_registered=False + debug.budget.unregistered_layers),
     不再静默走默认上限。
  ③ budget_chars 宽裕时输出与不传时**逐字节相同** —— 这是本次改动的风险闸:
     绝大多数生产会话(1M 窗口模型)不该有任何行为变化。
"""
from __future__ import annotations

import copy
import unittest

from context_engine import build_context_bundle
from context_engine._constants import MAX_LAYER_CHARS
from state import DEFAULT_STATE, GameState


def _state() -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.update_time("柏林宴会后半夜", source="player_set")
    s.update_location("哈布斯堡宴会大厅")
    return s


class BudgetDebugShape(unittest.TestCase):
    def test_every_layer_reports_truncation_fields(self):
        b = build_context_bundle(_state(), "继续", "")
        layers = b["debug"]["layers"]
        self.assertTrue(layers, "没有任何层,断言会假绿")
        for lyr in layers:
            for field in ("original_chars", "budget_chars", "truncated", "budget_registered"):
                self.assertIn(field, lyr, f"层 {lyr['id']} 缺字段 {field}")
            self.assertIsInstance(lyr["truncated"], bool)
            # chars 是截断后的,original_chars 是截断前的
            self.assertLessEqual(lyr["chars"], max(lyr["original_chars"], 1))

    def test_budget_report_exists(self):
        b = build_context_bundle(_state(), "继续", "")
        rep = b["debug"]["budget"]
        for k in ("budget_chars", "want_total_chars", "granted_total_chars",
                  "dropped_layers", "unregistered_layers", "truncated_layers"):
            self.assertIn(k, rep)
        # 不传 budget_chars → 不做求解
        self.assertEqual(rep["budget_chars"], 0)
        self.assertEqual(rep["dropped_layers"], [])

    def test_all_layers_in_this_path_are_registered(self):
        b = build_context_bundle(_state(), "继续", "")
        self.assertEqual(b["debug"]["budget"]["unregistered_layers"], [])
        for lyr in b["debug"]["layers"]:
            self.assertTrue(lyr["budget_registered"], f"{lyr['id']} 未登记预算")

    def test_truncation_is_reported_when_it_happens(self):
        """给一段超过 rag 上限的检索文本,truncated 必须为 True 且能查到砍了多少。"""
        big = "甲乙丙丁" * (MAX_LAYER_CHARS["rag"])  # 远超上限
        b = build_context_bundle(_state(), "继续", big)
        rag = next((x for x in b["debug"]["layers"] if x["id"] == "rag"), None)
        self.assertIsNotNone(rag, "兜底 rag 层没生成")
        self.assertTrue(rag["truncated"])
        self.assertGreater(rag["original_chars"], rag["chars"])
        self.assertIn("rag", b["debug"]["budget"]["truncated_layers"])


class BudgetSolvingIsOffByDefault(unittest.TestCase):
    def test_generous_budget_is_byte_identical(self):
        """1M 窗口模型换算出的预算远大于 want 之和 → prompt 必须逐字节相同。"""
        s1, s2 = _state(), _state()
        base = build_context_bundle(s1, "继续", "")
        generous = build_context_bundle(s2, "继续", "", budget_chars=10_000_000)
        self.assertEqual(base["prompt"], generous["prompt"])
        self.assertEqual(generous["debug"]["budget"]["dropped_layers"], [])

    def test_tight_budget_actually_bites(self):
        big = "甲乙丙丁" * 8000
        tight = build_context_bundle(_state(), "继续", big, budget_chars=6000)
        self.assertLessEqual(len(tight["prompt"]), 6000 + 2000)  # 段标题有额外开销
        rep = tight["debug"]["budget"]
        self.assertEqual(rep["budget_chars"], 6000)
        self.assertLess(rep["granted_total_chars"], rep["want_total_chars"])

    def test_user_input_survives_a_tight_budget(self):
        """user_input 的 min == want,不许被压缩;它是玩家这一轮说的话。"""
        big = "甲乙丙丁" * 8000
        b = build_context_bundle(_state(), "我要去找卡切尔问清楚那封信", big, budget_chars=12000)
        ui = next((x for x in b["debug"]["layers"] if x["id"] == "user_input"), None)
        self.assertIsNotNone(ui, "user_input 层在紧预算下被丢了")
        self.assertFalse(ui["truncated"])
        self.assertIn("卡切尔", b["prompt"])


if __name__ == "__main__":
    unittest.main()
