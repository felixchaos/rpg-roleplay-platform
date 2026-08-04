"""platform_app.api.script_edit.agent_doc —— 编辑器写作搭档:拖入 txt/md 文档暂存。

原文不进 LLM 上下文,返回 doc_id 供 agent 编排。仅 owner。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from .._deps import json_response, require_user, value_error_response
from ._shared import _require_owner, router


@router.post("/api/scripts/{script_id}/agent-doc")
async def api_agent_doc_upload(request: Request, script_id: int, user=Depends(require_user)):
    """编辑器写作搭档:拖入 txt/md 文档暂存(原文不进 LLM 上下文)。返回 doc_id,供 agent 调
    split_document_into_chapters / read_uploaded_document 编排。仅 owner。

    body: {filename, content_b64}(base64,优先)或 {filename, content_text}。
    """
    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)
    filename = str(body.get("filename") or "").strip()
    if filename and not (filename.lower().endswith(".txt") or filename.lower().endswith(".md")):
        return json_response({"ok": False, "error": "只支持 .txt / .md 文档"}, status_code=400)
    try:
        from platform_app.agent_docs import store_doc
        res = store_doc(
            user["id"], script_id, filename,
            content_b64=str(body.get("content_b64") or ""),
            content_text=str(body.get("content_text") or ""),
        )
        return json_response({"ok": True, **res})
    except ValueError as exc:
        return value_error_response(exc)
