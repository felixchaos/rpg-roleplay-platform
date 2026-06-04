"""platform_app.knowledge.script_pack — 剧本 export/import pack。

Pack 格式 (zip):
  manifest.json              — {format_version, exported_at, script_title, script_id_origin}
  script.json                — scripts row (脱敏 owner_id)
  chapters.jsonl             — script_chapters (key fields)
  chapter_facts.jsonl        — chapter_facts (key fields)
  character_cards.jsonl      — character_cards (key fields)
  worldbook.jsonl            — worldbook_entries (key fields)
  overrides.json             — script_overrides.data
  documents.jsonl            — documents (optional, no chunks)
  chunks.jsonl               — document_chunks (仅 include_chunks=true)

v2 新增 (task 67 — 5 张表,游戏体验依赖):
  kb_canon_entities.jsonl    — 核心实体 (GM retrieval / KB tools 依赖)
  timeline_anchors.jsonl     — script_timeline_anchors (剧情锚点)
  phase_digests.jsonl        — script 级阶段摘要 (5 段统一标签)
  worldlines.jsonl           — script_worldlines (世界树脊柱本体)
  worldline_nodes.jsonl      — script_worldline_nodes (脊柱节点)

不含: embeddings (vector 列,收件方 backfill), saves, credentials, document_chunks(可选)。
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from platform_app.db import connect

FORMAT_VERSION = 2  # task 67: v1 → v2,加 5 张世界树/锚点/digest 表
CHUNKS_VERSION = 1  # chunks 序列化格式版本; 未来改变字段时递增
MAX_ZIP_BYTES = 50 * 1024 * 1024  # 50 MB(压缩态上限)
MAX_EXPANDED_BYTES = 500 * 1024 * 1024  # 解压后总量上限,防 zip 炸弹(CWE-409)
MAX_MEMBER_BYTES = 200 * 1024 * 1024  # 单成员解压上限
MAX_JSONL_ROWS = 500_000  # 单个 JSONL 行数上限,防 materialize 打爆内存


def _safe_member_read(zf: zipfile.ZipFile, name: str) -> bytes:
    """有界解压单个成员:ZipInfo 预检 + 实读上限,双重防谎报 header 的炸弹。"""
    info = zf.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise ValueError(f"成员解压后过大: {name}")
    with zf.open(name) as fh:
        data = fh.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise ValueError(f"成员解压超限: {name}")
    return data


# ── Export ────────────────────────────────────────────────────────────────────

def export_script_pack(
    script_id: int,
    user_id: int,
    include_chunks: bool = False,
) -> tuple[bytes, str]:
    """导出指定 script 为 zip 包。返回 (zip_bytes, filename)。

    include_chunks=True 时把 document_chunks 一并打包 (不含 embedding_vec)。
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

        # task 67: 7-bis. v2 新增 5 张表 — kb_canon / timeline_anchors / phase_digests / worldlines / nodes
        # 全部排除 embedding 列(vector 类型,收件方 backfill);剩下都是文本/jsonb 安全导出。
        canon_entities = db.execute(
            """
            SELECT logical_key, name, aliases, type, summary, attrs,
                   first_revealed_chapter, public_knowledge, importance, metadata,
                   coalesce(full_name, '') as full_name,
                   coalesce(identity, '') as identity,
                   coalesce(background, '') as background,
                   coalesce(entity_subtype, '') as entity_subtype,
                   coalesce(parent_logical_key, '') as parent_logical_key
            FROM kb_canon_entities
            WHERE script_id = %s
            ORDER BY logical_key
            """,
            (script_id,),
        ).fetchall()
        canon_entities = [dict(r) for r in canon_entities]

        timeline_anchors = db.execute(
            """
            SELECT story_phase, story_time_label, chapter_min, chapter_max, chapter_count,
                   sample_title, sample_summary, keywords, confidence
            FROM script_timeline_anchors
            WHERE script_id = %s
            ORDER BY chapter_min, story_phase, story_time_label
            """,
            (script_id,),
        ).fetchall()
        timeline_anchors = [dict(r) for r in timeline_anchors]

        phase_digests = db.execute(
            """
            SELECT phase_label, chapter_min, chapter_max, summary,
                   key_events, key_locations, key_characters,
                   story_time_label_start, story_time_label_end, chapter_count
            FROM phase_digests
            WHERE script_id = %s
            ORDER BY chapter_min
            """,
            (script_id,),
        ).fetchall()
        phase_digests = [dict(r) for r in phase_digests]

        worldlines = db.execute(
            """
            SELECT wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata
            FROM script_worldlines
            WHERE script_id = %s
            ORDER BY is_primary DESC, wl_key
            """,
            (script_id,),
        ).fetchall()
        worldlines = [dict(r) for r in worldlines]

        worldline_nodes = db.execute(
            """
            SELECT wl_key, node_key, seq, label, summary, chapter_min, chapter_max,
                   anchor_keys, must_preserve, may_vary, causal_centrality,
                   first_revealed_chapter
            FROM script_worldline_nodes
            WHERE script_id = %s
            ORDER BY wl_key, seq
            """,
            (script_id,),
        ).fetchall()
        worldline_nodes = [dict(r) for r in worldline_nodes]

        # 8. chunks (可选) — 不含 embedding_vec / search_tsv (generated/不可移植)
        chunks: list[dict] = []
        if include_chunks:
            chunk_rows = db.execute(
                """
                SELECT dc.chapter_index, dc.chunk_index, dc.content,
                       dc.token_count, dc.embedding, dc.embedding_model,
                       dc.metadata,
                       d.source_kind, d.source_ref
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                WHERE dc.script_id = %s
                ORDER BY dc.chapter_index, dc.chunk_index
                """,
                (script_id,),
            ).fetchall()
            chunks = [dict(r) for r in chunk_rows]

    # 9. 构建 zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "format_version": FORMAT_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "script_title": script_dict.get("title"),
            "script_id_origin": script_id,
            "chunks_included": include_chunks,
            "chunks_version": CHUNKS_VERSION if include_chunks else None,
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
        # task 67: v2 5 张表
        zf.writestr("kb_canon_entities.jsonl", _dump_jsonl(canon_entities))
        zf.writestr("timeline_anchors.jsonl", _dump_jsonl(timeline_anchors))
        zf.writestr("phase_digests.jsonl", _dump_jsonl(phase_digests))
        zf.writestr("worldlines.jsonl", _dump_jsonl(worldlines))
        zf.writestr("worldline_nodes.jsonl", _dump_jsonl(worldline_nodes))
        if include_chunks:
            zf.writestr("chunks.jsonl", _dump_jsonl(chunks))

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

        # 解压前总量预检(CWE-409): 防小压缩包炸出超大内存占用
        declared_total = sum(i.file_size for i in zf.infolist())
        if declared_total > MAX_EXPANDED_BYTES:
            raise ValueError(
                f"pack expands too large (max {MAX_EXPANDED_BYTES // 1024 // 1024}MB)"
            )

        # 3. 读 manifest
        try:
            manifest = json.loads(_safe_member_read(zf, "manifest.json").decode("utf-8"))
        except KeyError as exc:
            raise ValueError("missing manifest.json in pack") from exc

        # task 67: v1 + v2 双兼容(v1 旧包缺 5 张世界树/锚点表,导入后给 warning 提示重跑 sync)
        pack_format_version = int(manifest.get("format_version") or 0)
        if pack_format_version not in (1, 2):
            raise ValueError(
                f"unsupported format_version: {manifest.get('format_version')!r} "
                f"(expected 1 or 2)"
            )

        # 4. 读各文件
        try:
            script_data = json.loads(_safe_member_read(zf, "script.json").decode("utf-8"))
        except KeyError as exc:
            raise ValueError("missing script.json in pack") from exc

        chapters = _read_jsonl(zf, "chapters.jsonl")
        facts = _read_jsonl(zf, "chapter_facts.jsonl")
        cards = _read_jsonl(zf, "character_cards.jsonl")
        wb = _read_jsonl(zf, "worldbook.jsonl")
        docs = _read_jsonl(zf, "documents.jsonl")

        # task 67: v2 新表 — v1 包这 5 个 jsonl 不存在,_read_jsonl KeyError 返 []
        canon_entities = _read_jsonl(zf, "kb_canon_entities.jsonl")
        timeline_anchors = _read_jsonl(zf, "timeline_anchors.jsonl")
        phase_digests_rows = _read_jsonl(zf, "phase_digests.jsonl")
        worldlines = _read_jsonl(zf, "worldlines.jsonl")
        worldline_nodes = _read_jsonl(zf, "worldline_nodes.jsonl")

        try:
            overrides: dict = json.loads(_safe_member_read(zf, "overrides.json").decode("utf-8"))
        except KeyError:
            overrides = {}

        # chunks — 仅在 manifest 声明且版本兼容时读取; 容错 fallback
        pack_chunks_included = bool(manifest.get("chunks_included"))
        pack_chunks_version = manifest.get("chunks_version")
        chunks: list[dict] = []
        if pack_chunks_included and pack_chunks_version == CHUNKS_VERSION:
            try:
                chunks = _read_jsonl(zf, "chunks.jsonl")
            except Exception:
                chunks = []  # 损坏/缺失 → fallback 到无 chunks

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

        # 5b'. 先 ensure book — chapter_facts/cards/worldbook 的 INSERT 都靠 books.script_id 子查询
        # (原来只在 5d 之后才建 book → chapter_facts SELECT FROM books 拿不到行 → 0 写入)
        from platform_app.knowledge._sync import _ensure_book
        try:
            _ensure_book(db, {
                "id": new_script_id,
                "owner_id": user_id,
                "title": title,
                "description": description,
                "source_path": "",
            })
        except Exception as _e:
            warnings.append(f"_ensure_book failed: {_e}")

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
                    ON CONFLICT DO NOTHING
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
        #     若 pack 含 chunks/docs/cards/worldbook, 提前确保 book 行存在
        if chunks or docs or cards or wb:
            from platform_app.knowledge._sync import _ensure_book
            try:
                _ensure_book(db, {
                    "id": new_script_id,
                    "owner_id": user_id,
                    "title": title,
                    "description": description,
                    "source_path": "",
                })
            except Exception:
                pass  # book 建失败时后续走 skip+warn 分支

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
                        ON CONFLICT DO NOTHING
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
                        ON CONFLICT DO NOTHING
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

        # 5g. documents — track (source_kind, source_ref) → new_document_id for chunks
        # key: (source_kind, source_ref) → new document id
        doc_key_to_new_id: dict[tuple[str, str], int] = {}
        if book_row and docs:
            for doc in docs:
                old_ch_id = doc.get("chapter_id")
                new_ch_id = old_chapter_id_to_new.get(int(old_ch_id)) if old_ch_id else None
                src_kind = str(doc.get("source_kind") or "chapter")
                src_ref = str(doc.get("source_ref") or "")
                try:
                    new_doc = db.execute(
                        """
                        INSERT INTO documents
                          (book_id, script_id, chapter_id, source_kind, source_ref,
                           title, content, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (book_id, source_kind, source_ref) DO UPDATE
                          SET updated_at = now()
                        RETURNING id
                        """,
                        (
                            book_id, new_script_id, new_ch_id,
                            src_kind, src_ref,
                            str(doc.get("title") or ""),
                            str(doc.get("content") or ""),
                            json.dumps(doc.get("metadata") or {}, ensure_ascii=False, default=str),
                        ),
                    ).fetchone()
                    if new_doc:
                        doc_key_to_new_id[(src_kind, src_ref)] = int(new_doc["id"])
                except Exception as exc:
                    warnings.append(f"document source_ref={src_ref!r} skipped: {exc}")
        elif docs and not book_row:
            warnings.append(
                f"{len(docs)} documents skipped (no books row yet; "
                "run /api/scripts/{id}/knowledge/sync to rebuild)"
            )

        # 5h. chunks — 仅当 pack 含 chunks 且 documents 成功插入时还原
        if chunks and book_row and doc_key_to_new_id:
            inserted_chunks = 0
            for ck in chunks:
                src_kind = str(ck.get("source_kind") or "chapter")
                src_ref = str(ck.get("source_ref") or "")
                doc_id = doc_key_to_new_id.get((src_kind, src_ref))
                if doc_id is None:
                    continue  # 对应 document 未插入, 跳过
                chapter_index = int(ck.get("chapter_index") or 0)
                ch_row = db.execute(
                    "SELECT id FROM script_chapters WHERE script_id = %s AND chapter_index = %s",
                    (new_script_id, chapter_index),
                ).fetchone()
                new_ch_id = int(ch_row["id"]) if ch_row else None
                try:
                    db.execute(
                        """
                        INSERT INTO document_chunks
                          (document_id, book_id, script_id, chapter_id, chapter_index,
                           chunk_index, content, token_count, embedding, embedding_model,
                           metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            doc_id, book_id, new_script_id, new_ch_id, chapter_index,
                            int(ck.get("chunk_index") or 0),
                            str(ck.get("content") or ""),
                            int(ck.get("token_count") or 0),
                            json.dumps(ck.get("embedding"), ensure_ascii=False, default=str)
                            if ck.get("embedding") is not None else None,
                            str(ck.get("embedding_model") or ""),
                            json.dumps(ck.get("metadata") or {}, ensure_ascii=False, default=str),
                        ),
                    )
                    inserted_chunks += 1
                except Exception as exc:
                    warnings.append(
                        f"chunk chapter_index={chapter_index} chunk_index={ck.get('chunk_index')} skipped: {exc}"
                    )
            if inserted_chunks:
                pass  # 正常还原, 不需要 warning
        elif chunks and not book_row:
            warnings.append(
                f"{len(chunks)} chunks skipped (no books row yet; "
                "run /api/scripts/{id}/knowledge/sync to rebuild)"
            )

        # ── task 67: v2 5 张世界树/锚点/digest 表导入 ──────────────────
        # 全部用 new_script_id 替换原 script_id,无 id 重映射(都用自然键/唯一约束)
        if pack_format_version >= 2:
            # 5i. kb_canon_entities — uniq (script_id, logical_key)
            for ent in canon_entities:
                try:
                    db.execute(
                        """
                        INSERT INTO kb_canon_entities
                          (script_id, logical_key, name, aliases, type, summary, attrs,
                           first_revealed_chapter, public_knowledge, importance, metadata,
                           full_name, identity, background, entity_subtype, parent_logical_key)
                        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s::jsonb,
                                %s, %s, %s, %s::jsonb,
                                %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            new_script_id,
                            str(ent.get("logical_key") or ""),
                            str(ent.get("name") or ""),
                            json.dumps(ent.get("aliases") or [], ensure_ascii=False, default=str),
                            str(ent.get("type") or ""),
                            str(ent.get("summary") or ""),
                            json.dumps(ent.get("attrs") or {}, ensure_ascii=False, default=str),
                            int(ent.get("first_revealed_chapter") or 0),
                            bool(ent.get("public_knowledge", False)),
                            int(ent.get("importance") or 0),
                            json.dumps(ent.get("metadata") or {}, ensure_ascii=False, default=str),
                            str(ent.get("full_name") or ""),
                            str(ent.get("identity") or ""),
                            str(ent.get("background") or ""),
                            str(ent.get("entity_subtype") or ""),
                            str(ent.get("parent_logical_key") or ""),
                        ),
                    )
                except Exception as exc:
                    warnings.append(f"kb_canon_entity {ent.get('logical_key')!r} skipped: {exc}")

            # 5j. script_timeline_anchors — uniq (script_id, story_phase, story_time_label)
            for anc in timeline_anchors:
                try:
                    db.execute(
                        """
                        INSERT INTO script_timeline_anchors
                          (script_id, story_phase, story_time_label, chapter_min, chapter_max,
                           chapter_count, sample_title, sample_summary, keywords, confidence)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            new_script_id,
                            str(anc.get("story_phase") or ""),
                            str(anc.get("story_time_label") or ""),
                            int(anc.get("chapter_min") or 0),
                            int(anc.get("chapter_max") or 0),
                            int(anc.get("chapter_count") or 0),
                            str(anc.get("sample_title") or ""),
                            str(anc.get("sample_summary") or ""),
                            anc.get("keywords") or [],  # text[] — psycopg 直接接 list
                            float(anc.get("confidence") or 1.0),
                        ),
                    )
                except Exception as exc:
                    warnings.append(f"timeline_anchor {anc.get('story_phase')}:{anc.get('story_time_label')!r} skipped: {exc}")

            # 5k. phase_digests — 无 uniq,先 DELETE WHERE script_id=new 防重复
            #     (这层是导入,new_script_id 是新建的,理论上没旧数据,但保守起见)
            try:
                db.execute("DELETE FROM phase_digests WHERE script_id = %s", (new_script_id,))
            except Exception:
                pass
            for pd in phase_digests_rows:
                try:
                    db.execute(
                        """
                        INSERT INTO phase_digests
                          (script_id, phase_label, chapter_min, chapter_max, summary,
                           key_events, key_locations, key_characters,
                           story_time_label_start, story_time_label_end, chapter_count)
                        VALUES (%s, %s, %s, %s, %s,
                                %s::jsonb, %s::jsonb, %s::jsonb,
                                %s, %s, %s)
                        """,
                        (
                            new_script_id,
                            str(pd.get("phase_label") or ""),
                            int(pd.get("chapter_min") or 0),
                            int(pd.get("chapter_max") or 0),
                            str(pd.get("summary") or ""),
                            json.dumps(pd.get("key_events") or [], ensure_ascii=False, default=str),
                            json.dumps(pd.get("key_locations") or [], ensure_ascii=False, default=str),
                            json.dumps(pd.get("key_characters") or [], ensure_ascii=False, default=str),
                            str(pd.get("story_time_label_start") or ""),
                            str(pd.get("story_time_label_end") or ""),
                            int(pd.get("chapter_count") or 0),
                        ),
                    )
                except Exception as exc:
                    warnings.append(f"phase_digest {pd.get('phase_label')!r} skipped: {exc}")

            # 5l. script_worldlines — uniq (script_id, wl_key)
            for wl in worldlines:
                try:
                    db.execute(
                        """
                        INSERT INTO script_worldlines
                          (script_id, wl_key, label, parent_wl, branch_at_node,
                           is_primary, source, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            new_script_id,
                            str(wl.get("wl_key") or ""),
                            str(wl.get("label") or ""),
                            wl.get("parent_wl"),
                            wl.get("branch_at_node"),
                            bool(wl.get("is_primary", False)),
                            str(wl.get("source") or "extracted"),
                            json.dumps(wl.get("metadata") or {}, ensure_ascii=False, default=str),
                        ),
                    )
                except Exception as exc:
                    warnings.append(f"worldline {wl.get('wl_key')!r} skipped: {exc}")

            # 5m. script_worldline_nodes — uniq (script_id, wl_key, node_key)
            for nd in worldline_nodes:
                try:
                    db.execute(
                        """
                        INSERT INTO script_worldline_nodes
                          (script_id, wl_key, node_key, seq, label, summary,
                           chapter_min, chapter_max, anchor_keys, must_preserve, may_vary,
                           causal_centrality, first_revealed_chapter)
                        VALUES (%s, %s, %s, %s, %s, %s,
                                %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                                %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            new_script_id,
                            str(nd.get("wl_key") or ""),
                            str(nd.get("node_key") or ""),
                            int(nd.get("seq") or 0),
                            str(nd.get("label") or ""),
                            str(nd.get("summary") or ""),
                            nd.get("chapter_min"),
                            nd.get("chapter_max"),
                            json.dumps(nd.get("anchor_keys") or [], ensure_ascii=False, default=str),
                            json.dumps(nd.get("must_preserve") or [], ensure_ascii=False, default=str),
                            json.dumps(nd.get("may_vary") or [], ensure_ascii=False, default=str),
                            float(nd.get("causal_centrality") or 0.0),
                            int(nd.get("first_revealed_chapter") or 0),
                        ),
                    )
                except Exception as exc:
                    warnings.append(f"worldline_node {nd.get('wl_key')}:{nd.get('node_key')!r} skipped: {exc}")
        else:
            # v1 包 — 缺世界树/锚点/digest,给出补救路径提示
            warnings.append(
                f"v1 pack imported into script {new_script_id} (缺 kb_canon_entities/timeline_anchors/phase_digests/worldlines)。"
                f"运行 /api/scripts/{new_script_id}/knowledge/sync 重建,否则 GM retrieval 上下文会残缺。"
            )

    # 6. overrides — must be after outer `with connect()` commits the scripts row
    if overrides:
        from platform_app.knowledge.script_overrides import upsert_overrides
        upsert_overrides(new_script_id, overrides)

    # phase_backend: warnings 数组 + warnings_count + warnings_summary
    # 之前调用方只能看 warnings 列表条数,不知道是哪一类失败。这里加汇总让前端能展开排查。
    by_kind: dict[str, int] = {}
    for w in warnings:
        key = (w or "").split(":", 1)[0].strip()[:40] or "other"
        by_kind[key] = by_kind.get(key, 0) + 1
    return {
        "ok": True,
        "script_id": new_script_id,
        "warnings": warnings,
        "warnings_count": len(warnings),
        "warnings_summary": by_kind,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dump_jsonl(rows: list[dict]) -> str:
    return "\n".join(
        json.dumps(r, ensure_ascii=False, default=str) for r in rows
    )


def _read_jsonl(zf: zipfile.ZipFile, name: str) -> list[dict]:
    try:
        text = _safe_member_read(zf, name).decode("utf-8")
    except KeyError:
        return []
    rows: list[dict] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        if len(rows) >= MAX_JSONL_ROWS:  # 防超长 JSONL materialize 打爆内存(CWE-409)
            raise ValueError(f"{name}: JSONL 行数超限(max {MAX_JSONL_ROWS})")
        rows.append(json.loads(line))
    return rows


def _dump_script_row(row: dict) -> str:
    d = {k: v for k, v in row.items()}
    # 脱敏 owner_id
    d.pop("owner_id", None)
    return json.dumps(d, ensure_ascii=False, default=str, indent=2)


def clone_public_script(src_script_id: int, dst_user_id: int) -> dict[str, Any]:
    """把一本【公开】剧本克隆进 dst_user 的账户。

    复用 export_script_pack(以原 owner 身份导出) + import_script_pack(导入给当前用户)
    这套已验证的跨表复制管线。只允许克隆 is_public=true 的剧本。
    成功后给源剧本 clone_count +1(热度)。返回 import_script_pack 的结果。
    """
    with connect() as db:
        row = db.execute(
            "SELECT owner_id, is_public, title FROM scripts WHERE id = %s",
            (src_script_id,),
        ).fetchone()
    if not row:
        raise ValueError("剧本不存在")
    src = dict(row)
    if not src.get("is_public"):
        raise PermissionError("该剧本未公开,无法导入")
    owner_id = src["owner_id"]
    if owner_id == dst_user_id:
        raise ValueError("这是你自己的剧本,无需从公开库导入")

    # 以原 owner 身份导出(满足 export 的 ownership 校验),再导入给当前用户。
    zip_bytes, _filename = export_script_pack(src_script_id, owner_id, include_chunks=False)
    result = import_script_pack(zip_bytes, dst_user_id)

    # 热度计数(克隆成功才 +1;失败上面已抛异常)
    try:
        with connect() as db:
            db.execute(
                "UPDATE scripts SET clone_count = clone_count + 1 WHERE id = %s",
                (src_script_id,),
            )
            db.commit()
    except Exception:
        pass  # 计数失败不影响克隆结果
    return result
