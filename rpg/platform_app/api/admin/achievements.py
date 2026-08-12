"""platform_app.api.admin.achievements —— 成就目录管理端点(见 docs/design/I_achievements.md)。纯机械搬家,行为零变化。

_re / _ACHV_SLUG / _ACHV_TIERS / _achv_validate_payload 仅本资源族使用,故与端点同居。
"""
from __future__ import annotations

import re as _re

from fastapi import Depends, Request
from psycopg.types.json import Jsonb

from ...db import connect
from .._deps import json_response
from ._shared import _require_admin, router

_ACHV_SLUG = _re.compile(r"^[a-z0-9][a-z0-9_]{1,48}[a-z0-9]$")
_ACHV_TIERS = {"bronze", "silver", "gold", None, ""}


def _achv_validate_payload(body: dict, *, require_id: bool) -> tuple[dict, str | None]:
    """校验 def 写入体。返回 (clean, error)。rule 走 engine.validate_rule(唯一安全闸)。"""
    from platform_app.achievements import validate_rule
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()
    category = (body.get("category") or "").strip()
    tier = (body.get("tier") or None)
    icon = (body.get("icon") or None)
    rule = body.get("rule")
    if not name:
        return {}, "name 必填"
    if not description:
        return {}, "description 必填"
    if not category:
        return {}, "category 必填"
    if tier not in _ACHV_TIERS:
        return {}, "tier 必须是 bronze/silver/gold 或留空"
    try:
        validate_rule(rule)
    except ValueError as exc:
        return {}, f"规则非法:{exc}"
    clean = {
        "name": name,
        "description": description,
        "category": category,
        "tier": (tier or None),
        "icon": (icon or None),
        "rule": rule,
        "hidden": bool(body.get("hidden", False)),
        "sort_order": int(body.get("sort_order", 0) or 0),
        "enabled": bool(body.get("enabled", True)),
    }
    if require_id:
        aid = (body.get("id") or "").strip()
        if not _ACHV_SLUG.match(aid):
            return {}, "id 必须是小写字母/数字/下划线的 slug(3-50 字符)"
        clean["id"] = aid
    return clean, None


@router.get("/api/admin/achievements")
async def admin_achv_list(admin=Depends(_require_admin)):
    """列全部定义(含 disabled),供 admin 管理表格。"""
    with connect() as db:
        rows = db.execute(
            "select * from achievement_defs order by category, sort_order, id"
        ).fetchall()
    return json_response({"ok": True, "items": [dict(r) for r in rows]})


@router.post("/api/admin/achievements")
async def admin_achv_create(request: Request, admin=Depends(_require_admin)):
    body = await request.json()
    clean, err = _achv_validate_payload(body, require_id=True)
    if err:
        return json_response({"ok": False, "error": err}, status_code=400)
    with connect() as db:
        exists = db.execute(
            "select 1 from achievement_defs where id = %s", (clean["id"],)
        ).fetchone()
        if exists:
            return json_response({"ok": False, "error": "id 已存在"}, status_code=409)
        row = db.execute(
            "insert into achievement_defs "
            "(id, name, description, icon, category, tier, rule, hidden, sort_order, enabled) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
            (
                clean["id"], clean["name"], clean["description"], clean["icon"],
                clean["category"], clean["tier"], Jsonb(clean["rule"]),
                clean["hidden"], clean["sort_order"], clean["enabled"],
            ),
        ).fetchone()
    return json_response({"ok": True, "item": dict(row)})


@router.put("/api/admin/achievements/{achievement_id}")
async def admin_achv_update(achievement_id: str, request: Request, admin=Depends(_require_admin)):
    """改定义。id 不可改;rule 改后已解锁用户不回收(只增不减)。"""
    body = await request.json()
    clean, err = _achv_validate_payload(body, require_id=False)
    if err:
        return json_response({"ok": False, "error": err}, status_code=400)
    with connect() as db:
        exists = db.execute(
            "select 1 from achievement_defs where id = %s", (achievement_id,)
        ).fetchone()
        if not exists:
            return json_response({"ok": False, "error": "未找到"}, status_code=404)
        row = db.execute(
            "update achievement_defs set "
            "name=%s, description=%s, icon=%s, category=%s, tier=%s, rule=%s, "
            "hidden=%s, sort_order=%s, enabled=%s, updated_at=now() "
            "where id=%s returning *",
            (
                clean["name"], clean["description"], clean["icon"], clean["category"],
                clean["tier"], Jsonb(clean["rule"]), clean["hidden"],
                clean["sort_order"], clean["enabled"], achievement_id,
            ),
        ).fetchone()
    return json_response({"ok": True, "item": dict(row)})


@router.delete("/api/admin/achievements/{achievement_id}")
async def admin_achv_delete(achievement_id: str, admin=Depends(_require_admin)):
    """软删:置 enabled=false,保留 user_achievements 引用(不剥夺已解锁)。"""
    with connect() as db:
        row = db.execute(
            "update achievement_defs set enabled=false, updated_at=now() "
            "where id=%s returning id",
            (achievement_id,),
        ).fetchone()
        if not row:
            return json_response({"ok": False, "error": "未找到"}, status_code=404)
    return json_response({"ok": True, "disabled": achievement_id})
