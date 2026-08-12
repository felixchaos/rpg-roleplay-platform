"""platform_app.api.script_edit.writing —— 作者写作规范(.cursorrules 风)+ 审稿问题(Problems)。

writing-rules get/put、issues list/dismiss/clear。仅 owner。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from ...perms import script_owned
from .._deps import json_response, require_user
from ._shared import router


@router.get("/api/scripts/{script_id}/writing-rules")
async def api_get_writing_rules(script_id: int, user=Depends(require_user)):
    """读作者写作规范(.cursorrules 风)。仅 owner(云端隔离)。"""
    with connect() as db:
        if not script_owned(db, script_id, int(user["id"])):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        row = db.execute("select writing_rules from scripts where id=%s", (script_id,)).fetchone()
    return json_response({"ok": True, "rules": str((row.get("writing_rules") if row else "") or "")})


@router.put("/api/scripts/{script_id}/writing-rules")
async def api_put_writing_rules(request: Request, script_id: int, user=Depends(require_user)):
    """写作者写作规范。body: {rules}。仅 owner;注入编辑器 agent 上下文最高优先层。"""
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)
    rules = str(body.get("rules") or "")[:8000]
    with connect() as db:
        if not script_owned(db, script_id, int(user["id"])):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        db.execute("update scripts set writing_rules=%s, updated_at=now() where id=%s", (rules, script_id))
        db.commit()
    return json_response({"ok": True, "rules": rules})


@router.get("/api/scripts/{script_id}/issues")
async def api_list_writing_issues(script_id: int, user=Depends(require_user)):
    """读编辑器 agent 持久化的审稿问题(VSCode Problems 风)。仅 owner(云端隔离)。"""
    with connect() as db:
        if not script_owned(db, script_id, int(user["id"])):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        rows = db.execute(
            "select id, chapter, severity, issue_type, detail, created_at "
            "from script_writing_issues where script_id=%s order by "
            "case lower(coalesce(severity,'')) when '高' then 0 when 'high' then 0 "
            "when '中' then 1 when 'medium' then 1 else 2 end, chapter nulls last, id",
            (script_id,),
        ).fetchall()
    issues = [{
        "id": r.get("id"), "chapter": r.get("chapter"), "severity": r.get("severity"),
        "type": r.get("issue_type"), "detail": r.get("detail"),
    } for r in (rows or [])]
    return json_response({"ok": True, "issues": issues})


@router.delete("/api/scripts/{script_id}/issues/{issue_id}")
async def api_dismiss_writing_issue(script_id: int, issue_id: int, user=Depends(require_user)):
    """消除单条审稿问题(作者已处理/忽略)。仅 owner;按 script_id 联检防 IDOR。"""
    with connect() as db:
        if not script_owned(db, script_id, int(user["id"])):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        db.execute("delete from script_writing_issues where id=%s and script_id=%s", (issue_id, script_id))
        db.commit()
    return json_response({"ok": True})


@router.delete("/api/scripts/{script_id}/issues")
async def api_clear_writing_issues(script_id: int, user=Depends(require_user)):
    """清空该剧本全部审稿问题。仅 owner。"""
    with connect() as db:
        if not script_owned(db, script_id, int(user["id"])):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        db.execute("delete from script_writing_issues where script_id=%s", (script_id,))
        db.commit()
    return json_response({"ok": True})
