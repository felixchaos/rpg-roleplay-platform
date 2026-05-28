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
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


class NoButtonInButton(unittest.TestCase):
    """SettingsToggle 必须 stopPropagation；ModelsSection 不再用 <button> 包 toggle。"""

    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).resolve().parents[3] / "frontend" / "src" / "platform-app.jsx"
        cls.text = cls.path.read_text(encoding="utf-8")

    def test_settings_toggle_has_stop_propagation(self):
        # SettingsToggle 渲染的 <button> 必须 stopPropagation，否则被
        # 父级可点击容器套住时会冒泡触发父级 onClick。
        idx = self.text.find("function SettingsToggle")
        self.assertGreater(idx, 0)
        end = self.text.find("\nfunction ", idx + 1)
        body = self.text[idx:end if end > 0 else idx + 600]
        self.assertIn("stopPropagation", body,
            "SettingsToggle 应在 onClick 里 e.stopPropagation()")
        self.assertIn('type="button"', body,
            "SettingsToggle 应显式 type=\"button\" 防止 form 误提交")

    def test_models_section_card_head_not_a_button(self):
        # ModelsSection 的 pl-api-card-head 容器以前是 <button>，里面塞了
        # <SettingsToggle/>（也是 button）→ invalid DOM。改成 div role="button"。
        idx = self.text.find("pl-api-card-head")
        self.assertGreater(idx, 0)
        window = self.text[max(0, idx - 200):idx + 50]
        self.assertNotIn('<button className="pl-api-card-head"', window,
            "pl-api-card-head 不应再是 <button>；改为 <div role='button'>")
        self.assertIn('role="button"', window,
            "pl-api-card-head 应保留键盘可访问性：role='button'")

    def test_models_section_card_head_has_keyboard_support(self):
        # div role="button" 需要 tabIndex 和 Enter/Space 键盘绑定
        idx = self.text.find('"pl-api-card-head"')
        self.assertGreater(idx, 0)
        snippet = self.text[idx:idx + 500]
        self.assertIn("tabIndex", snippet, "pl-api-card-head div 应有 tabIndex 让键盘 focus")
        self.assertIn("onKeyDown", snippet, "pl-api-card-head div 应处理 Enter/Space 键")


class GeneralButtonNestingScan(unittest.TestCase):
    """精确扫描：仅在 SettingsToggle 父级直接相邻一个 <button> 开标签且无中间 control={...} 转折时算 nest。
    避免 control={<button .../>} + control={<SettingsToggle .../>} 这种相邻 sibling 误报。
    """

    def test_no_nested_button_around_settings_toggle(self):
        path = Path(__file__).resolve().parents[3] / "frontend" / "src" / "platform-app.jsx"
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        # 反向数：找每个 <SettingsToggle，回看直到看到一个 jsx 元素 close（self-close 或 closing tag），
        # 期间不能有未闭合的 <button>
        bad: list[str] = []
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
                    bad.append(f"L{j+1}: {seg.strip()[:80]}  →  L{i+1}: {line.strip()[:80]}")
                    break
        self.assertEqual(bad, [], "发现 SettingsToggle 嵌在未闭合 <button> 内:\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
