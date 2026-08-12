"""流水线去 fork · 批次1 回归 + parity 守卫。

覆盖 GM 流水线审计的三条:
  1-a (P1): recorder tool_schema 必含 progress_motion(否则 Anthropic/Vertex 上 pace fallback 死)
            —— 同时是 provider-fork 的 parity 守卫:tool-schema 与 system prompt 必须同源声明该字段。
  1-b (B):  memory.facts 确定性拦截 acceptance 跳过元信息,不污染活事实库。
  1-d:      curator 存储的 acceptance 上限与 GM prompt 渲染上限对齐(都 6),消除必然假 unmet retry。
无需 DB / LLM。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class ProgressMotionSchemaParity(unittest.TestCase):
    """1-a + parity 守卫:progress_motion 必须同时在 tool-schema 和 system prompt 里声明。"""

    def test_tool_schema_declares_progress_motion(self):
        from agents.recorder import _build_tool_schema
        schema = _build_tool_schema(frozenset(["anchors"]), None)
        blob = json.dumps(schema, ensure_ascii=False)
        self.assertIn("progress_motion", blob,
                      "anchors 任务的 tool_schema 缺 progress_motion → 主力 provider 上 pace fallback 永不触发")
        # 定位 properties / required(容忍 input_schema 包裹或裸结构)
        inner = schema.get("input_schema", schema)
        props = inner.get("properties", {})
        required = inner.get("required", [])
        self.assertIn("progress_motion", props)
        self.assertIn("progress_motion", required)
        # 未破坏既有字段
        self.assertIn("reached", props)
        self.assertIn("current_chapter", props)

    def test_parity_system_prompt_also_declares_it(self):
        """provider fork parity:JSON-文本路径(system prompt)与原生 tool-use 路径必须同源。"""
        import inspect

        from agents.recorder import _build_system_prompt
        sig = inspect.signature(_build_system_prompt)
        # 尽量用最少必需参数调用;失败则回退到源码级断言
        try:
            kwargs = {}
            for name, p in sig.parameters.items():
                if p.default is not inspect._empty:
                    continue
                if "task" in name:
                    kwargs[name] = frozenset(["anchors"])
                else:
                    kwargs[name] = None
            sp = _build_system_prompt(**kwargs)
            self.assertIn("progress_motion", sp)
        except Exception:
            src = inspect.getsource(_build_system_prompt)
            self.assertIn("progress_motion", src,
                          "system prompt 与 tool schema 对 progress_motion 声明不同源")


class MemoryFactsAcceptanceFilter(unittest.TestCase):
    """1-b:acceptance 跳过元信息不得进 memory.facts。"""

    def _new_state(self):
        from state.core import GameState
        return GameState.new()

    def test_acceptance_skip_string_filtered(self):
        g = self._new_state()
        before = len(g.data.setdefault("memory", {}).setdefault("facts", []))
        g.apply_state_write_typed(
            "memory.facts",
            "acceptance 'GM需维持《阴阳易转论》修炼背景' 跳过因为今天是休息日",
            source="gm",
        )
        facts = g.data["memory"]["facts"]
        self.assertEqual(len(facts), before, "acceptance 跳过元信息不该进 memory.facts")
        self.assertFalse(any("跳过因为" in str(f) for f in facts))

    def test_normal_fact_still_appended(self):
        g = self._new_state()
        g.apply_state_write_typed("memory.facts", "素世在瀑布下泡了红茶", source="gm")
        self.assertTrue(any("素世" in str(f) for f in g.data["memory"]["facts"]),
                        "正常事实必须照常写入(过滤不能误伤)")


class AcceptanceCapAlignment(unittest.TestCase):
    """1-d:curator 存储 acceptance 上限 = GM 渲染上限(6)。"""

    def test_acceptance_capped_to_six(self):
        from agents.context_agent import _parse_curator_json
        payload = {"acceptance": [f"验收点{i}" for i in range(1, 9)]}  # 8 条
        plan = _parse_curator_json(json.dumps(payload, ensure_ascii=False))
        self.assertLessEqual(len(plan.get("acceptance", [])), 6,
                             "存储 acceptance 必须 <=6,与 rules_text 渲染 [:6] 对齐,否则第7/8条必然假 unmet")


if __name__ == "__main__":
    unittest.main()
