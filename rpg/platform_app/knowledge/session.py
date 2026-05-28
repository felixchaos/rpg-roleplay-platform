from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from core.logging import get_logger
from platform_app.db import connect, expose, init_db
from platform_app.knowledge._session_repo import _db_upsert_game_session
from platform_app.knowledge._sync import _ensure_book
from platform_app.knowledge._utils import _clean_text

log = get_logger(__name__)


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
        session = _db_upsert_game_session(
            db, save_id, book["id"], save["script_id"], user_id,
            save["title"] or save["script_title"], payload,
        )
        _sync_session_state(db, session, book["id"], user_id, payload)
    return expose(session)  # type: ignore[return-value]


def _aggregate_characters_from_facts(script_id: int) -> dict[str, Any]:
    """从 chapter_facts.characters 纯聚合角色卡候选,不调 LLM。

    返回与 _load_characters() 兼容的 chars dict:
    {name: {name, identity, appearance, personality, speech_style,
            secrets, sample_dialogue, priority, aliases}}
    过滤 count < 2 的路人,取 top 50 by priority(总出场次数)。
    """
    out: dict[str, Any] = {}
    with connect() as db:
        rows = db.execute(
            "select characters from chapter_facts where script_id=%s and characters is not null",
            (script_id,),
        ).fetchall()

    for row in rows:
        characters = row.get("characters") if isinstance(row, dict) else row[0]
        if not characters or not isinstance(characters, list):
            continue
        for ch in characters:
            if not isinstance(ch, dict):
                continue
            name = (ch.get("name") or "").strip()
            if not name or len(name) < 2:
                continue
            count = int(ch.get("count") or 1)
            if name not in out:
                out[name] = {
                    "name": name,
                    "identity": "",
                    "appearance": "",
                    "personality": "",
                    "speech_style": "",
                    "current_status": "",
                    "secrets": "",
                    "sample_dialogue": [],
                    "priority": count,
                    "aliases": [],
                }
            else:
                out[name]["priority"] += count

    # 过滤路人(total count < 2)
    filtered = {name: card for name, card in out.items() if card["priority"] >= 2}

    # 取 top 50 by priority
    top = sorted(filtered.items(), key=lambda kv: -kv[1]["priority"])[:50]
    return dict(top)


def sync_script_knowledge(user_id: int, script_id: int, *, rebuild: bool = False) -> dict[str, Any]:
    """Build the Postgres knowledge layer for one imported script.

    The import path stays deterministic and cheap: it creates documents/chunks,
    ChapterFact rows, character cards, and worldbook entries without requiring an
    LLM pass. A later refinement pass can overwrite the same rows.
    """
    from chapter_fact_indexer import (
        _known_concepts,
        _known_locations,
        _known_names,
        _load_summaries,
    )
    from context_engine.loaders import (
        _load_characters,
        _load_world,
    )
    from platform_app.db import init_db as _init_db
    from platform_app.knowledge._chunks import (
        _fact_from_chapter,
        _insert_chunk,
        _upsert_chapter_fact,
        _upsert_document,
    )
    from platform_app.knowledge._sync import (
        _backfill_chapters_from_local_source,
        _sync_character_cards,
        _sync_worldbook_entries,
    )
    from platform_app.knowledge._utils import _chunk_text

    _init_db()
    # task 80: 优先按 script_id scope 拉,新书 DB 空则 chars/world 为 {} (不再回退柏林 JSON 污染)
    chars = _load_characters(script_id=script_id) or {}
    world = _load_world(script_id=script_id) or {}
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
        # P0 fix #3: 新书 chars 为空时,从 chapter_facts 纯聚合写 character_cards (不调 LLM)
        if not chars:
            try:
                chars = _aggregate_characters_from_facts(script_id)
                log.info(
                    "[sync] 从 chapter_facts 聚合 %d 个角色 for script %s",
                    len(chars),
                    script_id,
                )
            except Exception as e:
                log.warning("[sync] 聚合 characters from facts failed: %s", e)
                chars = {}
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

    # P0 fix #2: chapter_facts 完成后,聚合 script_timeline_anchors
    try:
        from script_timeline import rebuild_timeline_anchors
        rebuild_timeline_anchors(script_id)
    except Exception as e:
        log.warning("[sync_script_knowledge] rebuild_timeline_anchors failed: %s", e)

    # P0 fix #1: chapter_facts 完成后,聚合 phase_digests
    try:
        from scripts.aggregate_phase_digests import aggregate_for_script
        aggregate_for_script(script_id)
    except Exception as e:
        log.warning("[sync_script_knowledge] aggregate_for_script failed: %s", e)

    with connect() as db:
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
