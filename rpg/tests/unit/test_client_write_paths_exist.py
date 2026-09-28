"""test_client_write_paths_exist.py — 客户端调用的写接口必须真的存在。

「按钮在、请求发出去了、后端根本没有这条路由」是 UI 存在 ≠ 生效最便宜也最隐蔽的一种:
客户端多半 `try? / catch(_){}` 吞掉 404/405,用户只看到「点了没反应」。前科:
  · v1.77.0 删了 POST /api/memory/mode(记忆模式什么都不控制),web / 手机删了选择器,
    iOS 游戏台左抽屉的「记忆模式」还在,每点一次打一个 404(`try?` 吞掉)。
  · api-client 里挂着 /api/auth/sms-code、/api/models/refresh、/api/library/delete 等
    从来没有后端的包装,谁接上谁就是一个死按钮。
  · /api/platform/commands 的命令清单(_deps.COMMANDS)还在向外宣告已删除的端点。

本守卫对三份清单逐条核对「方法 + 路径」是否命中 FastAPI 的真实路由表:
  1. frontend/src/api-client.js 的 POST/PUT/PATCH/DEL 包装;
  2. ios/Sources/API.swift 里带 method 的 request(...) / postExpectOK(...);
  3. platform_app.api._deps.COMMANDS。
解析是正则级的:认不出的新写法会漏检(不误报);另有「至少解析到 N 条」兜底,防止正则失效后静默全绿。
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

_RPG = pathlib.Path(__file__).resolve().parents[2]
REPO = _RPG.parent
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))
os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}


def _route_table() -> dict[str, set[str]]:
    import app as _app

    table: dict[str, set[str]] = {}

    def walk(routes, prefix=""):
        for r in routes:
            sub = getattr(r, "router", None) or getattr(r, "original_router", None)
            if sub is not None and hasattr(sub, "routes") and type(r).__name__ != "APIRoute":
                yield from walk(sub.routes, prefix + (getattr(r, "prefix", "") or ""))
                continue
            methods, path = getattr(r, "methods", None), getattr(r, "path", None)
            if methods and path:
                yield prefix + path, methods

    for path, methods in walk(_app.app.routes):
        table.setdefault(re.sub(r"\{[^}]+\}", "{}", path), set()).update(m.upper() for m in methods)
    return table


_TABLE = _route_table()
_COMPILED = [(re.compile("^" + re.escape(p).replace(r"\{\}", r"[^/]+") + "$"), ms) for p, ms in _TABLE.items()]


def _exists(method: str, path: str) -> bool:
    path = re.sub(r"^/api/v1(/|$)", r"/api\1", path.split("?")[0])
    probe = path.replace("{}", "X")
    return any(rx.match(probe) and method in ms for rx, ms in _COMPILED)


# ── 1. api-client.js ────────────────────────────────────────────────────────
_JS_PIECE = r"(?:`[^`]*`|\"[^\"]*\"|'[^']*'|[A-Za-z_$][\w$.()]*)"


def _js_path(expr: str) -> str:
    out = ""
    for part in re.split(r"\s*\+\s*", expr.strip()):
        if part[:1] in "`\"'" and part[-1:] == part[:1]:
            out += part[1:-1]
        else:
            out += "{}"
    out = out.replace("${API_PREFIX}", "/api")
    return re.sub(r"\$\{[^}]+\}", "{}", out)


def _api_client_calls() -> list[tuple[str, str]]:
    src = (REPO / "frontend" / "src" / "api-client.js").read_text(encoding="utf-8")
    calls = []
    for m in re.finditer(rf"\b(POST|PUT|PATCH|DEL)\(\s*({_JS_PIECE}(?:\s*\+\s*{_JS_PIECE})*)", src):
        method = "DELETE" if m.group(1) == "DEL" else m.group(1)
        path = _js_path(m.group(2))
        if path.startswith("/api"):
            calls.append((method, path))
    for m in re.finditer(rf"_send\(\s*({_JS_PIECE}(?:\s*\+\s*{_JS_PIECE})*)\s*,\s*\{{\s*method:\s*\"(\w+)\"", src):
        path = _js_path(m.group(1))
        if path.startswith("/api") and m.group(2).upper() in _WRITE:
            calls.append((m.group(2).upper(), path))
    return calls


def test_api_client_write_wrappers_hit_real_routes():
    calls = _api_client_calls()
    assert len(calls) >= 150, f"只解析到 {len(calls)} 条写调用,正则可能失效了"
    missing = sorted({f"{m} {p}" for m, p in calls if not _exists(m, p)})
    assert not missing, "api-client.js 里这些写接口后端没有对应路由(死按钮):\n  " + "\n  ".join(missing)


# ── 2. iOS API.swift ────────────────────────────────────────────────────────
def _swift_path(raw: str) -> str:
    return re.sub(r"\\\([^)]*\)", "{}", raw)


def _ios_calls() -> list[tuple[str, str]]:
    src = (REPO / "ios" / "Sources" / "API.swift").read_text(encoding="utf-8")
    calls = []
    for m in re.finditer(r'request\(base,\s*"(/api/[^"]*)"\s*,\s*method:\s*"(\w+)"', src):
        if m.group(2).upper() in _WRITE:
            calls.append((m.group(2).upper(), _swift_path(m.group(1))))
    for m in re.finditer(r'postExpectOK\(base,\s*"(/api/[^"]*)"', src):
        calls.append(("POST", _swift_path(m.group(1))))
    return calls


def test_ios_write_calls_hit_real_routes():
    calls = _ios_calls()
    assert len(calls) >= 40, f"只解析到 {len(calls)} 条 iOS 写调用,正则可能失效了"
    missing = sorted({f"{m} {p}" for m, p in calls if not _exists(m, p)})
    assert not missing, "iOS API.swift 里这些写接口后端没有对应路由:\n  " + "\n  ".join(missing)


# ── 3. /api/platform/commands 的命令清单 ──────────────────────────────────────
def test_platform_command_listing_only_advertises_real_routes():
    from platform_app.api._deps import COMMANDS

    missing = sorted({f"{m} {p}" for m, p, _desc in COMMANDS if not _exists(m, p)})
    assert not missing, "COMMANDS 宣告了不存在的端点:\n  " + "\n  ".join(missing)
