"""platform_app/knowledge/llm_extract.py — 平台侧 LLM 提取入口(Phase A 接通)。

把 extract.pipeline(Pass0-3)接到平台:查 script+book,跑 LLM 管线写 kb_canon_* +
constant worldbook + 时间线 + 实体嵌入。**替代/补强** _extract_fact 关键词管线。

成本铁律:默认便宜模型(gemini-3.5-flash);全书回填(866 章)是显式运营动作。
默认 import 不自动跑(避免每次 import 烧钱);由作业/管理触发或带 sample 小验。
"""
from __future__ import annotations

from typing import Any, Callable

from platform_app.db import connect
from platform_app.knowledge._sync import _ensure_book


def run_llm_extraction(
    user_id: int,
    script_id: int,
    *,
    author_era: str = "",
    author_power_system: list[str] | None = None,
    author_worldlines: list[dict] | None = None,
    model: str = "gemini-3.5-flash",
    api_id: str = "vertex_ai",
    sample_chapters: int | None = None,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    progress_cb: Callable[[str, dict], None] | None = None,
    confirmed: bool = False,
    max_book_usd: float = 10.0,
    monthly_book_limit: int | None = None,
) -> dict[str, Any]:
    """对一本已导入剧本跑 LLM 提取管线,产出规范层 KB(BYOK,用户付费)。

    sample_chapters: 限前 N 章(小验/控成本/懒提取);None=全书。
    护栏:跑前精确估算→超 max_book_usd 且未 confirmed 则返 needs_confirm;
         超月配额则拒;跑后记账。
    """
    import datetime

    from extract.budget import estimate
    from extract.pipeline import run_extraction

    period = datetime.date.today().strftime("%Y-%m")

    with connect() as db:
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        book_id = _ensure_book(db, script)["id"]

        # 跑前精确预算
        est = estimate(db, script_id, model=model, sample_chapters=sample_chapters)
        if not est.get("ok"):
            return {"ok": False, "error": est.get("error", "无可提取章节")}

        # 成本上限闸:超阈值需显式确认(保护用户自己的 key 不被意外大额消耗)
        if est["est_usd"] > max_book_usd and not confirmed:
            return {"ok": False, "needs_confirm": True, "estimate": est,
                    "message": f"本次提取约 ${est['est_usd']}(你的 {model} key)。超 ${max_book_usd},请确认。"}

        # 月配额闸(限平台编排负载;免费档)
        if monthly_book_limit is not None:
            q = db.execute(
                "select books_extracted from extraction_quota where user_id=%s and period=%s",
                (user_id, period),
            ).fetchone()
            used = int(q["books_extracted"]) if q else 0
            if used >= monthly_book_limit:
                return {"ok": False, "quota_exceeded": True,
                        "message": f"本月提取额度已用尽({used}/{monthly_book_limit})。"}

    # run_extraction 内部自管连接(LLM 期间不持连)
    result = run_extraction(
        script_id, book_id,
        user_id=user_id, author_era=author_era,
        author_power_system=author_power_system, author_worldlines=author_worldlines,
        model=model, api_id=api_id, sample_chapters=sample_chapters,
        chapter_min=chapter_min, chapter_max=chapter_max,
        progress_cb=progress_cb,
    )

    # 跑后记账(配额 + 估算花费)
    if result.get("ok"):
        with connect() as db:
            db.execute(
                """
                insert into extraction_quota(user_id, period, books_extracted, est_usd_spent)
                values (%s, %s, 1, %s)
                on conflict(user_id, period) do update set
                  books_extracted = extraction_quota.books_extracted + 1,
                  est_usd_spent = extraction_quota.est_usd_spent + excluded.est_usd_spent,
                  updated_at = now()
                """,
                (user_id, period, est["est_usd"]),
            )
        result["estimate"] = est
    return result
