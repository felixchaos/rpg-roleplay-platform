"""
test_frontend_no_premature_block_comment_close.py — 防 `/* ... */` 块注释被字符串里的 `*/` 提前闭合。

历史 bug 现场:
  Game Console.html 第 69 行写了 `provider:*/assembly/rules_engine/...`,
  外层是 `/* ... */` 块注释,作者本意 `*/` 是 URL 路径分隔写法,
  但 JS 解析器在第一个 `*/` 处就闭合注释,后续 `assembly/rules_engine/...`
  被当成 JS 代码 → `Uncaught SyntaxError: Missing semicolon`。

修复点:把多行 `/*  */` 改成多行 `//`,或在 `*` 与 `/` 间插一个空格。

本测试用极简扫描器复现 JS 解析器的注释行为:从 `/*` 起,遇到第一个 `*/` 立即闭合。
如果闭合点紧跟的字符是 ASCII 字母/下划线,就强烈怀疑作者把 `*/` 写进了文档字符串里
(标识符正文不会紧贴块注释结尾,正常 JS 是 `*/ var x` 这种空格隔开)。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
EXTS = {".jsx", ".js", ".html", ".ts", ".tsx"}
# 排除目录:不属于我们写的源
EXCLUDE_DIRS = {"node_modules", ".playwright-cli", "screenshots", "uploads", "output", ".git"}


def scan_premature_close(text: str) -> list[tuple[int, int, str]]:
    """返回 [(line, col, snippet)] — 每个疑似在 `/* */` 注释体内提前闭合的位置。"""
    bad: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n - 1:
        if text[i] == "/" and text[i + 1] == "*":
            j = i + 2
            while j < n - 1:
                if text[j] == "*" and text[j + 1] == "/":
                    break
                j += 1
            end = j + 2 if j < n - 1 else n
            after = text[end : end + 30] if end < n else ""
            # 闭合后紧跟 ASCII 字母 / 下划线 → 高度怀疑作者把 `*/` 当字符串里的 URL 段
            if re.match(r"[A-Za-z_]", after):
                line_no = text[:j].count("\n") + 1
                last_nl = text.rfind("\n", 0, j)
                col = j - last_nl
                snippet = text[max(0, j - 35) : j + 25].replace("\n", "\\n")
                bad.append((line_no, col, snippet))
            i = end
        else:
            i += 1
    return bad


def iter_frontend_files() -> list[Path]:
    out: list[Path] = []
    for p in FRONTEND.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in EXTS:
            continue
        if any(seg in EXCLUDE_DIRS for seg in p.parts):
            continue
        out.append(p)
    return out


class NoPrematureBlockCommentClose(unittest.TestCase):
    def test_no_premature_block_comment_close_anywhere(self):
        offenders: list[str] = []
        for p in iter_frontend_files():
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            hits = scan_premature_close(text)
            for line, col, snip in hits:
                rel = p.relative_to(FRONTEND.parent)
                offenders.append(f"{rel}:{line}:{col}  ...{snip}...")
        self.assertEqual(
            offenders,
            [],
            "块注释 `/* */` 被字符串里的 `*/` 提前闭合 → JS 解析炸。\n"
            "  修复:把多行 /* */ 改成多行 //;或在 `*` 和 `/` 间插一个空格。\n  "
            + "\n  ".join(offenders),
        )

    def test_known_offending_files_are_clean(self):
        # 显式锁定历史现场 — 这两个文件曾经触发过 SyntaxError。
        for rel in ("Game Console.html", "src/game-app.jsx"):
            p = FRONTEND / rel
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8")
            hits = scan_premature_close(text)
            self.assertEqual(
                hits,
                [],
                f"{rel} 又出现提前闭合块注释:{hits}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
