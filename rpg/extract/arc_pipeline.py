"""extract/arc_pipeline.py — RAG + 轻骨架 提取算法(替代 per_chapter 1166 次 LLM)。

设计:
  1. **章序切弧**:把全书按 chapter_index 切成 ~40 个弧段(每弧 ~30 章),不做 K-means
     (K-means on embeddings 会把同主题跨章拼一起,破坏时序;网文本身章序就是时间序)。
  2. **每弧 1 LLM 调用**:选首/中/末 3 章拼接喂 LLM,产 ChapterExtract(弧级)。
  3. **跨弧聚合**:复用 resolve.cluster_entities / build_timeline / build_constant_worldbook。
  4. **全章嵌入**:platform_app.knowledge.embedding.embed_script 走 Vertex(已部署)入 documents,
     供 GM 运行时 RAG 检索原文细节(本算法不出原文级数据)。

成本/耗时(1166 章):
  - LLM: 40 弧 × 1 call × ~3k token avg ≈ ~$0.05(deepseek-v4-flash)
  - 嵌入: ~$0.02(Vertex 768)
  - 高并发(20) 跑 40 弧 ≈ 2 waves × 30s = **~1-2 min**

与 per_chapter 老算法的对比:
  ✓ 时间线: 弧级段头/段尾/弧主题,**保留章节级摘要**(中间章 chapter_summary 即弧摘要)
  ✓ 实体: 弧 LLM 抽,跨弧 cluster_entities 合并全名/昵称
  ✓ 防剧透: first_revealed_chapter = 弧的首章 chapter_index
  ✓ 纪元: 弧级 era 多数共识(沿用 seed.py 共识门)
  ✗ 细节: 单章细微情节不入 KB(走 RAG 查询时拉原文补)
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from extract import resolve as R
from extract.embed import embed_canon_entities
from extract.llm import ExtractLLM
from extract.per_chapter import extract_chapter
from extract.seed import build_seed


def split_arcs(chapters: list[dict], *, target_arcs: int = 100,
               min_arc_size: int = 5, max_arc_size: int = 40) -> list[list[dict]]:
    """按 chapter_index 等分成 ~target_arcs 段(保持时序,书长自适应)。

    数据质量 #3:target_arcs 默认从 40 → 100,锚点颗粒度细化。
      · 1181 章书原来 40 弧 = 平均 30 章/锚 → 玩家 30 章空白才触发一个锚
      · 改成 100 弧 = 平均 12 章/锚 → 玩家 10 章左右就有重要锚点
      · max_arc_size 从 80 → 40,巨书也不会出现一弧 80 章塞不进 LLM 上下文
      · min_arc_size=5 不变,小书不被切碎
    成本影响:抽弧次数 ×2.5,deepseek-v4-flash 单次 ~$0.002 → 1181 章原 $0.09 → $0.13 一次性。

    UI 仍允许调用方覆盖 target_arcs(KbExtractPanel 已有该参数)。
    """
    n = len(chapters)
    if n == 0:
        return []
    # 期望弧数,但受 min/max 弧大小钳制
    desired = max(1, n // max(min_arc_size, n // target_arcs))
    # 上限钳:每弧 ≤ max_arc_size
    desired = max(desired, (n + max_arc_size - 1) // max_arc_size)
    # 下限钳:每弧 ≥ min_arc_size
    desired = min(desired, max(1, n // min_arc_size))
    sz = n / desired
    arcs = []
    for i in range(desired):
        start = int(round(i * sz))
        end = int(round((i + 1) * sz)) if i < desired - 1 else n
        if end > start:
            arcs.append(chapters[start:end])
    return arcs


def pick_representative_chapters(arc: list[dict], k: int = 3) -> list[dict]:
    """从弧里选 k 个有代表性的章节(首/中/末优先,k=3 时正好 3 段)。"""
    n = len(arc)
    if n <= k:
        return list(arc)
    if k == 3:
        return [arc[0], arc[n // 2], arc[-1]]
    # 均匀采样
    idxs = sorted({int(i * (n - 1) / (k - 1)) for i in range(k)})
    return [arc[i] for i in idxs]


def extract_arc(llm: ExtractLLM, arc: list[dict], *, era: str,
                power_system: list[str] | None = None,
                known_entities: list[str] | None = None,
                k_picks: int = 3, per_chapter_chars: int = 2500,
                max_tokens: int = 5500) -> Any:
    """LLM 抽一个弧。复用 per_chapter.extract_chapter 的 schema(章 → 弧的语义升维)。

    v28: identity/background 进 entity schema 后,弧级实体集更易撑爆原 4000 上限
    (弧含 3 代表章 + 弧角色全集,密度高于单章),提到 5500。

    返回 ChapterExtract,其中:
      chapter = 弧首章 chapter_index(用作 first_revealed_chapter)
      chapter_summary = 弧主线浓缩(LLM 看 3 章一并产出)
      entities/events/concepts = 弧级全集
    """
    picks = pick_representative_chapters(arc, k=k_picks)
    parts = []
    for ch in picks:
        title = (ch.get("title") or "").strip()
        body = (ch.get("content") or "").strip()[:per_chapter_chars]
        parts.append(f"【第{ch['chapter_index']}章 {title}】\n{body}")
    combined = "\n\n----\n\n".join(parts)
    descriptor = (
        f"本片段含弧段(第 {arc[0]['chapter_index']}-{arc[-1]['chapter_index']} 章,共 {len(arc)} 章)"
        f"的 {len(picks)} 个代表章节,请把 chapter_summary 写成本弧整体主线浓缩(120-200 字)"
    )
    return extract_chapter(
        llm, arc[0]["chapter_index"], combined, era=era,
        power_system=power_system, known_entities=known_entities,
        prev_summary="", title_descriptor=descriptor,
        max_tokens=max_tokens,
    )


def run_arc_extraction(
    script_id: int,
    book_id: int,
    *,
    user_id: int | None = None,
    author_era: str = "",
    author_power_system: list[str] | None = None,
    author_worldlines: list[dict] | None = None,
    model: str = "deepseek-v4-flash",
    api_id: str = "deepseek",
    target_arcs: int = 100,
    concurrency: int = 10,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    seed_sample: int = 12,
    progress_cb: Callable[[str, dict], None] | None = None,
) -> dict[str, Any]:
    """RAG + 弧段骨架算法。

    target_arc_size: 期望每弧章数(默认 30 → 1166 章 ≈ 39 弧)。
    concurrency: 弧级 LLM 并发。
    **不在 LLM 调用期间持有 DB 连接**(同 pipeline.py 铁律)。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from platform_app.db import connect

    def _emit(stage, info):
        if progress_cb:
            try:
                progress_cb(stage, info)
            except Exception as _exc:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "[arc_pipeline._emit] progress_cb stage=%s failed: %s",
                    stage, _exc, exc_info=True,
                )

    # 1) 读章节(短连接)
    with connect() as db:
        sql = ("select chapter_index, title, content, content_descriptor from script_chapters "
               "where script_id = %s and exclude_from_extraction = false")
        args: list = [script_id]
        if chapter_min is not None:
            sql += " and chapter_index >= %s"
            args.append(chapter_min)
        if chapter_max is not None:
            sql += " and chapter_index <= %s"
            args.append(chapter_max)
        sql += " order by chapter_index"
        chapters = [dict(r) for r in db.execute(sql, tuple(args)).fetchall()]
    if not chapters:
        return {"ok": False, "error": "无可提取章节"}

    # 2) 切弧
    arcs = split_arcs(chapters, target_arcs=target_arcs)
    _emit("arc_split", {"chapters": len(chapters), "arcs": len(arcs)})

    # 3) Pass 0 — 种子(同 per_chapter 走法,12 章采样)
    # P2-1: seed 阶段用独立 ExtractLLM(algorithm="seed"),使记账标签正确;
    #        弧段提取复用另一个 llm(algorithm="arc"),互不污染。
    llm_seed = ExtractLLM(model=model, api_id=api_id, user_id=user_id,
                          script_id=script_id, algorithm="seed")
    llm = ExtractLLM(model=model, api_id=api_id, user_id=user_id,
                     script_id=script_id, algorithm="arc")
    _seed_n = min(seed_sample, len(chapters))
    _emit("seed", {"sample": _seed_n, "done": 0, "total": _seed_n})
    seed = build_seed(
        llm_seed, chapters, author_era=author_era,
        author_power_system=author_power_system,
        author_worldlines=author_worldlines, sample=_seed_n,
    )
    # 标 seed.done — done >= total 触发 job_runner.cb 把状态置 done,
    # 否则前端看到 seed 始终 running(就算已经进 arc_extract 了)
    _emit("seed", {"sample": _seed_n, "done": _seed_n, "total": _seed_n, "succeeded": True})
    era = (seed.era or author_era or "").strip()  # 空字符串=未定,Pass 1 自抽供共识

    # 4) Pass 1 — 弧级 LLM 抽取(高并发)
    _emit("arc_extract", {"total": len(arcs), "concurrency": concurrency})
    extracts_dict: dict[int, Any] = {}
    done = [0]
    import threading
    lock = threading.Lock()

    failed_arcs: list[tuple[int, str]] = []  # phase_backend
    failed_lock = threading.Lock()

    def _one(idx: int, arc: list[dict]):
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                ex = extract_arc(
                    llm, arc, era=era,
                    power_system=seed.power_system,
                    known_entities=seed.entity_vocab,
                )
                return idx, ex
            except Exception as exc:
                last_exc = exc
                if attempt == 3:
                    return idx, None
                import time as _t
                _t.sleep(0.5 * (2 ** attempt))
        if last_exc is not None:
            with failed_lock:
                arc_min = arc[0].get("chapter_index", 0) if arc else 0
                arc_max = arc[-1].get("chapter_index", 0) if arc else 0
                failed_arcs.append((idx, f"arc[{arc_min}-{arc_max}]: {str(last_exc)[:200]}"))
        return idx, None

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_one, i, arc) for i, arc in enumerate(arcs)]
        for f in as_completed(futures):
            try:
                idx, ex = f.result()
                if ex is not None:
                    extracts_dict[idx] = ex
            except Exception as _exc:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "[arc_pipeline] _one future raised: %s", _exc, exc_info=True,
                )
            with lock:
                done[0] += 1
                if progress_cb:
                    _emit("arc_extract", {"done": done[0], "total": len(arcs)})

    extracts = [extracts_dict[i] for i in range(len(arcs)) if i in extracts_dict]
    succeeded = len(extracts)
    _emit("arc_extract", {"done": done[0], "total": len(arcs),
                          "succeeded": succeeded, "failed": len(arcs) - succeeded})

    if not extracts:
        return {"ok": False, "error": "全部弧段 LLM 提取失败"}

    # 4.5) era fallback:Pass 0 共识门严会返空,从 N 弧 Pass 1 二次共识(要求 ≥ 25% 弧投同票)
    if not era and extracts:
        from extract.seed import _normalize_era
        arc_era_hints: dict[str, str] = {}
        arc_era_count: dict[str, int] = {}
        for ex in extracts:
            eh = (ex.story_time or {}).get("era", "").strip()
            if eh:
                k = _normalize_era(eh)
                if not k:
                    continue
                arc_era_count[k] = arc_era_count.get(k, 0) + 1
                if not arc_era_hints.get(k) or len(eh) > len(arc_era_hints[k]):
                    arc_era_hints[k] = eh
        if arc_era_count:
            top = max(arc_era_count.items(), key=lambda x: x[1])
            # 比例阈值: ≥ 25% 弧或至少 3 票(任一满足);随书自适应,无书本特化
            need = max(3, len(extracts) // 4)
            if top[1] >= need:
                era = arc_era_hints[top[0]]
                seed.era = era  # 同步给 build_constant_worldbook
                _emit("era_fallback", {"new_era": era, "votes": top[1], "arcs": len(extracts)})

    # 5) Pass 2 — 跨弧实体消歧聚合 + 时间线 + 常驻骨架(复用 resolve.py)
    _emit("resolve", {"arc_extracts": len(extracts)})
    from platform_app.knowledge.embedding import _embed_batch

    def embedder(names):
        # Vertex 缺凭证 / 网络挂 → _embed_batch 返 None。下游 cluster_entities 把
        # 长度不匹配的当 None 处理,这里也直接返 None 让它走快通路(归一+子串聚类即可)。
        return _embed_batch(names) or None

    with connect() as db:
        # v28: 传 book_id → resolve_and_write 同步 NPC canon 进 character_cards 表
        stats = R.resolve_and_write(db, script_id, extracts, embedder=embedder, book_id=book_id)
        tl = R.build_timeline(db, script_id, extracts)
        wb = R.build_constant_worldbook(db, script_id, book_id, seed)

    # 6) Pass 3 — 规范实体嵌入(短连接)
    # 必须把 user_id 透传 — 没 user_id 时 _resolve_embed_config 走系统默认
    # vertex_ai 但生产模式禁全局 SA fallback,_embed_batch 返 None,
    # 441 个 canon 一个 embedding 都写不进去 → search_canon 永远返空。
    _emit("embed", {})
    with connect() as db:
        emb = embed_canon_entities(db, script_id, user_id=user_id)

    # phase_backend: succeeded < 90% 视为 partial,上层标 done_with_errors
    success_ratio = succeeded / max(1, len(arcs))
    partial_failures: list[dict] = []
    if success_ratio < 0.9:
        partial_failures = [
            {"arc_idx": idx, "stage": "extract_arc", "error": err}
            for idx, err in failed_arcs
        ]
    return {
        "ok": True,
        "algorithm": "arc_rag",
        "era": era,
        "chapters": len(chapters),
        "arcs": len(arcs),
        "arcs_succeeded": succeeded,
        "success_ratio": round(success_ratio, 3),
        "seed_vocab": len(seed.entity_vocab),
        "resolve": stats,
        "timeline_anchors": tl,
        "constant_worldbook": wb,
        "embed": emb,
        "partial_failures": partial_failures,
    }
