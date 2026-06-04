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
    # P0 大改:type 内细分 + 层级父级(faction/location/concept 用)
    entity_subtype: str = ""
    parent_name: str = ""  # 中间字段:cluster 出的 parent 实体名;upsert 阶段映射成 parent_logical_key
    parent_logical_key: str = ""


def _slug(name: str) -> str:
    s = re.sub(r"\s+", "_", name.strip())
    return re.sub(r"[^\w一-鿿·.-]", "", s)[:80] or "entity"


def _cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=False))
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
                                       "full_name": "", "identity": "", "background": "",
                                       # P0 大改:跟踪 subtype 候选 + parent 候选
                                       "subtype": "", "parent_names": []})
            # subtype:同实体多章可能 LLM 给不同 subtype,取首个非空(LLM 抽 prompt 严格的话基本一致)
            sb = (e.get("subtype") or "").strip()
            if sb and not rec["subtype"]:
                rec["subtype"] = sb
            # parent:多章可能给多个候选(如本章说"铁人团"父级是"德军",别章说"国防军") — 全收
            pr = (e.get("parent") or "").strip()
            if pr:
                rec["parent_names"].append(pr)
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
    for (_name, typ), rec in mentions.items():
        by_type[typ].append(rec)

    canon: list[CanonEntity] = []
    # phase_backend: 跟踪 embedder fallback 状态(给 resolve_and_write 落 stats 用)
    embedder_fallback = False
    fallback_reason = ""
    for typ, recs in by_type.items():
        recs.sort(key=lambda r: -r["count"])
        vecs = None
        if embedder is not None:
            try:
                vecs = embedder([r["name"] for r in recs])
            except Exception as _exc:
                vecs = None
                embedder_fallback = True
                fallback_reason = f"embedder raised: {type(_exc).__name__}"
            # embedder 失败 / Vertex 凭证缺 / Vertex 返空时,_embed_batch(...) or []
            # 会返空 list — 长度与 recs 不一致就走不进次信号分支(否则 vecs[i] 越界,
            # 整个 resolve stage 直接挂掉)。
            if vecs is not None and len(vecs) != len(recs):
                vecs = None
                embedder_fallback = True
                fallback_reason = fallback_reason or "embedder length mismatch"
            if embedder is not None and vecs is None and not fallback_reason:
                embedder_fallback = True
                fallback_reason = "embedder returned None/empty"
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
            # P0 大改:subtype + parent_names 跨成员聚合
            subtype = next((m.get("subtype", "") for m in members if m.get("subtype")), "")
            # parent 取 cluster 内最多人提到的候选(majority vote)
            parent_votes: dict[str, int] = {}
            for m in members:
                for pn in m.get("parent_names") or []:
                    parent_votes[pn] = parent_votes.get(pn, 0) + 1
            parent_name = max(parent_votes.items(), key=lambda kv: kv[1])[0] if parent_votes else ""
            # 数据质量 #2:summary 强制填(character: identity+background;其他: 空待 worldbook 富化兜)
            if typ == "character":
                summary_parts = []
                if identity:
                    summary_parts.append(identity[:50])
                if background:
                    summary_parts.append(background[:120])
                summary = " · ".join(summary_parts)
            else:
                summary = ""  # faction/location/item 走 worldbook 富化路径
            canon.append(CanonEntity(
                logical_key=_slug(rep["name"]),
                name=rep["name"], type=typ, aliases=aliases,
                first_revealed_chapter=min(m["first_chapter"] for m in members),
                importance=sum(m["count"] for m in members),
                summary=summary,
                full_name=full_name, identity=identity, background=background,
                # P0 大改字段
                entity_subtype=subtype,
                parent_name=parent_name,  # 实体名,后续在 resolve_and_write 阶段映射成 parent_logical_key
            ))
    # logical_key 去重(不同 type 撞名时加后缀)
    seen: dict[str, int] = {}
    for c in canon:
        if c.logical_key in seen:
            seen[c.logical_key] += 1
            c.logical_key = f"{c.logical_key}_{c.type}"
        else:
            seen[c.logical_key] = 1
    # phase_backend: 通过函数属性暴露 stats(避免改返回类型)
    cluster_method = "embedding+heuristic" if (embedder is not None and not embedder_fallback) else "heuristic"
    cluster_entities._last_stats = {"embedder_fallback": embedder_fallback, "fallback_reason": fallback_reason, "cluster_method": cluster_method}
    return canon


def resolve_and_write(db, script_id: int, chapter_extracts: list, *, embedder=None,
                      public_threshold: int = 3, book_id: int | None = None) -> dict:
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

    # P0 大改:用 name→logical_key 映射把 parent_name 解析成 parent_logical_key
    # 一次扫:先建 name index,再 backfill parent_logical_key
    name_to_lk: dict[str, str] = {c.name: c.logical_key for c in canon}
    # 同时把所有 aliases 也映射到对应 logical_key(如"国防军"是"德军"的 alias → 都映射 →
    # parent="国防军" 也能解析到"德军_faction" 这条 canon)
    for c in canon:
        for al in (c.aliases or []):
            if al and al not in name_to_lk:
                name_to_lk[al] = c.logical_key
    for c in canon:
        if c.parent_name and not c.parent_logical_key:
            c.parent_logical_key = name_to_lk.get(c.parent_name, "")
            # 没匹配到 = parent 在 LLM 抽到的实体集外(常见,父级未在本批章节出场)
            # 留空,后续若该 parent 被抽到再 re-resolve 会补上
        # 防自指:LLM 偶尔把"德国 parent=德国"标进来,要清空
        if c.parent_logical_key == c.logical_key:
            c.parent_logical_key = ""

    written = 0
    for c in canon:
        canon_repo.upsert_canon_entity(
            db, script_id, c.logical_key, name=c.name, type=c.type, aliases=c.aliases,
            summary=c.summary, first_revealed_chapter=c.first_revealed_chapter,
            public_knowledge=(c.importance > public_threshold and c.first_revealed_chapter == 1),
            importance=c.importance,
            entity_subtype=c.entity_subtype,
            parent_logical_key=c.parent_logical_key,
            # v33: full_name / identity / background 透传到 canon 层
            full_name=c.full_name,
            identity=c.identity,
            background=c.background,
        )
        written += 1

    # v28: 同步 character 类 canon → character_cards 表(NPC 角色卡)
    character_canon = [c for c in canon if c.type == "character"]
    npc_cards_written = sync_character_cards_from_canon(db, script_id, character_canon, book_id=book_id)

    # phase_backend: 把 cluster_entities 的 stats(embedder_fallback / cluster_method)
    # 透出给 progress 上报,告知前端"这次提取的实体聚类用了向量 vs 纯启发式",
    # 帮 ops 判断 Vertex 配额/凭证健康度。
    cluster_stats = getattr(cluster_entities, "_last_stats", {}) or {}
    return {"mentions": len(mentions), "entities_written": written,
            "by_type": _count_by_type(canon),
            "npc_cards_written": npc_cards_written,
            "embedder_fallback": bool(cluster_stats.get("embedder_fallback")),
            "fallback_reason": cluster_stats.get("fallback_reason", ""),
            "cluster_method": cluster_stats.get("cluster_method", "heuristic")}


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
def _reclassify_canon_type(name: str, current_type: str) -> str:
    """启发式纠正 LLM 抽错的 type — 实测 LLM 把"无忧宫/淡水河谷/皇家海军/泛人类主义者"
    都标 faction,但显然是 location/company/military-branch/ideology。

    返回纠正后的 type ∈ {character, faction, organization, location, item, concept}。
    保守策略:只在 name 后缀 / 关键词强信号时纠错,其他保留 LLM 原判断。
    """
    import re
    n = (name or "").strip()
    cur = (current_type or "").strip().lower()
    # 1. location 强信号(地点专用后缀)
    LOC_SUFFIX = ("宫", "府", "邸", "城", "楼", "塔", "港", "岛", "山", "河", "湖", "海",
                  "庄园", "宅", "广场", "街", "巷")
    if any(n.endswith(s) for s in LOC_SUFFIX):
        return "location"
    # 2. organization 强信号(文艺/商业组织)— 优先于军政规则,避免"XX乐团/乐队"被钉死为 faction
    ORG_KEYWORDS = (
        "乐队", "乐团", "组合", "剧团", "歌剧团", "合唱团", "舞团",
        "管弦乐团", "交响乐团", "俱乐部", "工作室", "事务所",
    )
    if any(k in n for k in ORG_KEYWORDS):
        return "organization"
    if re.search(r"[乐剧歌舞合唱管弦交响].{0,2}[团队社]", n):
        return "organization"
    # 3. concept 强信号(思想 / 制度 / 公司 / 工厂 / 体系)
    CONCEPT_SUFFIX = ("主义", "主义者", "派", "学派", "厂", "公司", "集团", "局", "院",
                      "议会", "委员会", "教", "宗")
    if any(n.endswith(s) for s in CONCEPT_SUFFIX):
        # 不要把"皇家海军"标 concept — 包含"军/师/团/旅/营"等军事单位词的归 faction
        if not any(k in n for k in ("军", "师", "团", "旅", "营", "队")):
            return "concept"
    return cur or current_type


def build_constant_worldbook(db, script_id: int, book_id: int, seed) -> int:
    """生成 worldbook_entries — 每个 faction/power_concept/location 独立一条。

    book_id 必填。constant 条目每轮无条件常驻注入,但 priority 分层 —
    纪元 100(必注入)/ 力量体系 95(头部)/ 主要势力 90(头部)/
    单个 faction-detail 80 / power-detail 75 / location-detail 65.

    content 富化策略:
      1. 优先用 kb_canon_entities.summary (LLM 抽取的背景描述)
      2. 没 summary → 用 importance + first_revealed_chapter + aliases 拼最小 stub
      3. 旧版聚合"主要势力:A、B、C" 改为每个独立 + 头部仍保留索引条

    设计动机:500 万字小说有 30-100 个核心势力/概念/地点,旧版聚合 1 条 12 个名字
    GM 几乎拿不到设定厚度。拆分后 search_canon / get_worldbook 检索到的是独立背景
    描述,GM 叙事有真实原著感。
    """
    # 清旧 extracted 之外的条目
    db.execute(
        "delete from worldbook_entries where script_id=%s and book_id=%s "
        "and (metadata->>'source' is null or metadata->>'source' <> 'extracted')",
        (script_id, book_id),
    )

    # 拉 canon_entities 拿 summary 富化 + 层级树(P0 大改)
    canon_by_name: dict[str, dict] = {}
    canon_by_lk: dict[str, dict] = {}
    children_by_lk: dict[str, list[dict]] = {}
    try:
        rows = db.execute(
            "select logical_key, name, type, entity_subtype, parent_logical_key, "
            "summary, aliases, first_revealed_chapter, importance "
            "from kb_canon_entities where script_id=%s",
            (script_id,),
        ).fetchall()
        for r in rows or []:
            nm = (r.get("name") or "").strip()
            lk = (r.get("logical_key") or "").strip()
            if nm:
                canon_by_name[nm] = dict(r)
            if lk:
                canon_by_lk[lk] = dict(r)
            plk = (r.get("parent_logical_key") or "").strip()
            if plk:
                children_by_lk.setdefault(plk, []).append(dict(r))
    except Exception:
        pass

    # 场景污染词 — summary 包含这些 = 章节内角色行为描述,不是永久设定
    # 例:"和服 · 茜茜和薇欧拉穿着..." 这种把"角色穿了什么"抽成"和服的设定" = 污染
    _CONTAMINATION_WORDS = (
        "穿着", "穿了", "穿过", "披着", "披上", "戴着", "戴上",
        "拿着", "拿起", "举着", "握着", "手持", "提着",
        "正在", "此时", "刚才", "刚刚", "瞬间", "突然",
        "腿上", "身上", "脸上", "手上", "胸前",
    )

    def _is_contaminated(summary: str) -> bool:
        return any(w in summary for w in _CONTAMINATION_WORDS)

    def _enrich(name: str, fallback_brief: str) -> str:
        c = canon_by_name.get(name)
        sm = (c or {}).get("summary") or ""
        sm = sm.strip()
        # summary 非空 + 没污染 → 直接用
        if sm and not _is_contaminated(sm):
            return sm[:600]
        # summary 污染了 → log + fallback,**不用这个 summary**
        # fallback:用 importance + aliases 拼 stub
        if c:
            parts = [fallback_brief]
            aliases = c.get("aliases") or []
            if aliases:
                parts.append("别名: " + "、".join(str(x) for x in aliases[:5]))
            first = c.get("first_revealed_chapter")
            if first:
                parts.append(f"首次出场: 第 {first} 章")
            imp = c.get("importance")
            if imp:
                parts.append(f"出场频次: {imp}")
            return " · ".join(parts)
        return fallback_brief

    # importance 阈值:概念 / 地点 < 5 次出场不入 worldbook(单次出场的章节细节剔出)
    # character 不走 worldbook 路径(走 character_cards),所以不在这里管
    MIN_IMPORTANCE_FOR_WORLDBOOK = 5
    def _passes_importance(name: str, min_imp: int = MIN_IMPORTANCE_FOR_WORLDBOOK) -> bool:
        c = canon_by_name.get(name)
        if not c:
            return True  # canon 没数据时不阻拦(走 fallback stub)
        return int(c.get("importance") or 0) >= min_imp

    entries: list[tuple[str, str, int]] = []  # (title, content, priority)
    if getattr(seed, "era", ""):
        entries.append((
            "纪元",
            f"本作纪元固定为「{seed.era}」。所有时间表述以此为准,绝不套用现实世界年代。",
            100,
        ))
    # 头部聚合索引(留给 GM 一眼看全貌)
    if getattr(seed, "power_system", None):
        entries.append((
            "力量体系 · 索引",
            "本作核心力量体系名录(详条另查): " + "、".join(seed.power_system),
            95,
        ))
    if getattr(seed, "key_factions", None):
        entries.append((
            "主要势力 · 索引",
            "本作主要势力名录(详条另查): " + "、".join(seed.key_factions[:20]),
            90,
        ))
    # P0 大改:按 subtype 聚类(开放标签 group by)+ parent 树状渲染
    # 旧版按硬编码"势力/力量/地点/概念"4 桶分;新版按 LLM 自抽的 subtype 自然聚类,
    # 不预设题材风格。每个 entity 走 importance 阈值 + 启发式 type 纠错。
    candidates: list[dict] = []
    for c in canon_by_name.values():
        orig_type = c.get("type") or ""
        if orig_type not in ("faction", "organization", "location", "concept"):
            continue  # character/item 不走 worldbook
        new_type = _reclassify_canon_type(c.get("name", ""), orig_type)
        if new_type not in ("faction", "organization", "location", "concept"):
            continue
        if not _passes_importance(c.get("name", "")):
            continue
        # concept 额外要求 summary 干净(避免"和服=茜茜穿着..."进库)
        if new_type == "concept":
            sm = (c.get("summary") or "").strip()
            if not sm or _is_contaminated(sm):
                continue
        candidates.append({**c, "_eff_type": new_type})

    # 按 (eff_type, subtype) group 聚类,subtype 空就归"其它"
    # 用 dict 保持插入顺序;每组内按 importance desc 排
    groups: dict[tuple[str, str], list[dict]] = {}
    for c in candidates:
        key = (c["_eff_type"], (c.get("entity_subtype") or "").strip() or "其他")
        groups.setdefault(key, []).append(c)
    for v in groups.values():
        v.sort(key=lambda c: int(c.get("importance") or 0), reverse=True)

    # seed.key_factions / power_system 是 author 钉死的核心,**优先**进 worldbook(免过滤)
    seed_factions = list((getattr(seed, "key_factions", None) or [])[:40])
    seed_powers = list((getattr(seed, "power_system", None) or [])[:30])
    seen_titles: set[str] = set()
    for fac in seed_factions:
        title = f"势力·{fac}"
        if title in seen_titles:
            continue
        seen_titles.add(title)
        content = _enrich(fac, f"势力名称: {fac}")
        entries.append((title, content, 82))
    for power in seed_powers:
        title = f"力量·{power}"
        if title in seen_titles:
            continue
        seen_titles.add(title)
        content = _enrich(power, f"力量体系条目: {power}")
        entries.append((title, content, 75))

    # 按 subtype 分组渲染 — 每组先一条索引(列出本 subtype 所有成员)+ 单条 detail
    # 例:subtype="军队" 索引条 = "军队类:德军、美军、俄军";单条 = "势力·德军 = ..."
    SUBTYPE_DISPLAY_PREFIX = {
        "faction": "势力",
        "organization": "组织",
        "location": "地点",
        "concept": "概念",
    }
    SUBTYPE_INDEX_PRIORITY = 88  # 索引在头部
    SUBTYPE_DETAIL_PRIORITY = {"faction": 78, "organization": 76, "location": 65, "concept": 70}
    GROUP_DETAIL_CAP = {"faction": 80, "organization": 60, "location": 40, "concept": 60}
    detail_counts: dict[str, int] = {}
    for (eff_type, subtype), members in sorted(groups.items(), key=lambda kv: -sum(int(m.get("importance") or 0) for m in kv[1])):
        if not members:
            continue
        prefix = SUBTYPE_DISPLAY_PREFIX.get(eff_type, eff_type)
        # 索引条:把同 subtype 的所有成员名字列出来,GM 一眼看到本类有哪些
        names = [m.get("name", "") for m in members if m.get("name")]
        idx_title = f"{prefix}索引·{subtype}"
        if idx_title not in seen_titles and len(names) >= 2:
            seen_titles.add(idx_title)
            idx_content = f"【{subtype}类 {eff_type}】" + "、".join(names[:30])
            entries.append((idx_title, idx_content, SUBTYPE_INDEX_PRIORITY))
        # 每个 entity 一条 detail,跟 parent 显式关联,GM 能看到层级
        for c in members:
            nm = c.get("name", "")
            if not nm:
                continue
            title = f"{prefix}·{nm}"
            if title in seen_titles:
                continue
            if detail_counts.get(eff_type, 0) >= GROUP_DETAIL_CAP.get(eff_type, 60):
                break
            seen_titles.add(title)
            detail_counts[eff_type] = detail_counts.get(eff_type, 0) + 1
            base = _enrich(nm, f"{prefix}: {nm}")
            # 拼层级标签
            parent_lk = (c.get("parent_logical_key") or "").strip()
            parent_label = ""
            if parent_lk:
                p = canon_by_lk.get(parent_lk)
                if p and p.get("name"):
                    parent_label = f" · 归属: {p['name']}"
            # 拼子级标签(本 entity 下有哪些 child)
            my_lk = (c.get("logical_key") or "").strip()
            child_names = [ch.get("name", "") for ch in (children_by_lk.get(my_lk) or []) if ch.get("name")]
            child_label = ""
            if child_names:
                child_label = f" · 下辖: {', '.join(child_names[:6])}"
            content = base + parent_label + child_label
            entries.append((title, content, SUBTYPE_DETAIL_PRIORITY.get(eff_type, 70)))

    written = 0
    for title, content, priority in entries:
        db.execute(
            """
            insert into worldbook_entries(book_id, script_id, title, content, keys, priority, insertion_position, enabled, metadata)
            values (%s, %s, %s, %s, %s, %s, 'constant', true, %s)
            on conflict(script_id, title) do update set
              content=excluded.content, priority=excluded.priority,
              insertion_position='constant',
              metadata=excluded.metadata, updated_at=now()
            """,
            (book_id, script_id, title, content, Jsonb([]), priority,
             Jsonb({"source": "extracted"})),
        )
        written += 1
    return written
