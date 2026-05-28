#!/usr/bin/env python3
"""把 /Volumes/.../正文/*.md + /设定集/*.md 灌进 script 9803。

· 正文 → script_chapters
· 设定集 → worldbook_entries
· 更新 scripts.chapter_count / word_count
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "/Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你/rpg")

from psycopg.types.json import Jsonb  # noqa: E402

from platform_app.db import connect, init_db  # noqa: E402

SCRIPT_ID = 9803
USER_ID = 7268
ROOT = Path("/Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你")
CHAPTERS_DIR = ROOT / "正文"
LORE_DIR = ROOT / "设定集"


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)


def parse_md(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(raw)
    meta = {}
    body = raw
    if m:
        fm_block = m.group(1)
        body = m.group(2)
        for line in fm_block.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"\'')
    return {"meta": meta, "content": body.strip()}


def chapter_index_from_filename(fname: str) -> int | None:
    m = re.search(r"第(\d+)章", fname)
    return int(m.group(1)) if m else None


def main():
    init_db()
    # ── 1. 章节 ──────────────────────────────────────
    chapter_files = sorted(CHAPTERS_DIR.glob("第*.md"))
    print(f"[正文] {len(chapter_files)} 个 .md")
    if not chapter_files:
        sys.exit("找不到正文文件")
    rows = []
    skipped = 0
    for p in chapter_files:
        idx = chapter_index_from_filename(p.name)
        if idx is None:
            skipped += 1
            continue
        parsed = parse_md(p)
        title = parsed["meta"].get("title") or p.stem
        # 去掉文件名前缀第000X章-标题模式
        title = re.sub(r"^第\d+章[\s-]*", "", title)
        vol = parsed["meta"].get("volume") or ""
        content = parsed["content"]
        wc = len(content)
        rows.append((SCRIPT_ID, idx, title, content, wc, vol, p.name, 1.0))
    print(f"[正文] 解析 OK {len(rows)},跳过 {skipped}")

    with connect() as db:
        # 清掉旧数据
        existing = db.execute(
            "select count(*) as n from script_chapters where script_id=%s",
            (SCRIPT_ID,),
        ).fetchone()
        print(f"[正文] 旧章节数 = {existing['n']}")
        db.execute("delete from script_chapters where script_id=%s", (SCRIPT_ID,))
        with db.cursor() as cur:
            cur.executemany(
                """
                insert into script_chapters(
                  script_id, chapter_index, title, content, word_count,
                  volume_title, source_marker, confidence
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        total_words = sum(r[4] for r in rows)
        db.execute(
            "update scripts set chapter_count=%s, word_count=%s, updated_at=now() where id=%s",
            (len(rows), total_words, SCRIPT_ID),
        )
        print(f"[正文] 写入 {len(rows)} 章, 共 {total_words} 字")

    # ── 2. 设定集 → worldbook ──────────────────────────
    # worldbook 需要 book_id; 找一本 book 与 script 关联,没有就创建。
    with connect() as db:
        book = db.execute(
            "select id from books where script_id=%s limit 1", (SCRIPT_ID,),
        ).fetchone()
        if not book:
            book = db.execute(
                """
                insert into books(owner_id, script_id, title, description)
                values (%s, %s, %s, %s) returning id
                """,
                (USER_ID, SCRIPT_ID, "《我蕾穆丽娜不爱你》设定", "设定集导入"),
            ).fetchone()
            print(f"[worldbook] 新建 book_id={book['id']}")
        book_id = book["id"]

        # 清旧 worldbook
        db.execute("delete from worldbook_entries where script_id=%s", (SCRIPT_ID,))
        lore_files = sorted(LORE_DIR.glob("*.md"))
        print(f"[设定集] {len(lore_files)} 个 .md")
        for p in lore_files:
            title = p.stem
            content = p.read_text(encoding="utf-8").strip()
            keys = []
            # 简单提取一些关键词作为 trigger
            for kw in re.findall(r"\*\*([^*]{2,12})\*\*", content):
                if kw not in keys:
                    keys.append(kw)
            db.execute(
                """
                insert into worldbook_entries(
                  book_id, script_id, title, content, keys, priority, token_budget
                ) values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (book_id, SCRIPT_ID, title, content, Jsonb(keys[:20]), 80, 800),
            )
            print(f"  · {title}  ({len(content)} chars, {len(keys[:20])} keys)")

    print("OK done.")


if __name__ == "__main__":
    main()
