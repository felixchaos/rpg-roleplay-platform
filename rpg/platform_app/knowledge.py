from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from chapter_fact_indexer import (
    _extract_fact,
    _iter_chapters,
    _known_concepts,
    _known_locations,
    _known_names,
    _load_characters,
    _load_summaries,
    _load_world,
)

from . import runtime
from .db import connect, expose, init_db, limit_value, page_payload


CHUNK_CHARS = 1400
CHUNK_OVERLAP = 160


def sync_script_knowledge(user_id: int, script_id: int, *, rebuild: bool = False) -> dict[str, Any]:
    """Build the Postgres knowledge layer for one imported script.

    The import path stays deterministic and cheap: it creates documents/chunks,
    ChapterFact rows, character cards, and worldbook entries without requiring an
    LLM pass. A later refinement pass can overwrite the same rows.
    """
    init_db()
    chars = _load_characters()
    world = _load_world()
    summaries = _load_summaries()
    known_names = _known_names(chars)
    known_locations = _known_locations(world)
    known_concepts = _known_concepts(world)

    with connect() as db:
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        book = _ensure_book(db, script)
        if rebuild:
            db.execute("delete from documents where script_id = %s", (script_id,))
            db.execute("delete from chapter_facts where script_id = %s", (script_id,))

        chapters = db.execute(
            """
            select * from script_chapters
            where script_id = %s
            order by chapter_index
            """,
            (script_id,),
        ).fetchall()
        if not chapters:
            _backfill_chapters_from_local_source(db, script)
            chapters = db.execute(
                """
                select * from script_chapters
                where script_id = %s
                order by chapter_index
                """,
                (script_id,),
            ).fetchall()
        card_count = _sync_character_cards(db, book, script, chars)
        worldbook_count = _sync_worldbook_entries(db, book, script, world)

        chunk_count = 0
        fact_count = 0
        for chapter in chapters:
            document = _upsert_document(db, book, script, chapter)
            db.execute("delete from document_chunks where document_id = %s", (document["id"],))
            chunks = _chunk_text(chapter["content"])
            for chunk_index, content in enumerate(chunks):
                _insert_chunk(db, book, script, chapter, document, chunk_index, content)
            chunk_count += len(chunks)

            fact = _fact_from_chapter(chapter, summaries, known_names, known_locations, known_concepts)
            _upsert_chapter_fact(db, book, script, chapter, document, fact)
            fact_count += 1

        db.execute(
            """
            update scripts
            set import_report = import_report || %s::jsonb,
                row_version = row_version + 1,
                updated_at = now()
            where id = %s
            """,
            (
                Jsonb({
                    "knowledge": {
                        "status": "ready",
                        "chapters": len(chapters),
                        "chunks": chunk_count,
                        "chapter_facts": fact_count,
                        "character_cards": card_count,
                        "worldbook_entries": worldbook_count,
                    }
                }),
                script_id,
            ),
        )

    return {
        "book": expose(book),
        "chapters": len(chapters),
        "chunks": chunk_count,
        "chapter_facts": fact_count,
        "character_cards": card_count,
        "worldbook_entries": worldbook_count,
    }


def ensure_game_session(user_id: int, save_id: int, state: dict[str, Any] | None = None) -> dict[str, Any]:
    init_db()
    with connect() as db:
        save = db.execute(
            """
            select game_saves.*, scripts.owner_id, scripts.title as script_title
            from game_saves
            join scripts on scripts.id = game_saves.script_id
            where game_saves.id = %s and game_saves.user_id = %s
            """,
            (save_id, user_id),
        ).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        script = db.execute("select * from scripts where id = %s", (save["script_id"],)).fetchone()
        book = _ensure_book(db, script)
        payload = state or {}
        session = db.execute(
            """
            insert into game_sessions(
              save_id, book_id, script_id, user_id, title, state,
              memory_mode, permission_mode, worldline, turn
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict(save_id) do update set
              book_id = excluded.book_id,
              script_id = excluded.script_id,
              title = excluded.title,
              state = excluded.state,
              memory_mode = excluded.memory_mode,
              permission_mode = excluded.permission_mode,
              worldline = excluded.worldline,
              turn = excluded.turn,
              row_version = game_sessions.row_version + 1,
              updated_at = now()
            returning *
            """,
            (
                save_id,
                book["id"],
                save["script_id"],
                user_id,
                save["title"] or save["script_title"],
                Jsonb(payload),
                (payload.get("memory") or {}).get("mode", "normal"),
                (payload.get("permissions") or {}).get("mode", "full_access"),
                Jsonb(payload.get("worldline") or {}),
                int(payload.get("turn") or 0),
            ),
        ).fetchone()
        _sync_session_state(db, session, book["id"], user_id, payload)
    return expose(session)


def set_worldline_variable(user_id: int, save_id: int, key: str, value: str, source: str = "user") -> dict[str, Any]:
    key = _clean_text(key)
    value = _clean_text(value)
    if not key or not value:
        raise ValueError("变量名和变量值不能为空")
    session = ensure_game_session(user_id, save_id, _state_from_save(user_id, save_id))
    with connect() as db:
        row = db.execute(
            """
            insert into worldline_variables(session_id, key, value, locked, source, metadata)
            values (%s, %s, %s, true, %s, %s)
            on conflict(session_id, key) do update set
              value = excluded.value,
              locked = excluded.locked,
              source = excluded.source,
              metadata = excluded.metadata,
              updated_at = now()
            returning *
            """,
            (session["id"], key, value, source, Jsonb({"api": True})),
        ).fetchone()
        state = dict(session.get("state") or {})
        worldline = state.setdefault("worldline", {})
        variables = worldline.setdefault("user_variables", {})
        variables[key] = {"value": value, "source": source, "locked": True}
        db.execute(
            "update game_sessions set state = %s, worldline = %s, updated_at = now(), row_version = row_version + 1 where id = %s",
            (Jsonb(state), Jsonb(worldline), session["id"]),
        )
    return expose(row)


def remove_worldline_variable(user_id: int, save_id: int, key: str) -> dict[str, Any]:
    key = _clean_text(key)
    if not key:
        raise ValueError("变量名不能为空")
    session = ensure_game_session(user_id, save_id, _state_from_save(user_id, save_id))
    with connect() as db:
        db.execute("delete from worldline_variables where session_id = %s and key = %s", (session["id"], key))
        state = dict(session.get("state") or {})
        worldline = state.setdefault("worldline", {})
        variables = worldline.setdefault("user_variables", {})
        variables.pop(key, None)
        db.execute(
            "update game_sessions set state = %s, worldline = %s, updated_at = now(), row_version = row_version + 1 where id = %s",
            (Jsonb(state), Jsonb(worldline), session["id"]),
        )
    return {"removed": key}


def _state_from_save(user_id: int, save_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute(
            "select state_snapshot from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError("无权访问该存档")
    state = row.get("state_snapshot") if isinstance(row, dict) else {}
    return state if isinstance(state, dict) else {}


def _sync_session_state(db, session: dict[str, Any], book_id: int, user_id: int, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    session_id = session["id"]
    db.execute(
        "delete from memories where session_id = %s and metadata->>'sync_source' = 'state_snapshot'",
        (session_id,),
    )
    memory = payload.get("memory") or {}
    for bucket in ("pinned", "facts", "abilities", "resources", "notes"):
        for index, content in enumerate(memory.get(bucket) or []):
            text = _clean_text(content)
            if not text:
                continue
            db.execute(
                """
                insert into memories(session_id, book_id, user_id, bucket, content, importance, metadata)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    book_id,
                    user_id,
                    bucket,
                    text,
                    90 if bucket == "pinned" else 60,
                    Jsonb({"sync_source": "state_snapshot", "index": index}),
                ),
            )
    for key in ("main_quest", "current_objective"):
        text = _clean_text(memory.get(key) or "")
        if text:
            db.execute(
                """
                insert into memories(session_id, book_id, user_id, bucket, content, importance, metadata)
                values (%s, %s, %s, 'summary', %s, %s, %s)
                """,
                (session_id, book_id, user_id, text, 70, Jsonb({"sync_source": "state_snapshot", "field": key})),
            )

    worldline = payload.get("worldline") or {}
    variables = worldline.get("user_variables") or {}
    db.execute("delete from worldline_variables where session_id = %s", (session_id,))
    for key, raw in variables.items():
        value = raw.get("value") if isinstance(raw, dict) else raw
        value_text = _clean_text(value)
        key_text = _clean_text(key)
        if not key_text or not value_text:
            continue
        db.execute(
            """
            insert into worldline_variables(session_id, key, value, locked, source, metadata)
            values (%s, %s, %s, %s, %s, %s)
            on conflict(session_id, key) do update set
              value = excluded.value,
              locked = excluded.locked,
              source = excluded.source,
              metadata = excluded.metadata,
              updated_at = now()
            """,
            (
                session_id,
                key_text,
                value_text,
                bool(raw.get("locked", True)) if isinstance(raw, dict) else True,
                str(raw.get("source", "state")) if isinstance(raw, dict) else "state",
                Jsonb(raw if isinstance(raw, dict) else {"raw": raw}),
            ),
        )

    projection = worldline.get("last_projection") or worldline.get("pending_projection")
    if projection:
        projection_text = projection.get("text") or projection.get("projection") if isinstance(projection, dict) else str(projection)
        projection_text = _clean_text(projection_text)
        validation = worldline.get("last_validation") or {}
        exists = db.execute(
            """
            select 1 from worldline_projections
            where session_id = %s and turn = %s and projection = %s
            limit 1
            """,
            (session_id, int(payload.get("turn") or 0), projection_text),
        ).fetchone()
        if projection_text and not exists:
            db.execute(
                """
                insert into worldline_projections(
                  session_id, turn, projection, validated, validation_status, variables_snapshot, metadata
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    int(payload.get("turn") or 0),
                    projection_text,
                    (validation.get("status") == "passed") if isinstance(validation, dict) else False,
                    validation.get("status", "none") if isinstance(validation, dict) else "none",
                    Jsonb(variables),
                    Jsonb(projection if isinstance(projection, dict) else {}),
                ),
            )


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def record_context_run(
    user_id: int,
    save_id: int,
    state: dict[str, Any],
    user_input: str,
    agent_result: dict[str, Any],
    bundle: dict[str, Any],
    retrieved_context: str,
    *,
    status: str = "done",
    error: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """记录一次上下文召回。status: running / done / stopped / failed。"""
    session = ensure_game_session(user_id, save_id, state)
    debug = bundle.get("debug") or {}
    with connect() as db:
        row = db.execute(
            """
            insert into context_runs(
              session_id, save_id, user_id, turn, user_input, agent_steps,
              curator_plan, layers, active_character_cards, active_worldbook,
              retrieved_chunks, estimated_tokens, cache_plan,
              status, error, duration_ms
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning *
            """,
            (
                session["id"],
                save_id,
                user_id,
                int(state.get("turn") or 0),
                user_input,
                Jsonb(agent_result.get("steps") or []),
                Jsonb(agent_result.get("curator_plan") or {}),
                Jsonb(debug.get("layers") or []),
                Jsonb(debug.get("active_character_cards") or []),
                Jsonb(debug.get("active_worldbook") or []),
                Jsonb(_retrieved_chunks_payload(retrieved_context)),
                int(debug.get("estimated_tokens") or 0),
                Jsonb(debug.get("cache_plan") or {}),
                status,
                error,
                int(duration_ms),
            ),
        ).fetchone()
    return expose(row)


def update_context_run_status(run_id: int, status: str, error: str = "", duration_ms: int | None = None) -> None:
    """更新已存在 context_run 的状态（如打断/失败转写）。"""
    init_db()
    with connect() as db:
        if duration_ms is None:
            db.execute(
                "update context_runs set status = %s, error = %s where id = %s",
                (status, error, run_id),
            )
        else:
            db.execute(
                "update context_runs set status = %s, error = %s, duration_ms = %s where id = %s",
                (status, error, int(duration_ms), run_id),
            )


def record_turn_messages(
    user_id: int,
    save_id: int,
    state: dict[str, Any],
    player_input: str,
    gm_output: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = ensure_game_session(user_id, save_id, state)
    turn = int(state.get("turn") or 0)
    with connect() as db:
        user_msg = db.execute(
            """
            insert into messages(session_id, save_id, turn, role, content, metadata)
            values (%s, %s, %s, 'user', %s, %s)
            returning *
            """,
            (session["id"], save_id, turn, player_input, Jsonb(metadata or {})),
        ).fetchone()
        gm_msg = db.execute(
            """
            insert into messages(session_id, save_id, turn, role, content, metadata)
            values (%s, %s, %s, 'assistant', %s, %s)
            returning *
            """,
            (session["id"], save_id, turn, gm_output, Jsonb(metadata or {})),
        ).fetchone()
    return {"user": expose(user_msg), "assistant": expose(gm_msg)}


def retrieve_runtime_context(
    query: str,
    *,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    top_k: int = 3,
    user_id: int | None = None,
) -> str:
    """按当前用户的 runtime 拿剧本 chunks。

    多用户安全：user_id 给定时严格按 user 读 runtime + 校验 save 归属。
    不给 user_id 仅在本地匿名（兼容旧逻辑），多用户场景一定要传。
    """
    meta = runtime.read_runtime(user_id=user_id)
    if not meta:
        return ""
    save_id = int(meta.get("save_id") or 0)
    if not save_id:
        return ""
    # 严格校验 runtime 属于当前 user
    if user_id and int(meta.get("user_id") or 0) != int(user_id):
        return ""
    with connect() as db:
        if user_id:
            save = db.execute(
                "select * from game_saves where id = %s and user_id = %s",
                (save_id, int(user_id)),
            ).fetchone()
        else:
            save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
        if not save:
            return ""
        return retrieve_script_context(
            int(save["script_id"]),
            query,
            chapter_min=chapter_min,
            chapter_max=chapter_max,
            top_k=top_k,
            db=db,
        )


def retrieve_script_context(
    script_id: int,
    query: str,
    *,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    top_k: int = 3,
    db=None,
) -> str:
    owns_connection = db is None
    if owns_connection:
        init_db()
        cm = connect()
        db = cm.__enter__()
    try:
        parts: list[str] = []
        fact_rows = db.execute(
            """
            select chapter, title, story_time_label, summary, events
            from chapter_facts
            where script_id = %s
              and (%s::integer is null or chapter >= %s)
              and (%s::integer is null or chapter <= %s)
            order by chapter
            limit %s
            """,
            (script_id, chapter_min, chapter_min, chapter_max, chapter_max, max(1, top_k + 2)),
        ).fetchall()
        if fact_rows:
            lines = []
            for row in fact_rows:
                events = row.get("events") or []
                event_text = "；".join(str(item.get("event", "")) for item in events[:2] if isinstance(item, dict))
                lines.append(
                    f"第{row['chapter']}章《{row['title']}》｜{row.get('story_time_label') or ''}\n"
                    f"摘要：{(row.get('summary') or '')[:180]}\n"
                    f"事件：{event_text[:220]}"
                )
            parts.append("=== Postgres ChapterFact ===\n" + "\n\n".join(lines))

        tokens = _query_tokens(query)
        chunk_rows = _search_chunks(db, script_id, tokens, chapter_min, chapter_max, top_k)
        if chunk_rows:
            parts.append(
                "=== Postgres 原文片段 ===\n"
                + "\n\n".join(
                    f"[第{row['chapter_index']}章片段]\n{row['content'][:360].strip()}"
                    for row in chunk_rows
                )
            )
        return "\n\n".join(parts)
    finally:
        if owns_connection:
            cm.__exit__(None, None, None)


def list_chapter_facts(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_chapter = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = db.execute(
            """
            select id, public_id, chapter, title, summary, story_phase, story_time_label,
                   scene_count, token_estimate, confidence, created_at, updated_at
            from chapter_facts
            where script_id = %s and (%s::integer is null or chapter > %s)
            order by chapter asc
            limit %s
            """,
            (script_id, before_chapter, before_chapter, page_limit + 1),
        ).fetchall()
    payload = page_payload(rows, page_limit)
    if payload["items"]:
        payload["page"]["next_cursor"] = str(payload["items"][-1]["chapter"]) if payload["page"]["has_more"] else None
    return payload


def list_character_cards(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = db.execute(
            """
            select * from character_cards
            where script_id = %s and (%s::bigint is null or id < %s)
            order by priority desc, id desc
            limit %s
            """,
            (script_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)


def get_character_card(user_id: int, script_id: int, card_id: int) -> dict[str, Any] | None:
    """单条剧本角色卡详情"""
    init_db()
    with connect() as db:
        _require_script(db, user_id, script_id)
        row = db.execute(
            "select * from character_cards where id = %s and script_id = %s",
            (card_id, script_id),
        ).fetchone()
    return expose(row) if row else None


def upsert_character_card(user_id: int, script_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """创建/更新剧本角色卡。card_id 给定就 update，否则 insert。"""
    init_db()
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("character.name 不能为空")
    card_id = payload.get("id")
    fields = {
        "name": name,
        "aliases": Jsonb(payload.get("aliases") or []),
        "identity": (payload.get("identity") or "").strip(),
        "appearance": (payload.get("appearance") or "").strip(),
        "personality": (payload.get("personality") or "").strip(),
        "speech_style": (payload.get("speech_style") or "").strip(),
        "current_status": (payload.get("current_status") or "").strip(),
        "secrets": (payload.get("secrets") or "").strip(),
        "sample_dialogue": Jsonb(payload.get("sample_dialogue") or []),
        "token_budget": int(payload.get("token_budget") or 450),
        "priority": int(payload.get("priority") or 100),
        "enabled": bool(payload.get("enabled", True)),
        "metadata": Jsonb(payload.get("metadata") or {}),
    }
    with connect() as db:
        script = _require_script(db, user_id, script_id)
        book = db.execute("select id from books where script_id = %s", (script_id,)).fetchone()
        if not book:
            raise ValueError("剧本 book 未初始化，先调一次 /api/scripts/{id}/knowledge/sync")
        book_id = int(book["id"])
        if card_id:
            owned = db.execute(
                "select 1 from character_cards where id = %s and script_id = %s",
                (int(card_id), script_id),
            ).fetchone()
            if not owned:
                raise ValueError("character_card 不存在或不属于该剧本")
            db.execute(
                """
                update character_cards set
                  name=%(name)s, aliases=%(aliases)s,
                  identity=%(identity)s, appearance=%(appearance)s,
                  personality=%(personality)s, speech_style=%(speech_style)s,
                  current_status=%(current_status)s, secrets=%(secrets)s,
                  sample_dialogue=%(sample_dialogue)s, token_budget=%(token_budget)s,
                  priority=%(priority)s, enabled=%(enabled)s, metadata=%(metadata)s,
                  row_version=row_version+1, updated_at=now()
                where id=%(id)s and script_id=%(script_id)s
                """,
                {**fields, "id": int(card_id), "script_id": script_id},
            )
            row = db.execute("select * from character_cards where id = %s", (int(card_id),)).fetchone()
        else:
            row = db.execute(
                """
                insert into character_cards(
                  book_id, script_id, name, aliases, identity, appearance, personality,
                  speech_style, current_status, secrets, sample_dialogue,
                  token_budget, priority, enabled, metadata
                ) values (
                  %(book_id)s, %(script_id)s, %(name)s, %(aliases)s, %(identity)s,
                  %(appearance)s, %(personality)s, %(speech_style)s, %(current_status)s,
                  %(secrets)s, %(sample_dialogue)s, %(token_budget)s,
                  %(priority)s, %(enabled)s, %(metadata)s
                )
                returning *
                """,
                {**fields, "book_id": book_id, "script_id": script_id},
            ).fetchone()
    return expose(row) or {}


def delete_character_card(user_id: int, script_id: int, card_id: int) -> dict[str, Any]:
    """删除剧本角色卡。"""
    init_db()
    with connect() as db:
        _require_script(db, user_id, script_id)
        cur = db.execute(
            "delete from character_cards where id = %s and script_id = %s returning id",
            (card_id, script_id),
        ).fetchone()
    return {"ok": True, "deleted": bool(cur), "id": card_id}


def set_character_card_enabled(user_id: int, script_id: int, card_id: int, enabled: bool) -> dict[str, Any]:
    """快捷启停切换，给前端"在检索中临时屏蔽这个角色"用。"""
    init_db()
    with connect() as db:
        _require_script(db, user_id, script_id)
        row = db.execute(
            """
            update character_cards set enabled = %s, row_version = row_version + 1, updated_at = now()
            where id = %s and script_id = %s
            returning *
            """,
            (bool(enabled), card_id, script_id),
        ).fetchone()
    if not row:
        raise ValueError("character_card 不存在")
    return expose(row) or {}


def list_worldbook_entries(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = db.execute(
            """
            select * from worldbook_entries
            where script_id = %s and (%s::bigint is null or id < %s)
            order by priority desc, id desc
            limit %s
            """,
            (script_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)


def list_context_runs(user_id: int, save_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        rows = db.execute(
            """
            select * from context_runs
            where save_id = %s and (%s::bigint is null or id < %s)
            order by id desc
            limit %s
            """,
            (save_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)


def list_worldline_variables(user_id: int, save_id: int) -> dict[str, Any]:
    """前端面板用：列出某存档的所有 worldline 变量。"""
    init_db()
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        rows = db.execute(
            """
            select wv.* from worldline_variables wv
            join game_sessions s on s.id = wv.session_id
            where s.save_id = %s
            order by wv.updated_at desc, wv.id desc
            """,
            (save_id,),
        ).fetchall()
    return {"items": [expose(r) for r in rows], "total": len(rows)}


def list_memories(user_id: int, save_id: int, bucket: str | None = None, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    """前端面板用：列出某存档的记忆，可按 bucket 过滤。"""
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        params: list[Any] = [save_id]
        where_clause = "s.save_id = %s"
        if bucket:
            where_clause += " and m.bucket = %s"
            params.append(bucket)
        where_clause += " and (%s::bigint is null or m.id < %s)"
        params.extend([before_id, before_id])
        params.append(page_limit + 1)
        rows = db.execute(
            f"""
            select m.* from memories m
            join game_sessions s on s.id = m.session_id
            where {where_clause}
            order by m.importance desc, m.id desc
            limit %s
            """,
            tuple(params),
        ).fetchall()
    return page_payload(rows, page_limit)


def _ensure_book(db, script: dict[str, Any]) -> dict[str, Any]:
    slug = _slugify(f"{script['id']}-{script['title']}")
    return db.execute(
        """
        insert into books(owner_id, script_id, title, slug, description, metadata)
        values (%s, %s, %s, %s, %s, %s)
        on conflict(script_id) do update set
          title = excluded.title,
          description = excluded.description,
          metadata = books.metadata || excluded.metadata,
          row_version = books.row_version + 1,
          updated_at = now()
        returning *
        """,
        (
            script["owner_id"],
            script["id"],
            script["title"],
            slug,
            script.get("description") or "",
            Jsonb({"source_path": script.get("source_path") or ""}),
        ),
    ).fetchone()


def _backfill_chapters_from_local_source(db, script: dict[str, Any]) -> int:
    chapters = _iter_chapters()
    if not chapters:
        return 0
    with db.cursor() as cur:
        cur.executemany(
            """
            insert into script_chapters(
              script_id, chapter_index, title, content, word_count,
              volume_title, source_marker, confidence
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict(script_id, chapter_index) do nothing
            """,
            [
                (
                    script["id"],
                    int(chapter["chapter"]),
                    str(chapter.get("title") or f"第{chapter['chapter']}章")[:200],
                    str(chapter.get("text") or ""),
                    len(str(chapter.get("text") or "")),
                    f"第{chapter.get('volume') or 0}卷" if chapter.get("volume") else "",
                    str(chapter.get("path") or ""),
                    0.95,
                )
                for chapter in chapters
            ],
        )
    db.execute(
        """
        update scripts
        set chapter_count = greatest(chapter_count, %s),
            word_count = (
              select coalesce(sum(word_count), 0)::integer
              from script_chapters
              where script_id = %s
            ),
            import_report = import_report || %s::jsonb,
            row_version = row_version + 1,
            updated_at = now()
        where id = %s
        """,
        (
            len(chapters),
            script["id"],
            Jsonb({"local_chapter_backfill": {"source": "正文/*.md", "chapters": len(chapters)}}),
            script["id"],
        ),
    )
    return len(chapters)


def _sync_character_cards(db, book: dict[str, Any], script: dict[str, Any], chars: dict[str, Any]) -> int:
    count = 0
    for name, card in chars.items():
        db.execute(
            """
            insert into character_cards(
              book_id, script_id, name, aliases, identity, appearance, personality,
              speech_style, current_status, secrets, sample_dialogue, token_budget,
              priority, enabled, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s)
            on conflict(script_id, name) do update set
              aliases = excluded.aliases,
              identity = excluded.identity,
              appearance = excluded.appearance,
              personality = excluded.personality,
              speech_style = excluded.speech_style,
              current_status = excluded.current_status,
              secrets = excluded.secrets,
              sample_dialogue = excluded.sample_dialogue,
              row_version = character_cards.row_version + 1,
              updated_at = now()
            """,
            (
                book["id"],
                script["id"],
                name,
                Jsonb(card.get("aliases") or []),
                card.get("identity") or "",
                card.get("appearance") or "",
                card.get("personality") or "",
                card.get("speech_style") or "",
                card.get("current_status") or "",
                card.get("secrets") or "",
                Jsonb(card.get("sample_dialogue") or []),
                int(card.get("token_budget") or 450),
                int(card.get("priority") or 100),
                Jsonb({"source": "indexes/characters.json"}),
            ),
        )
        count += 1
    return count


def _sync_worldbook_entries(db, book: dict[str, Any], script: dict[str, Any], world: dict[str, Any]) -> int:
    entries = _worldbook_seed_entries(world)
    for entry in entries:
        db.execute(
            """
            insert into worldbook_entries(
              book_id, script_id, title, content, keys, regex_keys, priority,
              token_budget, insertion_position, sticky_turns, cooldown_turns,
              probability, character_filter, scene_filter, enabled, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s)
            on conflict(script_id, title) do update set
              content = excluded.content,
              keys = excluded.keys,
              regex_keys = excluded.regex_keys,
              priority = excluded.priority,
              token_budget = excluded.token_budget,
              insertion_position = excluded.insertion_position,
              row_version = worldbook_entries.row_version + 1,
              updated_at = now()
            """,
            (
                book["id"],
                script["id"],
                entry["title"],
                entry["content"],
                Jsonb(entry["keys"]),
                Jsonb(entry.get("regex_keys") or []),
                entry["priority"],
                entry["token_budget"],
                entry["insertion_position"],
                entry["sticky_turns"],
                entry["cooldown_turns"],
                entry["probability"],
                Jsonb(entry.get("character_filter") or []),
                Jsonb(entry.get("scene_filter") or []),
                Jsonb({"source": "indexes/world.json"}),
            ),
        )
    return len(entries)


def _upsert_document(db, book: dict[str, Any], script: dict[str, Any], chapter: dict[str, Any]) -> dict[str, Any]:
    return db.execute(
        """
        insert into documents(book_id, script_id, chapter_id, source_kind, source_ref, title, content, metadata)
        values (%s, %s, %s, 'chapter', %s, %s, %s, %s)
        on conflict(book_id, source_kind, source_ref) do update set
          chapter_id = excluded.chapter_id,
          title = excluded.title,
          content = excluded.content,
          metadata = excluded.metadata,
          row_version = documents.row_version + 1,
          updated_at = now()
        returning *
        """,
        (
            book["id"],
            script["id"],
            chapter["id"],
            str(chapter["chapter_index"]),
            chapter["title"],
            chapter["content"],
            Jsonb({
                "chapter_index": chapter["chapter_index"],
                "volume_title": chapter.get("volume_title") or "",
                "source_marker": chapter.get("source_marker") or "",
            }),
        ),
    ).fetchone()


def _insert_chunk(db, book: dict[str, Any], script: dict[str, Any], chapter: dict[str, Any], document: dict[str, Any], chunk_index: int, content: str) -> None:
    db.execute(
        """
        insert into document_chunks(
          document_id, book_id, script_id, chapter_id, chapter_index,
          chunk_index, content, token_count, metadata
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            document["id"],
            book["id"],
            script["id"],
            chapter["id"],
            chapter["chapter_index"],
            chunk_index,
            content,
            max(1, len(content) // 2),
            Jsonb({"content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]}),
        ),
    )


def _upsert_chapter_fact(db, book: dict[str, Any], script: dict[str, Any], chapter: dict[str, Any], document: dict[str, Any], fact: dict[str, Any]) -> None:
    db.execute(
        """
        insert into chapter_facts(
          book_id, script_id, document_id, chapter_id, chapter, title, viewpoint,
          summary, story_phase, story_time_label, scene_count, token_estimate,
          characters, locations, factions, concepts, items, relationships, events,
          confidence, metadata
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict(script_id, chapter) do update set
          document_id = excluded.document_id,
          chapter_id = excluded.chapter_id,
          title = excluded.title,
          viewpoint = excluded.viewpoint,
          summary = excluded.summary,
          story_phase = excluded.story_phase,
          story_time_label = excluded.story_time_label,
          scene_count = excluded.scene_count,
          token_estimate = excluded.token_estimate,
          characters = excluded.characters,
          locations = excluded.locations,
          factions = excluded.factions,
          concepts = excluded.concepts,
          items = excluded.items,
          relationships = excluded.relationships,
          events = excluded.events,
          confidence = excluded.confidence,
          metadata = excluded.metadata,
          row_version = chapter_facts.row_version + 1,
          updated_at = now()
        """,
        (
            book["id"],
            script["id"],
            document["id"],
            chapter["id"],
            fact["chapter"],
            fact["title"],
            fact["viewpoint"],
            fact["summary"],
            fact["story_phase"],
            fact["story_time_label"],
            fact["scene_count"],
            fact["token_estimate"],
            Jsonb(fact["characters"]),
            Jsonb(fact["locations"]),
            Jsonb(fact["factions"]),
            Jsonb(fact["concepts"]),
            Jsonb(fact["items"]),
            Jsonb(fact["relationships"]),
            Jsonb(fact["events"]),
            fact["confidence"],
            Jsonb({"source": "deterministic_import"}),
        ),
    )


def _fact_from_chapter(
    chapter: dict[str, Any],
    summaries: dict[str, Any],
    known_names: dict[str, str],
    known_locations: list[str],
    known_concepts: list[str],
) -> dict[str, Any]:
    return _extract_fact(
        {
            "chapter": int(chapter["chapter_index"]),
            "title": chapter["title"],
            "volume": 0,
            "path": f"script:{chapter['script_id']}/chapter:{chapter['chapter_index']}",
            "text": chapter["content"],
        },
        summaries,
        known_names,
        known_locations,
        known_concepts,
    )


def _worldbook_seed_entries(world: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if world.get("setting"):
        entries.append(_wb("世界基础设定", ["世界", "设定", "薇瑟", "地球"], 100, world["setting"]))
    if world.get("current_situation"):
        entries.append(_wb("当前局势", ["局势", "图卢兹", "柏林", "战报"], 96, world["current_situation"]))
    for title, content in (world.get("key_factions") or {}).items():
        entries.append(_wb(title, _keys_for(title, content), 82, content))
    for title, content in (world.get("key_concepts") or {}).items():
        entries.append(_wb(title, _keys_for(title, content), 78, content))
    current_berlin = world.get("current_berlin") or {}
    if current_berlin:
        content = "\n".join([
            f"氛围：{current_berlin.get('atmosphere', '')}",
            f"风险等级：{current_berlin.get('risk_level', '')}",
            "在场势力：" + "；".join(current_berlin.get("power_presence") or []),
        ])
        entries.append(_wb("柏林战时暗流", ["柏林", "战役", "大西洋", "特洛耶德"], 92, content))
    return entries


def _wb(title: str, keys: list[str], priority: int, content: str) -> dict[str, Any]:
    return {
        "title": title,
        "keys": keys,
        "regex_keys": [],
        "priority": priority,
        "token_budget": 600,
        "insertion_position": "worldbook",
        "sticky_turns": 0,
        "cooldown_turns": 0,
        "probability": 100.0,
        "character_filter": [],
        "scene_filter": [],
        "content": content or "",
    }


def _chunk_text(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            window = text[start:end]
            split_at = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("！"), window.rfind("？"))
            if split_at > CHUNK_CHARS // 2:
                end = start + split_at + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _search_chunks(db, script_id: int, tokens: list[str], chapter_min: int | None, chapter_max: int | None, top_k: int) -> list[dict[str, Any]]:
    """检索：vector + BM25-like 双路。

    1. 如果有 query 的 vector embedding（_embed_query 拿到），且 document_chunks 有 embedding_vec，
       走 vector 余弦距离 ORDER BY embedding_vec <=> %s。
    2. 否则走原来的 ILIKE 词频。

    vector 不可用时 _embed_query 返 None，自动退化。
    """
    if not tokens:
        return []
    # 试 vector 路径
    vector_query = _embed_query(" ".join(tokens))
    if vector_query is not None and _vector_column_exists(db, "document_chunks"):
        try:
            query = """
                select id, chapter_index, content,
                       (1 - (embedding_vec <=> %s::vector)) as score
                from document_chunks
                where script_id = %s
                  and embedding_vec is not null
                  and (%s::integer is null or chapter_index >= %s)
                  and (%s::integer is null or chapter_index <= %s)
                order by embedding_vec <=> %s::vector
                limit %s
            """
            return db.execute(query, (
                vector_query, script_id,
                chapter_min, chapter_min, chapter_max, chapter_max,
                vector_query, max(1, min(top_k, 8)),
            )).fetchall()
        except Exception:
            pass  # vector 失败回退 ILIKE

    # 原 ILIKE 路径
    score_clauses = []
    where_clauses = []
    score_params: list[Any] = []
    where_params: list[Any] = []
    for token in tokens[:8]:
        pattern = f"%{token}%"
        score_clauses.append("case when content ilike %s then 1 else 0 end")
        where_clauses.append("content ilike %s")
        score_params.append(pattern)
        where_params.append(pattern)
    query = f"""
        select id, chapter_index, content,
               ({' + '.join(score_clauses)}) as score
        from document_chunks
        where script_id = %s
          and (%s::integer is null or chapter_index >= %s)
          and (%s::integer is null or chapter_index <= %s)
          and ({' or '.join(where_clauses)})
        order by score desc, chapter_index asc, chunk_index asc
        limit {max(1, min(top_k, 8))}
    """
    params = score_params + [script_id, chapter_min, chapter_min, chapter_max, chapter_max] + where_params
    return db.execute(query, tuple(params)).fetchall()


_VEC_COLUMN_CACHE: dict[str, bool] = {}


def _vector_column_exists(db, table: str) -> bool:
    if table in _VEC_COLUMN_CACHE:
        return _VEC_COLUMN_CACHE[table]
    try:
        row = db.execute(
            "select 1 from information_schema.columns "
            "where table_name = %s and column_name = 'embedding_vec'",
            (table,),
        ).fetchone()
        _VEC_COLUMN_CACHE[table] = bool(row)
    except Exception:
        _VEC_COLUMN_CACHE[table] = False
    return _VEC_COLUMN_CACHE[table]


def _embed_query(text: str) -> str | None:
    """把 query 文本转 vector(768) 字符串。

    生产应接 Vertex/Anthropic/OpenAI 的 embedding API。当前简化：
    返回 None 让上层走 ILIKE 兜底。未来在这里接 embedding 服务即可全栈打通。
    """
    return None


def _query_tokens(query: str) -> list[str]:
    text = re.sub(r"[^一-鿿A-Za-z0-9_]", " ", query or "")
    tokens = {part for part in text.split() if len(part) >= 2}
    compact = re.sub(r"\s+", "", text)
    for index in range(max(0, len(compact) - 1)):
        bg = compact[index:index + 2]
        if re.fullmatch(r"[\u4e00-\u9fff]{2}", bg):
            tokens.add(bg)
    return sorted(tokens, key=len, reverse=True)[:16]


def _retrieved_chunks_payload(text: str) -> list[dict[str, Any]]:
    blocks = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
    return [{"preview": block[:240], "chars": len(block)} for block in blocks[:12]]


def _keys_for(title: str, content: str) -> list[str]:
    values = {title}
    values.update(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", f"{title} {content or ''}")[:8])
    return [item for item in values if item][:10]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "-", text.strip()).strip("-").lower()
    return slug or "book"


def _cursor_int(value: str | int | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _require_script(db, user_id: int, script_id: int) -> dict[str, Any]:
    row = db.execute("select * from scripts where id = %s and owner_id = %s", (script_id, user_id)).fetchone()
    if not row:
        raise ValueError("无权访问该剧本")
    return row
