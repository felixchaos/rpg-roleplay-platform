"""extract/incremental.py — Phase G/W5 懒/增量提取(按玩家进度切片)。

只提取到玩家进度 + buffer 的章节(过进度的反正被防剧透过滤,急切全书提取浪费)。
extracted_through_chapter 标记已提取到哪;extend 幂等(已覆盖则跳过)。
配 W4 预算:成本只按本次切片算。设计 NEXT_PHASE_PLAN W5。
"""
from __future__ import annotations

from typing import Any, Callable

from platform_app.db import connect

INITIAL_CHAPTERS = 20   # 建档时先提的开局章数(开局够 KB 用)
LOOKAHEAD_BUFFER = 20   # 提到 进度 + buffer,给玩家留前瞻余量


def extraction_state(db, script_id: int) -> dict:
    row = db.execute(
        "select extracted_through_chapter, extraction_seeded, "
        "(select max(chapter_index) from script_chapters where script_id=%s and exclude_from_extraction=false) as max_ch "
        "from scripts where id=%s",
        (script_id, script_id),
    ).fetchone()
    if not row:
        return {"extracted_through": 0, "seeded": False, "max_chapter": 0}
    return {"extracted_through": row["extracted_through_chapter"],
            "seeded": row["extraction_seeded"], "max_chapter": row["max_ch"] or 0}


def extend_extraction(
    user_id: int,
    script_id: int,
    target_chapter: int,
    *,
    buffer: int = LOOKAHEAD_BUFFER,
    author_era: str = "",
    author_power_system: list[str] | None = None,
    model: str = "gemini-3.5-flash",
    confirmed: bool = False,
    max_book_usd: float = 10.0,
    progress_cb: Callable[[str, dict], None] | None = None,
) -> dict[str, Any]:
    """把规范层 KB 提取扩展到覆盖 target_chapter(+buffer)。幂等。

    返回 {ok, skipped?, extracted_from, extracted_through, ...run_result}。
    """
    from platform_app.knowledge.llm_extract import run_llm_extraction

    with connect() as db:
        st = extraction_state(db, script_id)
    through = st["extracted_through"]
    max_ch = st["max_chapter"]
    want_to = min(target_chapter + buffer, max_ch)

    # 已覆盖 → 幂等跳过
    if want_to <= through:
        return {"ok": True, "skipped": True, "extracted_through": through,
                "reason": "已覆盖目标进度"}

    frm = through + 1
    result = run_llm_extraction(
        user_id, script_id,
        author_era=author_era, author_power_system=author_power_system,
        model=model, chapter_min=frm, chapter_max=want_to,
        confirmed=confirmed, max_book_usd=max_book_usd, progress_cb=progress_cb,
    )
    if not result.get("ok"):
        return {**result, "extracted_through": through}

    # 更新标记
    with connect() as db:
        db.execute(
            "update scripts set extracted_through_chapter = greatest(extracted_through_chapter, %s), "
            "extraction_seeded = true where id = %s",
            (want_to, script_id),
        )
    return {"ok": True, "extracted_from": frm, "extracted_through": want_to,
            "run": {k: result.get(k) for k in ("era", "chapters", "resolve", "embed")}}


def ensure_initial_extraction(user_id: int, script_id: int, *, author_era: str = "",
                              author_power_system: list[str] | None = None,
                              model: str = "gemini-3.5-flash", confirmed: bool = True) -> dict:
    """建档时:确保开局 INITIAL_CHAPTERS 章已提取(若公开书共享已有则跳过)。"""
    return extend_extraction(user_id, script_id, INITIAL_CHAPTERS, buffer=0,
                             author_era=author_era, author_power_system=author_power_system,
                             model=model, confirmed=confirmed)
