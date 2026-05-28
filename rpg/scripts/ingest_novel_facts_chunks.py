#!/usr/bin/env python3
"""Phase 2: 把 sqlite chapter_facts → postgres chapter_facts + 给每章建一个 document_chunk。"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你/rpg")

from psycopg.types.json import Jsonb  # noqa: E402

from platform_app.db import connect, init_db  # noqa: E402

SCRIPT_ID = 9803
USER_ID = 7268
SQLITE_PATH = "/Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你/.webnovel/chapter_facts.db"


def main():
    init_db()
    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row
    with connect() as db:
        # 找到/建 book
        book = db.execute(
            "select id from books where script_id=%s limit 1", (SCRIPT_ID,),
        ).fetchone()
        if not book:
            book = db.execute(
                """insert into books(owner_id, script_id, title, description)
                   values (%s, %s, %s, %s) returning id""",
                (USER_ID, SCRIPT_ID, "《我蕾穆丽娜不爱你》", "原著本体"),
            ).fetchone()
        book_id = book["id"]

        # 找/建 document
        doc = db.execute(
            "select id from documents where script_id=%s limit 1", (SCRIPT_ID,),
        ).fetchone()
        if not doc:
            doc = db.execute(
                """insert into documents(book_id, script_id, source_kind, source_ref, title, content)
                   values (%s, %s, %s, %s, %s, %s) returning id""",
                (book_id, SCRIPT_ID, "chapter", "novel_body", "《我蕾穆丽娜不爱你》正文", ""),
            ).fetchone()
        doc_id = doc["id"]
        print(f"book_id={book_id}, doc_id={doc_id}")

        # 清旧
        db.execute("delete from chapter_facts where script_id=%s", (SCRIPT_ID,))
        db.execute("delete from document_chunks where script_id=%s", (SCRIPT_ID,))

        # chapter_facts: 从 sqlite 拷
        cur = sq.cursor()
        cur.execute("""
            SELECT chapter, volume, title, source_file, viewpoint, summary, story_phase,
                   story_time_label, scene_count, token_estimate,
                   characters_json, locations_json, factions_json, concepts_json,
                   items_json, relationships_json, events_json, confidence
            FROM chapter_facts ORDER BY chapter
        """)
        rows = cur.fetchall()
        print(f"sqlite chapter_facts rows = {len(rows)}")
        fact_inserts = []
        for r in rows:
            fact_inserts.append((
                book_id, SCRIPT_ID, doc_id, r["chapter"], r["title"] or "",
                r["viewpoint"] or "", r["summary"] or "",
                r["story_phase"] or "", r["story_time_label"] or "",
                r["scene_count"] or 0, r["token_estimate"] or 0,
                Jsonb(json.loads(r["characters_json"] or "[]")),
                Jsonb(json.loads(r["locations_json"] or "[]")),
                Jsonb(json.loads(r["factions_json"] or "[]")),
                Jsonb(json.loads(r["concepts_json"] or "[]")),
                Jsonb(json.loads(r["items_json"] or "[]")),
                Jsonb(json.loads(r["relationships_json"] or "[]")),
                Jsonb(json.loads(r["events_json"] or "[]")),
                r["confidence"] or 0.5,
            ))
        with db.cursor() as c:
            c.executemany(
                """insert into chapter_facts(
                  book_id, script_id, document_id, chapter, title, viewpoint, summary,
                  story_phase, story_time_label, scene_count, token_estimate,
                  characters, locations, factions, concepts, items, relationships, events, confidence
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                fact_inserts,
            )
        print(f"postgres chapter_facts 写入 {len(fact_inserts)} 行")

        # document_chunks: 每章存 1 个 chunk (后续 FTS 可分得更细)
        chunks = db.execute(
            "select chapter_index, title, content, word_count from script_chapters "
            "where script_id=%s order by chapter_index",
            (SCRIPT_ID,),
        ).fetchall()
        print(f"script_chapters = {len(chunks)}")
        chunk_inserts = []
        for i, ch in enumerate(chunks):
            chunk_inserts.append((
                doc_id, book_id, SCRIPT_ID, ch["chapter_index"], i,
                ch["content"] or "", len(ch["content"] or ""),
            ))
        with db.cursor() as c:
            c.executemany(
                """insert into document_chunks(
                  document_id, book_id, script_id, chapter_index, chunk_index,
                  content, token_count
                ) values (%s,%s,%s,%s,%s,%s,%s)""",
                chunk_inserts,
            )
        print(f"document_chunks 写入 {len(chunk_inserts)} 行")

    sq.close()
    print("OK")


if __name__ == "__main__":
    main()
