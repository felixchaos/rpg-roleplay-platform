"""bench LLM 裁判层离线逻辑测试(无需 LLM key / DB)。

用 FakeHarness 验证:JSON 解析兜底、batch_judge 胜率聚合与 verdict、
run_calibration 的 anti-position-bias 翻转一致率、build_judge_report 的 schema。
真模型端到端跑(evomap)是另行的人工验证步骤,不在本测试范围。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bench import judge, calibrate, judge_report  # noqa: E402


class FakeHarness:
    """按 (resp_a, resp_b) 内容决定胜者,模拟一个【真实、无位置偏差】的裁判。
    约定:谁的文本里含 'GOOD' 谁赢;都含或都不含 → tie。返回规范 JSON。"""
    def chat(self, messages, max_tokens=None):
        user = messages[-1]["content"]
        # prompt 里 A 段在 B 段之前,用标记定位
        a_seg = user.split("【回复 A】", 1)[-1].split("【回复 B】", 1)[0]
        b_seg = user.split("【回复 B】", 1)[-1]
        a_good = "GOOD" in a_seg
        b_good = "GOOD" in b_seg
        if a_good and not b_good:
            return '{"winner": "A", "reason": "A 更贴合"}'
        if b_good and not a_good:
            return '{"winner": "B", "reason": "B 更贴合"}'
        return '{"winner": "tie", "reason": "相当"}'


class PositionBiasedHarness:
    """永远选第一个(A)。模拟有严重位置偏差的裁判 → flip_consistency 应很低。"""
    def chat(self, messages, max_tokens=None):
        return 'winner is A. {"winner":"A","reason":"first"}'


class GarbageHarness:
    def chat(self, messages, max_tokens=None):
        return "这不是 JSON 也没有 winner 字段"


class JudgeParseTest(unittest.TestCase):
    def test_clean_json(self):
        self.assertEqual(judge._parse_judge_json('{"winner":"B","reason":"x"}'), ("B", "x"))

    def test_json_with_prose_around(self):
        raw = '好的,我的判断是:\n{"winner": "A", "reason": "理由"}\n以上。'
        self.assertEqual(judge._parse_judge_json(raw)[0], "A")

    def test_garbage_falls_back_to_tie(self):
        self.assertEqual(judge._parse_judge_json("完全不是 json")[0], "tie")

    def test_harness_error_is_tie(self):
        class Boom:
            def chat(self, *a, **k):
                raise RuntimeError("boom")
        r = judge.judge_pair({}, "a", "b", "faithfulness", Boom())
        self.assertEqual(r["winner"], "tie")
        self.assertEqual(r["reason"], "harness_error")


class BatchJudgeTest(unittest.TestCase):
    def test_b_better_verdict(self):
        # 5 个 case,B 全含 GOOD → B 应在所有维度全胜,verdict=B_better
        cases = [{"player_input": f"p{i}"} for i in range(5)]
        resps_a = ["平淡回复" for _ in range(5)]
        resps_b = ["GOOD 忠实原著的回复" for _ in range(5)]
        out = judge.batch_judge(cases, resps_a, resps_b, FakeHarness())
        self.assertEqual(out["overall"]["verdict"], "B_better")
        self.assertEqual(out["faithfulness"]["B_wins"], 5)
        self.assertEqual(out["faithfulness"]["B_win_rate"], 1.0)
        self.assertEqual(out["n_cases"], 5)

    def test_inconclusive_on_ties(self):
        cases = [{"player_input": "p"}]
        out = judge.batch_judge(cases, ["平"], ["平"], FakeHarness())
        self.assertEqual(out["overall"]["verdict"], "inconclusive")

    def test_max_cases_caps(self):
        cases = [{"player_input": str(i)} for i in range(10)]
        out = judge.batch_judge(cases, ["a"] * 10, ["b"] * 10, FakeHarness(), max_cases=3)
        self.assertEqual(out["n_cases"], 3)


class CalibrationTest(unittest.TestCase):
    def _golden(self):
        # expected=B(B 含 GOOD)
        return [
            {"case": {}, "resp_a": "平淡", "resp_b": "GOOD 好", "expected": "B"}
            for _ in range(4)
        ]

    def test_unbiased_harness_high_consistency_and_accuracy(self):
        out = calibrate.run_calibration(self._golden(), FakeHarness())
        # 无位置偏差 → 翻转一致率应为 1.0,accuracy 应为 1.0
        self.assertEqual(out["overall"]["flip_consistency"], 1.0)
        self.assertEqual(out["overall"]["accuracy"], 1.0)

    def test_position_biased_harness_low_consistency(self):
        out = calibrate.run_calibration(self._golden(), PositionBiasedHarness())
        # 永远选 A → forward=A, swapped 也选A→还原成B → 不一致 → flip_consistency=0
        self.assertEqual(out["overall"]["flip_consistency"], 0.0)


class ReportSchemaTest(unittest.TestCase):
    def test_schema_version_and_merge(self):
        det = {"label": "x", "metrics": {}}
        llm = judge.batch_judge([{"player_input": "p"}], ["平"], ["GOOD"], FakeHarness())
        cal = calibrate.run_calibration(
            [{"case": {}, "resp_a": "平", "resp_b": "GOOD", "expected": "B"}], FakeHarness())
        rep = judge_report.build_judge_report(
            det, llm, label="cand", n_det_cases=10, n_judge_cases=1, calibration=cal)
        self.assertEqual(rep["schema_version"], 1)
        self.assertEqual(rep["label"], "cand")
        self.assertIn("faithfulness", rep["llm_judge"])
        self.assertIn("calibration_note", rep["llm_judge"])
        # 校准注入了 flip_inconsistency
        self.assertIn("position_flip_inconsistency", rep["llm_judge"]["faithfulness"])


if __name__ == "__main__":
    unittest.main()
