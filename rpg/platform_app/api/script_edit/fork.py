"""platform_app.api.script_edit.fork —— 剧本 fork(整本复制成归当前用户的副本)。

纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from .._deps import json_response, require_user
from ._shared import _write_commit, router

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
                ON CONFLICT DO NOTHING
                """,
                # 修:character_cards 无 (script_id,name) 唯一约束 → 原 ON CONFLICT (script_id,name)
                # 在 plan 期就 InvalidColumnReference 报错 → fork 含角色卡的剧本必 500(生产日志实证)。
                # fork 目标是全新 script_id、无既有行可冲突,用裸 ON CONFLICT DO NOTHING(同 phase_digests/
                # worldlines 那几条),忠实全量复制。
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
