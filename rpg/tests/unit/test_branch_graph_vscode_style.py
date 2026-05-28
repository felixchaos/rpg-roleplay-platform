"""
test_branch_graph_vscode_style.py
=================================

用户要求"一个存档一个 git 系统",UI 一模一样 VSCode Git Graph。

落地方案:
- 后端不动 (branch_commits + branch_refs + parent_id 树已经是完整 git 语义)
- 新增 frontend/src/branch-graph.jsx 共用组件 (BranchGraph + swimlane 算法)
- 游戏内右侧 BranchTreeRail 改用 <BranchGraph variant="compact"/>
- Platform 分支管理页 BranchesPage 改用 <BranchGraph variant="full"/>
- 两个 HTML 都加载 branch-graph.jsx

测试 (纯静态扫源,不依赖 React 运行时):

Layer A — branch-graph.jsx 共用组件:
  · BranchGraph 函数存在 + 暴露在 window
  · 包含 swimlane 算法 (_assignColumns)
  · 含 variant=compact / variant=full 两个模式
  · 含 ref pill 渲染 (bg-ref-pill / bg-ref-head)
  · 含 fork 曲线 (path d=M...C 贝塞尔)

Layer B — 游戏内 BranchTreeRail 升级:
  · game-app.jsx BranchTreeRail 使用 <BranchGraph
  · 不再渲染旧 <ul className="gc-rail-branch-list"> 列表
  · 仍订阅 rpg-state-reload / rpg-saves-updated 事件

Layer C — Platform BranchesPage 升级:
  · platform-app.jsx BranchesPage 使用 <BranchGraph
  · 删除拖拽 layout (svgRef / dragId / onNodePointerDown)
  · 删除 zoom 按钮 (BranchGraph 不需要 zoom — 是表格行)

Layer D — HTML 加载顺序:
  · Game Console.html 加载 src/branch-graph.jsx (在 game-app.jsx 前)
  · Platform.html 加载 src/branch-graph.jsx (在 platform-app.jsx 前)
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"
BG_JSX = (FRONTEND / "src" / "branch-graph.jsx").read_text(encoding="utf-8")
GAME_APP = (FRONTEND / "src" / "game-app.jsx").read_text(encoding="utf-8")
PLATFORM = (FRONTEND / "src" / "platform-app.jsx").read_text(encoding="utf-8")
GAME_HTML = (FRONTEND / "Game Console.html").read_text(encoding="utf-8")
PLATFORM_HTML = (FRONTEND / "Platform.html").read_text(encoding="utf-8")


# ────────────────────────────────────────────────────────────
# Layer A: BranchGraph 组件
# ────────────────────────────────────────────────────────────


class BranchGraphComponent(unittest.TestCase):
    def test_branch_graph_function_exists(self):
        self.assertIn("function BranchGraph(", BG_JSX)

    def test_branch_graph_exposed_on_window(self):
        # 别的 jsx 文件用 BranchGraph 时需要 window 全局暴露
        self.assertIn("window, { BranchGraph }", BG_JSX)

    def test_swimlane_algorithm_present(self):
        # _assignColumns 是核心 layout 算法
        self.assertIn("function _assignColumns(", BG_JSX)
        # 按 turn_index 升序遍历
        self.assertIn("turn_index", BG_JSX)
        # 维护 columns 数组 + columnOf map
        self.assertIn("columns", BG_JSX)
        self.assertIn("columnOf", BG_JSX)

    def test_variant_compact_and_full(self):
        # variant prop 支持 compact / full
        self.assertIn('variant === "compact"', BG_JSX)
        self.assertIn('variant = "full"', BG_JSX)

    def test_ref_pill_rendering(self):
        # ref pill (branch / HEAD 标签)
        self.assertIn("bg-ref-pill", BG_JSX)
        self.assertIn("bg-ref-head", BG_JSX)
        # HEAD 指针文案
        self.assertIn("HEAD", BG_JSX)

    def test_fork_curve_bezier_path(self):
        # 跨 column 时的 S 形曲线 (VSCode 同款)
        # 找 SVG path 的 "M ... C ..." 模式
        self.assertTrue(
            re.search(r"`M \${[^}]+}.+C \${[^}]+}", BG_JSX) is not None,
            "BranchGraph 应有 S 形贝塞尔 path 函数 (跨 column 连线)",
        )

    def test_head_active_dot_has_stroke_ring(self):
        # active commit 的 dot 有外环 (VSCode 风格)
        # 找一个 circle 元素与 strokeWidth/stroke 关联
        self.assertIn("isActive", BG_JSX)
        self.assertIn("strokeWidth", BG_JSX)

    def test_color_palette_uses_theme_vars(self):
        # 颜色用主题变量,不 hardcode hex
        self.assertIn("var(--accent)", BG_JSX)
        self.assertIn("var(--info)", BG_JSX)
        # 至少 5 个不同颜色 (循环复用)
        self.assertIn("BG_COLORS", BG_JSX)


# ────────────────────────────────────────────────────────────
# Layer B: 游戏内 BranchTreeRail 升级
# ────────────────────────────────────────────────────────────


class GameRailUsesBranchGraph(unittest.TestCase):
    def test_branch_tree_rail_uses_branch_graph(self):
        # 找 BranchTreeRail 函数体
        idx = GAME_APP.find("function BranchTreeRail(")
        self.assertGreater(idx, 0)
        end = GAME_APP.find("\nfunction ", idx + 1)
        body = GAME_APP[idx:end if end > 0 else len(GAME_APP)]
        # 用 <BranchGraph
        self.assertIn("<BranchGraph", body,
            "BranchTreeRail 应渲染 <BranchGraph 组件")
        self.assertIn('variant="compact"', body,
            "侧边栏用紧凑变体")

    def test_old_ul_list_renderer_gone(self):
        # 不再渲染旧的 <ul className="gc-rail-branch-list"> 列表 + .map(n => <li>...)
        idx = GAME_APP.find("function BranchTreeRail(")
        end = GAME_APP.find("\nfunction ", idx + 1)
        body = GAME_APP[idx:end if end > 0 else len(GAME_APP)]
        self.assertNotIn("<ul className=\"gc-rail-branch-list\"", body,
            "BranchTreeRail 不应再渲染旧 <ul> 列表(已被 BranchGraph 取代)")

    def test_reload_events_still_subscribed(self):
        # 现有的 rpg-state-reload / rpg-saves-updated 监听必须保留
        idx = GAME_APP.find("function BranchTreeRail(")
        end = GAME_APP.find("\nfunction ", idx + 1)
        body = GAME_APP[idx:end if end > 0 else len(GAME_APP)]
        self.assertIn("rpg-state-reload", body)
        self.assertIn("rpg-saves-updated", body)


# ────────────────────────────────────────────────────────────
# Layer C: Platform BranchesPage 升级
# ────────────────────────────────────────────────────────────


class PlatformBranchesPageUsesBranchGraph(unittest.TestCase):
    def test_branches_page_uses_branch_graph(self):
        idx = PLATFORM.find("function BranchesPage(")
        self.assertGreater(idx, 0)
        end = PLATFORM.find("\nfunction ", idx + 1)
        body = PLATFORM[idx:end if end > 0 else len(PLATFORM)]
        self.assertIn("<BranchGraph", body,
            "BranchesPage 应渲染 <BranchGraph 组件")
        self.assertIn('variant="full"', body,
            "Platform 用完整变体")

    def test_old_drag_layout_removed(self):
        # 旧自由拖拽 SVG 的核心:dragId / onNodePointerDown / svgPoint
        idx = PLATFORM.find("function BranchesPage(")
        end = PLATFORM.find("\nfunction ", idx + 1)
        body = PLATFORM[idx:end if end > 0 else len(PLATFORM)]
        self.assertNotIn("onNodePointerDown", body,
            "BranchesPage 不应再有拖拽节点逻辑")
        self.assertNotIn("setDragId", body,
            "BranchesPage 不应再有 dragId state")
        # zoom 按钮也不应保留 (BranchGraph 是行布局,不需要)
        self.assertNotIn("zoomIn", body,
            "BranchesPage 不应再有 zoom 按钮")

    def test_delete_modal_preserved(self):
        # 删除子树确认弹窗仍要存在
        idx = PLATFORM.find("function BranchesPage(")
        end = PLATFORM.find("\nfunction ", idx + 1)
        body = PLATFORM[idx:end if end > 0 else len(PLATFORM)]
        self.assertIn("ConfirmModal", body)
        self.assertIn("/api/branches/delete", body)


# ────────────────────────────────────────────────────────────
# Layer D: HTML 加载顺序
# ────────────────────────────────────────────────────────────


class HtmlLoadsBranchGraph(unittest.TestCase):
    def test_game_console_loads_branch_graph_before_game_app(self):
        idx_bg = GAME_HTML.find("branch-graph.jsx")
        idx_app = GAME_HTML.find("game-app.jsx")
        self.assertGreater(idx_bg, 0, "Game Console.html 应加载 src/branch-graph.jsx")
        self.assertGreater(idx_app, 0)
        self.assertLess(idx_bg, idx_app,
            "branch-graph.jsx 必须在 game-app.jsx 之前加载 (BranchTreeRail 用到 BranchGraph)")

    def test_platform_loads_branch_graph_before_platform_app(self):
        idx_bg = PLATFORM_HTML.find("branch-graph.jsx")
        idx_app = PLATFORM_HTML.find("platform-app.jsx")
        self.assertGreater(idx_bg, 0, "Platform.html 应加载 src/branch-graph.jsx")
        self.assertGreater(idx_app, 0)
        self.assertLess(idx_bg, idx_app,
            "branch-graph.jsx 必须在 platform-app.jsx 之前加载")


# ────────────────────────────────────────────────────────────
# Layer E: CSS 样式存在
# ────────────────────────────────────────────────────────────


class CssClassesExist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (FRONTEND / "src" / "game-console.css").read_text(encoding="utf-8")

    def test_branch_graph_css_classes(self):
        # 必备 CSS 类
        for cls_name in (
            ".bg-root", ".bg-rows", ".bg-row", ".bg-svg",
            ".bg-message", ".bg-ref-pill", ".bg-ref-head",
            ".bg-active", ".bg-deleted", ".bg-actions",
        ):
            self.assertIn(cls_name, self.css,
                f"CSS 缺类 {cls_name} (BranchGraph 视觉)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
