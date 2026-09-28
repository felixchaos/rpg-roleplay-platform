"""platform_app.api.scripts.review —— Phase E 可视化复核端点(只读图 + god 编辑)。

复核图(graph)、canon god 编辑(patch)、复核状态机(mark/unmark-reviewed)。
纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from ...perms import script_owned
from .._deps import json_response, require_user
from ._shared import router


# ── Phase E: 可视化复核(只读图 + god 编辑)─────────────────────────────────
def _owned_script(db, script_id: int, user_id: int):
    # 严格 owner SQL 收敛到 perms.script_owned;返回 select * 整行(含
    # id/title/import_report/review_status/reviewed_at 等下游用到的列,为原版超集)。
    return script_owned(db, script_id, user_id)


@router.get("/api/scripts/{script_id}/graph")
async def api_script_graph(script_id: int, user=Depends(require_user)):
    """Phase E.1 复核图:规范实体 + 世界线 DAG + 时间线 + 摄入质量 flag。

    保持 owner-only:响应包含 import_report(extraction quality review 元数据,
    含 needs_review/author_notes/weird_titles/gaps/cleaning)和 review_status,
    是编辑工作流专属字段,不对订阅者开放。
    """
    with connect() as db:
        s = _owned_script(db, script_id, user["id"])
        if not s:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        entities = db.execute(
            "select id, logical_key, name, type, aliases, summary, importance, "
            "first_revealed_chapter, public_knowledge from kb_canon_entities "
            "where script_id=%s order by importance desc, logical_key limit 1000",
            (script_id,),
        ).fetchall()
        worldlines = db.execute(
            "select wl_key, label, parent_wl, branch_at_node, is_primary, source "
            "from script_worldlines where script_id=%s order by is_primary desc, wl_key",
            (script_id,),
        ).fetchall()
        nodes = db.execute(
            "select wl_key, node_key, seq, label, summary, chapter_min, chapter_max, "
            "anchor_keys, must_preserve, may_vary from script_worldline_nodes "
            "where script_id=%s order by wl_key, seq",
            (script_id,),
        ).fetchall()
        timeline = db.execute(
            "select story_time_label, chapter_min, chapter_max from script_timeline_anchors "
            "where script_id=%s order by chapter_min limit 500",
            (script_id,),
        ).fetchall()
        report = s.get("import_report") or {}
        review_flags = {
            "needs_review": report.get("needs_review"),
            "author_notes": report.get("author_notes", []),
            "weird_titles": report.get("weird_titles", []),
            "gaps": report.get("gaps", []),
            "cleaning": report.get("cleaning", {}),
        }
    return json_response({
        "ok": True, "script": {
            "id": script_id, "title": s["title"],
            "review_status": s.get("review_status") or "unreviewed",
            "reviewed_at": s.get("reviewed_at"),
        },
        "entities": [dict(e) for e in entities],
        "worldlines": [dict(w) for w in worldlines],
        "nodes": [dict(n) for n in nodes],
        "timeline": [dict(t) for t in timeline],
        "review_flags": review_flags,
    })


@router.patch("/api/scripts/{script_id}/canon")
async def api_patch_canon(request: Request, script_id: int, user=Depends(require_user)):
    """Phase E god 编辑(仅 owner)。

    Body 之一:
      {"op": "update_entity", "logical_key": "...", "summary": "...", "aliases": [...], "importance": N}
      {"op": "merge_entity", "from_key": "...", "into_key": "..."}  # from 的别名并入 into,删 from
      {"op": "delete_entity", "logical_key": "..."}

    与编辑器 REST(script_edit/canon.py)同口径:摘要 / 别名真改了 → 向量置空;删除 / 合并掉的
    实体,挂在它下面的子实体改成无上级 / 改挂到合并目标;写完作废别名归并缓存。
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)
    with connect() as db:
        if not _owned_script(db, script_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        resp = _apply_canon_patch(db, script_id, body)
    # 事务提交后再作废:提交前作废,并发请求可能把旧值又读进缓存。
    from kb.alias import invalidate_alias_cache
    invalidate_alias_cache(script_id)
    return resp


def _apply_canon_patch(db, script_id: int, body: dict):
    from psycopg.types.json import Jsonb

    from kb.canon_repo import canon_embedding_reset_sql, detach_canon_children
    op = (body.get("op") or "").strip()
    if op == "update_entity":
        lk = (body.get("logical_key") or "").strip()
        if not lk:
            return json_response({"ok": False, "error": "缺 logical_key"}, status_code=400)
        sets, args = [], []
        text_values: dict = {}
        for col in ("summary",):
            if col in body:
                val = "" if body[col] is None else str(body[col])
                sets.append(f"{col}=%s")
                args.append(val)
                text_values[col] = val
        if "importance" in body:
            try:
                imp = int(body["importance"] or 0)
            except (TypeError, ValueError):
                return json_response({"ok": False, "error": "「重要度」要填整数"}, status_code=400)
            sets.append("importance=%s")
            args.append(imp)
        if "aliases" in body and isinstance(body["aliases"], list):
            sets.append("aliases=%s")
            args.append(Jsonb(body["aliases"]))
            text_values["aliases"] = body["aliases"]
        if not sets:
            return json_response({"ok": False, "error": "无可更新字段"}, status_code=400)
        # 人工复核改过 → 标 source='editor',重建知识库保留(与编辑器 REST / agent 工具同口径)
        sets.append("attrs = coalesce(attrs, '{}'::jsonb) || %s::jsonb")
        args.append(Jsonb({"source": "editor"}))
        reset = canon_embedding_reset_sql(text_values)
        if reset:
            sets.append(reset[0])
            args.extend(reset[1])
        args.extend([script_id, lk])
        n = db.execute(
            f"update kb_canon_entities set {', '.join(sets)} where script_id=%s and logical_key=%s",
            tuple(args),
        ).rowcount
        return json_response({"ok": True, "updated": n})
    if op == "merge_entity":
        frm = (body.get("from_key") or "").strip()
        into = (body.get("into_key") or "").strip()
        if not frm or not into:
            return json_response({"ok": False, "error": "缺 from_key/into_key"}, status_code=400)
        src = db.execute("select name, aliases from kb_canon_entities where script_id=%s and logical_key=%s", (script_id, frm)).fetchone()
        if not src:
            return json_response({"ok": False, "error": f"from_key 不存在: {frm}"}, status_code=404)
        dst = db.execute("select 1 from kb_canon_entities where script_id=%s and logical_key=%s", (script_id, into)).fetchone()
        if not dst:
            return json_response({"ok": False, "error": f"into_key 不存在: {into}"}, status_code=400)
        merged_aliases = list({*(src.get("aliases") or []), src["name"]})
        # 别名并进来了 → 合并目标的嵌入文本变了,向量置空等重嵌。
        updated = db.execute(
            "update kb_canon_entities set aliases = (select to_jsonb(array(select distinct e from unnest("
            "  array(select jsonb_array_elements_text(coalesce(aliases,'[]'::jsonb))) || %s::text[]) e))), "
            "  embedding = null "
            "where script_id=%s and logical_key=%s",
            (merged_aliases, script_id, into),
        ).rowcount
        if updated == 0:
            return json_response({"ok": False, "error": "into_key 更新失败,未执行 DELETE"}, status_code=500)
        db.execute("delete from kb_canon_entities where script_id=%s and logical_key=%s", (script_id, frm))
        moved = detach_canon_children(db, script_id, frm, new_parent=into)
        return json_response({"ok": True, "merged": True, "moved_children": moved})
    if op == "delete_entity":
        lk = (body.get("logical_key") or "").strip()
        if not lk:
            return json_response({"ok": False, "error": "缺 logical_key"}, status_code=400)
        n = db.execute("delete from kb_canon_entities where script_id=%s and logical_key=%s", (script_id, lk)).rowcount
        detached = detach_canon_children(db, script_id, lk) if n else []
        return json_response({"ok": True, "deleted": n, "detached_children": detached})
    return json_response({"ok": False, "error": f"未知 op: {op}"}, status_code=400)


@router.post("/api/scripts/{script_id}/mark-reviewed")
async def api_script_mark_reviewed(script_id: int, user=Depends(require_user)):
    """Phase E.1 复核状态机:owner 复核完点这个,scripts.review_status='reviewed'。

    解锁开局闸——之后建档接口才会接受这本剧本。重切(resplit)会 reset 回 unreviewed。
    """
    with connect() as db:
        if not _owned_script(db, script_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        db.execute(
            "update scripts set review_status='reviewed', reviewed_at=now(), updated_at=now() "
            "where id=%s",
            (script_id,),
        )
    return json_response({"ok": True, "review_status": "reviewed"})


@router.post("/api/scripts/{script_id}/unmark-reviewed")
async def api_script_unmark_reviewed(script_id: int, user=Depends(require_user)):
    """owner 重新打开复核(回 unreviewed)。"""
    with connect() as db:
        if not _owned_script(db, script_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        db.execute(
            "update scripts set review_status='unreviewed', reviewed_at=null, updated_at=now() "
            "where id=%s",
            (script_id,),
        )
    return json_response({"ok": True, "review_status": "unreviewed"})
