"""
test_frontend_babel_parse.py — 用真实 @babel/parser 全量解析每个 JSX/JS 源 + HTML 内联 babel script。

前端是 Babel-standalone 在浏览器现场转译 JSX,没有构建期 lint;一个 `*/` 在
块注释里提前闭合、一个少写的逗号都能让整页 SyntaxError 白屏。本测试在 unittest 阶段
跑一次 @babel/parser,把这种"必白屏"错误前置到测试期。

依赖:
- node (任何最近版本)
- @babel/parser:测试会在 /tmp/babel_check 装一次,后续复用。

如果 node 不可用就 skip,不让没装 node 的环境失败。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
SRC = FRONTEND / "src"
HTMLS = [
    FRONTEND / "Game Console.html",
    FRONTEND / "Platform.html",
    FRONTEND / "Login.html",
    FRONTEND / "Design Canvas.html",
    FRONTEND / "Overview.html",
    FRONTEND / "index.html",
]
JS_SOURCES = [
    "game-app.jsx",
    "game-composer.jsx",
    "game-panels.jsx",
    "platform-app.jsx",
    "design-canvas.jsx",
    "tweaks-panel.jsx",
    "game-icons.jsx",
    "branch-graph.jsx",
    "api-client.js",
    "data-loader.js",
    "mock-data.js",
]


def _ensure_parser_installed() -> Path | None:
    """在 /tmp/babel_check 装一次 @babel/parser,返回 cwd;失败返回 None。"""
    if shutil.which("node") is None or shutil.which("npm") is None:
        return None
    cwd = Path(tempfile.gettempdir()) / "babel_check"
    cwd.mkdir(exist_ok=True)
    pkg = cwd / "node_modules" / "@babel" / "parser"
    if pkg.exists():
        return cwd
    try:
        subprocess.run(
            ["npm", "install", "--silent", "--no-audit", "--no-fund", "@babel/parser@7"],
            cwd=cwd,
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return cwd if pkg.exists() else None


PARSER_SCRIPT = r"""
const parser = require('@babel/parser');
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const PLUGINS_BABEL = ['jsx','optionalChaining','nullishCoalescingOperator','numericSeparator','objectRestSpread','classProperties'];
const PLUGINS_PLAIN = ['optionalChaining','nullishCoalescingOperator','numericSeparator','objectRestSpread','classProperties'];
const out = [];
for (const item of input) {
  try {
    parser.parse(item.src, {
      sourceType: 'module',
      plugins: item.jsx ? PLUGINS_BABEL : PLUGINS_PLAIN,
      allowReturnOutsideFunction: true,
    });
    out.push({ name: item.name, ok: true });
  } catch (e) {
    out.push({ name: item.name, ok: false, message: e.message, loc: e.loc || null });
  }
}
process.stdout.write(JSON.stringify(out));
"""


def _extract_inline_babel_scripts(html: str) -> list[tuple[str, str]]:
    """返回 [(label, src)] — 仅取 type=text/babel 的内联块。"""
    import re
    out: list[tuple[str, str]] = []
    pat = re.compile(r"<script\b([^>]*)>([\s\S]*?)</script>", re.IGNORECASE)
    for i, m in enumerate(pat.finditer(html)):
        attrs = m.group(1) or ""
        body = m.group(2) or ""
        if re.search(r"\bsrc\s*=", attrs):
            continue
        if re.search(r"type\s*=\s*[\"']?(application/json|importmap|text/template)", attrs, re.I):
            continue
        if not re.search(r"type\s*=\s*[\"']?text/babel", attrs, re.I):
            continue
        out.append((f"script#{i}", body))
    return out


class FrontendBabelParse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cwd = _ensure_parser_installed()
        if cls.cwd is None:
            raise unittest.SkipTest(
                "node/npm 不可用 或 @babel/parser 安装失败,跳过 babel 解析测试 "
                "(本机正常应当能装上,不要忽略)"
            )

    def _parse_items(self, items: list[dict]) -> list[dict]:
        proc = subprocess.run(
            ["node", "-e", PARSER_SCRIPT],
            cwd=str(self.cwd),
            input=json.dumps(items),
            text=True,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            self.fail(f"node 解析进程异常: rc={proc.returncode} stderr={proc.stderr[:400]}")
        return json.loads(proc.stdout)

    def test_jsx_and_js_sources_parse(self):
        items = []
        for name in JS_SOURCES:
            p = SRC / name
            if not p.exists():
                continue
            items.append({"name": name, "src": p.read_text(encoding="utf-8"), "jsx": name.endswith(".jsx")})
        results = self._parse_items(items)
        failed = [r for r in results if not r["ok"]]
        if failed:
            lines = [f"{r['name']}: {r['message']}  loc={r.get('loc')}" for r in failed]
            self.fail("以下源文件 @babel/parser 解析失败:\n  " + "\n  ".join(lines))

    def test_html_inline_babel_scripts_parse(self):
        items = []
        for html_path in HTMLS:
            if not html_path.exists():
                continue
            html = html_path.read_text(encoding="utf-8")
            for label, src in _extract_inline_babel_scripts(html):
                items.append({"name": f"{html_path.name}::{label}", "src": src, "jsx": True})
        if not items:
            self.skipTest("没有找到任何内联 text/babel 块")
        results = self._parse_items(items)
        failed = [r for r in results if not r["ok"]]
        if failed:
            lines = [f"{r['name']}: {r['message']}  loc={r.get('loc')}" for r in failed]
            self.fail("以下内联 babel 脚本 @babel/parser 解析失败:\n  " + "\n  ".join(lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
