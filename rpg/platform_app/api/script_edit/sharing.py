"""platform_app.api.script_edit.sharing —— 剧本引用(pin / unpin)。

设/解 pinned-snapshot | floating-latest 引用模式。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from .._deps import json_response, require_user
from ._shared import _require_owner, router

_VALID_SHARING_MODES = {"private", "public", "pinned-snapshot", "floating-latest"}


# ─── pin / unpin ──────────────────────────────────────────────────────────────

@router.post("/api/scripts/{script_id}/pin")
async def api_pin_script(request: Request, script_id: int, user=Depends(require_user)):
    """设当前 script 为引用(pin)模式。

    body: {target_script_id, mode: 'pinned-snapshot'|'floating-latest', commit_id?}
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)

    mode = str(body.get("mode") or "")
    if mode not in ("pinned-snapshot", "floating-latest"):
        return json_response(
            {"ok": False, "error": "mode 必须是 'pinned-snapshot' 或 'floating-latest'"},
            status_code=400,
        )
    target_script_id = body.get("target_script_id")
    if not target_script_id:
        return json_response({"ok": False, "error": "缺少 target_script_id"}, status_code=400)
    target_script_id = int(target_script_id)

    commit_id = body.get("commit_id")
    if mode == "pinned-snapshot" and not commit_id:
        return json_response(
            {"ok": False, "error": "pinned-snapshot 模式需要 commit_id"},
            status_code=400,
        )
    commit_id = int(commit_id) if commit_id else None

    with connect() as db:
        _require_owner(db, script_id, user["id"])

        # 用户隔离:target 必须【对当前用户可访问】(自己拥有 / 公开 / 已订阅),否则
        # 用户可把自己的剧本 pin 到别人的【私有剧本】,而 KB 读取的 pin 重定向会泄露
        # 该私有剧本的世界书/人物/时间线。与订阅的访问模型一致。
        target = db.execute(
            """
            SELECT 1 FROM scripts
            WHERE id = %s AND (
                owner_id = %s
                OR is_public
                OR id IN (SELECT script_id FROM user_script_subscriptions WHERE user_id = %s)
            )
            """,
            (target_script_id, user["id"], user["id"]),
        ).fetchone()
        if not target:
            return json_response({"ok": False, "error": "目标剧本不存在或无权引用"}, status_code=403)

        # 若 pinned-snapshot，校验 commit 归属于 target_script_id
        if commit_id:
            c = db.execute(
                "SELECT 1 FROM script_commits WHERE id = %s AND script_id = %s",
                (commit_id, target_script_id),
            ).fetchone()
            if not c:
                return json_response(
                    {"ok": False, "error": "commit_id 不属于目标剧本"},
                    status_code=400,
                )

        db.execute(
            """
            UPDATE scripts SET
              sharing_mode = %s,
              current_pin_script_id = %s,
              current_pin_commit_id = %s,
              updated_at = now()
            WHERE id = %s
            """,
            (mode, target_script_id, commit_id, script_id),
        )
        db.commit()

    return json_response({"ok": True, "sharing_mode": mode,
                          "current_pin_script_id": target_script_id,
                          "current_pin_commit_id": commit_id})


@router.post("/api/scripts/{script_id}/unpin")
async def api_unpin_script(script_id: int, user=Depends(require_user)):
    """解除 pin 引用，恢复为独立 private script。"""
    with connect() as db:
        _require_owner(db, script_id, user["id"])
        db.execute(
            """
            UPDATE scripts SET
              sharing_mode = 'private',
              current_pin_script_id = NULL,
              current_pin_commit_id = NULL,
              updated_at = now()
            WHERE id = %s
            """,
            (script_id,),
        )
        db.commit()
    return json_response({"ok": True, "sharing_mode": "private"})
