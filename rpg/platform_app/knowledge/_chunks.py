from __future__ import annotations

import hashlib
from typing import Any

from psycopg.types.json import Jsonb

from chapter_fact_indexer import _extract_fact
from platform_app.db import expose


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
