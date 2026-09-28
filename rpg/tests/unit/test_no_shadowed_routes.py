"""test_no_shadowed_routes.py — 同一 (method, path) 只能有一个处理函数。

FastAPI 按挂载顺序匹配:app.py 先 include platform_router,后 include frontend_routes。
两边都注册了同一路径时,后挂的那个永远到不了 —— 代码还在、看起来「有实现」,改它却不生效,
读代码的人也会被误导(巡检查到 frontend_routes 里 /api/me/preference、
/api/me/character-cards/import-json、/api/saves/{id}/export 三个就是这样的死代码,其中
/api/me/preference 的死实现写的是另一张表 profile_extras.preferences)。
"""
from __future__ import annotations

from fastapi.routing import APIRoute


def _walk(routes, prefix=""):
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
            continue
        # 新版 FastAPI 的 include_router 包成 _IncludedRouter(惰性展开)
        sub = getattr(r, "router", None) or getattr(r, "original_router", None)
        if sub is not None and hasattr(sub, "routes") and type(r).__name__ != "Mount":
            yield from _walk(sub.routes, prefix + (getattr(r, "prefix", "") or ""))


def test_no_duplicate_method_path():
    import app as _app
    seen: dict[tuple[str, str], str] = {}
    dups: list[str] = []
    n = 0
    for path, route in _walk(_app.app.router.routes):
        n += 1
        who = f"{route.endpoint.__module__}.{route.endpoint.__name__}"
        for method in route.methods or ():
            if method == "HEAD":
                continue
            key = (method, path)
            if key in seen and seen[key] != who:
                dups.append(f"{method} {path}: 生效={seen[key]} 被遮蔽={who}")
            else:
                seen.setdefault(key, who)
    assert n > 100, f"只遍历到 {n} 个路由,遍历方式失效了(守卫会假绿)"
    assert not dups, "被同名路由遮蔽的死代码:\n" + "\n".join(dups)
