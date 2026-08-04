"""platform_app.api.script_edit.versioning —— commit 历史 + 章节/世界书/角色卡撤销与恢复 + checkout。

commits log、章节 undoable/undo/history/restore、通用 undo-edit(worldbook/character_cards)、
checkout(stub)。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request
from psycopg.types.json import Jsonb

from ...db import connect
from ...perms import script_owned
from .._deps import json_response, require_user, value_error_response
from ._shared import _require_owner, _write_commit, router

# ─── commits log ─────────────────────────────────────────────────────────────

@router.get("/api/scripts/{script_id}/commits")
async def api_list_commits(
    script_id: int,
    limit: int = 30,
    user=Depends(require_user),
):
    """列出 script 的 commit 历史（最新优先）。"""
    limit = max(1, min(int(limit), 200))
    with connect() as db:
        owned = db.execute(
            "SELECT 1 FROM scripts WHERE id = %s AND owner_id = %s",
            (script_id, user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)

        rows = db.execute(
            """
            SELECT c.id, c.parent_commit_id, c.kind, c.message,
                   c.is_checkpoint, c.created_at,
                   u.username AS author_username, u.display_name AS author_display_name
            FROM script_commits c
            LEFT JOIN users u ON u.id = c.author_user_id
            WHERE c.script_id = %s
            ORDER BY c.id DESC
            LIMIT %s
            """,
            (script_id, limit),
        ).fetchall()

    return json_response({
        "ok": True,
        "commits": [dict(r) for r in rows],
        "count": len(rows),
    })


@router.get("/api/scripts/{script_id}/chapters/{chapter_index}/undoable")
async def api_chapter_undoable(script_id: int, chapter_index: int, user=Depends(require_user)):
    """本章是否有可撤销的 AI 改动(给前端决定是否显示「撤销」)。"""
    with connect() as db:
        if not script_owned(db, script_id, int(user["id"])):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        row = db.execute(
            """SELECT id, message FROM script_commits
               WHERE script_id=%s AND kind='chapter_edit'
                 AND coalesce((payload->>'undoable')::boolean, false) IS TRUE
                 AND coalesce((payload->>'undone')::boolean, false) IS FALSE
                 AND coalesce(payload->'ids'->>'chapter_index','') = %s
               ORDER BY id DESC LIMIT 1""",
            (script_id, str(chapter_index)),
        ).fetchone()
    return json_response({"ok": True, "undoable": bool(row),
                          "commit_id": int(row["id"]) if row else None})


@router.post("/api/scripts/{script_id}/chapters/{chapter_index}/undo")
async def api_undo_chapter_edit(script_id: int, chapter_index: int, user=Depends(require_user)):
    """撤销本章最近一次可撤销的改动:把正文恢复到那次改动之前(commit.payload.before)。

    确定性、作者主动触发(非 agent 工具,不指望 LLM)。恢复后标记该 commit 已撤销 + 写一条
    chapter_revert,故可连续往前逐次撤销。手动编辑走 CodeMirror 自带撤销,这里专治「AI 改了库
    才发现不对」—— 与落库前的「改动预览」一前一后构成写作搭档的安全网。"""
    uid = int(user["id"])
    with connect() as db:
        if not script_owned(db, script_id, uid):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        row = db.execute(
            """SELECT id, payload FROM script_commits
               WHERE script_id=%s AND kind='chapter_edit'
                 AND coalesce((payload->>'undoable')::boolean, false) IS TRUE
                 AND coalesce((payload->>'undone')::boolean, false) IS FALSE
                 AND coalesce(payload->'ids'->>'chapter_index','') = %s
               ORDER BY id DESC LIMIT 1""",
            (script_id, str(chapter_index)),
        ).fetchone()
        if not row:
            return json_response({"ok": False, "error": "本章没有可撤销的 AI 改动"}, status_code=404)
        before = ((row["payload"] or {}).get("before") or {})
        commit_id = int(row["id"])
    # 恢复改前全文(走现成 update_chapter,自带 owner 校验 + word_count 同步)
    from platform_app.script_import import update_chapter
    bc = before.get("content")
    update_chapter(
        uid, script_id, int(chapter_index),
        title=(str(before["title"]) if before.get("title") is not None else None),
        content=(str(bc) if bc is not None else None),
        volume_title=(str(before["volume_title"]) if before.get("volume_title") is not None else None),
    )
    with connect() as db:
        if not script_owned(db, script_id, uid):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        db.execute(
            "UPDATE script_commits SET payload = jsonb_set(payload, '{undone}', 'true') WHERE id=%s",
            (commit_id,),
        )
        _write_commit(
            db, script_id=script_id, user_id=uid, kind="chapter_revert",
            message=f"撤销章节 #{chapter_index} 的改动",
            payload={"table": "script_chapters", "op": "revert",
                     "ids": {"chapter_index": int(chapter_index)},
                     "reverted_commit_id": commit_id},
        )
        db.commit()
    return json_response({"ok": True, "chapter_index": int(chapter_index),
                          "reverted_commit_id": commit_id})


# 通用撤销:把世界书条目 / NPC 角色卡 恢复到最近一次 AI 改动之前(与章节撤销同款安全网,确定性、
# 作者主动触发)。依赖各写工具落 commit 时存了 payload.before + undoable。
_UNDO_SPEC = {
    "worldbook_entries": ("worldbook_edit", "entry_id"),
    "character_cards": ("card_edit", "card_id"),
}


@router.post("/api/scripts/{script_id}/undo-edit")
async def api_undo_edit(request: Request, script_id: int, user=Depends(require_user)):
    """撤销某世界书条目 / 角色卡最近一次可撤销的 AI 改动。body: {table, entity_id}。"""
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)
    table = str(body.get("table") or "")
    if table not in _UNDO_SPEC:
        return json_response({"ok": False, "error": "不支持的 table"}, status_code=400)
    try:
        entity_id = int(body.get("entity_id"))
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "entity_id 必填且为整数"}, status_code=400)
    kind, id_key = _UNDO_SPEC[table]
    uid = int(user["id"])
    with connect() as db:
        if not script_owned(db, script_id, uid):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        row = db.execute(
            """SELECT id, payload FROM script_commits
               WHERE script_id=%s AND kind=%s
                 AND coalesce((payload->>'undoable')::boolean, false) IS TRUE
                 AND coalesce((payload->>'undone')::boolean, false) IS FALSE
                 AND coalesce(payload->'ids'->>%s,'') = %s
               ORDER BY id DESC LIMIT 1""",
            (script_id, kind, id_key, str(entity_id)),
        ).fetchone()
        if not row:
            return json_response({"ok": False, "error": "没有可撤销的 AI 改动"}, status_code=404)
        before = ((row["payload"] or {}).get("before") or {})
        commit_id = int(row["id"])
        if not before:
            return json_response({"ok": False, "error": "该改动未存改前快照,无法撤销"}, status_code=409)

        if table == "worldbook_entries":
            sets, params = [], []
            for c in ("title", "content", "priority", "token_budget", "sticky_turns",
                      "cooldown_turns", "probability", "enabled", "insertion_position"):
                if c in before:
                    sets.append(f"{c}=%s"); params.append(before[c])
            for c in ("keys", "regex_keys", "character_filter", "scene_filter"):
                if c in before:
                    sets.append(f"{c}=%s"); params.append(Jsonb(before[c] or []))
            if sets:
                sets.append("updated_at=now()")
                params.extend([entity_id, script_id])
                db.execute(f"update worldbook_entries set {', '.join(sets)} "
                           f"where id=%s and script_id=%s", tuple(params))
        elif table == "character_cards":
            from platform_app.knowledge.character_cards import upsert_character_card
            upsert_character_card(uid, script_id, {**before, "id": entity_id})

        db.execute("UPDATE script_commits SET payload = jsonb_set(payload, '{undone}', 'true') WHERE id=%s",
                   (commit_id,))
        _write_commit(db, script_id=script_id, user_id=uid, kind=f"{kind}_revert",
                      message=f"撤销 {table} #{entity_id} 的改动",
                      payload={"table": table, "op": "revert", "ids": {id_key: entity_id},
                               "reverted_commit_id": commit_id})
        db.commit()
    return json_response({"ok": True, "table": table, "entity_id": entity_id,
                          "reverted_commit_id": commit_id})


# ─── checkout（stub）─────────────────────────────────────────────────────────

@router.post("/api/scripts/{script_id}/checkout/{commit_id}")
async def api_checkout_commit(
    script_id: int, commit_id: int, user=Depends(require_user)
):
    """回滚到指定 commit（TODO：回放 payload chain 还原历史状态）。

    当前实现为 stub，仅校验权限 + 返回 501。
    """
    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        # 校验 commit 存在且属于该 script
        c = db.execute(
            "SELECT id, kind, created_at FROM script_commits WHERE id=%s AND script_id=%s",
            (commit_id, script_id),
        ).fetchone()
        if not c:
            return json_response({"ok": False, "error": "commit 不存在"}, status_code=404)

    return json_response(
        {
            "ok": False,
            "error": "checkout 尚未实现（TODO：回放 payload chain 还原历史状态）",
            "commit": dict(c),
        },
        status_code=501,
    )


@router.get("/api/scripts/{script_id}/chapters/{chapter_index}/history")
async def api_chapter_history(script_id: int, chapter_index: int, user=Depends(require_user)):
    """某章的 AI 改动历史(版本列表):每条 = commit_id + 时间 + 摘要 + 是否含改前快照(可恢复)。
    仅 owner(云端多用户隔离)。配合 restore 实现「版本浏览 + 回滚」。"""
    uid = int(user["id"])
    with connect() as db:
        if not script_owned(db, script_id, uid):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        rows = db.execute(
            """SELECT id, kind, message, created_at,
                      coalesce((payload->>'undone')::boolean, false) AS undone,
                      (payload->'before' IS NOT NULL) AS has_before
               FROM script_commits
               WHERE script_id=%s AND kind IN ('chapter_edit','chapter_revert','chapter_add')
                 AND coalesce(payload->'ids'->>'chapter_index','') = %s
               ORDER BY id DESC LIMIT 100""",
            (script_id, str(chapter_index)),
        ).fetchall()
    return json_response({"ok": True, "chapter_index": int(chapter_index),
                          "versions": [dict(r) for r in rows]})


@router.post("/api/scripts/{script_id}/chapters/{chapter_index}/restore")
async def api_chapter_restore(request: Request, script_id: int, chapter_index: int, user=Depends(require_user)):
    """把某章恢复到指定 commit 的【改前快照】(版本回滚)。body: {commit_id}。仅 owner。
    与撤销同款安全网,但可回到历史任意一次改动之前,不止最近一次。"""
    uid = int(user["id"])
    try:
        body = await request.json()
        commit_id = int(body.get("commit_id"))
    except Exception:
        return json_response({"ok": False, "error": "commit_id 必填且为整数"}, status_code=400)
    with connect() as db:
        if not script_owned(db, script_id, uid):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        row = db.execute(
            "SELECT payload FROM script_commits WHERE id=%s AND script_id=%s AND kind='chapter_edit'",
            (commit_id, script_id),
        ).fetchone()
        if not row:
            return json_response({"ok": False, "error": "找不到该版本"}, status_code=404)
        before = ((row["payload"] or {}).get("before") or {})
        if not before:
            return json_response({"ok": False, "error": "该版本未存改前快照,无法恢复"}, status_code=409)
    from platform_app.script_import import update_chapter
    bc = before.get("content")
    update_chapter(
        uid, script_id, int(chapter_index),
        title=(str(before["title"]) if before.get("title") is not None else None),
        content=(str(bc) if bc is not None else None),
        volume_title=(str(before["volume_title"]) if before.get("volume_title") is not None else None),
    )
    with connect() as db:
        if not script_owned(db, script_id, uid):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        _write_commit(db, script_id=script_id, user_id=uid, kind="chapter_revert",
                      message=f"恢复章节 #{chapter_index} 到版本 #{commit_id} 之前",
                      payload={"table": "script_chapters", "op": "revert",
                               "ids": {"chapter_index": int(chapter_index)},
                               "reverted_commit_id": commit_id})
        db.commit()
    return json_response({"ok": True, "chapter_index": int(chapter_index), "restored_from": commit_id})


