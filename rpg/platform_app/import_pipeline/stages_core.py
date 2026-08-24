"""import_pipeline.stages_core — 确定性阶段(chunks/facts/phase_digests/entities)+ canon/anchors/embeddings 阶段

来源: 原 rpg/platform_app/import_pipeline.py _stage_chunks / _stage_facts / _stage_phase_digests / _stage_entities / _final_stage_status / _stage_canon_extract / canon 回填 helpers / _stage_embeddings(原 L850-914, 1128-1261, 1600-1732, 1878-2138) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from psycopg.types.json import Jsonb

from ..db import connect
from ..perms import script_owned
from .stages_llm import _resolve_extractor_llm


# ══════════════════════════════════════════════════════════════════════
#  阶段实现
# ══════════════════════════════════════════════════════════════════════
def _stage_chunks(ctl: JobController, script_id: int, user_id: int) -> int:
    """切块入 document_chunks（确定性，无 LLM）"""
    from .. import knowledge
    with connect() as db:
        chapters = db.execute(
            "select * from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        if not chapters:
            return 0
        script = script_owned(db, script_id, user_id)
        if not script:
            raise ValueError("script not found")
        book = knowledge._ensure_book(db, script)

        ctl.update(stage_progress=0, stage_total=len(chapters))
        chunk_count = 0
        for i, chapter in enumerate(chapters):
            if ctl.is_cancelled():
                raise RuntimeError("cancelled")
            doc = knowledge._upsert_document(db, book, script, chapter)
            db.execute("delete from document_chunks where document_id = %s", (doc["id"],))
            for ci, content in enumerate(knowledge._chunk_text(chapter["content"])):
                knowledge._insert_chunk(db, book, script, chapter, doc, ci, content)
                chunk_count += 1
            if (i + 1) % 5 == 0 or i == len(chapters) - 1:
                ctl.update(stage_progress=i + 1)
    return chunk_count


def _stage_facts(ctl: JobController, script_id: int, user_id: int) -> int:
    """规则 ChapterFact 入 chapter_facts（确定性）"""
    from .. import knowledge
    chars = knowledge._load_characters()
    world = knowledge._load_world()
    summaries = knowledge._load_summaries()
    known_names = knowledge._known_names(chars)
    known_locations = knowledge._known_locations(world)
    known_concepts = knowledge._known_concepts(world)

    with connect() as db:
        script = script_owned(db, script_id, user_id)
        book = knowledge._ensure_book(db, script)
        chapters = db.execute(
            "select * from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        ctl.update(stage_progress=0, stage_total=len(chapters))
        for i, chapter in enumerate(chapters):
            if ctl.is_cancelled():
                raise RuntimeError("cancelled")
            doc_row = db.execute(
                "select * from documents where script_id = %s and chapter_id = %s",
                (script_id, chapter["id"]),
            ).fetchone()
            if not doc_row:
                doc_row = knowledge._upsert_document(db, book, script, chapter)  # type: ignore[assignment]
            fact = knowledge._fact_from_chapter(chapter, summaries, known_names, known_locations, known_concepts)
            knowledge._upsert_chapter_fact(db, book, script, chapter, doc_row, fact)
            if (i + 1) % 10 == 0 or i == len(chapters) - 1:
                ctl.update(stage_progress=i + 1)
    return len(chapters)

def _stage_phase_digests(script_id: int) -> int:
    """task 86: 把 chapter_facts 按 story_phase 聚合到 phase_digests。

    必须在 _stage_story_phase_llm 之后跑 — 该函数把 story_phase 字段填好。
    生成的 phase_digests 行供 worldbook_agent.consult 的 _resolve_anchor 使用,
    没有这步,新 import 的 script 永远 "未找到精确锚点"。

    实现源自 scripts/aggregate_phase_digests.py:aggregate_for_script,搬进 platform_app
    避免 import pipeline 依赖 scripts/ CLI 路径。
    """
    with connect() as db:
        rows = db.execute(
            """select chapter, story_phase, story_time_label, summary,
                   events, locations, characters
               from chapter_facts where script_id=%s order by chapter""",
            (script_id,),
        ).fetchall()
        if not rows:
            return 0

        by_phase: dict[str, list[dict]] = {}
        for r in rows:
            phase = (r["story_phase"] or "").strip() or "未分组"
            by_phase.setdefault(phase, []).append(dict(r))

        # 重跑前清表(避免重复 import 时残留旧 phase_label)
        db.execute("delete from phase_digests where script_id=%s", (script_id,))

        n = 0
        for phase, chapters in by_phase.items():
            chs = [c["chapter"] for c in chapters]
            cmin, cmax = min(chs), max(chs)
            summary_parts = []
            for c in chapters[:50]:
                s = (c.get("summary") or "").strip()
                if s:
                    summary_parts.append(f"第{c['chapter']}章 · {s[:120]}")
            summary = "\n".join(summary_parts)[:3000]
            tls = [c.get("story_time_label") or "" for c in chapters if c.get("story_time_label")]
            tl_start = tls[0] if tls else ""
            tl_end = tls[-1] if tls else ""
            ev_seen: set[str] = set()
            ev_entries: list[dict] = []
            for c in chapters:
                for ev in (c.get("events") or [])[:5]:
                    if isinstance(ev, dict):
                        text = str(ev.get("event") or "").strip()
                        if text and text not in ev_seen:
                            ev_seen.add(text)
                            ev_entries.append({"chapter": c["chapter"], "event": text})
            key_events = ev_entries[:30]
            loc_counter: Counter = Counter()
            for c in chapters:
                for loc in (c.get("locations") or []):
                    name = loc.get("name") if isinstance(loc, dict) else str(loc)
                    if name:
                        loc_counter[name] += loc.get("count", 1) if isinstance(loc, dict) else 1
            key_locations = [{"name": n_, "freq": cnt} for n_, cnt in loc_counter.most_common(15)]
            char_counter: Counter = Counter()
            for c in chapters:
                for ch in (c.get("characters") or []):
                    name = ch.get("name") if isinstance(ch, dict) else str(ch)
                    if name:
                        char_counter[name] += ch.get("count", 1) if isinstance(ch, dict) else 1
            key_characters = [{"name": n_, "freq": cnt} for n_, cnt in char_counter.most_common(15)]

            db.execute(
                """insert into phase_digests(
                  script_id, phase_label, chapter_min, chapter_max, summary,
                  key_events, key_locations, key_characters,
                  story_time_label_start, story_time_label_end, chapter_count
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (script_id, phase, cmin, cmax, summary,
                 Jsonb(key_events), Jsonb(key_locations), Jsonb(key_characters),
                 tl_start, tl_end, len(chapters)),
            )
            n += 1
        return n


# 高频人名扫描的章节范围,与 UI 文案「扫前 30 章高频角色名」对齐。
_ENTITY_SCAN_CHAPTERS = 30
# 候选名最大扩展长度(中文姓名/称谓极少超过 6 字:「猛虎教练」「阿尔弗雷德」)。
_NAME_MAX_LEN = 6
# 扩展判据:某个后继/前驱字占该候选全部出现次数的比例 ≥ 此值,说明候选只是更长名字的
# 影子(如「猛虎教」几乎总是出现在「猛虎教练」里),用更长的形式取代它。
_SHADOW_RATIO = 0.85
# 扩展分析扫描的正文上限(整本书没必要全扫,前 20 万字足够定名)。
_NAME_SCAN_CHARS = 200_000


# 中文人名/称谓的首尾几乎不会是虚词。这是**结构性**判据(与 task 47 那份逐词维护的
# 黑名单不同,不需要跟着内容更新),专门清掉「教练的」「的说」「着教练」这类
# n-gram 残渣 —— 它们不产生错卡(LLM 会判 false),但会白占前 30 的名额、挤掉真名。
# 只在**首尾**位置判,避免误伤「不知火」「花不弃」这类名字中间含虚词的情形。
_EDGE_PARTICLES = frozenset("的了着是在和就都也把被而却又还很太最更与但只这那有会能")
# 名字后面最常跟的动词/短语字:扩展时绝不能把它们吞进名字(「郑吒说」「许荣泰道」)。
_TAIL_STOP_CHARS = frozenset("说道看想问笑点走来去回叫喊听坐站转拿手心脸眼身上下里中前后")


def _is_particle_fragment(name: str) -> bool:
    return bool(name) and (name[0] in _EDGE_PARTICLES or name[-1] in _EDGE_PARTICLES)


def _absorbable(ch: str, cur: str, *, tail: bool) -> bool:
    """扩展时能不能把邻字 ch 吞进候选 cur。

    两条止损:
      · 动词/方位字与虚词不能进名字(「郑吒」+「说」→「郑吒说」);
      · 邻字等于候选自己的首字(右扩)或尾字(左扩)= 周期性重复的签名,
        再吞就是把相邻那一次出现的头/尾吃进来了。
    """
    if not ch or not cur:
        return False
    if ch == (cur[0] if tail else cur[-1]):
        return False
    if ch in _EDGE_PARTICLES:
        return False
    return not (tail and ch in _TAIL_STOP_CHARS)


def _grow_name(text: str, name: str) -> tuple[str, int]:
    """把 n-gram 候选长回真名,返回 (定型后的名字, 出现次数)。

    2–3 字 n-gram 切不出 4 字以上的名字:「猛虎教练」只会以「猛虎教」「虎教练」的
    形式进候选池,于是同一个人被拆成两张卡,而真名一张都没有(生产实测:script 321
    同时有「猛虎教」和「猛虎教练」两张 NPC 卡)。

    判据是确定性的:统计该候选每次出现时紧邻的前/后一个字,若某个字占比 ≥
    _SHADOW_RATIO,说明这个候选几乎从不独立出现 —— 它是更长名字的一截,吞掉那个字
    继续长,直到没有压倒性的邻字或到 _NAME_MAX_LEN 为止。
    """
    cur = name
    for _ in range(_NAME_MAX_LEN):
        if len(cur) >= _NAME_MAX_LEN:
            break
        occ = list(re.finditer(re.escape(cur), text))
        total = len(occ)
        if total < 3:  # 样本太少,不做扩展判断
            break
        nxt: Counter[str] = Counter()
        prv: Counter[str] = Counter()
        for m in occ:
            e, b = m.end(), m.start()
            if e < len(text) and "\u4e00" <= text[e] <= "\u9fff":
                nxt[text[e]] += 1
            if b > 0 and "\u4e00" <= text[b - 1] <= "\u9fff":
                prv[text[b - 1]] += 1
        best_next = nxt.most_common(1)[0] if nxt else ("", 0)
        best_prev = prv.most_common(1)[0] if prv else ("", 0)
        # 两条止损:
        #  · 动词/方位字不能吞进名字(「郑吒」+「说」→「郑吒说」);虚词同理。
        #  · 邻字等于候选自己的首字 = 周期性重复(「暗夜观察者暗夜观察者…」),
        #    再长下去就是把下一次出现的头吃进来了。
        grow_next = (best_next[1] >= total * _SHADOW_RATIO
                     and _absorbable(best_next[0], cur, tail=True))
        grow_prev = (best_prev[1] >= total * _SHADOW_RATIO
                     and _absorbable(best_prev[0], cur, tail=False))
        # 前后都能长时取占比更高的那侧
        if grow_next and best_next[1] >= best_prev[1]:
            cur = cur + best_next[0]
        elif grow_prev:
            cur = best_prev[0] + cur
        else:
            break
    return cur, text.count(cur)


def _collapse_name_shadows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """丢掉「只是更长候选的一截」的影子名,保留信息量更大的长名。

    A 是 B 的子串且两者出现次数接近(A 几乎只在 B 里出现)→ A 是影子。
    这一步专治同一个人被拆成两张卡(「猛虎教」605 次 / 「猛虎教练」604 次)。
    """
    kept: list[dict[str, Any]] = []
    for it in sorted(items, key=lambda x: (-len(x["name"]), -x["count"])):
        name, cnt = it["name"], it["count"]
        shadow = False
        for k in kept:
            if name != k["name"] and name in k["name"] and cnt <= k["count"] * 1.15:
                shadow = True
                break
        if not shadow:
            kept.append(it)
    # 还原调用方给的名次(词频序),不按 count 重排 —— 见 _stage_entities 的注释。
    order = {it["name"]: i for i, it in enumerate(items)}
    kept.sort(key=lambda x: order.get(x["name"], 10**6))
    return kept


def _stage_entities(ctl: JobController, script_id: int, user_id: int) -> list[dict[str, Any]]:
    """高频人名提取（中文 2-3 字 + 出现次数排序）。

    简化策略：从 character_cards 已有别名 + 文本里出现的高频候选名合并。
    实际生产可换更聪明的 NER。
    """
    with connect() as db:
        chapters = db.execute(
            # 与 UI「扫前 30 章高频角色名」一致:只扫前 30 章(主角通常早出场)。
            # 此前漏 order/limit → 扫全书,把后期/次要角色也生成了卡(用户反馈:全书 NPC 都生成了)。
            "select content from script_chapters where script_id = %s "
            "order by chapter_index limit %s",
            (script_id, _ENTITY_SCAN_CHAPTERS),
        ).fetchall()
        existing_names = set()
        for r in db.execute(
            # v28: 显式 card_type='npc' 过滤,虽然 PC/persona 当前没 script_id 不会被命中,
            # 但避免未来加跨表用法时静默污染候选词表
            "select name, aliases from character_cards where script_id = %s and card_type = 'npc'",
            (script_id,),
        ).fetchall():
            existing_names.add(r["name"])
            existing_names.update(r.get("aliases") or [])

    full_text = "\n".join(c["content"] for c in chapters)
    # 候选：2-3 字中文连续词，且不在常见停用词里
    candidates = re.findall(r"[一-鿿]{2,3}", full_text)
    # task 47: 复用 session.py 的统一 blacklist,避免维护两份。包含 40+ 高频副词/
    # 连词/语气词("不知道/起来/有德的/不过/这时候/看起来"等)+ 盗版宣传残留。
    from platform_app.knowledge.session import _CHINESE_NON_NAME_BLACKLIST
    stop = set(_CHINESE_NON_NAME_BLACKLIST)
    counter = Counter(c for c in candidates if c not in stop)
    ctl.update(stage_progress=1, stage_total=1)

    # n-gram 候选 → 长回真名 → 去影子。
    # 只做 n-gram 词频的老口径有两个硬伤(生产实测):4 字以上的名字永远进不了候选池,
    # 而它的 3 字前缀会冒充人名 → 同一个人两张卡、真名一张没有(反馈 #99 的主因)。
    scan_text = full_text[:_NAME_SCAN_CHARS]
    # ⚠️ 只**定型**,不重排:名次仍按原始 n-gram 词频序。
    # (用「长完后的全文计数」重排会让「自己」「什么」这类泛词跃到前面,把真名挤出前 30
    #  —— 实测 script 379 的「木湘茹」「小岩姐」就是这么掉出去的。)
    grown: dict[str, int] = {}
    for n, cnt in counter.most_common(80):
        if _is_particle_fragment(n):
            continue
        name, real_cnt = _grow_name(scan_text, n)
        if not name or len(name) < 2 or _is_particle_fragment(name):
            continue
        # 长回来后可能与别的候选重名(「猛虎教」「虎教练」都长成「猛虎教练」)→ 取大;
        # dict 保持首次出现序 = 原词频名次,后来者不改变名次。
        grown[name] = max(grown.get(name, 0), real_cnt or cnt)
    top_n = [{"name": n, "count": c} for n, c in grown.items()]
    top_n = _collapse_name_shadows(top_n)
    # 已有卡的名字始终保留(用户手编/上一轮确认过的,不能被本轮候选挤掉)
    for n in existing_names:
        if not any(x["name"] == n for x in top_n):
            top_n.append({"name": n, "count": full_text.count(n) or counter.get(n, 0)})
    return top_n[:60]


def _final_stage_status(stages_progress: list[dict[str, Any]]) -> str:
    """phase_backend: 根据各 stage 是否有 error 决定 job 终态。
    返回 'done' / 'done_with_errors'。任何 stage 标 error → done_with_errors。
    """
    for s in stages_progress:
        if s.get("status") == "error":
            return "done_with_errors"
    return "done"

def _stage_canon_extract(
    ctl: JobController, user_id: int, script_id: int,
) -> tuple[int, int, str, str]:
    """v29 (一站完成): 在 wizard 末尾 chain LLM 弧段提取。

    跑 extract.arc_pipeline.run_arc_extraction:
      - resolve_and_write → kb_canon_entities
      - build_timeline → script_timeline_anchors
      - build_constant_worldbook → canon-based worldbook_entries
      - embed_canon_entities → canon entity 向量

    返回 (canon_count, anchors_count, canon_status, anchors_status)。
    任何失败:写 warnings,把对应 stage 标 error,**不抛**给 _run_pipeline。
    """
    import logging as _logging
    import traceback as _tb
    _log = _logging.getLogger(__name__)
    api_id, model = _resolve_extractor_llm(user_id)

    # 读 book_id(canon_extract 必须有)
    with connect() as db:
        book_row = db.execute(
            "select b.id as book_id from books b "
            "where b.script_id = %s order by b.id limit 1",
            (script_id,),
        ).fetchone()
        if not book_row:
            _log.warning("[canon_extract] no book row for script %s", script_id)
            try:
                ctl.update(warnings={
                    "stage": "canon_extract",
                    "exception": "MissingBook",
                    "message": "无 book 记录,canon/anchors/worldbook 跳过",
                })
            except Exception:
                pass
            return 0, 0, "error", "error"
        book_id = int(book_row["book_id"])

    # 进度回调 — 把 arc_pipeline 的 stage 转成 stage_progress
    def _progress(stage: str, info: dict) -> None:
        try:
            if stage == "arc_extract" and "done" in info and "total" in info:
                ctl.update(
                    stage_progress=int(info.get("done") or 0),
                    stage_total=int(info.get("total") or 1),
                )
        except Exception:
            pass

    ctl.update(stage_progress=0, stage_total=1)
    try:
        from extract.arc_pipeline import run_arc_extraction
        result = run_arc_extraction(
            script_id, book_id,
            user_id=user_id,
            model=model, api_id=api_id,
            progress_cb=_progress,
        )
    except Exception as exc:
        _log.warning("[canon_extract] run_arc_extraction raised: %s", exc, exc_info=True)
        try:
            ctl.update(warnings={
                "stage": "canon_extract",
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
                "traceback": _tb.format_exc()[:600],
            })
        except Exception:
            pass
        return 0, 0, "error", "error"

    if not result.get("ok"):
        err = str(result.get("error") or "unknown")
        _log.warning("[canon_extract] arc_pipeline returned !ok: %s", err)
        try:
            ctl.update(warnings={
                "stage": "canon_extract",
                "exception": "ArcPipelineFailed",
                "message": err[:300],
            })
        except Exception:
            pass
        # 部分写入(seed/部分 arc)可能有,从 DB 实际计数
        canon_n, anchors_n = _count_canon_and_anchors(script_id)
        return canon_n, anchors_n, "error", "error"

    canon_n, anchors_n = _count_canon_and_anchors(script_id)
    # 时间线为 0 不算 fatal — canon 写了就 ok,只把 anchors 标 error
    anchors_status = "done" if anchors_n > 0 else "error"
    canon_status = "done" if canon_n > 0 else "error"
    # canon 写完后回填 character_cards 的主角标识 + priority 排序
    # (cards stage 跑在 canon_extract 之前,当时 kb_canon_entities 是空,
    # 没法 join 排序,只能等 canon 写完再做)
    _rerank_cards_by_canon_importance(script_id)

    # 数据线接通: kb_canon_entities (LLM 抽过) → chapter_facts.events (启发式抽不出)。
    # 用户出生点选了 ch1 → harness 在 retrieval.py 已经会把 save_anchor_states 的
    # pending 锚点喂给 GM 做"命运式手段拉回剧情"。但 save_anchor_states 是从
    # chapter_facts.events 抽的,启发式 _extract_fact 在新剧本(没 known_names seed)
    # 时 events 全空 → ch1 永远没 anchor → GM 完全不知道该让卡切尔登场。
    # 这里用现成的 LLM 产物把 events 接回来,链路自然修通。
    try:
        _backfilled = _backfill_chapter_facts_events_from_canon(script_id)
        import logging as _log
        _log.getLogger(__name__).info(
            "[canon→facts] script_id=%s backfilled events for %d chapters",
            script_id, _backfilled,
        )
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning(
            "[canon→facts] backfill failed: %s", exc, exc_info=True,
        )

    # 时间感知 KB(P1/P4):canon + chapter_facts.events 已就绪 → 物化揭示锚点 DAG + 三实体表
    # reveal_anchor_key 映射,使该剧本的【新游戏】立刻具备前沿门控/统一召回(防剧透 + 进度按锚点)。
    # 不挂这里则新导入的剧本无 reveal_anchors → 其上的新游戏退化为"不防剧透"。非致命,失败只告警。
    try:
        from kb.reveal import backfill_entity_reveal_anchors, backfill_reveal_anchors
        _ra = backfill_reveal_anchors(script_id)
        _ea = backfill_entity_reveal_anchors(script_id)
        import logging as _log
        _log.getLogger(__name__).info(
            "[temporal-kb] script_id=%s reveal_anchors=%s entity_mapped=%s",
            script_id, _ra.get("anchors"), _ea.get("total"),
        )
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning("[temporal-kb] anchor backfill failed: %s", exc, exc_info=True)

    ctl.update(stage_progress=1, stage_total=1)
    return canon_n, anchors_n, canon_status, anchors_status

def _backfill_chapter_facts_events_from_canon(script_id: int) -> int:
    """把 kb_canon_entities 反向回填到 chapter_facts.events,把数据线接通。

    chapter_facts.events 由启发式 _extract_fact 产出 — 依赖 known_names seed,
    新剧本(无 seed)时 events 几乎全空 → save_anchor_states 漏抽 → harness
    pending_anchors 注入 GM 时缺关键钩子(如 ch1 卡切尔登场)。

    kb_canon_entities 是 LLM 抽过的:每个实体有 first_revealed_chapter + summary
    + importance + type。这里按 first_revealed_chapter 分组,每章把当章首次
    登场的实体合成 events 数组项,merge 进 chapter_facts.events。

    merge 策略:不覆盖已有 events 项(启发式抽出来的保留),只追加 canon 派生
    的 "实体登场" 事件,避免重复。
    """
    n_chapters = 0
    with connect() as db:
        # 1. 拉 canon entities 按章节分组 - 多拉 identity / background / aliases 给 anchor 注入
        # 用更多 hint。D20 等 item 类 summary 通常空,但 identity / aliases 一般有,
        # 拼进 anchor 文本让 GM 不会把"D20"按 d&d 训练偏见写成二十面骰子。
        ent_rows = db.execute(
            """select name, type, importance, first_revealed_chapter, summary,
                      identity, background, aliases
               from kb_canon_entities
               where script_id=%s and first_revealed_chapter is not null
                 and coalesce(importance, 0) >= 3
               order by first_revealed_chapter asc, importance desc nulls last""",
            (script_id,),
        ).fetchall() or []
        by_chapter: dict[int, list[dict[str, Any]]] = {}
        for r in ent_rows:
            ch = int(r["first_revealed_chapter"])
            by_chapter.setdefault(ch, []).append(dict(r))

        for chapter_num, ents in by_chapter.items():
            # 2. 拉本章现有 events,合并去重
            cf = db.execute(
                "select events from chapter_facts where script_id=%s and chapter=%s",
                (script_id, chapter_num),
            ).fetchone()
            if not cf:
                continue
            existing = cf["events"] or []
            if not isinstance(existing, list):
                existing = []
            # 已有 event.text 集合,canon 派生事件不重复添加
            seen_texts: set[str] = set()
            for e in existing:
                if isinstance(e, dict):
                    t = str(e.get("event") or "").strip()
                    if t:
                        seen_texts.add(t)
            # 3. 把 canon entities 合成 events (一个 entity = 一个 "X 在此章首次登场" 事件)
            added = 0
            new_events = list(existing)
            for ent in ents:
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                etype = ent.get("type") or "entity"
                summary = (ent.get("summary") or "").strip()
                identity = (ent.get("identity") or "").strip()
                background = (ent.get("background") or "").strip()
                aliases_raw = ent.get("aliases") or []
                if isinstance(aliases_raw, str):
                    aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]
                else:
                    aliases = [a for a in aliases_raw if isinstance(a, str) and a and a != name]
                imp = int(ent.get("importance") or 0)
                # 拼 entity hint: 优先 summary, 否则 identity, 都空 → background, 都空 → 无 hint
                # 再附 aliases (前 3 个 != name 的) 防 GM 按裸名脑补
                hint_parts: list[str] = []
                if summary:
                    hint_parts.append(summary[:120])
                elif identity:
                    hint_parts.append(identity[:120])
                elif background:
                    hint_parts.append(background[:120])
                if aliases[:3]:
                    hint_parts.append(f"别名: {', '.join(aliases[:3])}")
                hint = " / ".join(hint_parts)

                # 事件文本:type 不同模板不同
                if etype == "character":
                    ev_text = f"{name}({etype})首次登场"
                elif etype == "location":
                    ev_text = f"场景{name}首次出现"
                elif etype in ("concept", "item", "faction"):
                    ev_text = f"{etype}「{name}」首次引入"
                else:
                    ev_text = f"{name} 首次出现"
                if hint:
                    ev_text += f": {hint}"
                if ev_text in seen_texts:
                    continue
                seen_texts.add(ev_text)
                # importance 转 high/medium/low (anchor_seed _compute_importance 读这个)
                if imp >= 20:
                    sev = "high"
                elif imp >= 8:
                    sev = "medium"
                else:
                    sev = "low"
                new_events.append({
                    "scene_index": 0,
                    "event": ev_text[:300],
                    "participants": [name] if etype == "character" else [],
                    "locations": [name] if etype == "location" else [],
                    "concepts": [name] if etype in ("concept", "item", "faction") else [],
                    "importance": sev,
                    "evidence": summary[:180] if summary else "",
                    "_source": "canon_backfill",
                    "_canon_importance": imp,
                })
                added += 1
            if added > 0:
                db.execute(
                    "update chapter_facts set events = %s, updated_at = now() "
                    "where script_id=%s and chapter=%s",
                    (Jsonb(new_events), script_id, chapter_num),
                )
                n_chapters += 1
    return n_chapters


def _rerank_cards_by_canon_importance(script_id: int) -> None:
    """canon_extract 完成后,按 kb_canon_entities.importance 重排 character_cards.priority。

    - importance 最高的 character → 主角(priority=110, metadata.is_protagonist=true)
    - 其他配角 priority = max(50, 110 - canon_rank),按 importance desc 递减
    - metadata 写 canon_rank / canon_importance,前端可显示"主角 / 重要配角"等
    - cards 表里没在 canon 里的(LLM 没识别成 character entity)保持原 priority=100
    """
    try:
        with connect() as db:
            # 人工锁定的主角(metadata.protagonist_locked=true)优先于 canon importance:
            # 用户手动纠正过主角后,重新提取不能再按 LLM importance 把它覆盖回去
            # (见 character_cards.set_character_card_protagonist)。有锁时:① 锁定卡完全
            # 不动(WHERE 排除),② 其它 canon 卡一律 is_protagonist=false 且不抢 110 位。
            has_lock = bool(db.execute(
                "select 1 from character_cards "
                "where script_id=%s and card_type='npc' "
                "and coalesce((metadata->>'protagonist_locked')::boolean, false) limit 1",
                (script_id,),
            ).fetchone())
            db.execute(
                """
                with imp as (
                  select name, importance,
                         row_number() over (order by importance desc) as rk
                  from kb_canon_entities
                  where script_id=%(sid)s and type='character'
                )
                update character_cards cc
                set priority = case when %(has_lock)s then greatest(50, 110 - imp.rk)
                                    when imp.rk = 1 then 110
                                    else greatest(50, 110 - imp.rk) end,
                    metadata = cc.metadata || jsonb_build_object(
                        'is_protagonist', case when %(has_lock)s then false else imp.rk = 1 end,
                        'canon_importance', imp.importance,
                        'canon_rank', imp.rk
                    )
                from imp
                where cc.script_id=%(sid)s and cc.name = imp.name
                  and coalesce((cc.metadata->>'protagonist_locked')::boolean, false) = false
                """,
                {"sid": script_id, "has_lock": has_lock},
            )
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[cards] _rerank_cards_by_canon_importance failed for script %s: %s",
            script_id, exc,
        )


def _count_canon_and_anchors(script_id: int) -> tuple[int, int]:
    """读 DB 拿 (kb_canon_entities count, script_timeline_anchors count)。"""
    try:
        with connect() as db:
            c_row = db.execute(
                "select count(*) as c from kb_canon_entities where script_id = %s",
                (script_id,),
            ).fetchone()
            a_row = db.execute(
                "select count(*) as c from script_timeline_anchors where script_id = %s",
                (script_id,),
            ).fetchone()
            return int(c_row["c"]) if c_row else 0, int(a_row["c"]) if a_row else 0
    except Exception:
        return 0, 0


def _stage_embeddings(
    ctl: JobController, user_id: int, script_id: int,
) -> tuple[str, int]:
    """v29 一站完成: 触发 chunks / cards / worldbook 向量化。
    canon embedding 在 canon_extract 已做。embed_script 是 fire-and-forget,
    本 stage 等几秒看 chunks 进度,完成或部分完成都返 done — 后台线程继续跑。

    返回 (status, done_count)。partial 状态归 'done'(后台会继续)。
    """
    import logging as _logging
    import time as _time
    _log = _logging.getLogger(__name__)

    # 验证 embedding provider 可用
    try:
        from ..knowledge.embedding import embed_script, embed_status
        result = embed_script(user_id, script_id)
    except Exception as exc:
        _log.warning("[embeddings] embed_script raised: %s", exc, exc_info=True)
        try:
            ctl.update(warnings={
                "stage": "embeddings",
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
            })
        except Exception:
            pass
        return "error", 0

    if not result.get("ok"):
        err = str(result.get("error") or "embedding provider unavailable")
        _log.warning("[embeddings] embed_script !ok: %s", err)
        try:
            ctl.update(warnings={
                "stage": "embeddings",
                "exception": "EmbeddingProviderUnavailable",
                "message": err[:300],
            })
        except Exception:
            pass
        return "error", 0

    # 后台线程已在跑;轮询 ~30s 报进度,但不阻塞到全部完成(大书可能要分钟)
    try:
        status = embed_status(script_id) or {}
        chunks_total = int(((status.get("chunks") or {}).get("total")) or 0)
        ctl.update(stage_progress=0, stage_total=max(1, chunks_total))
        for _ in range(30):  # 最多等 30s
            if ctl.is_cancelled():
                return "done", 0
            status = embed_status(script_id) or {}
            chunks = status.get("chunks") or {}
            done = int(chunks.get("done") or 0)
            total = int(chunks.get("total") or 0)
            ctl.update(stage_progress=done, stage_total=max(1, total))
            running = bool(status.get("running"))
            if not running:
                # 后台已跑完(可能很快/小书)
                return "done", done
            if total > 0 and done >= total:
                return "done", done
            _time.sleep(1.0)
        # 30s 后后台仍在跑 — wizard 标 done,后台继续
        status = embed_status(script_id) or {}
        chunks = status.get("chunks") or {}
        return "done", int(chunks.get("done") or 0)
    except Exception as exc:
        _log.warning("[embeddings] polling failed: %s", exc, exc_info=True)
        return "done", 0
