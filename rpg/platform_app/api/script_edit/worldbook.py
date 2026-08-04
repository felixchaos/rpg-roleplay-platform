"""platform_app.api.script_edit.worldbook —— 世界书条目写侧 CRUD(编辑/新增/删除/批量)。

写 commit(worldbook_edit/add/delete/bulk_*),改动后清 constant 层缓存。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from psycopg.types.json import Jsonb

from ...db import connect
from .._deps import json_response, require_user, value_error_response
from ._shared import _require_owner, _write_commit, router

# ─── worldbook CRUD ───────────────────────────────────────────────────────────

@router.put("/api/scripts/{script_id}/worldbook/{entry_id}")
async def api_worldbook_update(
    request: Request, script_id: int, entry_id: int, user=Depends(require_user)
):
    """编辑 worldbook entry，写 commit kind=worldbook_edit。

    body: {title?, content?, priority?, enabled?, tags?, keys?, regex_keys?,
           character_filter?, scene_filter?, token_budget?, sticky_turns?,
           cooldown_turns?, probability?, insertion_position?}
    （keys/regex_keys/character_filter/scene_filter 为 jsonb 字符串数组列）
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)

    _WB_COLS = (
        "id, title, content, priority, enabled, metadata, "
        "keys, regex_keys, character_filter, scene_filter, "
        # probability 是 numeric → psycopg 读出 Decimal,JSON 不可序列化 → 必须 ::float8 转浮点
        "token_budget, sticky_turns, cooldown_turns, probability::float8 as probability, insertion_position"
    )

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        before_row = db.execute(
            f"SELECT {_WB_COLS} FROM worldbook_entries WHERE id = %s AND script_id = %s",
            (entry_id, script_id),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": "worldbook entry 不存在"}, status_code=404)

        before = dict(before_row)

        sets, args = [], []
        for col in ("title", "content", "insertion_position"):
            if col in body:
                sets.append(f"{col}=%s")
                args.append(str(body[col]))
        for col in ("priority", "token_budget", "sticky_turns", "cooldown_turns"):
            if col in body:
                sets.append(f"{col}=%s")
                args.append(int(body[col]))
        if "probability" in body:
            sets.append("probability=%s")
            args.append(float(body["probability"]))
        if "enabled" in body:
            sets.append("enabled=%s")
            args.append(bool(body["enabled"]))
        # jsonb 字符串数组列(与 init.py 实际 schema 一致):keys/regex_keys/character_filter/scene_filter
        for col in ("keys", "regex_keys", "character_filter", "scene_filter"):
            if col in body and isinstance(body[col], list):
                sets.append(f"{col}=%s")
                args.append(Jsonb([str(x) for x in body[col]]))
        if "tags" in body and isinstance(body["tags"], list):
            # tags 存进 metadata.tags
            meta = dict(before.get("metadata") or {})
            meta["tags"] = body["tags"]
            sets.append("metadata=%s")
            args.append(Jsonb(meta))

        # KB 卫生(设计 O §5.2):正文/标题变了 → 脏化向量(NULL embedding_vec)。否则编辑后行仍带
        # 旧内容的向量,而「重做」的增量循环 `WHERE embedding_vec IS NULL` 命中 0 行 → 秒完成且向量过期
        # (群反馈 行者无疆「改了世界书条目后重做秒完成、实际没重新生成」的根因)。脏化后重做/增量会真重嵌。
        if ("title" in body) or ("content" in body):
            sets.append("embedding_vec=NULL")
            sets.append("embedded_at=NULL")

        if not sets:
            return json_response({"ok": False, "error": "无可更新字段"}, status_code=400)

        sets.append("updated_at=now()")
        args.extend([entry_id, script_id])
        db.execute(
            f"UPDATE worldbook_entries SET {', '.join(sets)} WHERE id=%s AND script_id=%s",
            tuple(args),
        )

        after_row = db.execute(
            f"SELECT {_WB_COLS} FROM worldbook_entries WHERE id = %s",
            (entry_id,),
        ).fetchone()
        after = dict(after_row)

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="worldbook_edit",
            message=f"编辑 worldbook: {after.get('title', entry_id)}",
            payload={"table": "worldbook_entries", "op": "edit", "before": before, "after": after, "ids": {"entry_id": entry_id}},
        )
        db.commit()

    # L-4: worldbook 改动后清 constant 层缓存(本 worker 即时;其余 worker 300s TTL 自愈)。
    try:
        from gm_serving.context_inject import invalidate_constant_cache
        invalidate_constant_cache(script_id)
    except Exception:
        pass
    return json_response({"ok": True, "entry": after, "commit_id": commit_id})


@router.post("/api/scripts/{script_id}/worldbook")
async def api_worldbook_add(
    request: Request, script_id: int, user=Depends(require_user)
):
    """新建 worldbook entry，写 commit kind=worldbook_add。

    body: {title, content, priority?, enabled?, tags?, keys?, regex_keys?,
           character_filter?, scene_filter?, token_budget?, sticky_turns?,
           cooldown_turns?, probability?, insertion_position?}
    （keys/regex_keys/character_filter/scene_filter 为 jsonb 字符串数组列）
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)

    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "")
    if not title:
        return json_response({"ok": False, "error": "缺少 title"}, status_code=400)

    def _strlist(v: Any) -> list[str]:
        return [str(x) for x in v] if isinstance(v, list) else []

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        # book_id 是遗留列、可空(migration 85);有 books 行就带上,没有就 NULL,归属看 script_id。
        book_row = db.execute(
            "SELECT id FROM books WHERE script_id = %s", (script_id,)
        ).fetchone()
        book_id = int(book_row["id"]) if book_row else None

        tags = body.get("tags") if isinstance(body.get("tags"), list) else []
        # source='editor':标记为「用户/编辑器手写」,与 AI 工具 upsert_worldbook_entry 一致,
        # 让 resolve.py 重建知识库时豁免本条(coalesce(source)<>'editor'),不被 canon 重建覆盖/清除。
        # 此前 UI「新建」漏打此标记 → 手建条目会被重建静默覆盖(harness provenance 审计 P1)。
        meta: dict[str, Any] = {"tags": tags, "source": "editor"}

        new_row = db.execute(
            """
            INSERT INTO worldbook_entries
              (book_id, script_id, title, content, priority, enabled, metadata,
               keys, regex_keys, character_filter, scene_filter,
               token_budget, sticky_turns, cooldown_turns, probability, insertion_position)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, title, content, priority, enabled, metadata,
                      keys, regex_keys, character_filter, scene_filter,
                      token_budget, sticky_turns, cooldown_turns,
                      probability::float8 as probability, insertion_position
            """,
            (
                book_id, script_id, title, content,
                int(body.get("priority") or 50),
                bool(body.get("enabled", True)),
                Jsonb(meta),
                Jsonb(_strlist(body.get("keys"))),
                Jsonb(_strlist(body.get("regex_keys"))),
                Jsonb(_strlist(body.get("character_filter"))),
                Jsonb(_strlist(body.get("scene_filter"))),
                int(body.get("token_budget") or 600),
                int(body.get("sticky_turns") or 0),
                int(body.get("cooldown_turns") or 0),
                float(body["probability"]) if body.get("probability") is not None else 100.0,
                str(body.get("insertion_position") or "worldbook"),
            ),
        ).fetchone()
        after = dict(new_row)

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="worldbook_add",
            message=f"新增 worldbook: {title}",
            payload={"table": "worldbook_entries", "op": "add", "after": after, "ids": {"entry_id": int(after["id"])}},
        )
        db.commit()

    # L-4: worldbook 改动后清 constant 层缓存(本 worker 即时;其余 worker 300s TTL 自愈)。
    try:
        from gm_serving.context_inject import invalidate_constant_cache
        invalidate_constant_cache(script_id)
    except Exception:
        pass
    return json_response({"ok": True, "entry": after, "commit_id": commit_id})


@router.delete("/api/scripts/{script_id}/worldbook/{entry_id}")
async def api_worldbook_delete(
    script_id: int, entry_id: int, user=Depends(require_user)
):
    """删除 worldbook entry（物理删除），写 commit kind=worldbook_delete。

    历史上这里是「软删除」(UPDATE enabled=false),但世界书列表不按 enabled 过滤、且
    enabled 同时被「停用/启用」开关复用 —— 导致「删除」和「停用」语义冲突:删完前端本地
    移除了行,reload 后该条又以「停用」态出现(用户反馈「管理不方便」的一部分)。改为物理
    删除:删除=真没了,停用=enabled 开关切换,两者彻底分离。commit 仍记 before 供审计。
    注:DELETE 用 `where id=` 的 targeted 形式(provenance 守卫豁免,见 test_editor_provenance_guards)。
    """
    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        before_row = db.execute(
            "SELECT id, title, content, priority, enabled, metadata FROM worldbook_entries WHERE id = %s AND script_id = %s",
            (entry_id, script_id),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": "worldbook entry 不存在"}, status_code=404)

        before = dict(before_row)
        db.execute(
            "DELETE FROM worldbook_entries WHERE id=%s AND script_id=%s",
            (entry_id, script_id),
        )

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="worldbook_delete",
            message=f"删除 worldbook: {before.get('title', entry_id)}",
            payload={"table": "worldbook_entries", "op": "delete", "before": before, "ids": {"entry_id": entry_id}},
        )
        db.commit()

    # L-4: worldbook 改动后清 constant 层缓存(本 worker 即时;其余 worker 300s TTL 自愈)。
    try:
        from gm_serving.context_inject import invalidate_constant_cache
        invalidate_constant_cache(script_id)
    except Exception:
        pass
    return json_response({"ok": True, "deleted": True, "commit_id": commit_id})


# 批量动作 → (中文标签, commit kind 后缀)。set_priority 需附带 priority。
_WB_BATCH_ACTIONS = {
    "delete": "删除",
    "enable": "启用",
    "disable": "停用",
    "set_priority": "设置优先级",
}


@router.post("/api/scripts/{script_id}/worldbook/batch")
async def api_worldbook_batch(
    request: Request, script_id: int, user=Depends(require_user)
):
    """批量操作 worldbook entries:delete(物理删除)/ enable / disable / set_priority。

    body: {entry_ids: [int], action: 'delete'|'enable'|'disable'|'set_priority', priority?: int}

    单事务 + 单 commit(kind=worldbook_bulk_<action>),避免逐条 PUT 撑爆 script_commits
    版本历史。SQL 用 `id = ANY(%s) AND script_id=%s`:① 双重校验防 IDOR(只动本剧本的条目);
    ② targeted 形式豁免 provenance 守卫(用户主动选删,非全量重建)。一次 invalidate_constant_cache。
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)

    action = str(body.get("action") or "").strip()
    if action not in _WB_BATCH_ACTIONS:
        return json_response({"ok": False, "error": f"不支持的 action: {action}"}, status_code=400)

    raw_ids = body.get("entry_ids") or body.get("ids") or []
    if not isinstance(raw_ids, list):
        return json_response({"ok": False, "error": "entry_ids 必须是数组"}, status_code=400)
    try:
        ids = [int(x) for x in raw_ids if x is not None]
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "entry_ids 含非法 id"}, status_code=400)
    ids = list(dict.fromkeys(ids))  # 去重保序
    if not ids:
        return json_response({"ok": False, "error": "entry_ids 不能为空"}, status_code=400)
    MAX_BATCH = 1000
    if len(ids) > MAX_BATCH:
        return json_response({"ok": False, "error": f"单次批量上限 {MAX_BATCH} 条"}, status_code=400)

    priority = None
    if action == "set_priority":
        try:
            priority = int(body.get("priority"))
        except (TypeError, ValueError):
            return json_response({"ok": False, "error": "set_priority 需要整数 priority"}, status_code=400)
        priority = max(0, min(1000, priority))  # clamp 到合理区间

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        if action == "delete":
            rows = db.execute(
                "DELETE FROM worldbook_entries WHERE id = ANY(%s) AND script_id=%s RETURNING id",
                (ids, script_id),
            ).fetchall()
        elif action in ("enable", "disable"):
            rows = db.execute(
                "UPDATE worldbook_entries SET enabled=%s, updated_at=now() "
                "WHERE id = ANY(%s) AND script_id=%s RETURNING id",
                (action == "enable", ids, script_id),
            ).fetchall()
        else:  # set_priority
            rows = db.execute(
                "UPDATE worldbook_entries SET priority=%s, updated_at=now() "
                "WHERE id = ANY(%s) AND script_id=%s RETURNING id",
                (priority, ids, script_id),
            ).fetchall()
        affected = len(rows or [])

        payload: dict[str, Any] = {
            "table": "worldbook_entries", "op": action,
            "ids": ids, "requested": len(ids), "count": affected,
        }
        if priority is not None:
            payload["priority"] = priority
        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind=f"worldbook_bulk_{action}",
            message=f"批量{_WB_BATCH_ACTIONS[action]} worldbook × {affected}",
            payload=payload,
        )
        db.commit()

    try:
        from gm_serving.context_inject import invalidate_constant_cache
        invalidate_constant_cache(script_id)
    except Exception:
        pass
    return json_response({"ok": True, "action": action, "affected": affected, "commit_id": commit_id})
