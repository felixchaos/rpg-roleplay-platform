"""platform_app.api.script_edit.canon —— canon 实体写侧 CRUD(编辑/新增/软删除)。

写 commit(canon_edit/add/delete)。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request
from psycopg.types.json import Jsonb

from ...db import connect
from .._deps import json_response, require_user, value_error_response
from ._shared import _require_owner, _write_commit, router

# ─── canon-entities CRUD ─────────────────────────────────────────────────────

@router.put("/api/scripts/{script_id}/canon-entities/{logical_key}")
async def api_canon_update(
    request: Request, script_id: int, logical_key: str, user=Depends(require_user)
):
    """编辑 canon entity，写 commit kind=canon_edit。

    body: {summary?, identity?, background?, parent_logical_key?, entity_subtype?,
           importance?, aliases?, attrs?, first_revealed_chapter?, public_knowledge?}
    （aliases 为 jsonb 字符串数组,attrs 为 jsonb 开放对象）
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)

    _CANON_COLS = (
        "id, logical_key, name, full_name, type, entity_subtype, parent_logical_key, "
        "summary, identity, background, aliases, attrs, "
        "first_revealed_chapter, public_knowledge, importance, created_at"
    )

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        before_row = db.execute(
            f"SELECT {_CANON_COLS} FROM kb_canon_entities WHERE script_id = %s AND logical_key = %s",
            (script_id, logical_key),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": "canon entity 不存在"}, status_code=404)

        before = dict(before_row)
        sets, args = [], []
        for col in ("summary", "identity", "background", "parent_logical_key", "entity_subtype"):
            if col in body:
                sets.append(f"{col}=%s")
                args.append(str(body[col]))
        if "importance" in body:
            sets.append("importance=%s")
            args.append(int(body["importance"]))
        if "first_revealed_chapter" in body:
            sets.append("first_revealed_chapter=%s")
            args.append(int(body["first_revealed_chapter"]))
        if "public_knowledge" in body:
            sets.append("public_knowledge=%s")
            args.append(bool(body["public_knowledge"]))
        if "aliases" in body and isinstance(body["aliases"], list):
            # aliases 为 jsonb 字符串数组
            sets.append("aliases=%s")
            args.append(Jsonb([str(x) for x in body["aliases"]]))
        if "attrs" in body and isinstance(body["attrs"], dict):
            # attrs 为 jsonb 开放对象,原样写回
            sets.append("attrs=%s")
            args.append(Jsonb(body["attrs"]))

        if not sets:
            return json_response({"ok": False, "error": "无可更新字段"}, status_code=400)

        args.extend([script_id, logical_key])
        db.execute(
            f"UPDATE kb_canon_entities SET {', '.join(sets)} WHERE script_id=%s AND logical_key=%s",
            tuple(args),
        )

        after_row = db.execute(
            f"SELECT {_CANON_COLS} FROM kb_canon_entities WHERE script_id = %s AND logical_key = %s",
            (script_id, logical_key),
        ).fetchone()
        after = dict(after_row)

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="canon_edit",
            message=f"编辑 canon entity: {logical_key}",
            payload={"table": "kb_canon_entities", "op": "edit", "before": before, "after": after, "ids": {"logical_key": logical_key}},
        )
        db.commit()

    return json_response({"ok": True, "entity": after, "commit_id": commit_id})


@router.post("/api/scripts/{script_id}/canon-entities")
async def api_canon_add(
    request: Request, script_id: int, user=Depends(require_user)
):
    """新增 canon entity，写 commit kind=canon_add。

    body: {logical_key, name, type, summary?, identity?, background?, entity_subtype?,
           parent_logical_key?, importance?, full_name?, aliases?, attrs?,
           first_revealed_chapter?, public_knowledge?}
    （aliases 为 jsonb 字符串数组,attrs 为 jsonb 开放对象）
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)

    logical_key = str(body.get("logical_key") or "").strip()
    name = str(body.get("name") or "").strip()
    entity_type = str(body.get("type") or "").strip()
    if not logical_key or not name or not entity_type:
        return json_response(
            {"ok": False, "error": "缺少必填字段 logical_key / name / type"},
            status_code=400,
        )

    aliases = body.get("aliases")
    aliases_jsonb = Jsonb([str(x) for x in aliases]) if isinstance(aliases, list) else Jsonb([])
    attrs = body.get("attrs")
    attrs_jsonb = Jsonb(attrs) if isinstance(attrs, dict) else Jsonb({})

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        new_row = db.execute(
            """
            INSERT INTO kb_canon_entities
              (script_id, logical_key, name, full_name, type, summary, identity, background,
               entity_subtype, parent_logical_key, importance,
               aliases, attrs, first_revealed_chapter, public_knowledge)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (script_id, logical_key) DO NOTHING
            RETURNING id, logical_key, name, full_name, type, summary, identity, background,
                      entity_subtype, parent_logical_key, importance,
                      aliases, attrs, first_revealed_chapter, public_knowledge, created_at
            """,
            (
                script_id, logical_key, name,
                str(body.get("full_name") or ""),
                entity_type,
                str(body.get("summary") or ""),
                str(body.get("identity") or ""),
                str(body.get("background") or ""),
                str(body.get("entity_subtype") or ""),
                str(body.get("parent_logical_key") or ""),
                int(body.get("importance") or 0),
                aliases_jsonb,
                attrs_jsonb,
                int(body.get("first_revealed_chapter") or 0),
                bool(body.get("public_knowledge", False)),
            ),
        ).fetchone()
        if not new_row:
            return json_response(
                {"ok": False, "error": f"logical_key '{logical_key}' 已存在"},
                status_code=409,
            )
        after = dict(new_row)

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="canon_add",
            message=f"新增 canon entity: {logical_key}",
            payload={"table": "kb_canon_entities", "op": "add", "after": after, "ids": {"logical_key": logical_key}},
        )
        db.commit()

    return json_response({"ok": True, "entity": after, "commit_id": commit_id})


@router.delete("/api/scripts/{script_id}/canon-entities/{logical_key}")
async def api_canon_delete(
    script_id: int, logical_key: str, user=Depends(require_user)
):
    """软删除 canon entity（importance=-1 标记删除），写 commit kind=canon_delete。"""
    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        before_row = db.execute(
            "SELECT id, logical_key, name, summary, importance FROM kb_canon_entities WHERE script_id = %s AND logical_key = %s",
            (script_id, logical_key),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": "canon entity 不存在"}, status_code=404)

        before = dict(before_row)
        # 用 importance=-1 做软删除标记（保留行供 checkout 回放）
        db.execute(
            "UPDATE kb_canon_entities SET importance=-1 WHERE script_id=%s AND logical_key=%s",
            (script_id, logical_key),
        )

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="canon_delete",
            message=f"删除 canon entity: {logical_key}",
            payload={"table": "kb_canon_entities", "op": "delete", "before": before, "ids": {"logical_key": logical_key}},
        )
        db.commit()

    return json_response({"ok": True, "deleted": True, "commit_id": commit_id})
