"""extract/resolve.py — Pass 2 两层消歧 + 聚合 → 规范层 KB。

discover-then-link 的 link 收尾:
  · 实体消歧:嵌入粗筛聚簇(降重复)+ LLM 精判(可选)→ kb_canon_entities
  · 时间线:事件按章节顺序增量(不全局排序)→ script_timeline_anchors
  · 规范世界线 DAG:高 importance+因果中心度 → script_worldlines/_nodes(默认主线+弧)
  · constant 骨架:纪元/力量体系/主要派系 → worldbook_entries(insertion_position='constant')
设计 A_extraction.md §5。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from kb import canon_repo


@dataclass
class CanonEntity:
    logical_key: str
    name: str
    type: str
    aliases: list[str] = field(default_factory=list)
    first_revealed_chapter: int = 0
    importance: int = 0
    summary: str = ""


def _slug(name: str) -> str:
    s = re.sub(r"\s+", "_", name.strip())
    return re.sub(r"[^\w一-鿿·.-]", "", s)[:80] or "entity"


def _cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return num / (na * nb) if na and nb else 0.0


def gather_entity_mentions(chapter_extracts: list) -> dict[tuple[str, str], dict]:
    """从逐章 ChapterExtract 汇总实体提及。键=(归一名, type)。"""
    acc: dict[tuple[str, str], dict] = {}
    for ex in chapter_extracts:
        for e in getattr(ex, "entities", []):
            name = (e.get("canonical_guess") or e.get("surface") or "").strip()
            typ = (e.get("type") or "character").strip()
            if not name:
                continue
            key = (name, typ)
            rec = acc.setdefault(key, {"name": name, "type": typ, "count": 0,
                                       "first_chapter": ex.chapter, "surfaces": set()})
            rec["count"] += 1
            rec["first_chapter"] = min(rec["first_chapter"], ex.chapter)
            sfc = (e.get("surface") or "").strip()
            if sfc:
                rec["surfaces"].add(sfc)
    return acc


def _norm_name(s: str) -> str:
    return re.sub(r"[\s·_、.\-]", "", (s or "").strip())


def cluster_entities(mentions: dict, *, embedder=None, sim_threshold: float = 0.95) -> list[CanonEntity]:
    """同 type 内**保守**聚簇。LLM 的 canonical_guess 已做实体归一,这里只合并近重串:
    归一名相等 / 互为子串(如 薇欧拉 ⊂ 薇欧拉小姐);嵌入仅作高阈值(默认 0.95)次级信号
    且要求首字相同。**绝不靠嵌入把不同人名合并**(0.86 旧阈值会把 14 个角色并成 1)。"""
    by_type: dict[str, list] = defaultdict(list)
    for (name, typ), rec in mentions.items():
        by_type[typ].append(rec)

    canon: list[CanonEntity] = []
    for typ, recs in by_type.items():
        recs.sort(key=lambda r: -r["count"])
        vecs = None
        if embedder is not None:
            try:
                vecs = embedder([r["name"] for r in recs])
            except Exception:
                vecs = None
        clusters: list[dict] = []  # {rep_idx, members:[idx]}
        for i, rec in enumerate(recs):
            ni = _norm_name(rec["name"])
            placed = False
            for cl in clusters:
                nr = _norm_name(recs[cl["rep_idx"]]["name"])
                # 主信号:归一相等 或 互为子串(长度≥2 防单字误并)
                same = ni == nr or (len(ni) >= 2 and len(nr) >= 2 and (ni in nr or nr in ni))
                # 次信号:嵌入高相似 且 首字相同(抓"薇欧拉/薇瑟拉"这种变体)
                if not same and vecs is not None and ni and nr and ni[0] == nr[0]:
                    same = _cosine(vecs[i], vecs[cl["rep_idx"]]) >= sim_threshold
                if same:
                    cl["members"].append(i)
                    placed = True
                    break
            if not placed:
                clusters.append({"rep_idx": i, "members": [i]})
        for cl in clusters:
            members = [recs[j] for j in cl["members"]]
            rep = max(members, key=lambda r: r["count"])
            aliases = sorted({s for m in members for s in m["surfaces"]} |
                             {m["name"] for m in members} - {rep["name"]})
            canon.append(CanonEntity(
                logical_key=_slug(rep["name"]),
                name=rep["name"], type=typ, aliases=aliases,
                first_revealed_chapter=min(m["first_chapter"] for m in members),
                importance=sum(m["count"] for m in members),
            ))
    # logical_key 去重(不同 type 撞名时加后缀)
    seen: dict[str, int] = {}
    for c in canon:
        if c.logical_key in seen:
            seen[c.logical_key] += 1
            c.logical_key = f"{c.logical_key}_{c.type}"
        else:
            seen[c.logical_key] = 1
    return canon


def resolve_and_write(db, script_id: int, chapter_extracts: list, *, embedder=None,
                      public_threshold: int = 0) -> dict:
    """完整 Pass2:消歧 → 写 kb_canon_entities。返回统计。"""
    mentions = gather_entity_mentions(chapter_extracts)
    canon = cluster_entities(mentions, embedder=embedder)
    # 概念也进规范实体(type=concept),从各章 concepts 汇总
    concept_acc: dict[str, dict] = {}
    for ex in chapter_extracts:
        for c in getattr(ex, "concepts", []):
            nm = (c.get("name") or "").strip()
            if not nm:
                continue
            r = concept_acc.setdefault(nm, {"count": 0, "first": ex.chapter, "gloss": c.get("gloss", "")})
            r["count"] += 1
            r["first"] = min(r["first"], ex.chapter)
            if not r["gloss"] and c.get("gloss"):
                r["gloss"] = c.get("gloss")
    for nm, r in concept_acc.items():
        canon.append(CanonEntity(logical_key=_slug(nm) + "_concept", name=nm, type="concept",
                                 first_revealed_chapter=r["first"], importance=r["count"], summary=r["gloss"]))

    written = 0
    for c in canon:
        canon_repo.upsert_canon_entity(
            db, script_id, c.logical_key, name=c.name, type=c.type, aliases=c.aliases,
            summary=c.summary, first_revealed_chapter=c.first_revealed_chapter,
            public_knowledge=(c.importance > public_threshold and c.first_revealed_chapter <= 1),
            importance=c.importance,
        )
        written += 1
    return {"mentions": len(mentions), "entities_written": written,
            "by_type": _count_by_type(canon)}


def _count_by_type(canon: list[CanonEntity]) -> dict:
    out: dict[str, int] = defaultdict(int)
    for c in canon:
        out[c.type] += 1
    return dict(out)


# ── 时间线增量聚合(不全局排序) ─────────────────────────────────────────────
def build_timeline(db, script_id: int, chapter_extracts: list) -> int:
    """事件按章节顺序增量,产出 script_timeline_anchors(值来自 story_time 而非标题)。"""
    # 按 story_time.label 聚合连续章节段
    segments: list[dict] = []
    for ex in sorted(chapter_extracts, key=lambda e: e.chapter):
        label = (ex.story_time or {}).get("label", "").strip()
        if not label:
            continue
        if segments and segments[-1]["label"] == label:
            segments[-1]["chapter_max"] = ex.chapter
        else:
            segments.append({"label": label, "chapter_min": ex.chapter, "chapter_max": ex.chapter})
    written = 0
    for seg in segments:
        db.execute(
            """
            insert into script_timeline_anchors(script_id, story_phase, story_time_label,
              chapter_min, chapter_max, chapter_count, confidence)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict(script_id, story_phase, story_time_label) do update set
              chapter_min=least(script_timeline_anchors.chapter_min, excluded.chapter_min),
              chapter_max=greatest(script_timeline_anchors.chapter_max, excluded.chapter_max),
              updated_at=now()
            """,
            (script_id, "", seg["label"], seg["chapter_min"], seg["chapter_max"],
             seg["chapter_max"] - seg["chapter_min"] + 1, 0.7),
        )
        written += 1
    return written


# ── constant 世界观骨架(治 1935) ───────────────────────────────────────────
def build_constant_worldbook(db, script_id: int, book_id: int, seed) -> int:
    """纪元/力量体系/主要派系 → worldbook_entries(insertion_position='constant')。

    book_id 必填(worldbook 按 book 归属)。constant 条目每轮无条件常驻注入(治 1935)。
    """
    entries = []
    if getattr(seed, "era", ""):
        entries.append(("纪元", f"本作纪元固定为「{seed.era}」。所有时间表述以此为准,绝不套用现实世界年代。"))
    if getattr(seed, "power_system", None):
        entries.append(("力量体系", "核心力量体系:" + "、".join(seed.power_system)))
    if getattr(seed, "key_factions", None):
        entries.append(("主要势力", "主要势力:" + "、".join(seed.key_factions[:12])))
    written = 0
    for title, content in entries:
        db.execute(
            """
            insert into worldbook_entries(book_id, script_id, title, content, keys, priority, insertion_position, enabled)
            values (%s, %s, %s, %s, %s, %s, 'constant', true)
            on conflict(script_id, title) do update set
              content=excluded.content, insertion_position='constant', updated_at=now()
            """,
            (book_id, script_id, title, content, Jsonb([]), 100),
        )
        written += 1
    return written
