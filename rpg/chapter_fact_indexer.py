"""
chapter_fact_indexer.py - Full-book ChapterFact first pass.

This is the local, deterministic pass of the "拆书" pipeline:
- every chapter gets a ChapterFact row
- entities/events are indexed for timeline/RAG filtering
- existing vectors.db receives per-chapter story-time metadata

The shape follows AI-Reader-style ChapterFact, while staying cheap enough to run
locally before an optional LLM refinement pass.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from timeline_index import ensure_timeline_schema

BASE = Path(__file__).parent
_OVERRIDES_DIR = BASE / "modules" / "_script_overrides"


@lru_cache(maxsize=8)
def _load_overrides_for_script(script_key: str | None) -> dict:
    """从 modules/_script_overrides/<script_key>.json 加载剧本专有规则。

    无 script_key 或文件不存在时返回空 dict,让调用方走 hardcoded fallback。
    """
    if not script_key:
        return {}
    p = _OVERRIDES_DIR / f"{script_key}.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
ROOT = BASE.parent
CHAPTER_DIR = ROOT / "正文"
OUT_DB = ROOT / ".webnovel" / "chapter_facts.db"
VECTOR_DB = ROOT / ".webnovel" / "vectors.db"
CHAR_IDX = BASE / "indexes" / "characters.json"
WORLD_IDX = BASE / "indexes" / "world.json"
SUMMARY_IDX = BASE / "indexes" / "summaries.json"
MANIFEST = BASE / "indexes" / "chapter_facts_manifest.json"

EVENT_KEYWORDS = [
    "决定", "发现", "确认", "推断", "告知", "收到", "命令", "战报", "袭击", "撤离",
    "死亡", "失踪", "失守", "会面", "讨论", "调查", "追查", "暴露", "隐藏", "背叛",
    "保护", "启动", "交涉", "拒绝", "答应", "怀疑", "安排", "撤走", "清空", "突破",
]

LOCATION_SEEDS = [
    "地球", "火星", "薇瑟帝国", "地球联邦", "柏林", "图卢兹", "布鲁塞尔", "欧洲", "非洲",
    "乌里克欧兹", "北城旧宅", "内城旧楼", "哈布斯堡庄园", "皇宫", "基地", "地下基地",
    "太平洋", "大西洋", "月球", "轨道", "登陆城", "居所", "宴会厅", "小会议室",
]

CONCEPT_SEEDS = [
    "aldnoal", "Aldnoal", "aldnoal遗命权限", "烈锋", "渊戮", "顶王", "魔力", "蛇信",
    "调令伪造", "特洛耶德", "薇瑟帝国", "地联", "大西洋方面", "太平洋方面",
]

KEY_CHAPTER_TIME_LABELS = {
    1309: "图卢兹失守前夜，柏林宴会",
    1310: "图卢兹失守当晚，柏林",
    1311: "图卢兹失守后翌日，柏林",
    1312: "图卢兹失守后翌日，柏林",
    1313: "图卢兹失守后翌日，柏林",
    1314: "图卢兹失守后次日，柏林内城",
    1315: "图卢兹失守后次日，柏林",
}


def build_chapter_facts(script_key: str | None = None) -> dict[str, Any]:
    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    chars = _load_characters()
    world = _load_world()
    summaries = _load_summaries()
    known_names = _known_names(chars)
    known_locations = _known_locations(world)
    known_concepts = _known_concepts(world)

    chapters = list(_iter_chapters())
    conn = sqlite3.connect(str(OUT_DB))
    try:
        _create_schema(conn)
        cur = conn.cursor()
        cur.execute("DELETE FROM chapter_fact_entities")
        cur.execute("DELETE FROM chapter_fact_events")
        cur.execute("DELETE FROM chapter_facts")

        total_events = 0
        total_entities = 0
        for chapter in chapters:
            fact = _extract_fact(chapter, summaries, known_names, known_locations, known_concepts, script_key=script_key)
            _insert_fact(cur, fact)
            total_events += len(fact["events"])
            total_entities += sum(len(fact[key]) for key in ("characters", "locations", "factions", "concepts", "items"))
        cur.execute("""
            INSERT OR REPLACE INTO chapter_fact_meta(key, value, updated_at)
            VALUES ('version', '1', CURRENT_TIMESTAMP)
        """)
        conn.commit()
    finally:
        conn.close()

    vector_result = update_vector_timeline_from_facts()
    manifest = {
        "version": 1,
        "chapters": len(chapters),
        "events": total_events,
        "entities": total_entities,
        "chapter_min": chapters[0]["chapter"] if chapters else None,
        "chapter_max": chapters[-1]["chapter"] if chapters else None,
        "database": str(OUT_DB),
        "vector_update": vector_result,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def update_vector_timeline_from_facts() -> dict[str, Any]:
    if not VECTOR_DB.exists():
        return {"updated_vectors": 0, "inserted_events": 0}
    ensure_timeline_schema(VECTOR_DB)
    fact_conn = sqlite3.connect(str(OUT_DB))
    vec_conn = sqlite3.connect(str(VECTOR_DB))
    try:
        fact_cur = fact_conn.cursor()
        vec_cur = vec_conn.cursor()
        fact_cur.execute("""
            SELECT chapter, story_phase, story_time_label, summary, events_json, characters_json, locations_json
            FROM chapter_facts
        """)
        rows = fact_cur.fetchall()
        inserted_events = 0
        updated_vectors = 0
        for chapter, phase, time_label, summary, events_json, chars_json, locs_json in rows:
            events = json.loads(events_json or "[]")
            first_event = (summary or "").strip()[:160] or (events[0]["event"] if events else f"第{chapter}章事件")
            event_id = f"fact_ch{int(chapter):04d}_e001"
            vec_cur.execute("""
                INSERT OR REPLACE INTO story_timeline_events
                (event_id, chapter, event_order, event, story_phase, story_time_label,
                 participants_json, locations_json, importance, confidence, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, chapter, int(chapter), first_event, phase, time_label,
                chars_json or "[]", locs_json or "[]", "medium", 0.55, "chapter_fact_indexer",
            ))
            inserted_events += 1
            vec_cur.execute("""
                UPDATE vectors
                SET story_phase = ?, story_time_label = ?, timeline_event_id = ?,
                    timeline_order = COALESCE(timeline_order, chapter * 1000 + COALESCE(scene_index, 0))
                WHERE chapter = ?
            """, (phase, time_label, event_id, chapter))
            updated_vectors += vec_cur.rowcount
            vec_cur.execute("""
                UPDATE chunk_timeline
                SET story_phase = ?, story_time_label = ?, event_id = ?, event = ?, updated_at = CURRENT_TIMESTAMP
                WHERE chapter = ?
            """, (phase, time_label, event_id, first_event, chapter))
        vec_cur.execute("""
            INSERT OR REPLACE INTO rag_schema_meta(key, value, updated_at)
            VALUES ('chapter_fact_timeline_version', '1', CURRENT_TIMESTAMP)
        """)
        vec_conn.commit()
        return {"updated_vectors": updated_vectors, "inserted_events": inserted_events}
    finally:
        fact_conn.close()
        vec_conn.close()


def _create_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chapter_facts (
            chapter INTEGER PRIMARY KEY,
            volume INTEGER,
            title TEXT NOT NULL,
            source_file TEXT NOT NULL,
            viewpoint TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            story_phase TEXT NOT NULL DEFAULT '',
            story_time_label TEXT NOT NULL DEFAULT '',
            scene_count INTEGER NOT NULL DEFAULT 0,
            token_estimate INTEGER NOT NULL DEFAULT 0,
            characters_json TEXT NOT NULL DEFAULT '[]',
            locations_json TEXT NOT NULL DEFAULT '[]',
            factions_json TEXT NOT NULL DEFAULT '[]',
            concepts_json TEXT NOT NULL DEFAULT '[]',
            items_json TEXT NOT NULL DEFAULT '[]',
            relationships_json TEXT NOT NULL DEFAULT '[]',
            events_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.50,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chapter_fact_entities (
            chapter INTEGER NOT NULL,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            first_scene INTEGER NOT NULL DEFAULT 0,
            evidence TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(chapter, entity_type, name)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chapter_fact_events (
            event_id TEXT PRIMARY KEY,
            chapter INTEGER NOT NULL,
            scene_index INTEGER NOT NULL DEFAULT 0,
            event_order INTEGER NOT NULL,
            event TEXT NOT NULL,
            participants_json TEXT NOT NULL DEFAULT '[]',
            locations_json TEXT NOT NULL DEFAULT '[]',
            concepts_json TEXT NOT NULL DEFAULT '[]',
            importance TEXT NOT NULL DEFAULT 'medium',
            evidence TEXT NOT NULL DEFAULT ''
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chapter_fact_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fact_entities_name ON chapter_fact_entities(name, entity_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fact_events_chapter ON chapter_fact_events(chapter, event_order)")
    conn.commit()


def _insert_fact(cur: sqlite3.Cursor, fact: dict[str, Any]) -> None:
    cur.execute("""
        INSERT OR REPLACE INTO chapter_facts
        (chapter, volume, title, source_file, viewpoint, summary, story_phase, story_time_label,
         scene_count, token_estimate, characters_json, locations_json, factions_json, concepts_json,
         items_json, relationships_json, events_json, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fact["chapter"], fact["volume"], fact["title"], fact["source_file"], fact["viewpoint"],
        fact["summary"], fact["story_phase"], fact["story_time_label"], fact["scene_count"],
        fact["token_estimate"], _json(fact["characters"]), _json(fact["locations"]),
        _json(fact["factions"]), _json(fact["concepts"]), _json(fact["items"]),
        _json(fact["relationships"]), _json(fact["events"]), fact["confidence"],
    ))
    for entity_type in ("characters", "locations", "factions", "concepts", "items"):
        for item in fact[entity_type]:
            cur.execute("""
                INSERT OR REPLACE INTO chapter_fact_entities
                (chapter, entity_type, name, count, first_scene, evidence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                fact["chapter"], entity_type[:-1], item["name"], item.get("count", 1),
                item.get("first_scene", 0), item.get("evidence", ""),
            ))
    for order, event in enumerate(fact["events"], start=1):
        cur.execute("""
            INSERT OR REPLACE INTO chapter_fact_events
            (event_id, chapter, scene_index, event_order, event, participants_json,
             locations_json, concepts_json, importance, evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"ch{fact['chapter']:04d}_e{order:02d}", fact["chapter"], event.get("scene_index", 0),
            order, event["event"], _json(event.get("participants", [])),
            _json(event.get("locations", [])), _json(event.get("concepts", [])),
            event.get("importance", "medium"), event.get("evidence", ""),
        ))


def _extract_fact(chapter: dict[str, Any], summaries: dict[str, Any], known_names: dict[str, str], known_locations: list[str], known_concepts: list[str], script_key: str | None = None) -> dict[str, Any]:
    text = _strip_frontmatter(chapter["text"])
    body = _strip_notes(text)
    scenes = _split_scenes(body)
    sentences = _sentences(body)
    title = chapter["title"]
    chapter_num = chapter["chapter"]
    known_summary = summaries.get("summaries", {}).get(str(chapter_num), "")
    characters = _rank_entities(body, known_names, "character")
    locations = _rank_terms(body, known_locations, "location")
    concepts = _rank_terms(body, known_concepts, "concept")
    factions = _rank_terms(body, list(_load_world().get("key_factions", {}).keys()), "faction")
    items = _extract_items(body)
    events = _extract_events(sentences, scenes, known_names, known_locations, known_concepts)
    if not events and known_summary:
        events = [{
            "scene_index": 0,
            "event": known_summary[:120],
            "participants": [x["name"] for x in characters[:5]],
            "locations": [x["name"] for x in locations[:3]],
            "concepts": [x["name"] for x in concepts[:4]],
            "importance": "medium",
            "evidence": known_summary[:180],
        }]
    return {
        "chapter": chapter_num,
        "volume": chapter.get("volume", 0),
        "title": title,
        "source_file": str(chapter["path"]),
        "viewpoint": _viewpoint(body),
        "summary": known_summary or _summary_from_events(events, sentences),
        "story_phase": _story_phase(chapter_num, body, script_key=script_key),
        "story_time_label": _story_time_label(chapter_num, title, body, known_summary),
        "scene_count": len(scenes),
        "token_estimate": max(1, len(body) // 2),
        "characters": characters,
        "locations": locations,
        "factions": factions,
        "concepts": concepts,
        "items": items,
        "relationships": _extract_relationships(sentences, known_names),
        "events": events,
        "confidence": 0.65 if known_summary else 0.48,
    }


def _iter_chapters() -> list[dict[str, Any]]:
    chapters = []
    for path in CHAPTER_DIR.glob("第*章-*.md"):
        match = re.search(r"第(\d{4})章-(.+)\.md$", path.name)
        if not match:
            continue
        text = path.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        chapters.append({
            "chapter": int(match.group(1)),
            "title": meta.get("title") or match.group(2),
            "volume": int(meta.get("volume") or 0),
            "path": path,
            "text": text,
        })
    chapters.sort(key=lambda x: x["chapter"])
    return chapters


def _load_characters() -> dict[str, Any]:
    return json.loads(CHAR_IDX.read_text(encoding="utf-8")).get("characters", {})


def _load_world() -> dict[str, Any]:
    return json.loads(WORLD_IDX.read_text(encoding="utf-8"))


def _load_summaries() -> dict[str, Any]:
    return json.loads(SUMMARY_IDX.read_text(encoding="utf-8"))


def _known_names(chars: dict[str, Any]) -> dict[str, str]:
    names = {}
    for name, card in chars.items():
        names[name] = name
        for alias in card.get("aliases") or []:
            names[alias] = name
    names.update({name: name for name in ["蕾穆丽娜", "雷利加利亚", "赫克勒斯", "扎兹巴鲁姆", "黎骨月", "爱乌", "韵子", "莱艾", "艾瑟依拉姆", "迪卡亚", "路易威斯"]})
    return names


def _known_locations(world: dict[str, Any]) -> list[str]:
    values = set(LOCATION_SEEDS)
    for text in world.get("key_factions", {}).values():
        values.update(re.findall(r"[\u4e00-\u9fffA-Za-z]{2,12}(?:方面|帝国|联邦|家族|分支|城|宫|基地|庄园|旧宅|旧楼)", text))
    return sorted(values, key=len, reverse=True)


def _known_concepts(world: dict[str, Any]) -> list[str]:
    values = set(CONCEPT_SEEDS)
    values.update(world.get("key_concepts", {}).keys())
    values.update(world.get("key_factions", {}).keys())
    return sorted(values, key=len, reverse=True)


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip().strip('"')
    return out


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4:] if end >= 0 else text


def _strip_notes(text: str) -> str:
    return re.split(r"\n\s*(?:ps|PS|作者|本章说)[。.:：]", text, maxsplit=1)[0]


def _split_scenes(text: str) -> list[str]:
    chunks = re.split(r"\n(?=【[^】]{1,20}】)|\n{3,}", text)
    return [chunk.strip() for chunk in chunks if len(chunk.strip()) > 20]


def _sentences(text: str) -> list[str]:
    text = re.sub(r"#+\s*.+", "", text)
    raw = re.split(r"(?<=[。！？!?])\s*|\n+", text)
    return [_clean_sentence(x) for x in raw if 16 <= len(_clean_sentence(x)) <= 220]


def _clean_sentence(text: str) -> str:
    return re.sub(r"\s+", "", text).strip(" 　")


def _viewpoint(text: str) -> str:
    match = re.search(r"【([^】]{1,20})】", text)
    return match.group(1) if match else ""


def _rank_entities(text: str, aliases: dict[str, str], entity_type: str) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    evidence: dict[str, str] = {}
    for alias, canonical in aliases.items():
        count = text.count(alias)
        if count:
            counts[canonical] += count
            evidence.setdefault(canonical, _evidence(text, alias))
    return [
        {"name": name, "count": count, "first_scene": 0, "evidence": evidence.get(name, "")}
        for name, count in counts.most_common(18)
    ]


def _rank_terms(text: str, terms: list[str], entity_type: str) -> list[dict[str, Any]]:
    counts = Counter()
    evidence = {}
    for term in terms:
        count = text.count(term)
        if count:
            counts[term] += count
            evidence.setdefault(term, _evidence(text, term))
    return [
        {"name": name, "count": count, "first_scene": 0, "evidence": evidence.get(name, "")}
        for name, count in counts.most_common(16)
    ]


def _extract_items(text: str) -> list[dict[str, Any]]:
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,16}(?:报告|文件袋|封条|通讯|坐标|密令|权限|钥匙|纸片|设备|匕首|机甲|甲胄骑士|战报)", text)
    counts = Counter(candidates)
    return [{"name": name, "count": count, "first_scene": 0, "evidence": _evidence(text, name)} for name, count in counts.most_common(12)]


def _extract_events(sentences: list[str], scenes: list[str], aliases: dict[str, str], locations: list[str], concepts: list[str]) -> list[dict[str, Any]]:
    scored = []
    for index, sentence in enumerate(sentences):
        score = sum(3 for key in EVENT_KEYWORDS if key in sentence)
        participants = _names_in(sentence, aliases)
        locs = [loc for loc in locations if loc in sentence][:4]
        cons = [concept for concept in concepts if concept in sentence][:5]
        score += len(participants) * 2 + len(locs) + len(cons)
        if "。" in sentence or "！" in sentence or "？" in sentence:
            score += 1
        if score >= 5:
            scored.append((score, index, sentence, participants, locs, cons))
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = sorted(scored[:8], key=lambda x: x[1])
    out = []
    for order, (score, index, sentence, participants, locs, cons) in enumerate(chosen, start=1):
        out.append({
            "scene_index": _scene_index_for_sentence(sentence, scenes),
            "event": sentence[:160],
            "participants": participants[:8],
            "locations": locs[:4],
            "concepts": cons[:5],
            "importance": "high" if score >= 12 else "medium",
            "evidence": sentence[:200],
        })
    return out


def _extract_relationships(sentences: list[str], aliases: dict[str, str]) -> list[dict[str, str]]:
    out = []
    for sentence in sentences:
        names = _names_in(sentence, aliases)
        if len(names) >= 2 and any(key in sentence for key in ("保护", "信任", "怀疑", "命令", "追查", "交给", "告诉", "隐瞒", "合作")):
            out.append({"source": names[0], "target": names[1], "note": sentence[:120]})
        if len(out) >= 8:
            break
    return out


def _names_in(text: str, aliases: dict[str, str]) -> list[str]:
    found = []
    for alias, canonical in aliases.items():
        if alias in text and canonical not in found:
            found.append(canonical)
    return found


def _scene_index_for_sentence(sentence: str, scenes: list[str]) -> int:
    for index, scene in enumerate(scenes):
        if sentence[:20] in scene:
            return index
    return 0


def _summary_from_events(events: list[dict[str, Any]], sentences: list[str]) -> str:
    if events:
        return "；".join(event["event"] for event in events[:3])[:240]
    return "；".join(sentences[:3])[:240]


def _story_phase(chapter: int, text: str, script_key: str | None = None) -> str:
    """推断章节所在故事阶段（5 级 full 模式）。

    按 phase_inference.rules 顺序匹配（chapter_min + or_text_needles），
    第一条命中即返回对应 phase；全不命中返回 fallback_simple。
    无 script_key 或 JSON 不存在时返回空字符串，调用方自行处理。
    """
    overrides = _load_overrides_for_script(script_key)
    rules = (overrides.get("phase_inference") or {}).get("rules") or []
    fallback = (overrides.get("phase_inference") or {}).get("fallback_simple") or ""
    if rules:
        for rule in rules:
            ch_min = rule.get("chapter_min", 0)
            needles = rule.get("or_text_needles") or []
            # 有 needles 时：chapter >= ch_min OR 任一 needle 命中（等价原始 OR 逻辑）
            # 无 needles 时：chapter >= ch_min（纯章节范围规则）
            if needles:
                if chapter >= ch_min or any(n in text for n in needles):
                    return rule["phase"]
            else:
                if chapter >= ch_min:
                    return rule["phase"]
        return fallback
    # 无 script_key 或 JSON 不存在 → 返回空字符串
    return ""


# task 121b: 章节标题质量检测 — 通用算法,不依赖单本书。
# 排除:作者口语吐槽 / 纯章节序号 / 太短太长。
_AUTHOR_META_KEYWORDS = (
    "周推", "收藏", "月票", "感谢", "PS:", "PS：", "求票", "推荐票",
    "上架", "鞠躬", "请假", "本章免费", "VIP", "加更", "正版",
    "求评论", "求订阅", "求月票", "码字", "更新", "请假条", "感言",
    "新书", "签约", "推荐位", "点击", "撒娇", "认输", "卷末",
)
_GENERIC_CHAPTER_RE = re.compile(
    r"^第?[一二三四五六七八九十百千万0-9〇零]+\s*[章回节卷部话集]?$"
)
_CHAPTER_PREFIX_RE = re.compile(
    r"^第[一二三四五六七八九十百千万0-9〇零]+\s*[章回节卷部话集]?[\s　：:、,，\-—]*"
)


def _strip_chapter_prefix(t: str) -> str:
    """剥掉 '第一章 ' / 'Chapter 1: ' 前缀,留剧情部分。"""
    if not t:
        return ""
    s = t.lstrip().lstrip("#").strip()
    s = _CHAPTER_PREFIX_RE.sub("", s).strip()
    s = re.sub(r"^[Cc]hapter\s*\d+[\s\-:：]*", "", s).strip()
    return s


def _good_title(title: str) -> str:
    """返回清洗后的可用标题,不可用返回空串。"""
    if not title:
        return ""
    s = _strip_chapter_prefix(title)
    if not s or len(s) < 2 or len(s) > 28:
        return ""
    if _GENERIC_CHAPTER_RE.match(s):
        return ""
    if any(kw in s for kw in _AUTHOR_META_KEYWORDS):
        return ""
    # 全数字 / 全标点
    if re.match(r"^[\d\W_]+$", s):
        return ""
    return s


def _good_summary_lead(summary: str) -> str:
    """从 summary 抽第一句话作为锚点(15-30 字最佳)。"""
    if not summary:
        return ""
    # 切第一个句号/分号/换行
    first = re.split(r"[。;；\n]", summary)[0].strip()
    if 4 <= len(first) <= 40:
        # 去引号包裹
        first = first.strip("“”\"'")
        if 4 <= len(first) <= 40:
            return first
    return ""


def _good_event_lead(events_or_text: Any) -> str:
    """从 events list 或 text 抽第一个有意义的语句。"""
    if isinstance(events_or_text, list) and events_or_text:
        first = events_or_text[0]
        if isinstance(first, dict):
            first = first.get("event") or ""
        return _good_summary_lead(str(first or ""))
    if isinstance(events_or_text, str):
        return _good_summary_lead(events_or_text)
    return ""


def _story_time_label(chapter: int, title: str, text: str, summary: str) -> str:
    """通用算法 — 多级 fallback 选最佳锚点,不依赖单本书。

    优先级:
      1. 章节标题质量过关 (剥掉'第 N 章'前缀,排除作者口语/纯序号)
      2. summary 首句 (15-30 字最佳)
      3. text 首段首句
      4. 最后退化到 'ch{N} 节点' (绝不是'原著第 N 章附近'垃圾标签)
    """
    # 1. title 路径
    good = _good_title(title)
    if good:
        return good
    # 2. summary 路径
    lead = _good_summary_lead(summary)
    if lead:
        return lead
    # 3. text 首句
    lead = _good_summary_lead(text or "")
    if lead:
        return lead
    # 4. 兜底
    return f"ch{chapter} 节点"


def _evidence(text: str, needle: str) -> str:
    index = text.find(needle)
    if index < 0:
        return ""
    start = max(0, index - 30)
    end = min(len(text), index + len(needle) + 50)
    return re.sub(r"\s+", "", text[start:end])


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--script-key", default=None, help="剧本标识 (对应 modules/_script_overrides/<key>.json),不传则用 generic 推断")
    args = parser.parse_args()
    print(json.dumps(build_chapter_facts(script_key=args.script_key), ensure_ascii=False, indent=2))
