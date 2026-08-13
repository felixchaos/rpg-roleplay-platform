"""
test_except_handler_scope.py —— except 处理块不许引用「只在 try 内绑定」的局部变量。

来源:OSS PR #98(dragonjay-lyj)把静默 `except Exception: pass` 改成打日志 —— 方向对,
但其中两处日志引用了在 try **内部**才赋值的变量:

    try:
        from platform_app.cluster import request_stop      # ← 这行抛的话
        current_run = _lru_get(...)                        # ← 就没执行到
    except Exception:
        log.warning(..., current_run, ...)                 # ← UnboundLocalError

结果是把本来被吞掉的错误升级成真崩溃 —— 比原来的 `pass` 更糟。
维护者已修(在 try 之前先绑定),这里用 AST 锁死,防止同类写法再回流。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

RPG = Path(__file__).resolve().parents[2]
# `.venv/` 必须排除:生产与本地约定的 venv 就在 rpg/.venv(见部署 runbook),不排除的话
# 这个守卫会扫进 site-packages,拿第三方代码的写法把自己判红(153 处全是依赖里的)。
# CI 恰好没暴露 —— 它把依赖装在 runner 的系统 python 里,rpg/ 下没有 venv。
# 其余条目对齐 pyproject.toml 的 ruff extend-exclude。
SKIP_DIRS = (
    "tests/", "claude_design_upload/", ".venv/", "venv/",
    "user_skills/", "saves/", "platform_data/", "indexes/", "modules/",
)


def _names_bound_before(node_body: list[ast.stmt], upto: ast.stmt) -> set[str]:
    """同级语句中,排在 upto 之前的赋值/for/with 目标名。"""
    out: set[str] = set()
    for stmt in node_body:
        if stmt is upto:
            break
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                out.add(sub.id)
    return out


def _scan(tree: ast.AST, src_name: str) -> list[str]:
    problems: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        params: set[str] = set()
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = fn.args
            params = {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]}
            if a.vararg: params.add(a.vararg.arg)
            if a.kwarg: params.add(a.kwarg.arg)
        for parent in ast.walk(fn):
            body = getattr(parent, "body", None)
            if not isinstance(body, list):
                continue
            for stmt in body:
                if not isinstance(stmt, ast.Try):
                    continue
                bound_in_try = {
                    n.id for s in stmt.body for n in ast.walk(s)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                }
                outer = _names_bound_before(body, stmt) | params
                for handler in stmt.handlers:
                    local = set(outer)
                    if handler.name:
                        local.add(handler.name)
                    # handler 自己绑定的名字要排除:大量正常写法是在 except 里先重新赋值
                    # 再使用(如 `out = {...}` 兜底、`with connect() as db:` 重开连接)。
                    # 保守起见按整个 handler 的 Store 集合算,宁可漏报也不误报。
                    local |= {
                        n.id for n in ast.walk(handler)
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                    }
                    local |= {
                        (n.optional_vars.id if isinstance(getattr(n, "optional_vars", None), ast.Name) else "")
                        for n in ast.walk(handler) if isinstance(n, ast.withitem)
                    }
                    for n in ast.walk(handler):
                        if not (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)):
                            continue
                        if n.id in bound_in_try and n.id not in local:
                            problems.append(f"{src_name}:{n.lineno} except 引用了只在 try 内绑定的 {n.id!r}")
    return problems


class ExceptHandlersDontReferenceTryLocals(unittest.TestCase):
    def test_no_unbound_local_in_except(self):
        problems: list[str] = []
        for path in sorted(RPG.rglob("*.py")):
            rel = path.relative_to(RPG).as_posix()
            if rel.startswith(SKIP_DIRS):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            problems.extend(_scan(tree, rel))
        self.assertEqual(
            problems, [],
            "except 里引用 try 内才赋值的变量 → 异常发生在赋值之前时抛 UnboundLocalError,"
            "把被吞的错误升级成崩溃。请在 try 之前先绑定默认值:\n  " + "\n  ".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
