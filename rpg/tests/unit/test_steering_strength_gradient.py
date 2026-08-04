"""test_steering_strength_gradient.py
====================================

回归 / Issue A:游戏内「剧情引导强度」三档必须有**真实强度梯度**:
  free   = 不注入;
  guided = 温和软目标;
  rail   = 强力(把下一个待发生锚点当「必须推进的下一拍」,强措辞收束 + 偏离 1-3 轮拉回)。

历史病灶:steering.py 已为 rail 产出强措辞,但 context_inject.build_injection 无条件用
「软目标(引导非铁轨)」外层标签包裹,把 rail 又软化掉 → 贴原著退化成跟软引导一样温和,
用户报「设成贴原著 AI 仍自创剧情」。本测试钉死:rail 的内文与外层标签都必须强,
guided/free 行为不变。

本书 script_worldline_nodes 多半为空 → steering 实际走 _fallback_soft_goal,故重点覆盖它。
"""
from __future__ import annotations

import unittest
from unittest import mock

import gm_serving.steering as ST
from gm_serving.context_inject import build_injection

_PEND = [
    {"anchor_key": "a1", "summary": "蕾穆丽娜登场", "must_preserve": ["登场地点=学院"]},
    {"anchor_key": "a2", "summary": "主角与穆蕾莉娅初遇", "must_preserve": []},
]


class _ConstEmptyDB:
    """constant 世界书空(rows=[]),只考察 steering 外层包裹标签。"""

    def execute(self, sql, params=None):
        self._sql = sql
        return self

    def fetchone(self):
        if "count(*)" in self._sql:
            return {"c": 0}
        return None

    def fetchall(self):
        return []


class FallbackSoftGoalGradient(unittest.TestCase):
    """script_worldline_nodes 缺失时(本书实况)的降级路径强度梯度。"""

    def _run(self, strength):
        with mock.patch("agents.anchor_seed_agent.get_progress_window",
                        return_value={"chapter_min": 1, "chapter_max": 5}), \
             mock.patch("agents.anchor_seed_agent.list_pending_for_phase",
                        return_value=list(_PEND)):
            return ST._fallback_soft_goal(123, wl_key="main", steering_strength=strength)

    def test_free_injects_nothing(self):
        out = self._run("free")
        self.assertEqual(out["soft_goal"], "")
        self.assertEqual(out.get("strength"), "free")

    def test_guided_is_gentle(self):
        out = self._run("guided")
        self.assertIn("蕾穆丽娜登场", out["soft_goal"])
        self.assertNotIn("强制", out["soft_goal"])
        self.assertIn("交给玩家选择", out["soft_goal"])

    def test_rail_is_forceful(self):
        out = self._run("rail")
        sg = out["soft_goal"]
        # 强措辞:下一拍 / 强制收束 / 1-3 轮拉回 / 允许变体但不长期跑偏
        self.assertIn("强制收束", sg)
        self.assertIn("下一拍", sg)
        self.assertIn("1-3 轮", sg)
        self.assertIn("drift", sg)  # 允许合理变体
        self.assertIn("不可另起炉灶", sg)
        # 带 top-2 提示往哪推
        self.assertIn("主角与穆蕾莉娅初遇", sg)
        # rail 决不能退化成 guided 的温和措辞
        self.assertNotIn("交给玩家选择", sg)
        self.assertEqual(out["pending_anchors"], ["a1"])
        self.assertEqual(out.get("strength"), "rail")

    def test_rail_strictly_stronger_than_guided(self):
        rail = self._run("rail")["soft_goal"]
        guided = self._run("guided")["soft_goal"]
        self.assertNotEqual(rail, guided)
        self.assertGreater(len(rail), len(guided))


class InjectionWrapperLabelGradient(unittest.TestCase):
    """真根因层:build_injection 外层标签必须随强度变,rail 不得再被软标签包裹。"""

    def test_rail_uses_hard_label_not_soft(self):
        out = build_injection(_ConstEmptyDB(), script_id=1,
                              steering_hint="X", steering_strength="rail")
        self.assertIn("强制下一拍", out["text"])
        self.assertNotIn("软目标(引导非铁轨)", out["text"])

    def test_guided_keeps_soft_label(self):
        out = build_injection(_ConstEmptyDB(), script_id=1,
                              steering_hint="X", steering_strength="guided")
        self.assertIn("软目标(引导非铁轨)", out["text"])
        self.assertNotIn("强制下一拍", out["text"])

    def test_default_strength_is_guided(self):
        # 不传 steering_strength → 默认 guided 行为不变(软标签)
        out = build_injection(_ConstEmptyDB(), script_id=1, steering_hint="X")
        self.assertIn("软目标(引导非铁轨)", out["text"])

    def test_empty_hint_injects_no_steering_block(self):
        out = build_injection(_ConstEmptyDB(), script_id=1,
                              steering_hint="", steering_strength="rail")
        self.assertNotIn("强制下一拍", out["text"])
        self.assertNotIn("软目标", out["text"])


if __name__ == "__main__":
    unittest.main()
