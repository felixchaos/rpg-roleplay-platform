"""platform_app.knowledge.script_pack — 剧本 export/import pack。

Pack 格式 (zip):
  manifest.json          — {format_version, exported_at, script_title, script_id_origin}
  script.json            — scripts row (脱敏 owner_id)
  chapters.jsonl         — script_chapters (key fields)
  chapter_facts.jsonl    — chapter_facts (key fields)
  character_cards.jsonl  — character_cards (key fields)
  worldbook.jsonl        — worldbook_entries (key fields)
  overrides.json         — script_overrides.data
  documents.jsonl        — documents (optional, no chunks)

不含: document_chunks / embeddings (收件方重建), saves, credentials。
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any

from platform_app.db import connect

FORMAT_VERSION = 1
MAX_ZIP_BYTES = 50 * 1024 * 1024  # 50 MB


# ── Export ────────────────────────────────────────────────────────────────────

def export_script_pack(script_id: int, user_id: int) -> tuple[bytes, str]:
    """导出指定 script 为 zip 包。返回 (zip_bytes, filename)。

    校验 ownership; 不含 chunks/embeddings (收件方重建)。
    """
    with connect() as db:
        # 1. 校验 ownership
        script_row = db.execute(
            "SELECT * FROM scripts WHERE id = %s AND owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script_row:
            raise PermissionError("script not found or not owner")

        script_dict = dict(script_row)

        # 2. 收集 chapters
        chapters = db.execute(
            """
            SELECT id, chapter_index, title, content, word_count, volume_title, source_marker, confidence
            FROM script_chapters
            WHERE script_id = %s
            ORDER BY chapter_index
            """,
            (script_id,),
        ).fetchall()
        chapters = [dict(r) for r in chapters]

        # 3. chapter_facts — 按 chapter (index) 导出核心字段
        facts = db.execute(
            """
            SELECT id, chapter, title, viewpoint, summary, story_phase, story_time_label,
                   scene_count, token_estimate, confidence,
                   characters, locations, factions, concepts, items, relationships, events,
                   metadata
            FROM chapter_facts
            WHERE script_id = %s
            ORDER BY chapter
            """,
            (script_id,),
        ).fetchall()
        facts = [dict(r) for r in facts]

        # 4. character_cards
        cards = db.execute(
            """
            SELECT id, name, aliases, identity, appearance, personality, speech_style,
                   current_status, secrets, sample_dialogue, token_budget, priority,
                   enabled, metadata
            FROM character_cards
            WHERE script_id = %s
            ORDER BY priority DESC, id
            """,
            (script_id,),
        ).fetchall()
        cards = [dict(r) for r in cards]

        # 5. worldbook_entries
        wb = db.execute(
            """
            SELECT id, title, content, keys, regex_keys, priority, token_budget,
                   insertion_position, sticky_turns, cooldown_turns, probability,
                   character_filter, scene_filter, enabled, metadata
            FROM worldbook_entries
            WHERE script_id = %s
            ORDER BY priority DESC, id
            """,
            (script_id,),
        ).fetchall()
        wb = [dict(r) for r in wb]

        # 6. documents (no chunks/embeddings)
        docs = db.execute(
            """
            SELECT id, source_kind, source_ref, title, content, metadata
            FROM documents
            WHERE script_id = %s
            ORDER BY id
            """,
            (script_id,),
        ).fetchall()
        docs = [dict(r) for r in docs]

        # 7. overrides
        ov_row = db.execute(
            "SELECT data FROM script_overrides WHERE script_id = %s",
            (script_id,),
        ).fetchone()
        overrides = dict(ov_row["data"]) if ov_row and ov_row["data"] else {}

    # 8. 构建 zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "format_version": FORMAT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "script_title": script_dict.get("title"),
            "script_id_origin": script_id,
            # 不含 owner_id / user_id
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("script.json", _dump_script_row(script_dict))
        zf.writestr("chapters.jsonl", _dump_jsonl(chapters))
        zf.writestr("chapter_facts.jsonl", _dump_jsonl(facts))
        zf.writestr("character_cards.jsonl", _dump_jsonl(cards))
        zf.writestr("worldbook.jsonl", _dump_jsonl(wb))
        zf.writestr("overrides.json", json.dumps(overrides, ensure_ascii=False, default=str, indent=2))
        zf.writestr("documents.jsonl", _dump_jsonl(docs))

    title_slug = str(script_dict.get("title") or "unknown").replace("/", "-").replace("\\", "-")[:40]
    filename = f"script_{script_id}_{title_slug}.zip"
    return buf.getvalue(), filename


# ── Import ────────────────────────────────────────────────────────────────────

def import_script_pack(zip_bytes: bytes, user_id: int) -> dict[str, Any]:
    """导入剧本 pack zip。返回 {ok, script_id, warnings}。"""
    # 1. 校验大小
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise ValueError(f"zip too large (max {MAX_ZIP_BYTES // 1024 // 1024}MB)")

    # 2. 解压 + zip-slip 防护
    try:
        zf_handle = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid zip file: {exc}") from exc

    with zf_handle as zf:
        # zip-slip 防护: entry path 不含 ".." 或绝对路径
        for name in zf.namelist():
            parts = name.replace("\\", "/").split("/")
            if name.startswith("/") or ".." in parts:
                raise ValueError(f"zip-slip attempt detected: {name!r}")

        # 3. 读 manifest
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except KeyError as exc:
            raise ValueError("missing manifest.json in pack") from exc

        if manifest.get("format_version") != FORMAT_VERSION:
            raise ValueError(
                f"unsupported format_version: {manifest.get('format_version')!r} "
                f"(expected {FORMAT_VERSION})"
            )

        # 4. 读各文件
        try:
            script_data = json.loads(zf.read("script.json").decode("utf-8"))
        except KeyError as exc:
            raise ValueError("missing script.json in pack") from exc

        chapters = _read_jsonl(zf, "chapters.jsonl")
        facts = _read_jsonl(zf, "chapter_facts.jsonl")
        cards = _read_jsonl(zf, "character_cards.jsonl")
        wb = _read_jsonl(zf, "worldbook.jsonl")
        docs = _read_jsonl(zf, "documents.jsonl")

        try:
            overrides: dict = json.loads(zf.read("overrides.json").decode("utf-8"))
        except KeyError:
            overrides = {}

    warnings: list[str] = []

    # 5. 写 DB
    with connect() as db:
        # 5a. 创建新 script — owner_id 强制 current_user
        title = str(script_data.get("title") or "Imported script")
        description = str(script_data.get("description") or "")
        chapter_count = len(chapters)
        word_count = sum(int(c.get("word_count") or 0) for c in chapters)

        new_script = db.execute(
            """
            INSERT INTO scripts (owner_id, title, description, source_path,
                                 chapter_count, word_count)
            VALUES (%s, %s, %s, '', %s, %s)
            RETURNING id
            """,
            (user_id, title, description, chapter_count, word_count),
        ).fetchone()
        new_script_id: int = int(new_script["id"])

        # 5b. 写入 chapters，建 old_id → new_id 映射
        old_chapter_id_to_new: dict[int, int] = {}
        for ch in chapters:
            new_ch = db.execute(
                """
                INSERT INTO script_chapters
                  (script_id, chapter_index, title, content, word_count,
                   volume_title, source_marker, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    new_script_id,
                    int(ch.get("chapter_index") or 0),
                    str(ch.get("title") or ""),
                    str(ch.get("content") or ""),
                    int(ch.get("word_count") or 0),
                    str(ch.get("volume_title") or ""),
                    str(ch.get("source_marker") or ""),
                    float(ch.get("confidence") or 0.0),
                ),
            ).fetchone()
            if ch.get("id") is not None:
                old_chapter_id_to_new[int(ch["id"])] = int(new_ch["id"])

        # 5c. 写 chapter_facts — 不依赖 book_id/document_id (允许为 NULL 直到知识同步)
        #     用 chapter (index) 作 conflict key
        for fact in facts:
            # 映射 chapter_id
            old_ch_id = fact.get("chapter_id")
            new_ch_id = old_chapter_id_to_new.get(int(old_ch_id)) if old_ch_id else None
            try:
                db.execute(
                    """
                    INSERT INTO chapter_facts
                      (book_id, script_id, document_id, chapter_id, chapter, title,
                       viewpoint, summary, story_phase, story_time_label, scene_count,
                       token_estimate, characters, locations, factions, concepts,
                       items, relationships, events, confidence, metadata)
                    SELECT b.id, %s, NULL, %s, %s, %s,
                           %s, %s, %s, %s, %s,
                           %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                           %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb
                    FROM books b
                    WHERE b.script_id = %s
                    ON CONFLICT (script_id, chapter) DO NOTHING
                    """,
                    (
                        new_script_id,
                        new_ch_id,
                        int(fact.get("chapter") or 0),
                        str(fact.get("title") or ""),
                        str(fact.get("viewpoint") or ""),
                        str(fact.get("summary") or ""),
                        str(fact.get("story_phase") or ""),
                        str(fact.get("story_time_label") or ""),
                        int(fact.get("scene_count") or 0),
                        int(fact.get("token_estimate") or 0),
                        json.dumps(fact.get("characters") or [], ensure_ascii=False, default=str),
                        json.dumps(fact.get("locations") or [], ensure_ascii=False, default=str),
                        json.dumps(fact.get("factions") or [], ensure_ascii=False, default=str),
                        json.dumps(fact.get("concepts") or [], ensure_ascii=False, default=str),
                        json.dumps(fact.get("items") or [], ensure_ascii=False, default=str),
                        json.dumps(fact.get("relationships") or [], ensure_ascii=False, default=str),
                        json.dumps(fact.get("events") or [], ensure_ascii=False, default=str),
                        float(fact.get("confidence") or 0.5),
                        json.dumps(fact.get("metadata") or {}, ensure_ascii=False, default=str),
                        new_script_id,  # for books subquery
                    ),
                )
            except Exception as exc:
                warnings.append(f"chapter_fact chapter={fact.get('chapter')} skipped: {exc}")

        # 5d. character_cards — 需要 book_id
        #     新 script 没有 books 行 (知识同步前),先尝试插入; 无 book 则 skip + warn
        book_row = db.execute(
            "SELECT id FROM books WHERE script_id = %s",
            (new_script_id,),
        ).fetchone()
        if book_row:
            book_id = int(book_row["id"])
            for card in cards:
                try:
                    db.execute(
                        """
                        INSERT INTO character_cards
                          (book_id, script_id, name, aliases, identity, appearance,
                           personality, speech_style, current_status, secrets,
                           sample_dialogue, token_budget, priority, enabled, metadata)
                        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s,
                                %s::jsonb, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (script_id, name) DO NOTHING
                        """,
                        (
                            book_id, new_script_id,
                            str(card.get("name") or ""),
                            json.dumps(card.get("aliases") or [], ensure_ascii=False, default=str),
                            str(card.get("identity") or ""),
                            str(card.get("appearance") or ""),
                            str(card.get("personality") or ""),
                            str(card.get("speech_style") or ""),
                            str(card.get("current_status") or ""),
                            str(card.get("secrets") or ""),
                            json.dumps(card.get("sample_dialogue") or [], ensure_ascii=False, default=str),
                            int(card.get("token_budget") or 450),
                            int(card.get("priority") or 100),
                            bool(card.get("enabled", True)),
                            json.dumps(card.get("metadata") or {}, ensure_ascii=False, default=str),
                        ),
                    )
                except Exception as exc:
                    warnings.append(f"character_card {card.get('name')!r} skipped: {exc}")
        else:
            if cards:
                warnings.append(
                    f"{len(cards)} character_cards skipped (no books row yet; "
                    "run /api/scripts/{id}/knowledge/sync to rebuild)"
                )

        # 5e. worldbook_entries
        if book_row:
            for entry in wb:
                try:
                    db.execute(
                        """
                        INSERT INTO worldbook_entries
                          (book_id, script_id, title, content, keys, regex_keys,
                           priority, token_budget, insertion_position, sticky_turns,
                           cooldown_turns, probability, character_filter, scene_filter,
                           enabled, metadata)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb,
                                %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                                %s, %s::jsonb)
                        ON CONFLICT (script_id, title) DO NOTHING
                        """,
                        (
                            book_id, new_script_id,
                            str(entry.get("title") or ""),
                            str(entry.get("content") or ""),
                            json.dumps(entry.get("keys") or [], ensure_ascii=False, default=str),
                            json.dumps(entry.get("regex_keys") or [], ensure_ascii=False, default=str),
                            int(entry.get("priority") or 50),
                            int(entry.get("token_budget") or 600),
                            str(entry.get("insertion_position") or "worldbook"),
                            int(entry.get("sticky_turns") or 0),
                            int(entry.get("cooldown_turns") or 0),
                            float(entry.get("probability") or 100.0),
                            json.dumps(entry.get("character_filter") or [], ensure_ascii=False, default=str),
                            json.dumps(entry.get("scene_filter") or [], ensure_ascii=False, default=str),
                            bool(entry.get("enabled", True)),
                            json.dumps(entry.get("metadata") or {}, ensure_ascii=False, default=str),
                        ),
                    )
                except Exception as exc:
                    warnings.append(f"worldbook entry {entry.get('title')!r} skipped: {exc}")
        else:
            if wb:
                warnings.append(
                    f"{len(wb)} worldbook_entries skipped (no books row yet; "
                    "run /api/scripts/{id}/knowledge/sync to rebuild)"
                )

        # 5g. documents (no chunks)
        if book_row and docs:
            for doc in docs:
                # map chapter_id
                old_ch_id = doc.get("chapter_id")
                new_ch_id = old_chapter_id_to_new.get(int(old_ch_id)) if old_ch_id else None
                try:
                    db.execute(
                        """
                        INSERT INTO documents
                          (book_id, script_id, chapter_id, source_kind, source_ref,
                           title, content, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (book_id, source_kind, source_ref) DO NOTHING
                        """,
                        (
                            book_id, new_script_id, new_ch_id,
                            str(doc.get("source_kind") or "chapter"),
                            str(doc.get("source_ref") or ""),
                            str(doc.get("title") or ""),
                            str(doc.get("content") or ""),
                            json.dumps(doc.get("metadata") or {}, ensure_ascii=False, default=str),
                        ),
                    )
                except Exception as exc:
                    warnings.append(f"document source_ref={doc.get('source_ref')!r} skipped: {exc}")
        elif docs and not book_row:
            warnings.append(
                f"{len(docs)} documents skipped (no books row yet; "
                "run /api/scripts/{id}/knowledge/sync to rebuild)"
            )

    # 6. overrides — must be after outer `with connect()` commits the scripts row
    if overrides:
        from platform_app.knowledge.script_overrides import upsert_overrides
        upsert_overrides(new_script_id, overrides)

    return {"ok": True, "script_id": new_script_id, "warnings": warnings}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dump_jsonl(rows: list[dict]) -> str:
    return "\n".join(
        json.dumps(r, ensure_ascii=False, default=str) for r in rows
    )


def _read_jsonl(zf: zipfile.ZipFile, name: str) -> list[dict]:
    try:
        text = zf.read(name).decode("utf-8")
    except KeyError:
        return []
    return [
        json.loads(line)
        for line in text.split("\n")
        if line.strip()
    ]


def _dump_script_row(row: dict) -> str:
    d = {k: v for k, v in row.items()}
    # 脱敏 owner_id
    d.pop("owner_id", None)
    return json.dumps(d, ensure_ascii=False, default=str, indent=2)
