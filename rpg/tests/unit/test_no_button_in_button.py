"""
test_no_button_in_button.py — 防止 <button> 嵌套 <button> 的 invalid DOM。

React 在浏览器里报：
  validateDOMNesting(...): <button> cannot appear as a descendant of <button>.
  at SettingsToggle
  ...
  at ModelsSection

复测时 Platform.html#settings 的 API 折叠条把 SettingsToggle（本身是 <button>）
塞进 <button class="pl-api-card-head"> 里 → 浏览器报 4 个 warning + 点 toggle
还会冒泡触发外层展开。

住址更新(平台拆分):SettingsToggle 现住 components/platform/shared.jsx;
ModelsSection 现住 pages/settings.jsx(API 折叠卡整体重建为表格,旧
pl-api-card-head 折叠条已不存在,toggle 单元格用 stopPropagation 的 <span> 包)。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

FRONTEND_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
SHARED_JSX = (FRONTEND_SRC / "components" / "platform" / "shared.jsx").read_text(encoding="utf-8")
SETTINGS_JSX = (FRONTEND_SRC / "pages" / "settings.jsx").read_text(encoding="utf-8")


class NoButtonInButton(unittest.TestCase):
    """SettingsToggle 必须 stopPropagation；ModelsSection 不再用 <button> 包 toggle。"""

    def test_settings_toggle_has_stop_propagation(self):
        # SettingsToggle 渲染的 <button> 必须 stopPropagation，否则被
        # 父级可点击容器套住时会冒泡触发父级 onClick。
        idx = SHARED_JSX.find("function SettingsToggle")
        self.assertGreater(idx, 0, "components/platform/shared.jsx 应有 SettingsToggle(拆分后住这里)")
        end = SHARED_JSX.find("\nfunction ", idx + 1)
        body = SHARED_JSX[idx:end if end > 0 else idx + 600]
        self.assertIn("stopPropagation", body,
            "SettingsToggle 应在 onClick 里 e.stopPropagation()")
        self.assertIn('type="button"', body,
            "SettingsToggle 应显式 type=\"button\" 防止 form 误提交")

    def test_models_section_card_head_not_a_button(self):
        # 事发地 pl-api-card-head 折叠条已随 UI 重构整体让位给表格(pages/settings.jsx
        # ModelsSection);等价不变量:① bug 的原始签名(<button> 挂 pl-api-card-head 类)
        # 不得在任何前端源码里复活;② 表格里包 SettingsToggle 的 provider 行 toggle
        # 单元格必须是非 button 容器且 stopPropagation(防点 toggle 触发行选中)。
        for f in FRONTEND_SRC.rglob("*.jsx"):
            self.assertNotIn('<button className="pl-api-card-head"', f.read_text(encoding="utf-8"),
                f"{f} 复活了 <button class='pl-api-card-head'>(button-in-button 原始签名)")
        m = re.search(
            r"<span onClick=\{\(e\) => e\.stopPropagation\(\)\}>\s*\n\s*<SettingsToggle",
            SETTINGS_JSX,
        )
        self.assertIsNotNone(m,
            "ModelsSection provider 行的 SettingsToggle 必须包在 stopPropagation 的 <span> 里")

    def test_models_section_card_head_has_keyboard_support(self):
        # 旧 div role="button" 折叠条已不存在(表格自带交互语义)。守住模式本身:
        # 若日后在这两个文件里复活 role="button" 的非 button 元素,必须带
        # tabIndex + onKeyDown 键盘支持(当前无出现 → 空转通过,防回归留哨)。
        for name, text in (("shared.jsx", SHARED_JSX), ("settings.jsx", SETTINGS_JSX)):
            for m in re.finditer(r'role="button"', text):
                window = text[m.start(): m.start() + 500]
                self.assertIn("tabIndex", window,
                    f"{name}: role='button' 元素应有 tabIndex 让键盘 focus")
                self.assertIn("onKeyDown", window,
                    f"{name}: role='button' 元素应处理 Enter/Space 键")


class GeneralButtonNestingScan(unittest.TestCase):
    """精确扫描：仅在 SettingsToggle 父级直接相邻一个 <button> 开标签且无中间 control={...} 转折时算 nest。
    避免 control={<button .../>} + control={<SettingsToggle .../>} 这种相邻 sibling 误报。
    拆分后 <SettingsToggle 的渲染点分散多文件 → 动态发现所有使用文件逐一扫,
    不再盯死 platform-app.jsx(那里已无渲染点,盯死=空转假绿)。
    """

    def test_no_nested_button_around_settings_toggle(self):
        usage_files = [
            f for f in FRONTEND_SRC.rglob("*.jsx")
            if "<SettingsToggle" in f.read_text(encoding="utf-8")
        ]
        self.assertTrue(usage_files,
            "全前端找不到 <SettingsToggle 渲染点 —— 扫描目标丢失,请更新本测试")
        bad: list[str] = []
        for path in usage_files:
            lines = path.read_text(encoding="utf-8").split("\n")
            # 反向数：找每个 <SettingsToggle，回看直到看到一个 jsx 元素 close（self-close 或 closing tag），
            # 期间不能有未闭合的 <button>
            for i, line in enumerate(lines):
                if "<SettingsToggle" not in line:
                    continue
                depth_button = 0
                for j in range(i - 1, max(0, i - 60), -1):
                    seg = lines[j]
                    # 跳过整行的 self-close 或 closing
                    # 简化：碰到 control={ 之类 prop 边界就停（不是真嵌套）
                    if "control={" in seg or "<SettingsBlock" in seg or " />" in seg:
                        break
                    if "</button>" in seg:
                        depth_button -= 1
                    if re.search(r"<button(?!Toggle|\w)", seg):
                        depth_button += 1
                    if depth_button > 0:
                        bad.append(f"{path.name} L{j+1}: {seg.strip()[:80]}  →  L{i+1}: {line.strip()[:80]}")
                        break
        self.assertEqual(bad, [], "发现 SettingsToggle 嵌在未闭合 <button> 内:\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
