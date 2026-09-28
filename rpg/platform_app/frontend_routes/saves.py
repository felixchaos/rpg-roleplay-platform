"""platform_app.frontend_routes.saves —— SAVES 补充路由(/api/saves/{save_id}/*)。

原单文件「SAVES supplements」段逐端点搬运,零行为变化:
删除 / 改名 / 激活(切 runtime + 清 ui 缓存)。
"""
from __future__ import annotations

from fastapi import Request

from ..api import json_response, require_user
from ..db import connect, init_db
from ..perms import owns_save
from ._shared import _bad, router


# ------------------------------------------------------------
#  SAVES supplements
# ------------------------------------------------------------
@router.post("/api/saves/{save_id}/delete")
async def api_save_delete(save_id: int, request: Request):
    user = require_user(request)
    init_db()
    with connect() as db:
        if not owns_save(db, save_id, user["id"]):
            return _bad("无权操作该存档", 403)
        db.execute("delete from game_saves where id = %s and user_id = %s", (save_id, user["id"]))
    return json_response({"ok": True})


@router.post("/api/saves/{save_id}/rename")
async def api_save_rename(save_id: int, request: Request):
    user = require_user(request)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        return _bad("标题不能为空")
    init_db()
    with connect() as db:
        if not owns_save(db, save_id, user["id"]):
            return _bad("无权操作该存档", 403)
        db.execute(
            "update game_saves set title = %s, updated_at = now() where id = %s",
            (title, save_id),
        )
    return json_response({"ok": True, "title": title})


@router.post("/api/saves/{save_id}/activate")
async def api_save_activate(save_id: int, request: Request):
    """task 30：之前只 select 1 验证归属就返 ok=True，既不写 user_runtime，
    也不清 ui._state_by_user 缓存 → 用户点完「继续」跳到 Game Console，
    GET /api/state 拿到的是上一个 active save 的 player/world。

    现在：调用 branches.activate_save → runtime.activate_state_snapshot
    把 user_runtime 切到目标 save 的 active commit；并清掉 ui 进程内 state 缓存，
    下一次 _ensure_loaded 会从 user_runtime 重新加载新 save 的 state。"""
    user = require_user(request)
    from .. import branches as _branches
    try:
        result = _branches.activate_save(user["id"], save_id)
    except ValueError as exc:
        return _bad(str(exc), 403)
    # 清 ui 模块内的 per-user state 缓存（跨模块耦合：ui 在主进程内长期持有 cache，
    # activate 必须告诉它丢掉旧 save 的 state，否则 GET /api/state 仍读旧档）。
    try:
        import app as _ui
        _ui._invalidate_user_cache(user)
    except Exception:
        pass
    return json_response(result)


# 注:GET /api/saves/{save_id}/export 由 platform_app/api/saves.py 提供(先挂载);这里原有的
# 同名实现被它遮蔽、永远到不了,已删除(守卫 tests/unit/test_no_shadowed_routes.py)。
