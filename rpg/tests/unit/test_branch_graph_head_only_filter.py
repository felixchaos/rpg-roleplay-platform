"""
test_branch_graph_head_only_filter.py
=====================================

用户报告:分支图看不懂,与 git 不一样。澄清后明确两种语义:
  · 游戏内右侧 BranchTreeRail = 当前子分支(HEAD ancestor chain 一条线)
  · Platform BranchesPage     = 完整 DAG(所有分支路线)

修复:BranchGraph 加 headOnly prop。
  · 默认值:variant="compact" → headOnly=true (游戏内单线)
            variant="full"    → headOnly=false (Platform 完整 DAG)
  · 显式传入会覆盖默认
  · _filterToHeadAncestors 沿 parent_id 从 active_commit_id 向上溯源,
    返回 ancestor chain (含 root)

附加视觉:ref pill 着色用 _colorForRef(refName) 稳定 hash → palette
  · HEAD / refs/heads/main 永远是主色 accent
  · 其他 ref 按尾段名 hash 选 palette index 1..5
  · 这样即便所有 commits 都在 column 0 线性,不同 ref 也用不同 pill 颜色
    避免"看起来全是一条线"的视觉混淆
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
BG_JSX = (PROJECT / "frontend" / "src" / "branch-graph.jsx").read_text(encoding="utf-8")
GAME_APP = (PROJECT / "frontend" / "src" / "game-app.jsx").read_text(encoding="utf-8")
ZH_CN = json.loads((PROJECT / "frontend" / "src" / "i18n" / "locales" / "zh-CN.json").read_text(encoding="utf-8"))


def _zh(dotted: str) -> str:
    """按点分 key 取 zh-CN locale 文案(文案已迁 i18n,jsx 里只剩 t() key)。"""
    node = ZH_CN
    for part in dotted.split("."):
        node = node[part]
    return node


class HeadOnlyFilterImplementation(unittest.TestCase):
    def test_filter_helper_exists(self):
        self.assertIn("function _filterToHeadAncestors(", BG_JSX,
            "branch-graph.jsx 应有 _filterToHeadAncestors helper")
        # 算法关键字
        self.assertIn("parent_id", BG_JSX)
        self.assertIn("active_commit_id", BG_JSX)

    def test_branch_graph_accepts_head_only_prop(self):
        # BranchGraph 函数签名应有 headOnly 参数
        m = re.search(r"function BranchGraph\(\s*\{([^}]+)\}", BG_JSX)
        self.assertIsNotNone(m, "BranchGraph 函数签名应是 destructuring 形")
        params_blob = m.group(1)
        self.assertIn("headOnly", params_blob,
            "BranchGraph 函数应接 headOnly prop")

    def test_effective_head_only_defaults_by_variant(self):
        # variant="compact" 默认 headOnly=true,"full" 默认 false
        self.assertIn("effectiveHeadOnly", BG_JSX,
            "BranchGraph 应根据 variant 算 effective headOnly")
        # 默认逻辑必须区分 compact / 非 compact
        self.assertTrue(
            re.search(r'variant\s*===\s*"compact"', BG_JSX) is not None,
            "默认值切换必须基于 variant === 'compact'",
        )

    def test_compact_uses_filter_full_does_not(self):
        # 找算 effectiveHeadOnly 之后用它过滤 nodes 的位置
        self.assertIn("_filterToHeadAncestors(rawNodes", BG_JSX,
            "BranchGraph 应在 effectiveHeadOnly=true 时调 _filterToHeadAncestors")


class RefPillColorHashing(unittest.TestCase):
    """ref pill 视觉区分:不同 ref 用不同 palette 颜色,即便 commits 线性。"""

    def test_color_for_ref_helper_exists(self):
        self.assertIn("function _colorForRef(", BG_JSX,
            "branch-graph.jsx 应有 _colorForRef helper")

    def test_color_for_ref_special_cases_head_and_main(self):
        idx = BG_JSX.find("function _colorForRef(")
        end = BG_JSX.find("\nfunction ", idx + 1)
        body = BG_JSX[idx:end if end > 0 else len(BG_JSX)]
        # HEAD 和 refs/heads/main 走主色
        self.assertIn("HEAD", body)
        self.assertIn("refs/heads/main", body)

    def test_ref_pill_uses_color_for_ref(self):
        # 渲染 ref pill 处必须调用 _colorForRef
        self.assertIn("_colorForRef(refName)", BG_JSX,
            "ref pill 渲染必须用 _colorForRef 算颜色")


class GameRailWording(unittest.TestCase):
    """游戏内侧栏 head 文案应明示是"当前子分支",避免被误以为是全 DAG。
    文案已迁 i18n:jsx 里查 t() key 被引用 + locale JSON 里查中文实词(同
    test_continue_picker_uses_commit_activate 等姊妹测试的范式)。"""

    @staticmethod
    def _rail_body() -> str:
        idx = GAME_APP.find("function BranchTreeRail(")
        end = GAME_APP.find("\nfunction ", idx + 1)
        return GAME_APP[idx:end if end > 0 else len(GAME_APP)]

    def test_rail_head_says_current_sub_branch(self):
        body = self._rail_body()
        self.assertIn("game.app.branch.current_branches", body,
            "BranchTreeRail head 应引用 current_branches 文案 key")
        self.assertIn("game.app.branch.head_history", body,
            "BranchTreeRail head 副标题应引用 head_history 文案 key")
        self.assertIn("当前子分支", _zh("game.app.branch.current_branches"),
            "locale 文案应写'当前子分支'区分于 Platform 完整 DAG")
        self.assertIn("HEAD 历史", _zh("game.app.branch.head_history"),
            "locale 文案应是'HEAD 历史'明示是 ancestor chain")

    def test_link_to_platform_explains_full_dag(self):
        body = self._rail_body()
        self.assertIn("game.app.branch.open_full_tip", body,
            "BranchTreeRail 跳 Platform 链接 tooltip 应引用 open_full_tip 文案 key")
        self.assertIn("所有分支路线", _zh("game.app.branch.open_full_tip"),
            "locale tooltip 文案应明示是'查看所有分支路线'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
