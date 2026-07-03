"""regex_scripts.py — 用户自定义正则脚本管理路由 (/api/regex/scripts)。反馈#93 之三。

存 user_preferences.preferences.regex_scripts = [{id,name,find,replace,flags,enabled}, ...]。
应用在 state/regex_scripts.apply_output_regex(生成热路径,输出/显示作用域)。
"""
from __future__ import annotations

import re as _re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_user

router = APIRouter()

_MAX = 50


def _uid(u: dict[str, Any] | None) -> int:
    return int(u.get("id")) if u and u.get("id") else 0


def _load(db, uid: int) -> list[dict]:
    r = db.execute("select preferences from user_preferences where user_id=%s", (uid,)).fetchone()
    prefs = dict((r or {}).get("preferences") or {})
    sc = prefs.get("regex_scripts")
    return sc if isinstance(sc, list) else []


def _save(db, uid: int, scripts: list[dict]) -> None:
    from psycopg.types.json import Jsonb
    db.execute(
        "insert into user_preferences(user_id, preferences) "
        "values (%s, jsonb_build_object('regex_scripts', %s::jsonb)) "
        "on conflict (user_id) do update set preferences = "
        "user_preferences.preferences || jsonb_build_object('regex_scripts', %s::jsonb)",
        (uid, Jsonb(scripts), Jsonb(scripts)),
    )


@router.get("/api/regex/scripts")
async def api_regex_list(api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    from platform_app.db import connect, init_db
    uid = _uid(api_user)
    if not uid:
        return JSONResponse({"ok": True, "scripts": []})
    init_db()
    with connect() as db:
        return JSONResponse({"ok": True, "scripts": _load(db, uid)})


@router.post("/api/regex/scripts")
async def api_regex_save(request: Request, api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    """新增/更新一条脚本。body {id?, name, find, replace, flags, enabled}。无 id=新增。服务端校验正则可编译。"""
    from platform_app.db import connect, init_db
    uid = _uid(api_user)
    if not uid:
        return JSONResponse({"ok": False, "error": "需登录"}, status_code=401)
    body = await request.json()
    find = str((body or {}).get("find") or "").strip()
    if not find:
        return JSONResponse({"ok": False, "error": "匹配正则不能为空"}, status_code=400)
    if len(find) > 2000:
        return JSONResponse({"ok": False, "error": "正则过长"}, status_code=400)
    try:
        _re.compile(find)
    except _re.error as e:
        return JSONResponse({"ok": False, "error": f"正则无效: {e}"}, status_code=400)
    from state.regex_scripts import is_risky_pattern
    if is_risky_pattern(find):
        return JSONResponse(
            {"ok": False, "error": "该正则含嵌套无界量词（如 (a+)+），可能导致灾难回溯，已拒绝。请简化。"},
            status_code=400,
        )
    item = {
        "name": str((body or {}).get("name") or "")[:80],
        "find": find,
        "replace": str((body or {}).get("replace") or "")[:4000],
        "flags": "".join(c for c in str((body or {}).get("flags") or "").lower() if c in "ims"),
        "enabled": bool((body or {}).get("enabled", True)),
    }
    init_db()
    with connect() as db:
        scripts = _load(db, uid)
        rid = (body or {}).get("id")
        try:
            rid = int(rid) if rid is not None else None
        except (TypeError, ValueError):
            rid = None
        if rid is not None and any(isinstance(s, dict) and s.get("id") == rid for s in scripts):
            for s in scripts:
                if isinstance(s, dict) and s.get("id") == rid:
                    s.update(item)
                    s["id"] = rid
                    break
        else:
            nid = max([int(s.get("id") or 0) for s in scripts if isinstance(s, dict)] + [0]) + 1
            item["id"] = nid
            scripts.append(item)
        if len(scripts) > _MAX:
            scripts = scripts[-_MAX:]
        _save(db, uid, scripts)
        return JSONResponse({"ok": True, "scripts": scripts})


@router.post("/api/regex/scripts/remove")
async def api_regex_remove(request: Request, api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    from platform_app.db import connect, init_db
    uid = _uid(api_user)
    if not uid:
        return JSONResponse({"ok": False, "error": "需登录"}, status_code=401)
    body = await request.json()
    try:
        rid = int((body or {}).get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "id 无效"}, status_code=400)
    init_db()
    with connect() as db:
        scripts = [s for s in _load(db, uid) if not (isinstance(s, dict) and s.get("id") == rid)]
        _save(db, uid, scripts)
        return JSONResponse({"ok": True, "scripts": scripts})
