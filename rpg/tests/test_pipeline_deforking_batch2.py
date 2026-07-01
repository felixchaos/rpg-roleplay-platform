"""流水线去 fork · 批次2:steering_strength(mode)打通整条目标管线。

行者无疆「GM 永远默认我在修炼、日常也被拽回主线」的根修:mode 开关此前只接 steering.py,
curator/steering末节点/retrieval收束段 都不看 → 发散(free)局被当 rail railroad。
本批把 mode 贯穿:
  - curator(context_agent._curator_task_prompt):free 不强推主线 acceptance,guided 温和,rail 收束。
  - steering.py 末节点:free 不注软目标(补齐缺失分支)。
  - retrieval.py 收束段:free 跳过(admin 真机 e2e 已验,见 scratchpad/e2e_mode_gate)。
curator 部分纯函数可单测;steering/retrieval 部分加源码级 free-gate 守卫防回归。
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class CuratorModeAware(unittest.TestCase):
    def _prompt(self, ss: str) -> str:
        from agents.context_agent import _curator_task_prompt
        from state.core import GameState
        g = GameState.new()
        g.data["memory"]["main_quest"] = "阴阳易转论融汇测试"
        g.data["memory"]["current_objective"] = "打通转化环节"
        return _curator_task_prompt(g, "我今天休息,在房里喝红茶", [], steering_strength=ss)

    def test_free_forbids_canon_acceptance(self):
        p = self._prompt("free")
        self.assertIn("严禁", p)
        self.assertIn("自由", p)
        # 主线仍作背景出现,但明确"勿强推"
        self.assertIn("阴阳易转论", p)
        self.assertIn("勿强推", p)

    def test_guided_is_gentle(self):
        p = self._prompt("guided")
        self.assertIn("温和", p)
        self.assertNotIn("严禁", p)

    def test_rail_still_converges(self):
        p = self._prompt("rail")
        self.assertIn("收束", p)
        self.assertNotIn("严禁", p)

    def test_default_is_guided(self):
        # 不传 steering_strength → 默认 guided
        from agents.context_agent import _curator_task_prompt
        from state.core import GameState
        g = GameState.new()
        p = _curator_task_prompt(g, "test", [])
        self.assertIn("温和", p)


class SteeringFreeGateGuard(unittest.TestCase):
    """源码级守卫:free 分支不能再被删掉(防 fork 复发)。"""

    def test_steering_end_node_has_free_branch(self):
        from gm_serving import steering
        src = inspect.getsource(steering.resolve_steering_target)
        # 末节点 else 块必须区分 free(否则 free 走到剧情末尾又被注软目标)
        self.assertIn('steering_strength == "free"', src)

    def test_retrieval_convergence_gates_free(self):
        import retrieval
        src = inspect.getsource(retrieval.retrieve_context)
        # 收束段入口必须带 free gate
        self.assertIn('_steering_strength != "free"', src)


if __name__ == "__main__":
    unittest.main()
