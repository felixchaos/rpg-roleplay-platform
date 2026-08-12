"""platform_app.api.script_edit.search —— 全书检索(用户面板 Cmd/Ctrl+Shift+F)。

在所有章节正文搜词/短语/正则,返回结构化命中。仅 owner。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends

from ...db import connect, limit_value
from ...perms import script_owned
from .._deps import json_response, require_user
from ._shared import router


@router.get("/api/scripts/{script_id}/search")
async def api_script_search(script_id: int, q: str = "", regex: bool = False,
                            chapter_min: int | None = None, chapter_max: int | None = None,
                            limit: int = 80, user=Depends(require_user)):
    """全书检索(用户面板 Cmd/Ctrl+Shift+F):在所有章节正文搜词/短语/正则,返回结构化命中
    (章号 + 标题 + 偏移 + 上下文片段)。**仅 owner**(云端多用户隔离)。"""
    uid = int(user["id"])
    query = (q or "").strip()
    if not query:
        return json_response({"ok": True, "results": [], "total": 0})
    import re as _re
    try:
        pat = _re.compile(query if regex else _re.escape(query), _re.I)
    except _re.error as exc:
        return json_response({"ok": False, "error": f"正则无效: {exc}"}, status_code=400)
    lim = limit_value(limit, default=80, maximum=300)
    CAP = 3000
    where = "script_id=%s"
    params: list[Any] = [script_id]
    if chapter_min is not None:
        where += " and chapter_index>=%s"; params.append(int(chapter_min))
    if chapter_max is not None:
        where += " and chapter_index<=%s"; params.append(int(chapter_max))
    if not regex:
        where += " and content ILIKE %s"; params.append(f"%{query}%")
    with connect() as db:
        if not script_owned(db, script_id, uid):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        rows = db.execute(
            f"select chapter_index, title, content from script_chapters where {where} order by chapter_index",
            tuple(params),
        ).fetchall()
    results: list[dict[str, Any]] = []
    total = 0
    capped = False
    for row in rows:
        ci = row.get("chapter_index")
        title = str(row.get("title") or "")
        content = str(row.get("content") or "")
        if not content:
            continue
        for m in pat.finditer(content):
            total += 1
            if len(results) < lim:
                s = max(0, m.start() - 48)
                e = min(len(content), m.end() + 48)
                results.append({
                    "chapter_index": ci, "title": title, "offset": m.start(),
                    "snippet": content[s:e].replace("\n", " ").strip(),
                    "pre": "…" if s > 0 else "", "suf": "…" if e < len(content) else "",
                })
            if total >= CAP:
                capped = True
                break
        if capped:
            break
    return json_response({"ok": True, "results": results,
                          "total": total, "capped": capped,
                          "chapters": len({r["chapter_index"] for r in results})})
