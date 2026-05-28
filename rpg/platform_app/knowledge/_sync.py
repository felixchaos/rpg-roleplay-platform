from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from chapter_fact_indexer import _iter_chapters

from platform_app.knowledge._utils import _slugify, _worldbook_seed_entries


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
