"""
timeline_index.py - Story-time metadata for chapter/chunk retrieval.

Inspired by AI Reader's ChapterFact/Event Timeline pipeline, but kept light:
we use the existing chapter summaries and key timeline to annotate vectors.db.
Later this can be replaced by a full LLM ChapterFact extraction pass.
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent
_OVERRIDES_DIR = BASE / "modules" / "_script_overrides"


@lru_cache(maxsize=8)
def _load_overrides_for_script(script_key: str | None) -> dict:
    """加载指定剧本的 overrides。

    优先从 DB script_overrides 表读取（按 script_key 匹配 scripts.title）；
    DB 不可用时 fallback 到 modules/_script_overrides/<script_key>.json。
    无 script_key 或无记录时返回空 dict,让调用方走 hardcoded fallback。
    """
    if not script_key:
        return {}
    try:
        from platform_app.knowledge.script_overrides import load_all_overrides_by_key
        all_overrides = load_all_overrides_by_key()
        if script_key in all_overrides:
            return all_overrides[script_key]
        # DB 里没有该 key 时不回退 JSON（数据已迁移到 DB）
        return {}
    except Exception:
        pass
    # fallback: 读 JSON 文件（本地开发 / DB 不可用时兜底）
    p = _OVERRIDES_DIR / f"{script_key}.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
DB_PATH = BASE.parent / ".webnovel" / "vectors.db"
SUMMARY_PATH = BASE / "indexes" / "summaries.json"


def _db_available(db_path: Path) -> bool:
    """检查 SQLite 文件是否真实存在。

    sqlite3.connect 会在父目录存在时自动创建空文件；如果父目录不存在则
    抛 OperationalError("unable to open database file")。
    这里收口：任何 SQLite 访问前先确认文件已落地，否则直接 return 空，
    让上层走 PostgreSQL ChapterFact 或 graceful 默认值。
    """
    try:
        return db_path.exists() and db_path.is_file() and db_path.stat().st_size > 0
    except Exception:
        return False


def ensure_timeline_schema(db_path: Path = DB_PATH) -> None:
    # 缺 SQLite 文件直接返回——这是可选的旧索引层，Postgres ChapterFact 是主路径
    if not _db_available(db_path):
        return
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS story_timeline_events (
                event_id TEXT PRIMARY KEY,
                chapter INTEGER NOT NULL,
                event_order INTEGER NOT NULL,
                event TEXT NOT NULL,
                story_phase TEXT NOT NULL DEFAULT '',
                story_time_label TEXT NOT NULL DEFAULT '',
                participants_json TEXT NOT NULL DEFAULT '[]',
                locations_json TEXT NOT NULL DEFAULT '[]',
                importance TEXT NOT NULL DEFAULT 'medium',
                confidence REAL NOT NULL DEFAULT 0.60,
                source TEXT NOT NULL DEFAULT 'summaries.key_timeline'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunk_timeline (
                chunk_id TEXT PRIMARY KEY,
                chapter INTEGER NOT NULL,
                scene_index INTEGER,
                timeline_order INTEGER NOT NULL,
                story_phase TEXT NOT NULL DEFAULT '',
                story_time_label TEXT NOT NULL DEFAULT '',
                event_id TEXT,
                event TEXT NOT NULL DEFAULT '',
                temporal_scope TEXT NOT NULL DEFAULT 'current',
                participants_json TEXT NOT NULL DEFAULT '[]',
                locations_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.60,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(chunk_id) REFERENCES vectors(chunk_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chunk_timeline_chapter ON chunk_timeline(chapter)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chunk_timeline_order ON chunk_timeline(timeline_order)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chunk_timeline_phase ON chunk_timeline(story_phase)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_timeline_events_chapter ON story_timeline_events(chapter)")
        for col, ddl in {
            "timeline_order": "INTEGER",
            "story_phase": "TEXT",
            "story_time_label": "TEXT",
            "timeline_event_id": "TEXT",
            "temporal_scope": "TEXT DEFAULT 'current'",
        }.items():
            if not _column_exists(cur, "vectors", col):
                cur.execute(f"ALTER TABLE vectors ADD COLUMN {col} {ddl}")
        cur.execute("""
            INSERT OR REPLACE INTO rag_schema_meta(key, value, updated_at)
            VALUES ('timeline_schema_version', '1', CURRENT_TIMESTAMP)
        """)
        conn.commit()
    finally:
        conn.close()


def bootstrap_timeline_from_summaries(db_path: Path = DB_PATH, summary_path: Path = SUMMARY_PATH, script_key: str | None = None) -> dict[str, Any]:
    if not _db_available(db_path):
        return {"events": 0, "chunks": 0, "skipped": "sqlite_unavailable"}
    ensure_timeline_schema(db_path)
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {"events": 0, "chunks": 0, "skipped": "summary_missing"}
    summaries: dict[str, str] = data.get("summaries", {})
    key_events: list[dict] = data.get("key_timeline", [])
    event_by_chapter = {int(item["chapter"]): item for item in key_events if item.get("chapter")}

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        inserted_events = 0
        for order, item in enumerate(key_events, start=1):
            chapter = int(item["chapter"])
            event = str(item.get("event", "")).strip()
            summary = summaries.get(str(chapter), "")
            phase = _phase_for(chapter, event, summary, script_key=script_key)
            time_label = _time_label_for(chapter, event, summary, script_key=script_key)
            participants = _extract_names(event + " " + summary, script_key=script_key)
            locations = _extract_locations(event + " " + summary, script_key=script_key)
            event_id = f"ch{chapter:04d}_e{order:03d}"
            cur.execute("""
                INSERT OR REPLACE INTO story_timeline_events
                (event_id, chapter, event_order, event, story_phase, story_time_label,
                 participants_json, locations_json, importance, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, chapter, order, event, phase, time_label,
                json.dumps(participants, ensure_ascii=False),
                json.dumps(locations, ensure_ascii=False),
                "high", 0.75,
            ))
            inserted_events += 1

        cur.execute("SELECT chunk_id, chapter, scene_index, content FROM vectors")
        rows = cur.fetchall()
        annotated = 0
        for chunk_id, chapter, scene_index, content in rows:
            chapter = int(chapter or 0)
            scene_index = int(scene_index or 0)
            event_item = _nearest_event(chapter, event_by_chapter)
            event_chapter = int(event_item["chapter"]) if event_item else chapter
            event = str(event_item.get("event", "")) if event_item else ""
            summary = summaries.get(str(chapter), "")
            phase = _phase_for(chapter, event, summary or content or "", script_key=script_key)
            time_label = _time_label_for(chapter, event, summary or content or "", script_key=script_key)
            event_id = _event_id_for(event_chapter, key_events) if event_item else None  # type: ignore[assignment]
            participants = _extract_names(" ".join([event, summary, content or ""]), script_key=script_key)
            locations = _extract_locations(" ".join([event, summary, content or ""]), script_key=script_key)
            timeline_order = chapter * 1000 + scene_index
            cur.execute("""
                INSERT OR REPLACE INTO chunk_timeline
                (chunk_id, chapter, scene_index, timeline_order, story_phase, story_time_label,
                 event_id, event, temporal_scope, participants_json, locations_json, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'current', ?, ?, ?)
            """, (
                chunk_id, chapter, scene_index, timeline_order, phase, time_label,
                event_id, event,
                json.dumps(participants, ensure_ascii=False),
                json.dumps(locations, ensure_ascii=False),
                0.65 if summary else 0.45,
            ))
            cur.execute("""
                UPDATE vectors
                SET timeline_order = ?, story_phase = ?, story_time_label = ?,
                    timeline_event_id = ?, temporal_scope = 'current'
                WHERE chunk_id = ?
            """, (timeline_order, phase, time_label, event_id, chunk_id))
            annotated += 1

        cur.execute("""
            INSERT OR REPLACE INTO rag_schema_meta(key, value, updated_at)
            VALUES ('timeline_bootstrap_source', ?, CURRENT_TIMESTAMP)
        """, (str(summary_path),))
        conn.commit()
        return {"events": inserted_events, "chunks": annotated}
    finally:
        conn.close()


def timeline_filter_for_label(label: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Return a chapter window for the current story label.

    This is intentionally conservative: until we have full ChapterFact extraction,
    it anchors by key timeline events and nearby chapters rather than pretending to
    know exact in-world dates.
    """
    # SQLite 索引缺失时返回空 filter，让 retrieval 走 Postgres ChapterFact 主路径
    if not _db_available(db_path):
        return {"chapter_min": None, "chapter_max": None, "anchor_chapter": None,
                "anchor_event": "", "story_time_label": "", "confidence": 0.0}
    ensure_timeline_schema(db_path)
    label = label or ""
    direct_chapter = _direct_chapter(label)
    if direct_chapter:
        direct = _chapter_filter(direct_chapter, db_path)
        if direct:
            return direct
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT chapter, event, story_time_label FROM story_timeline_events ORDER BY event_order")
        events = cur.fetchall()
    finally:
        conn.close()

    if not events:
        return {"chapter_min": None, "chapter_max": None, "anchor_event": "", "confidence": 0.0}

    scored = []
    for chapter, event, time_label in events:
        text = f"{event} {time_label}"
        score = _overlap_score(label, text)
        if "图卢兹" in label and "图卢兹" in text:
            score += 5
        if "柏林" in label and "柏林" in text:
            score += 3
        if "翌日" in label and ("暂留" in text or "北城" in text or "蛇信" in text):
            score += 2
        scored.append((score, chapter, event, time_label))
    scored.sort(reverse=True)
    best_score, chapter, event, time_label = scored[0]
    if best_score <= 0:
        return {
            "chapter_min": None,
            "chapter_max": None,
            "anchor_chapter": None,
            "anchor_event": "未能匹配原著时间线",
            "story_time_label": "",
            "confidence": 0.0,
        }
    return {
        "chapter_min": max(1, int(chapter) - 2),
        "chapter_max": int(chapter) + 2,
        "anchor_chapter": int(chapter),
        "anchor_event": event,
        "story_time_label": time_label,
        "confidence": min(0.95, 0.45 + max(best_score, 0) * 0.08),
    }


def _direct_chapter(label: str) -> int | None:
    match = re.search(r"(?:第\s*)?(\d{1,5})\s*(?:章|回|chapter|Chapter)", label or "")
    if not match:
        match = re.search(r"(?:chapter|ch)\s*\.?\s*(\d{1,5})", label or "", re.I)
    if not match:
        return None
    chapter = int(match.group(1))
    return chapter if 1 <= chapter <= 99999 else None


def _chapter_filter(chapter: int, db_path: Path) -> dict[str, Any] | None:
    if not _db_available(db_path):
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT chapter, event, story_time_label, confidence
            FROM story_timeline_events
            WHERE chapter = ?
            ORDER BY confidence DESC, event_order
            LIMIT 1
            """,
            (chapter,),
        )
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT min(chapter), max(chapter) FROM vectors")
            bounds = cur.fetchone()
            if not bounds or bounds[0] is None:
                return None
            if not (int(bounds[0]) <= chapter <= int(bounds[1])):
                return None
            return {
                "chapter_min": max(1, chapter - 1),
                "chapter_max": chapter + 1,
                "anchor_chapter": chapter,
                "anchor_event": f"原著第{chapter}章",
                "story_time_label": f"原著第{chapter}章附近",
                "confidence": 0.80,
                "source": "direct_chapter",
            }
        ch, event, time_label, confidence = row
        return {
            "chapter_min": max(1, int(ch) - 1),
            "chapter_max": int(ch) + 1,
            "anchor_chapter": int(ch),
            "anchor_event": event,
            "story_time_label": time_label,
            "confidence": max(float(confidence or 0), 0.88),
            "source": "direct_chapter",
        }
    finally:
        conn.close()


def _column_exists(cur, table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def _nearest_event(chapter: int, event_by_chapter: dict[int, dict]) -> dict | None:
    if not event_by_chapter:
        return None
    candidates = [c for c in event_by_chapter if c <= chapter] or list(event_by_chapter)
    nearest = max(candidates)
    return event_by_chapter[nearest]


def _event_id_for(chapter: int, events: list[dict]) -> str | None:
    for order, item in enumerate(events, start=1):
        if int(item.get("chapter", 0)) == chapter:
            return f"ch{chapter:04d}_e{order:03d}"
    return None


def _phase_for(chapter: int, event: str, text: str, script_key: str | None = None) -> str:
    """推断当前 story phase。

    timeline_index 只区分"是否柏林暗流篇"（binary 语义），因此只匹配
    phase_inference.rules 的第一条；其余 chapter 返回 fallback_simple。
    无 script_key 或 JSON 不存在时返回空字符串，调用方自行处理。
    """
    overrides = _load_overrides_for_script(script_key)
    rules = (overrides.get("phase_inference") or {}).get("rules") or []
    fallback = (overrides.get("phase_inference") or {}).get("fallback_simple") or ""
    hay = f"{event} {text}"
    if rules:
        rule = rules[0]
        ch_min = rule.get("chapter_min", 0)
        needles = rule.get("or_text_needles") or []
        if chapter >= ch_min or (needles and any(n in hay for n in needles)):
            return rule["phase"]
        return fallback
    # 无 script_key 或 JSON 不存在 → 返回空字符串
    return ""


def _time_label_for(chapter: int, event: str, text: str, script_key: str | None = None) -> str:
    """推断当前 time label。

    优先按 time_label_inference 规则匹配（needles_any 或 chapter_min），
    命中即返回对应 label；兜底用 time_label_fallback_template。
    无 script_key 或 JSON 不存在时返回 f"第{chapter}章"。
    """
    overrides = _load_overrides_for_script(script_key)
    tl_rules = overrides.get("time_label_inference") or []
    tl_fallback_tmpl = overrides.get("time_label_fallback_template") or ""
    hay = f"{event} {text}"
    if tl_rules:
        for rule in tl_rules:
            needles = rule.get("needles_any") or []
            ch_min = rule.get("chapter_min")
            if needles and any(n in hay for n in needles):
                return rule["label"]
            if ch_min is not None and not needles and chapter >= ch_min:
                return rule["label"]
        if tl_fallback_tmpl:
            return tl_fallback_tmpl.format(chapter=chapter)
    # 无 script_key 或 JSON 不存在 → 通用兜底
    return f"第{chapter}章"


def _extract_names(text: str, script_key: str | None = None) -> list[str]:
    """从 text 中抽取已知角色名（按 known_names 顺序）。

    优先从 script_key 对应 JSON 的 known_names 加载列表；
    无 script_key 或 JSON 不存在时返回空列表。
    """
    overrides = _load_overrides_for_script(script_key)
    known = overrides.get("known_names") or []
    return [name for name in known if name in text][:8]


def _extract_locations(text: str, script_key: str | None = None) -> list[str]:
    """从 text 中抽取已知地点名（按 known_locations 顺序）。

    优先从 script_key 对应 JSON 的 known_locations 加载列表；
    无 script_key 或 JSON 不存在时返回空列表。
    """
    overrides = _load_overrides_for_script(script_key)
    known = overrides.get("known_locations") or []
    return [loc for loc in known if loc in text][:8]


def _overlap_score(a: str, b: str) -> int:
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", a or ""))
    return sum(1 for token in tokens if token in b)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--script-key", default=None, help="剧本标识 (对应 modules/_script_overrides/<key>.json),不传则用 generic 推断")
    args = parser.parse_args()
    result = bootstrap_timeline_from_summaries(script_key=args.script_key)
    print(json.dumps(result, ensure_ascii=False, indent=2))
