"""extract/pipeline.py — Phase A 提取总编排(Pass0→1→2→3)。

替代 chapter_fact_indexer._extract_fact 关键词管线。产出规范层 KB(kb_canon_* + 时间线 +
constant 骨架 + 实体嵌入)。设计 A_extraction.md。

成本铁律:Pass1 逐章便宜模型 + 可采样;全书回填(866 章)是 Phase H 运营动作(用户触发)。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from extract import resolve as R
from extract.embed import embed_canon_entities
from extract.llm import ExtractLLM
from extract.per_chapter import extract_chapter
from extract.seed import build_seed


def run_extraction(
    script_id: int,
    book_id: int,
    *,
    user_id: int | None = None,
    author_era: str = "",
    author_power_system: list[str] | None = None,
    author_worldlines: list[dict] | None = None,
    model: str = "gemini-3.5-flash",
    api_id: str = "vertex_ai",
    sample_chapters: int | None = None,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    seed_sample: int = 12,
    concurrency: int = 10,
    progress_cb: Callable[[str, dict], None] | None = None,
) -> dict[str, Any]:
    """端到端提取。chapters 从 script_chapters 读(exclude_from_extraction=false)。

    **铁律:绝不在 LLM 调用期间持有 DB 连接**(LLM 慢/网络,长事务持连会拖垮池)。
    只在读章节、写规范层时短暂开连接;Pass0/1(LLM)期间不持连。

    sample_chapters: 只提取前 N 章(测试/控成本);None=全书(Phase H 回填)。
    progress_cb(stage, info): 可选进度回调(挂 import_jobs)。
    """
    from platform_app.db import connect

    def _emit(stage, info):
        if progress_cb:
            try:
                progress_cb(stage, info)
            except Exception as _exc:
                # phase_backend: 不 silent — log.warning(exc_info)
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "[pipeline._emit] progress_cb failed for stage=%s: %s",
                    stage, _exc, exc_info=True,
                )

    # 读可提取章节(短连接,立即释放)— 用 content_descriptor 优先于怪标题
    # chapter_min/max 支持懒/增量提取的切片(W5)
    with connect() as db:
        sql = (
            "select chapter_index, title, content, content_descriptor from script_chapters "
            "where script_id = %s and exclude_from_extraction = false"
        )
        args: list = [script_id]
        if chapter_min is not None:
            sql += " and chapter_index >= %s"
            args.append(chapter_min)
        if chapter_max is not None:
            sql += " and chapter_index <= %s"
            args.append(chapter_max)
        sql += " order by chapter_index"
        rows = db.execute(sql, tuple(args)).fetchall()
        chapters = [dict(r) for r in rows]
    if sample_chapters:
        chapters = chapters[:sample_chapters]
    if not chapters:
        return {"ok": False, "error": "无可提取章节"}

    # —— 以下 Pass0/Pass1 全程不持有 DB 连接 ——
    # P2-1: seed 阶段用独立 ExtractLLM(algorithm="seed"),使记账标签正确;
    #        逐章提取复用另一个 llm(algorithm="per_chapter"),互不污染。
    llm_seed = ExtractLLM(model=model, api_id=api_id, user_id=user_id,
                          script_id=script_id, algorithm="seed")
    llm = ExtractLLM(model=model, api_id=api_id, user_id=user_id,
                     script_id=script_id, algorithm="per_chapter")

    # Pass 0:种子 + 自举词表
    _seed_n = min(seed_sample, len(chapters))
    _emit("seed", {"chapters": len(chapters), "done": 0, "total": _seed_n})
    seed = build_seed(llm_seed, chapters, author_era=author_era,
                      author_power_system=author_power_system,
                      author_worldlines=author_worldlines, sample=_seed_n)
    # 标 seed.done — 不发就一直 running,前端 stage 灯不动
    _emit("seed", {"chapters": len(chapters), "done": _seed_n, "total": _seed_n, "succeeded": True})
    era = (seed.era or author_era or "").strip()  # 空字符串=未定

    # Pass 1:逐章提取(高并发,放弃滚动 prev_summary 时序 hint 换吞吐)
    # 1166 章串行 ~6h;concurrency=50 → ~5min。瓶颈是 LLM RTT(~18s/章)+API 限流。
    _emit("per_chapter", {"total": len(chapters), "concurrency": concurrency})
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    extracts_dict: dict[int, Any] = {}
    done_count = [0]
    done_lock = threading.Lock()

    failed_chapters: list[tuple[int, str]] = []  # phase_backend: 收集失败章节
    failed_lock = threading.Lock()

    def _one(idx: int, ch: dict):
        # 用空 prev_summary(并发下章序不保证) + 单章 5 次重试容 429/网络抖
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                ex = extract_chapter(
                    llm, ch["chapter_index"], ch.get("content") or "", era=era,
                    power_system=seed.power_system, known_entities=seed.entity_vocab,
                    prev_summary="", title_descriptor=ch.get("content_descriptor") or "",
                )
                return idx, ex
            except Exception as exc:
                last_exc = exc
                if attempt == 4:
                    return idx, None
                # 指数退避(0.5s, 1s, 2s, 4s) — 让 429 缓解
                import time as _t
                _t.sleep(0.5 * (2 ** attempt))
        # phase_backend: 5 次重试全失败 — 记到 failed_chapters
        if last_exc is not None:
            with failed_lock:
                failed_chapters.append((ch.get("chapter_index", idx), str(last_exc)[:200]))
        return idx, None

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_one, i, ch) for i, ch in enumerate(chapters)]
        for f in as_completed(futures):
            try:
                idx, ex = f.result()
                if ex is not None:
                    extracts_dict[idx] = ex
            except Exception as _exc:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "[pipeline] _one future raised: %s", _exc, exc_info=True,
                )
            with done_lock:
                done_count[0] += 1
                if progress_cb and done_count[0] % 20 == 0:
                    _emit("per_chapter", {"done": done_count[0], "total": len(chapters)})

    # 按原始章节顺序还原
    extracts = [extracts_dict[i] for i in range(len(chapters)) if i in extracts_dict]
    _emit("per_chapter", {"done": done_count[0], "total": len(chapters),
                          "succeeded": len(extracts), "failed": len(chapters) - len(extracts)})

    # Pass 2:消歧聚合 → 规范层(短连接,写完即释放)
    _emit("resolve", {"extracts": len(extracts)})
    from platform_app.knowledge.embedding import _embed_batch

    def embedder(names):
        # 同 arc_pipeline:Vertex 失败时返 None 让 cluster_entities 走快通路,
        # 不要返空 list 让下游误判有 vec 然后越界。
        return _embed_batch(names) or None

    with connect() as db:
        # v28: 传 book_id → resolve_and_write 同步 NPC canon 进 character_cards 表
        stats = R.resolve_and_write(db, script_id, extracts, embedder=embedder, book_id=book_id)
        tl = R.build_timeline(db, script_id, extracts)
        wb = R.build_constant_worldbook(db, script_id, book_id, seed)

    # Pass 3:规范实体嵌入(短连接)— user_id 必须透传,见 arc_pipeline 同名 stage 注释
    _emit("embed", {})
    with connect() as db:
        emb = embed_canon_entities(db, script_id, user_id=user_id)

    result = {
        "ok": True, "era": era, "chapters": len(chapters),
        "seed_vocab": len(seed.entity_vocab),
        "resolve": stats, "timeline_anchors": tl, "constant_worldbook": wb, "embed": emb,
        # phase_backend: 失败章节列表(可能为空),让上层 job_runner 标 done_with_errors
        "partial_failures": [
            {"chapter": ch_idx, "stage": "extract_chapter", "error": err}
            for ch_idx, err in failed_chapters
        ],
    }
    _emit("done", result)
    return result
