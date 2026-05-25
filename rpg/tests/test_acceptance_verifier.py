"""
test_acceptance_verifier.py — task 84: acceptance 验证三模式 smoke test

覆盖：
- mode='rule' 行为与 task 81 同（不调 LLM）
- mode='llm' 失败时 fallback 到 rule
- mode='hybrid' 在 rule 全通过路径短路（不调 LLM）
- mode='hybrid' 在 rule 判 unmet 时调 LLM 二次确认
- verify_acceptance_llm 解析格式正确
- verify_acceptance_llm 解析失败 / backend 异常 → None
- _acceptance_verifier_mode 默认 'rule'，未知值落回 'rule'
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# 让测试能 import 顶层模块
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class VerifyAcceptanceRuleMode(unittest.TestCase):
    """mode='rule' 与 task 81 原行为完全一致。"""

    def test_rule_mode_hit_returns_empty(self):
        import app
        # 肯定条款：response 包含 '灯塔' → 通过 → unmet=[]
        unmet = app._verify_acceptance(
            ["回应了去灯塔意图"],
            "你点头，向灯塔走去。",
            [],
            mode="rule",
        )
        self.assertEqual(unmet, [])

    def test_rule_mode_miss_returns_unmet(self):
        import app
        # 肯定条款：response 不含 '灯塔'/'去' 等关键 bigram → unmet
        unmet = app._verify_acceptance(
            ["回应了去灯塔意图"],
            "你坐下来喝茶。",
            [],
            mode="rule",
        )
        self.assertEqual(unmet, ["回应了去灯塔意图"])

    def test_rule_mode_does_not_call_llm(self):
        """rule 模式不能触发 LLM 调用。"""
        import app
        with patch("acceptance_verifier.verify_acceptance_llm") as mock_llm:
            app._verify_acceptance(
                ["回应了去灯塔意图"],
                "你坐下来喝茶。",
                [],
                mode="rule",
            )
            mock_llm.assert_not_called()

    def test_default_mode_is_rule(self):
        """不传 mode → 默认 'rule'，与老接口语义一致。"""
        import app
        unmet1 = app._verify_acceptance(
            ["回应了去灯塔意图"], "你坐下来喝茶。", [],
        )
        unmet2 = app._verify_acceptance(
            ["回应了去灯塔意图"], "你坐下来喝茶。", [], mode="rule",
        )
        self.assertEqual(unmet1, unmet2)

    def test_unknown_mode_falls_back_to_rule(self):
        """未知 mode 字符串 → 当成 'rule'。"""
        import app
        unmet = app._verify_acceptance(
            ["回应了去灯塔意图"], "你坐下来喝茶。", [], mode="weird",
        )
        # 与 rule 同
        self.assertEqual(unmet, ["回应了去灯塔意图"])


class VerifyAcceptanceLLMMode(unittest.TestCase):
    """mode='llm'：成功用 LLM 结果；失败/None 时降级 rule。"""

    def test_llm_mode_uses_llm_result(self):
        import app
        with patch("acceptance_verifier.verify_acceptance_llm", return_value=[]) as mock_llm:
            unmet = app._verify_acceptance(
                ["条款A", "条款B"],
                "GM 叙事",
                ["update1"],
                mode="llm",
                user_id=42,
            )
            self.assertEqual(unmet, [])
            mock_llm.assert_called_once()
            kwargs = mock_llm.call_args.kwargs
            self.assertEqual(kwargs.get("user_id"), 42)

    def test_llm_mode_returns_llm_unmet(self):
        import app
        with patch(
            "acceptance_verifier.verify_acceptance_llm",
            return_value=["条款A"],
        ):
            unmet = app._verify_acceptance(
                ["条款A", "条款B"],
                "GM 叙事",
                [],
                mode="llm",
            )
            self.assertEqual(unmet, ["条款A"])

    def test_llm_mode_none_falls_back_to_rule(self):
        """LLM 返回 None（不可用）→ 降级 rule。"""
        import app
        with patch(
            "acceptance_verifier.verify_acceptance_llm",
            return_value=None,
        ):
            unmet = app._verify_acceptance(
                ["回应了去灯塔意图"],
                "你坐下来喝茶。",  # rule 判定 unmet
                [],
                mode="llm",
            )
            # 与 rule 直接跑结果一致
            self.assertEqual(unmet, ["回应了去灯塔意图"])

    def test_llm_mode_exception_falls_back_to_rule(self):
        """LLM 抛异常 → 降级 rule，不破坏主流程。"""
        import app
        with patch(
            "acceptance_verifier.verify_acceptance_llm",
            side_effect=RuntimeError("backend down"),
        ):
            unmet = app._verify_acceptance(
                ["回应了去灯塔意图"],
                "你坐下来喝茶。",
                [],
                mode="llm",
            )
            self.assertEqual(unmet, ["回应了去灯塔意图"])


class VerifyAcceptanceHybridMode(unittest.TestCase):
    """mode='hybrid'：rule 全通过短路；rule 判 unmet 才让 LLM 二次确认。"""

    def test_hybrid_rule_passes_short_circuits(self):
        """rule 没问题 → 不调 LLM。"""
        import app
        with patch("acceptance_verifier.verify_acceptance_llm") as mock_llm:
            unmet = app._verify_acceptance(
                ["回应了去灯塔意图"],
                "你点头，向灯塔走去。",  # rule 判 met
                [],
                mode="hybrid",
            )
            self.assertEqual(unmet, [])
            mock_llm.assert_not_called()

    def test_hybrid_calls_llm_only_on_rule_unmet(self):
        """rule 判 unmet → LLM 复核；LLM 说"实际通过" → 最终 []。"""
        import app
        # rule 会判 ["回应了去灯塔意图"] unmet（response 里没"灯塔"）
        with patch(
            "acceptance_verifier.verify_acceptance_llm",
            return_value=[],  # LLM 说"其实通过了"
        ) as mock_llm:
            unmet = app._verify_acceptance(
                ["回应了去灯塔意图"],
                "你点头同意，朝那个高高的指引灯走去。",  # 同义改写，规则抓不到
                [],
                mode="hybrid",
            )
            self.assertEqual(unmet, [])
            # 应被调用一次，且喂的是 rule_unmet 而不是全量
            mock_llm.assert_called_once()
            kwargs = mock_llm.call_args.kwargs
            self.assertEqual(kwargs.get("acceptance"), ["回应了去灯塔意图"])

    def test_hybrid_llm_confirms_unmet(self):
        """LLM 也说 unmet → 最终就是 unmet。"""
        import app
        with patch(
            "acceptance_verifier.verify_acceptance_llm",
            return_value=["回应了去灯塔意图"],
        ):
            unmet = app._verify_acceptance(
                ["回应了去灯塔意图"],
                "你坐下来喝茶。",
                [],
                mode="hybrid",
            )
            self.assertEqual(unmet, ["回应了去灯塔意图"])

    def test_hybrid_llm_none_keeps_rule_verdict(self):
        """LLM 不可用 → 保留 rule unmet（保守）。"""
        import app
        with patch(
            "acceptance_verifier.verify_acceptance_llm",
            return_value=None,
        ):
            unmet = app._verify_acceptance(
                ["回应了去灯塔意图"],
                "你坐下来喝茶。",
                [],
                mode="hybrid",
            )
            self.assertEqual(unmet, ["回应了去灯塔意图"])

    def test_hybrid_llm_exception_keeps_rule_verdict(self):
        import app
        with patch(
            "acceptance_verifier.verify_acceptance_llm",
            side_effect=RuntimeError("backend down"),
        ):
            unmet = app._verify_acceptance(
                ["回应了去灯塔意图"],
                "你坐下来喝茶。",
                [],
                mode="hybrid",
            )
            self.assertEqual(unmet, ["回应了去灯塔意图"])


class AcceptanceVerifierLLMModule(unittest.TestCase):
    """acceptance_verifier.py 模块行为 smoke。"""

    def test_empty_acceptance_returns_empty(self):
        from acceptance_verifier import verify_acceptance_llm
        self.assertEqual(verify_acceptance_llm([], "anything", []), [])

    def test_empty_response_returns_empty(self):
        from acceptance_verifier import verify_acceptance_llm
        self.assertEqual(verify_acceptance_llm(["a"], "", []), [])

    def test_backend_returns_unmet_list(self):
        """模拟 backend 返回 {"unmet": [...]}。"""
        import acceptance_verifier
        fake_response = '{"unmet": ["条款A"]}'
        with patch(
            "acceptance_verifier._call_verifier_backend",
            return_value=fake_response,
        ):
            out = acceptance_verifier.verify_acceptance_llm(
                ["条款A", "条款B"], "GM 叙事", [],
            )
            self.assertEqual(out, ["条款A"])

    def test_backend_returns_empty_unmet(self):
        import acceptance_verifier
        with patch(
            "acceptance_verifier._call_verifier_backend",
            return_value='{"unmet": []}',
        ):
            out = acceptance_verifier.verify_acceptance_llm(
                ["条款A"], "GM 叙事", [],
            )
            self.assertEqual(out, [])

    def test_backend_exception_returns_none(self):
        """backend 异常 → None 让上层 fallback。"""
        import acceptance_verifier
        with patch(
            "acceptance_verifier._call_verifier_backend",
            side_effect=RuntimeError("nope"),
        ):
            out = acceptance_verifier.verify_acceptance_llm(
                ["条款A"], "GM 叙事", [],
            )
            self.assertIsNone(out)

    def test_backend_garbage_returns_none(self):
        """完全无法解析的回包 → None。"""
        import acceptance_verifier
        with patch(
            "acceptance_verifier._call_verifier_backend",
            return_value="this is not json at all",
        ):
            out = acceptance_verifier.verify_acceptance_llm(
                ["条款A"], "GM 叙事", [],
            )
            self.assertIsNone(out)

    def test_backend_fence_wrapped_json_parses(self):
        """LLM 包了 ```json fence → 仍能解析。"""
        import acceptance_verifier
        with patch(
            "acceptance_verifier._call_verifier_backend",
            return_value='```json\n{"unmet": ["条款A"]}\n```',
        ):
            out = acceptance_verifier.verify_acceptance_llm(
                ["条款A"], "GM 叙事", [],
            )
            self.assertEqual(out, ["条款A"])

    def test_backend_empty_string_returns_none(self):
        """空回包 → None。"""
        import acceptance_verifier
        with patch(
            "acceptance_verifier._call_verifier_backend",
            return_value="",
        ):
            out = acceptance_verifier.verify_acceptance_llm(
                ["条款A"], "GM 叙事", [],
            )
            self.assertIsNone(out)

    def test_unmet_normalization_back_to_original(self):
        """LLM 返回的 unmet 字符串与原文有差异时，做 fuzzy 回填。"""
        import acceptance_verifier
        # LLM 返回截断版，应回填到完整原文
        with patch(
            "acceptance_verifier._call_verifier_backend",
            return_value='{"unmet": ["回应了去灯塔"]}',
        ):
            out = acceptance_verifier.verify_acceptance_llm(
                ["回应了去灯塔意图"], "GM 叙事", [],
            )
            self.assertEqual(out, ["回应了去灯塔意图"])


class AcceptanceVerifierModePref(unittest.TestCase):
    """_acceptance_verifier_mode 偏好读取。"""

    def test_default_when_no_user(self):
        import app
        self.assertEqual(app._acceptance_verifier_mode(None), "rule")
        self.assertEqual(app._acceptance_verifier_mode({}), "rule")
        self.assertEqual(app._acceptance_verifier_mode({"id": None}), "rule")

    def test_unknown_pref_value_falls_back_to_rule(self):
        """preferences 里写了奇怪的值 → 默认 rule。"""
        import app
        fake_row = {"preferences": {"acceptance_verifier.mode": "magic"}}
        # 模拟 db connect 上下文，让其返回 fake_row
        class _FakeCursor:
            def fetchone(self_inner):
                return fake_row
        class _FakeDB:
            def execute(self_inner, *args, **kwargs):
                return _FakeCursor()
        class _FakeConnCM:
            def __enter__(self_inner):
                return _FakeDB()
            def __exit__(self_inner, *exc):
                return False

        with patch("platform_app.db.connect", return_value=_FakeConnCM()), \
                patch("platform_app.db.init_db"):
            mode = app._acceptance_verifier_mode({"id": 1})
            self.assertEqual(mode, "rule")

    def test_each_valid_mode_round_trips(self):
        import app
        class _FakeCursor:
            def __init__(self_inner, row):
                self_inner._row = row
            def fetchone(self_inner):
                return self_inner._row
        class _FakeDB:
            def __init__(self_inner, row):
                self_inner._row = row
            def execute(self_inner, *args, **kwargs):
                return _FakeCursor(self_inner._row)
        class _FakeConnCM:
            def __init__(self_inner, row):
                self_inner._row = row
            def __enter__(self_inner):
                return _FakeDB(self_inner._row)
            def __exit__(self_inner, *exc):
                return False

        for val in ("rule", "llm", "hybrid"):
            row = {"preferences": {"acceptance_verifier.mode": val}}
            with patch("platform_app.db.connect", return_value=_FakeConnCM(row)), \
                    patch("platform_app.db.init_db"):
                self.assertEqual(app._acceptance_verifier_mode({"id": 1}), val)


if __name__ == "__main__":
    unittest.main()
