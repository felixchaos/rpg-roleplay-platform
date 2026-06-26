"""platform_app.api.script_edit — schema v44 剧本 fork / Git 版本控制 / 手动编辑。

endpoints:
  POST   /api/scripts/{script_id}/fork
  GET    /api/scripts/{script_id}/commits
  POST   /api/scripts/{script_id}/pin
  POST   /api/scripts/{script_id}/unpin
  PUT    /api/scripts/{script_id}/worldbook/{entry_id}
  POST   /api/scripts/{script_id}/worldbook
  DELETE /api/scripts/{script_id}/worldbook/{entry_id}
  POST   /api/scripts/{script_id}/worldbook/batch   (批量 delete/enable/disable/set_priority)
  PUT    /api/scripts/{script_id}/canon-entities/{logical_key}
  POST   /api/scripts/{script_id}/canon-entities
  DELETE /api/scripts/{script_id}/canon-entities/{logical_key}
  PUT    /api/scripts/{script_id}/anchors/{anchor_id}
  POST   /api/scripts/{script_id}/anchors
  DELETE /api/scripts/{script_id}/anchors/{anchor_id}
  POST   /api/scripts/{script_id}/checkout/{commit_id}
"""
from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, Depends, Request
from psycopg.types.json import Jsonb

from ..db import connect
from ..perms import script_owned
from ._deps import json_response, require_user

router = APIRouter()

# ─── helpers ──────────────────────────────────────────────────────────────────

_VALID_SHARING_MODES = {"private", "public", "pinned-snapshot", "floating-latest"}


def _require_owner(db, script_id: int, user_id: int):
    """确认 user 是 script owner，不是则 raise ValueError。

    严格 owner SQL 收敛到 perms.script_owned;但保留本函数特有的两段区分性文案
    (「剧本不存在」vs「必须 fork 后才能编辑」)—— 故非 owner 时再查一次存在性以选文案
    (仅失败分支多一次查询,正常路径单查)。
    """
    owned = script_owned(db, script_id, user_id)
    if owned:
        return owned
    exists = db.execute("SELECT owner_id FROM scripts WHERE id = %s", (script_id,)).fetchone()
    if not exists:
        raise ValueError("剧本不存在")
    raise ValueError("必须 fork 后才能编辑（当前用户不是该剧本 owner）")


def _write_commit(
    db,
    *,
    script_id: int,
    user_id: int,
    kind: str,
    message: str,
    payload: dict,
    is_checkpoint: bool = False,
) -> int:
    """写入一条 script_commit，更新 scripts.head_commit_id，返回新 commit id。"""
    # 取当前 head 作 parent
    head = db.execute(
        "SELECT head_commit_id FROM scripts WHERE id = %s",
        (script_id,),
    ).fetchone()
    parent_id = int(head["head_commit_id"]) if head and head["head_commit_id"] else None

    row = db.execute(
        """
        INSERT INTO script_commits
          (script_id, parent_commit_id, author_user_id, message, kind, payload, is_checkpoint)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (script_id, parent_id, user_id, message, kind, Jsonb(payload), is_checkpoint),
    ).fetchone()
    commit_id = int(row["id"])

    db.execute(
        "UPDATE scripts SET head_commit_id = %s, updated_at = now() WHERE id = %s",
        (commit_id, script_id),
    )
    return commit_id


# ─── fork ─────────────────────────────────────────────────────────────────────

@router.post("/api/scripts/{script_id}/fork")
async def api_fork_script(request: Request, script_id: int, user=Depends(require_user)):
    """复制整个剧本到新 script，owner=当前用户。

    body: {title?, message?}
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    title_override = (body.get("title") or "").strip()
    commit_message = (body.get("message") or "fork").strip() or "fork"

    with connect() as db:
        # IDOR 修复:fork 会把源剧本的全部正文/世界书/角色卡/锚点复制成归当前用户的副本,
        # 等于"读取"。必须校验当前用户有读权限(owner 或订阅者),否则任意登录用户传别人
        # 的私有 script_id 即可窃取整本未公开内容。门控与 _require_script(只读级)一致;
        # 公开剧本的 fork 走另一端点 /api/scripts/public/{id}/fork。
        src = db.execute(
            """
            SELECT id, owner_id, title, description, source_path,
                   chapter_count, word_count, content_fingerprint,
                   head_commit_id
            FROM scripts WHERE id = %s AND (
              owner_id = %s
              OR id IN (SELECT script_id FROM user_script_subscriptions WHERE user_id = %s)
            )
            """,
            (script_id, user["id"], user["id"]),
        ).fetchone()
        if not src:
            # 不区分"不存在"与"无权",避免私有剧本 id 枚举探测
            return json_response({"ok": False, "error": "源剧本不存在或无权访问"}, status_code=404)

        fork_title = title_override or f"[fork] {src['title']}"
        forked_at_commit = src["head_commit_id"]

        # 1. 新建 script 行
        new_script = db.execute(
            """
            INSERT INTO scripts
              (owner_id, title, description, source_path,
               chapter_count, word_count, content_fingerprint,
               forked_from_script_id, forked_at_commit_id, sharing_mode)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'private')
            RETURNING id
            """,
            (
                user["id"],
                fork_title,
                str(src["description"] or ""),
                str(src["source_path"] or ""),
                int(src["chapter_count"] or 0),
                int(src["word_count"] or 0),
                src.get("content_fingerprint"),
                script_id,
                forked_at_commit,
            ),
        ).fetchone()
        new_id: int = int(new_script["id"])

        # 2. 确保 book 行（knowledge sync 依赖）
        try:
            from platform_app.knowledge._sync import _ensure_book
            _ensure_book(db, {
                "id": new_id,
                "owner_id": user["id"],
                "title": fork_title,
                "description": str(src["description"] or ""),
                "source_path": "",
            })
        except Exception:
            pass  # 非致命，后续 knowledge/sync 可修复

        # 3. 复制 script_chapters
        db.execute(
            """
            INSERT INTO script_chapters
              (script_id, chapter_index, title, content, word_count,
               volume_title, source_marker, confidence)
            SELECT %s, chapter_index, title, content, word_count,
                   volume_title, source_marker, confidence
            FROM script_chapters WHERE script_id = %s
            """,
            (new_id, script_id),
        )

        # 4. 复制 worldbook_entries（via book）
        new_book = db.execute(
            "SELECT id FROM books WHERE script_id = %s", (new_id,)
        ).fetchone()
        old_book = db.execute(
            "SELECT id FROM books WHERE script_id = %s", (script_id,)
        ).fetchone()

        if new_book and old_book:
            db.execute(
                """
                INSERT INTO worldbook_entries
                  (book_id, script_id, title, content, keys, regex_keys,
                   priority, token_budget, insertion_position, sticky_turns,
                   cooldown_turns, probability, character_filter, scene_filter,
                   enabled, metadata)
                SELECT %s, %s, title, content, keys, regex_keys,
                       priority, token_budget, insertion_position, sticky_turns,
                       cooldown_turns, probability, character_filter, scene_filter,
                       enabled, metadata
                FROM worldbook_entries WHERE script_id = %s
                """,
                (int(new_book["id"]), new_id, script_id),
            )

        # 5. 复制 kb_canon_entities
        db.execute(
            """
            INSERT INTO kb_canon_entities
              (script_id, logical_key, name, aliases, type, summary,
               attrs, first_revealed_chapter, public_knowledge, importance,
               metadata, full_name, identity, background, entity_subtype, parent_logical_key)
            SELECT %s, logical_key, name, aliases, type, summary,
                   attrs, first_revealed_chapter, public_knowledge, importance,
                   metadata, full_name, identity, background, entity_subtype, parent_logical_key
            FROM kb_canon_entities WHERE script_id = %s
            ON CONFLICT (script_id, logical_key) DO NOTHING
            """,
            (new_id, script_id),
        )

        # 6. 复制 script_timeline_anchors
        db.execute(
            """
            INSERT INTO script_timeline_anchors
              (script_id, story_phase, story_time_label,
               chapter_min, chapter_max, chapter_count,
               sample_title, sample_summary, keywords, confidence, source)
            SELECT %s, story_phase, story_time_label,
                   chapter_min, chapter_max, chapter_count,
                   sample_title, sample_summary, keywords, confidence,
                   coalesce(source, 'novel')
            FROM script_timeline_anchors WHERE script_id = %s
            ON CONFLICT (script_id, story_phase, story_time_label) DO NOTHING
            """,
            (new_id, script_id),
        )

        # 7. 复制 character_cards（若 book 行存在）
        if new_book and old_book:
            db.execute(
                """
                INSERT INTO character_cards
                  (book_id, script_id, name, aliases, identity, appearance,
                   personality, speech_style, current_status, secrets,
                   sample_dialogue, token_budget, priority, enabled, metadata)
                SELECT %s, %s, name, aliases, identity, appearance,
                       personality, speech_style, current_status, secrets,
                       sample_dialogue, token_budget, priority, enabled, metadata
                FROM character_cards WHERE script_id = %s
                ON CONFLICT (script_id, name) DO NOTHING
                """,
                (int(new_book["id"]), new_id, script_id),
            )

        # 7b. 复制 phase_digests（阶段摘要 — script 级,GM 检索会读;fork 漏掉会让新剧本丢阶段上下文）
        db.execute(
            """
            INSERT INTO phase_digests
              (script_id, phase_label, chapter_min, chapter_max, summary,
               key_events, key_locations, key_characters,
               story_time_label_start, story_time_label_end, chapter_count)
            SELECT %s, phase_label, chapter_min, chapter_max, summary,
                   key_events, key_locations, key_characters,
                   story_time_label_start, story_time_label_end, chapter_count
            FROM phase_digests WHERE script_id = %s
            ON CONFLICT DO NOTHING
            """,
            (new_id, script_id),
        )

        # 7c. 复制 script_worldlines（世界树主/支线 — 用 wl_key 文本键,无需 id 重映射）
        db.execute(
            """
            INSERT INTO script_worldlines
              (script_id, wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata)
            SELECT %s, wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata
            FROM script_worldlines WHERE script_id = %s
            ON CONFLICT DO NOTHING
            """,
            (new_id, script_id),
        )

        # 7d. 复制 script_worldline_nodes（世界树节点 — 同样 wl_key/node_key 文本键)
        db.execute(
            """
            INSERT INTO script_worldline_nodes
              (script_id, wl_key, node_key, seq, label, summary, chapter_min, chapter_max,
               anchor_keys, must_preserve, may_vary, causal_centrality, first_revealed_chapter)
            SELECT %s, wl_key, node_key, seq, label, summary, chapter_min, chapter_max,
                   anchor_keys, must_preserve, may_vary, causal_centrality, first_revealed_chapter
            FROM script_worldline_nodes WHERE script_id = %s
            ON CONFLICT DO NOTHING
            """,
            (new_id, script_id),
        )

        # 8. 初始 commit（fork 类型）
        commit_id = _write_commit(
            db,
            script_id=new_id,
            user_id=user["id"],
            kind="fork",
            message=commit_message,
            payload={
                "source_script_id": script_id,
                "source_head_commit_id": forked_at_commit,
                "fork_title": fork_title,
            },
            is_checkpoint=True,
        )
        db.commit()

        # 9. 返回新 script 行
        new_row = db.execute(
            "SELECT id, title, owner_id, forked_from_script_id, forked_at_commit_id, head_commit_id, created_at FROM scripts WHERE id = %s",
            (new_id,),
        ).fetchone()

    return json_response({
        "ok": True,
        "script": dict(new_row),
        "commit_id": commit_id,
    })


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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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


# ─── anchors CRUD ─────────────────────────────────────────────────────────────

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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

        before_row = db.execute(
            f"SELECT {_ANCHOR_COLS} FROM script_timeline_anchors WHERE id = %s AND script_id = %s",
            (anchor_id, script_id),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": "anchor 不存在"}, status_code=404)

        before = dict(before_row)
        sets, args = [], []
        # sample_summary → 字段名实际是 sample_summary
        if "summary" in body:
            sets.append("sample_summary=%s")
            args.append(str(body["summary"]))
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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
                {"ok": False, "error": f"story_phase+story_time_label 组合已存在"},
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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)

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
    lim = max(1, min(int(limit or 80), 300))
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
            return json_response({"ok": False, "error": str(exc)}, status_code=403)
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
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
