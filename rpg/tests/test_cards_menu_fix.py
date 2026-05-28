"""
test_cards_menu_fix.py — 角色卡 CardGrid 菜单 CSS + NPC 迁移修复防退化。

用户报告：
1. 角色卡 "更多" 下拉菜单透明叠在 bio 文字上 → CSS bug
2. 缺 NPC 角色卡 → 用户角色卡一键迁移
"""
from __future__ import annotations

import unittest
from pathlib import Path


class CardGridMenuCss(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "platform-app.jsx"
        cls.text = cls.path.read_text(encoding="utf-8")

    def test_menu_no_longer_uses_undefined_surface_var(self):
        # tokens.css 没定义 --surface；用了会回退透明 → 叠在 bio 文字上。
        # 修后必须用 --panel-2 等真实变量。
        self.assertNotIn(
            'background: "var(--surface)"', self.text,
            "CardGrid 菜单不应再用未定义的 var(--surface)，"
            "tokens.css 只定义了 --bg/--panel/--panel-2/--panel-3"
        )

    def test_menu_uses_panel_2_or_panel(self):
        # 至少有一处 pl-card-menu 用到 panel-2
        self.assertIn(
            'background: "var(--panel-2)"', self.text,
            "CardGrid 菜单应用 var(--panel-2) 当深色背景"
        )

    def test_menu_has_explicit_text_color(self):
        # 避免菜单文字颜色不可见
        self.assertIn(
            'color: "var(--text)"', self.text,
            "CardGrid 菜单应显式设 color: var(--text)，避免暗色背景下文字不可见"
        )


class NpcPromoteToUserCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "platform-app.jsx"
        cls.text = cls.path.read_text(encoding="utf-8")

    def test_promote_function_exists(self):
        self.assertIn(
            "promoteNpcToUserCard", self.text,
            "应存在 promoteNpcToUserCard 函数把 NPC 卡迁成 user_card"
        )

    def test_promote_calls_user_card_api(self):
        self.assertIn(
            "window.api.cards.myUpsert", self.text,
            "promoteNpcToUserCard 必须走 cards.myUpsert 真后端"
        )

    def test_promote_action_only_for_npc_kind(self):
        # 菜单里应有一个 "转为用户角色卡" 按钮，且只在 kind === "npc" 显示
        self.assertIn("转为用户角色卡", self.text,
            "CardGrid 菜单应含『转为用户角色卡』入口")
        # 检查 "转为用户角色卡" 附近（前 600 字符内）有 kind === "npc" guard
        idx = self.text.find("转为用户角色卡")
        self.assertGreater(idx, 0)
        window_start = max(0, idx - 600)
        window = self.text[window_start:idx + 200]
        self.assertIn('kind === "npc"', window,
            f"『转为用户角色卡』前 600 字符内应有 kind === \"npc\" 守卫；"
            f"窗口={window[-500:]!r}")

    def test_promote_emits_refresh_event(self):
        self.assertIn(
            "rpg-user-cards-updated", self.text,
            "迁移后应 dispatch rpg-user-cards-updated 让 UserCardsView 自动刷新"
        )

    def test_user_cards_view_listens_for_promotion(self):
        self.assertIn(
            'addEventListener("rpg-user-cards-updated"', self.text,
            "UserCardsView 必须监听 rpg-user-cards-updated 事件来 reload"
        )


class NoUndefinedCssVar(unittest.TestCase):
    """全文兜底：platform-app.jsx 不应再有任何 var(--surface) 实际 CSS 用法。"""

    def test_no_var_surface_in_platform_app(self):
        path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "platform-app.jsx"
        text = path.read_text(encoding="utf-8")
        for line in text.split("\n"):
            if "var(--surface)" not in line:
                continue
            stripped = line.strip()
            # 跳过注释（/* ... */ 块内或单行注释 // 内的提及）
            if stripped.startswith(("/*", "*", "//")) or "原 var(--surface)" in line:
                continue
            self.fail(
                f"platform-app.jsx 仍有 var(--surface) 实际 CSS 用法（非注释）：\n  {stripped}\n"
                f"tokens.css 没定义该变量，会回退透明背景。"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
