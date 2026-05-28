"""Unit tests for agents.anchor_seed_agent.

测试纯逻辑函数 (classify_event_fatal, _compute_importance, _derive_must_preserve)。
不依赖 DB / LLM。
"""
from __future__ import annotations

import unittest


class TestClassifyEventFatal(unittest.TestCase):
    """classify_event_fatal: 启发式死神来了关键词检测。"""

    def _fn(self, text):
        from agents.anchor_seed_agent import classify_event_fatal
        return classify_event_fatal(text)

    def test_empty_string_returns_false(self):
        self.assertFalse(self._fn(""))

    def test_none_like_empty_returns_false(self):
        self.assertFalse(self._fn(""))

    def test_death_keyword_returns_true(self):
        self.assertTrue(self._fn("将军在战场上战死,消息震动朝野"))

    def test_missing_keyword_returns_false(self):
        self.assertFalse(self._fn("将军率兵出征,士气高昂"))

    def test_disappear_keyword(self):
        self.assertTrue(self._fn("蕾穆丽娜失踪,下落不明"))

    def test_surrender_keyword(self):
        self.assertTrue(self._fn("守城将领宣告投降"))

    def test_execution_keyword(self):
        self.assertTrue(self._fn("三名叛党被处决于广场"))

    def test_reveal_keyword(self):
        self.assertTrue(self._fn("卧底身份暴露,全军哗然"))

    def test_no_partial_match_false(self):
        """不含关键词的普通文本不应误报。"""
        self.assertFalse(self._fn("国王宣布和平协议,民众欢庆"))


class TestComputeImportance(unittest.TestCase):
    """_compute_importance: 0-100 综合重要性得分。"""

    def _fn(self, event, summary):
        from agents.anchor_seed_agent import _compute_importance
        return _compute_importance(event, summary)

    def test_high_importance_base(self):
        score = self._fn({"importance": "high"}, "普通事件")
        # base=70, 无加成
        self.assertEqual(score, 70)

    def test_medium_importance_base(self):
        score = self._fn({"importance": "medium"}, "普通事件")
        self.assertEqual(score, 50)

    def test_low_importance_base(self):
        score = self._fn({"importance": "low"}, "普通事件")
        self.assertEqual(score, 30)

    def test_unknown_importance_defaults_to_40(self):
        score = self._fn({}, "普通事件")
        self.assertEqual(score, 40)

    def test_participants_bonus(self):
        event = {"importance": "medium", "participants": ["A", "B", "C"]}
        score = self._fn(event, "三人参与事件")
        # base=50, bonus= 3*2=6
        self.assertEqual(score, 56)

    def test_participants_capped_at_5(self):
        event = {"importance": "medium", "participants": ["A", "B", "C", "D", "E", "F", "G"]}
        # min(7,5)*2 = 10
        score = self._fn(event, "多人事件")
        self.assertEqual(score, 60)

    def test_critical_keyword_adds_15(self):
        # "宣战" 是 _CRITICAL_KEYWORDS 之一
        score = self._fn({"importance": "medium"}, "国王正式宣战邻国")
        self.assertEqual(score, 65)  # 50 + 15

    def test_fatal_keyword_adds_10(self):
        # "战死" 是 _FATAL_KEYWORDS 之一
        score = self._fn({"importance": "medium"}, "将军战死沙场")
        self.assertEqual(score, 60)  # 50 + 10

    def test_score_capped_at_100(self):
        event = {
            "importance": "high",
            "participants": ["A", "B", "C", "D", "E", "F"],
            "locations": ["X", "Y", "Z"],
            "concepts": ["P", "Q", "R"],
        }
        # 即使堆满加成也不超过 100
        score = self._fn(event, "宣战后战死于广场联姻后驾崩")
        self.assertLessEqual(score, 100)

    def test_score_non_negative(self):
        score = self._fn({"importance": "low"}, "")
        self.assertGreaterEqual(score, 0)


class TestDeriveMustPreserve(unittest.TestCase):
    """_derive_must_preserve: 从事件文本 + 参与者推出 must_preserve 列表。"""

    def _fn(self, summary, participants):
        from agents.anchor_seed_agent import _derive_must_preserve
        return _derive_must_preserve(summary, participants)

    def test_empty_inputs_returns_empty(self):
        result = self._fn("", [])
        self.assertEqual(result, [])

    def test_string_participants_included(self):
        result = self._fn("普通事件", ["蕾穆丽娜", "穆蕾莉娅"])
        self.assertIn("蕾穆丽娜 参与", result)
        self.assertIn("穆蕾莉娅 参与", result)

    def test_dict_participants_included(self):
        result = self._fn("普通事件", [{"name": "蕾穆丽娜"}, {"name": "穆蕾莉娅"}])
        self.assertIn("蕾穆丽娜 参与", result)

    def test_participants_capped_at_3(self):
        participants = ["A", "B", "C", "D", "E"]
        result = self._fn("事件", participants)
        participant_items = [x for x in result if "参与" in x]
        self.assertLessEqual(len(participant_items), 3)

    def test_fatal_keyword_appended(self):
        result = self._fn("将军战死于北疆", [])
        self.assertTrue(any("战死" in x or "死亡" in x or "这一结果" in x for x in result))

    def test_critical_keyword_appended(self):
        result = self._fn("国王宣战邻国", [])
        self.assertIn("宣战", result)

    def test_result_capped_at_5(self):
        # 大量输入也不超过 5 项
        participants = ["A", "B", "C", "D"]
        result = self._fn("宣战并战死", participants)
        self.assertLessEqual(len(result), 5)

    def test_empty_participant_name_skipped(self):
        # 空字符串 "" 被 `and p` 过滤; dict 中 name="" 被 strip 后过滤
        # 注: str "  " 是 truthy, 源码不做 strip, 会漏进来 — 仅测 "" 和 dict 两种
        result_empty_str = self._fn("事件", [""])
        self.assertEqual(result_empty_str, [])

        result_dict_empty = self._fn("事件", [{"name": ""}])
        self.assertEqual(result_dict_empty, [])


if __name__ == "__main__":
    unittest.main()
