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
    # v28: 与玩家 PC 角色卡字段对齐(character_cards 多态合并)。
    # 仅 type='character' 才有意义;其它类型为空。
    full_name: str = ""
    identity: str = ""
    background: str = ""


def _slug(name: str) -> str:
    s = re.sub(r"\s+", "_", name.strip())
    return re.sub(r"[^\w一-鿿·.-]", "", s)[:80] or "entity"


def _cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return num / (na * nb) if na and nb else 0.0


def gather_entity_mentions(chapter_extracts: list) -> dict[tuple[str, str], dict]:
    """从逐章 ChapterExtract 汇总实体提及。键=(归一名, type)。

    优先取 full_name(欧美名全套)作 name,canonical_guess 退化兜底。所有 surface/aliases_in_chapter
    塞进 surfaces 用于 cluster_entities 的别名子串合并。

    v28:同步累计 identity / background 候选(取最长非空),full_name 保留独立列(character_cards.full_name)。
    """
    acc: dict[tuple[str, str], dict] = {}
    for ex in chapter_extracts:
        for e in getattr(ex, "entities", []):
            full = (e.get("full_name") or "").strip()
            cg = (e.get("canonical_guess") or "").strip()
            sfc = (e.get("surface") or "").strip()
            # 选 name 优先级:full_name > canonical_guess > surface,且取最长(欧美名 "Mulelia Zazbarum" 胜 "Mulelia")
            name = max([n for n in (full, cg, sfc) if n], key=len, default="")
            typ = (e.get("type") or "character").strip()
            if not name:
                continue
            key = (name, typ)
            rec = acc.setdefault(key, {"name": name, "type": typ, "count": 0,
                                       "first_chapter": ex.chapter, "surfaces": set(),
                                       "full_name": "", "identity": "", "background": ""})
            rec["count"] += 1
            rec["first_chapter"] = min(rec["first_chapter"], ex.chapter)
            for s in (sfc, cg, full):
                if s:
                    rec["surfaces"].add(s)
            for a in (e.get("aliases_in_chapter") or []):
                if isinstance(a, str) and a.strip():
                    rec["surfaces"].add(a.strip())
            # v28: full_name / identity / background 取最长(信息量更大的胜出)
            if full and len(full) > len(rec["full_name"]):
                rec["full_name"] = full
            ident = (e.get("identity") or "").strip()
            if ident and len(ident) > len(rec["identity"]):
                rec["identity"] = ident
            bg = (e.get("background") or "").strip()
            if bg and len(bg) > len(rec["background"]):
                rec["background"] = bg
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
            ni_surfaces = {_norm_name(s) for s in (rec.get("surfaces") or set()) if s}
            placed = False
            for cl in clusters:
                rep_rec = recs[cl["rep_idx"]]
                nr = _norm_name(rep_rec["name"])
                nr_surfaces = {_norm_name(s) for s in (rep_rec.get("surfaces") or set()) if s}
                # 主信号:归一相等 / 互为子串(长度≥2 防单字误并)
                same = ni == nr or (len(ni) >= 2 and len(nr) >= 2 and (ni in nr or nr in ni))
                # 别名信号:本实体的某 surface 与对端 name/surfaces 相交(欧美全名↔昵称 + 跨语言别名靠这条)
                if not same and ni_surfaces and (nr in ni_surfaces or ni in nr_surfaces
                                                  or (ni_surfaces & nr_surfaces)):
                    same = True
                # 次信号:嵌入高相似 且 首字相同(同语言变体如"薇欧拉/薇瑟拉")
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
            # v28: full_name / identity / background 跨成员取最长非空(信息量最大的胜出)
            full_name = max((m.get("full_name", "") for m in members), key=len, default="")
            identity = max((m.get("identity", "") for m in members), key=len, default="")
            background = max((m.get("background", "") for m in members), key=len, default="")
            canon.append(CanonEntity(
                logical_key=_slug(rep["name"]),
                name=rep["name"], type=typ, aliases=aliases,
                first_revealed_chapter=min(m["first_chapter"] for m in members),
                importance=sum(m["count"] for m in members),
                full_name=full_name, identity=identity, background=background,
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
                      public_threshold: int = 0, book_id: int | None = None) -> dict:
    """完整 Pass2:消歧 → 写 kb_canon_entities + 同步 NPC 角色卡到 character_cards。

    v28: 新增 character_cards 同步。把 type='character' 的 canon entity 落进 character_cards
    (card_type='npc', source='extracted'),这样前端 NPC 卡片视图能直接看到提取出来的角色,
    字段与 PC/persona 完全对齐(由 v28 多态合并保证)。

    book_id 可不传(从 books 表按 script_id 反查),传则直接用。
    """
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
            public_knowledge=(c.importance > public_threshold and c.first_revealed_chapter == 1),
            importance=c.importance,
        )
        written += 1

    # v28: 同步 character 类 canon → character_cards 表(NPC 角色卡)
    character_canon = [c for c in canon if c.type == "character"]
    npc_cards_written = sync_character_cards_from_canon(db, script_id, character_canon, book_id=book_id)

    return {"mentions": len(mentions), "entities_written": written,
            "by_type": _count_by_type(canon),
            "npc_cards_written": npc_cards_written}


def sync_character_cards_from_canon(db, script_id: int, character_canon: list[CanonEntity],
                                    *, book_id: int | None = None) -> int:
    """把 type='character' 的 CanonEntity 同步进 character_cards(card_type='npc')。

    v28 后 character_cards 是多态表(npc/pc/persona 三态合一),NPC 行约束:
      - card_type='npc', source='extracted', scope='script'
      - user_id=NULL, script_id 必填
      - (script_id, name) 在 NPC 内唯一(partial unique index)

    upsert:同名 NPC 覆盖 identity/background/full_name/importance 等提取字段,
    保留人工编辑过的 token_budget/priority/enabled 等(NOT TOUCHED in EXCLUDED)。
    """
    if not character_canon:
        return 0
    if book_id is None:
        row = db.execute("select id from books where script_id = %s", (script_id,)).fetchone()
        if not row:
            return 0  # 无 book → 不写(import 链路应已建 book,缺失说明未走通)
        book_id = int(row["id"])

    written = 0
    for c in character_canon:
        # INSERT … ON CONFLICT DO UPDATE 一把搞定:
        #   新行 → 插入提取字段
        #   旧行 → 用 LLM 新抽的覆盖 aliases/first_revealed_chapter/importance,
        #          identity/background/full_name 仅在 EXCLUDED 非空时覆盖(避免空字符串
        #          把用户已编辑过的内容刷没)
        db.execute(
            """
            insert into character_cards(
              book_id, script_id, name, full_name, aliases, identity, background,
              first_revealed_chapter, importance, card_type, source, scope,
              metadata, enabled
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'npc', 'extracted', 'script',
                    %s, true)
            on conflict(script_id, name) where card_type = 'npc' do update set
              full_name = case when length(excluded.full_name) > 0
                               then excluded.full_name else character_cards.full_name end,
              aliases = excluded.aliases,
              identity = case when length(excluded.identity) > 0
                              then excluded.identity else character_cards.identity end,
              background = case when length(excluded.background) > 0
                                then excluded.background else character_cards.background end,
              -- 重抽时:取更早的首次出场章节、保留更高的 importance(防 LLM 偶尔漏抽某章而回退)
              first_revealed_chapter = case
                when character_cards.first_revealed_chapter = 0 then excluded.first_revealed_chapter
                when excluded.first_revealed_chapter = 0 then character_cards.first_revealed_chapter
                else least(character_cards.first_revealed_chapter, excluded.first_revealed_chapter)
              end,
              importance = greatest(character_cards.importance, excluded.importance),
              row_version = character_cards.row_version + 1,
              updated_at = now()
            """,
            (book_id, script_id, c.name, c.full_name, Jsonb(c.aliases),
             c.identity, c.background, c.first_revealed_chapter, c.importance,
             Jsonb({"source": "extracted", "logical_key": c.logical_key})),
        )
        written += 1
    return written


def _count_by_type(canon: list[CanonEntity]) -> dict:
    out: dict[str, int] = defaultdict(int)
    for c in canon:
        out[c.type] += 1
    return dict(out)


# ── 时间线增量聚合(不全局排序) ─────────────────────────────────────────────
def build_timeline(db, script_id: int, chapter_extracts: list) -> int:
    """事件按章节顺序增量,产出 script_timeline_anchors(值来自 story_time 而非标题)。

    每段收集成员章节的 chapter_summary 拼接成 sample_summary(分段),让 GM 拉时间线
    得到结构化摘要而不是 raw event 碎片。
    """
    # 按 story_time.label 聚合连续章节段
    segments: list[dict] = []
    for ex in sorted(chapter_extracts, key=lambda e: e.chapter):
        label = (ex.story_time or {}).get("label", "").strip()
        if not label:
            continue
        summary = (getattr(ex, "chapter_summary", "") or "").strip()
        if segments and segments[-1]["label"] == label:
            segments[-1]["chapter_max"] = ex.chapter
            if summary:
                segments[-1]["summaries"].append((ex.chapter, summary))
        else:
            segments.append({
                "label": label, "chapter_min": ex.chapter, "chapter_max": ex.chapter,
                "summaries": [(ex.chapter, summary)] if summary else [],
            })
    written = 0
    for seg in segments:
        # 每段取首 + 中 + 末三个 summary 拼接(避免过长 + 给 GM 段头/段中/段尾的脉络)
        sums = seg.get("summaries") or []
        if sums:
            picks = [sums[0]]
            if len(sums) >= 3:
                picks.append(sums[len(sums) // 2])
            if len(sums) >= 2:
                picks.append(sums[-1])
            sample_summary = " / ".join(f"第{ch}章:{s}" for ch, s in picks)[:1900]
        else:
            sample_summary = ""
        db.execute(
            """
            insert into script_timeline_anchors(script_id, story_phase, story_time_label,
              chapter_min, chapter_max, chapter_count, sample_summary, confidence)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict(script_id, story_phase, story_time_label) do update set
              chapter_min=least(script_timeline_anchors.chapter_min, excluded.chapter_min),
              chapter_max=greatest(script_timeline_anchors.chapter_max, excluded.chapter_max),
              sample_summary=case when length(excluded.sample_summary) > 0
                then excluded.sample_summary else script_timeline_anchors.sample_summary end,
              updated_at=now()
            """,
            (script_id, "", seg["label"], seg["chapter_min"], seg["chapter_max"],
             seg["chapter_max"] - seg["chapter_min"] + 1, sample_summary, 0.7),
        )
        written += 1
    return written


# ── constant 世界观骨架(治 1935) ───────────────────────────────────────────
def build_constant_worldbook(db, script_id: int, book_id: int, seed) -> int:
    """纪元/力量体系/主要派系 → worldbook_entries(insertion_position='constant')。

    book_id 必填(worldbook 按 book 归属)。constant 条目每轮无条件常驻注入(治 1935)。
    """
    # 清旧:此 script 之前任何路径(_stage_worldbook 等)写入的非 extracted 条目作废,
    # 防新旧两套常驻骨架同时喂 GM 造成自相矛盾(如旧"哥本哈根 2927"和新纪元打架)。
    db.execute(
        "delete from worldbook_entries where script_id=%s "
        "and (metadata->>'source' is null or metadata->>'source' <> 'extracted')",
        (script_id,),
    )
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
            insert into worldbook_entries(book_id, script_id, title, content, keys, priority, insertion_position, enabled, metadata)
            values (%s, %s, %s, %s, %s, %s, 'constant', true, %s)
            on conflict(script_id, title) do update set
              content=excluded.content, insertion_position='constant',
              metadata=excluded.metadata, updated_at=now()
            """,
            (book_id, script_id, title, content, Jsonb([]), 100,
             Jsonb({"source": "extracted"})),
        )
        written += 1
    return written
