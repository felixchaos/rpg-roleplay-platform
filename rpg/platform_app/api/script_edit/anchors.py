"""platform_app.api.script_edit.anchors —— 时间线锚点写侧 CRUD(编辑/新增/删除)。

写 commit(anchor_edit/add/delete),含 _anchor_update_sets 字段对称兼容辅助。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from .._deps import json_response, require_user, value_error_response
from ._shared import _require_owner, _write_commit, router

# ─── anchors CRUD ─────────────────────────────────────────────────────────────

def _anchor_update_sets(body: dict) -> tuple[list[str], list]:
    """从 PUT body 构造 anchor 更新的 (sets, args)。

    字段名对称坑(反复报「无可更新字段」根因):摘要在列名 / GET / timeline / md-editor 里
    一律叫 sample_summary,而旧 PUT 只认 API 名 summary → md-editor round-trip 回发的
    sample_summary 被忽略,只改摘要时 sets 为空必报「无可更新字段」。这里两者都收
    (优先 summary,回退 sample_summary),保持向后兼容。
    """
    sets: list[str] = []
    args: list = []
    if "summary" in body or "sample_summary" in body:
        sets.append("sample_summary=%s")
        args.append(str(body.get("summary", body.get("sample_summary"))))
    for col in ("story_phase", "story_time_label", "sample_title"):
        if col in body:
            sets.append(f"{col}=%s")
            args.append(str(body[col]))
    for col in ("chapter_min", "chapter_max"):
        if col in body:
            sets.append(f"{col}=%s")
            args.append(int(body[col]))
    if "confidence" in body:
        sets.append("confidence=%s")
        args.append(float(body["confidence"]))
    if "keywords" in body and isinstance(body["keywords"], list):
        # keywords 列是 PostgreSQL 原生 text[](非 jsonb):psycopg 直接绑 Python list,
        # 参数化 %s 传 list 即按数组写回;绝不可 json.dumps 当 jsonb 写。
        sets.append("keywords=%s")
        args.append([str(x) for x in body["keywords"]])
    return sets, args


@router.put("/api/scripts/{script_id}/anchors/{anchor_id}")
async def api_anchor_update(
    request: Request, script_id: int, anchor_id: int, user=Depends(require_user)
):
    """编辑 script_timeline_anchor，写 commit kind=anchor_edit。

    body: {summary?, story_phase?, story_time_label?, chapter_min?, chapter_max?,
           keywords?, confidence?, sample_title?}
    （keywords 列是 PostgreSQL 原生 text[],写回直接绑 Python list,绝不 json.dumps 当 jsonb）
    （is_fatal / importance 在 save_anchor_states，不在 script_timeline_anchors）
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)

    _ANCHOR_COLS = (
        "id, story_phase, story_time_label, sample_title, sample_summary, "
        "chapter_min, chapter_max, chapter_count, keywords, confidence"
    )

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        before_row = db.execute(
            f"SELECT {_ANCHOR_COLS} FROM script_timeline_anchors WHERE id = %s AND script_id = %s",
            (anchor_id, script_id),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": "anchor 不存在"}, status_code=404)

        before = dict(before_row)
        sets, args = _anchor_update_sets(body)

        if not sets:
            return json_response(
                {"ok": False, "error": "无可更新字段（可更新: summary, story_phase, story_time_label, chapter_min, chapter_max, keywords, confidence, sample_title）"},
                status_code=400,
            )

        sets.append("updated_at=now()")
        args.extend([anchor_id, script_id])
        db.execute(
            f"UPDATE script_timeline_anchors SET {', '.join(sets)} WHERE id=%s AND script_id=%s",
            tuple(args),
        )

        after_row = db.execute(
            f"SELECT {_ANCHOR_COLS} FROM script_timeline_anchors WHERE id=%s",
            (anchor_id,),
        ).fetchone()
        after = dict(after_row)

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="anchor_edit",
            message=f"编辑 anchor: {before.get('story_time_label', anchor_id)}",
            payload={"table": "script_timeline_anchors", "op": "edit", "before": before, "after": after, "ids": {"anchor_id": anchor_id}},
        )
        db.commit()

    return json_response({"ok": True, "anchor": after, "commit_id": commit_id})


@router.post("/api/scripts/{script_id}/anchors")
async def api_anchor_add(
    request: Request, script_id: int, user=Depends(require_user)
):
    """新建 anchor，写 commit kind=anchor_add。

    body: {story_time_label, story_phase?, chapter_min, chapter_max, summary?}
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)

    story_time_label = str(body.get("story_time_label") or "").strip()
    story_phase = str(body.get("story_phase") or "").strip()
    if not story_time_label:
        return json_response({"ok": False, "error": "缺少 story_time_label"}, status_code=400)
    chapter_min = int(body.get("chapter_min") or 0)
    chapter_max = int(body.get("chapter_max") or chapter_min)

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        new_row = db.execute(
            """
            INSERT INTO script_timeline_anchors
              (script_id, story_phase, story_time_label,
               chapter_min, chapter_max, chapter_count, sample_summary)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (script_id, story_phase, story_time_label) DO NOTHING
            RETURNING id, story_phase, story_time_label, chapter_min, chapter_max, sample_summary
            """,
            (
                script_id, story_phase, story_time_label,
                chapter_min, chapter_max,
                max(0, chapter_max - chapter_min + 1),
                str(body.get("summary") or ""),
            ),
        ).fetchone()
        if not new_row:
            return json_response(
                {"ok": False, "error": "story_phase+story_time_label 组合已存在"},
                status_code=409,
            )
        after = dict(new_row)

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="anchor_add",
            message=f"新增 anchor: {story_time_label}",
            payload={"table": "script_timeline_anchors", "op": "add", "after": after, "ids": {"anchor_id": int(after["id"])}},
        )
        db.commit()

    return json_response({"ok": True, "anchor": after, "commit_id": commit_id})


@router.delete("/api/scripts/{script_id}/anchors/{anchor_id}")
async def api_anchor_delete(
    script_id: int, anchor_id: int, user=Depends(require_user)
):
    """删除 anchor（物理删除，写 commit kind=anchor_delete）。"""
    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        before_row = db.execute(
            "SELECT id, story_phase, story_time_label, sample_summary FROM script_timeline_anchors WHERE id=%s AND script_id=%s",
            (anchor_id, script_id),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": "anchor 不存在"}, status_code=404)

        before = dict(before_row)
        db.execute(
            "DELETE FROM script_timeline_anchors WHERE id=%s AND script_id=%s",
            (anchor_id, script_id),
        )

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="anchor_delete",
            message=f"删除 anchor: {before.get('story_time_label', anchor_id)}",
            payload={"table": "script_timeline_anchors", "op": "delete", "before": before, "ids": {"anchor_id": anchor_id}},
        )
        db.commit()

    return json_response({"ok": True, "deleted": True, "commit_id": commit_id})
