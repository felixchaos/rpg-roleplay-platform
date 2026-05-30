"""extract/pipeline.py — Phase A 提取总编排(Pass0→1→2→3)。

替代 chapter_fact_indexer._extract_fact 关键词管线。产出规范层 KB(kb_canon_* + 时间线 +
constant 骨架 + 实体嵌入)。设计 A_extraction.md。

成本铁律:Pass1 逐章便宜模型 + 可采样;全书回填(866 章)是 Phase H 运营动作(用户触发)。
"""
from __future__ import annotations

from typing import Any, Callable

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
    seed_sample: int = 12,
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
            except Exception:
                pass

    # 读可提取章节(短连接,立即释放)— 用 content_descriptor 优先于怪标题
    with connect() as db:
        rows = db.execute(
            """
            select chapter_index, title, content, content_descriptor
            from script_chapters
            where script_id = %s and exclude_from_extraction = false
            order by chapter_index
            """,
            (script_id,),
        ).fetchall()
        chapters = [dict(r) for r in rows]
    if sample_chapters:
        chapters = chapters[:sample_chapters]
    if not chapters:
        return {"ok": False, "error": "无可提取章节"}

    # —— 以下 Pass0/Pass1 全程不持有 DB 连接 ——
    llm = ExtractLLM(model=model, api_id=api_id, user_id=user_id)

    # Pass 0:种子 + 自举词表
    _emit("seed", {"chapters": len(chapters)})
    seed = build_seed(llm, chapters, author_era=author_era,
                      author_power_system=author_power_system,
                      author_worldlines=author_worldlines, sample=min(seed_sample, len(chapters)))
    era = seed.era or author_era or "未知纪元"

    # Pass 1:逐章提取(滚动梗概给时序连续)
    _emit("per_chapter", {"total": len(chapters)})
    extracts = []
    prev_summary = ""
    for i, ch in enumerate(chapters):
        ex = extract_chapter(
            llm, ch["chapter_index"], ch.get("content") or "", era=era,
            power_system=seed.power_system, known_entities=seed.entity_vocab,
            prev_summary=prev_summary, title_descriptor=ch.get("content_descriptor") or "",
        )
        extracts.append(ex)
        # 滚动梗概 = 本章首个事件
        if ex.events:
            prev_summary = ex.events[0].get("summary", "")
        if progress_cb and (i + 1) % 20 == 0:
            _emit("per_chapter", {"done": i + 1, "total": len(chapters)})

    # Pass 2:消歧聚合 → 规范层(短连接,写完即释放)
    _emit("resolve", {"extracts": len(extracts)})
    from platform_app.knowledge.embedding import _embed_batch

    def embedder(names):
        return _embed_batch(names) or []

    with connect() as db:
        stats = R.resolve_and_write(db, script_id, extracts, embedder=embedder)
        tl = R.build_timeline(db, script_id, extracts)
        wb = R.build_constant_worldbook(db, script_id, book_id, seed)

    # Pass 3:规范实体嵌入(短连接)
    _emit("embed", {})
    with connect() as db:
        emb = embed_canon_entities(db, script_id)

    result = {
        "ok": True, "era": era, "chapters": len(chapters),
        "seed_vocab": len(seed.entity_vocab),
        "resolve": stats, "timeline_anchors": tl, "constant_worldbook": wb, "embed": emb,
    }
    _emit("done", result)
    return result
