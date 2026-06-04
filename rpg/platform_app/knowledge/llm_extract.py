"""platform_app/knowledge/llm_extract.py — 平台侧 LLM 提取入口(Phase A 接通)。

默认走 **弧段算法**(arc_pipeline):1166 章 ~40 LLM 调用 / 2 分钟 / ~$0.05。
老 per_chapter 算法(1166 LLM 调用)留作 algorithm='per_chapter' 兼容选项。

成本铁律:默认便宜模型 + 弧段算法。
默认 import 不自动跑(避免每次 import 烧钱);由作业/管理触发或带 sample 小验。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from platform_app.db import connect
from platform_app.knowledge._sync import _ensure_book


def run_llm_extraction(
    user_id: int,
    script_id: int,
    *,
    algorithm: str = "arc",
    author_era: str = "",
    author_power_system: list[str] | None = None,
    author_worldlines: list[dict] | None = None,
    model: str = "deepseek-v4-flash",
    api_id: str = "deepseek",
    target_arcs: int = 40,
    concurrency: int = 15,
    sample_chapters: int | None = None,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    progress_cb: Callable[[str, dict], None] | None = None,
    confirmed: bool = False,
    max_book_usd: float = 10.0,
    monthly_book_limit: int | None = None,
) -> dict[str, Any]:
    """对一本已导入剧本跑 LLM 提取管线,产出规范层 KB(BYOK,用户付费)。

    algorithm:
      'arc'(默认): 等分弧段算法,~40 LLM / 1166 章。便宜快。
      'per_chapter': 老算法,1 LLM / 章。慢贵,留兼容。
    target_arcs / concurrency: 仅 arc 模式生效。
    sample_chapters: 仅 per_chapter 模式生效(限前 N 章小验);arc 模式因要全书等分而忽略。
    护栏:跑前精确估算→超 max_book_usd 且未 confirmed 则返 needs_confirm;
         超月配额则拒;跑后记账。
    """
    import datetime

    from extract.budget import estimate

    period = datetime.date.today().strftime("%Y-%m")

    with connect() as db:
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        book_id = _ensure_book(db, script)["id"]

        # 跑前预算(算法感知,arc/per_chapter 分开估)
        est = estimate(db, script_id, model=model, algorithm=algorithm,
                       target_arcs=target_arcs, sample_chapters=sample_chapters)
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

    # 调用相应算法(内部自管连接,LLM 期间不持连)
    if algorithm == "arc":
        from extract.arc_pipeline import run_arc_extraction
        result = run_arc_extraction(
            script_id, book_id,
            user_id=user_id, author_era=author_era,
            author_power_system=author_power_system, author_worldlines=author_worldlines,
            model=model, api_id=api_id,
            target_arcs=target_arcs, concurrency=concurrency,
            chapter_min=chapter_min, chapter_max=chapter_max,
            progress_cb=progress_cb,
        )
    else:
        from extract.pipeline import run_extraction
        result = run_extraction(
            script_id, book_id,
            user_id=user_id, author_era=author_era,
            author_power_system=author_power_system, author_worldlines=author_worldlines,
            model=model, api_id=api_id, sample_chapters=sample_chapters,
            chapter_min=chapter_min, chapter_max=chapter_max,
            progress_cb=progress_cb,
        )

    # 跑后记账(配额 + 估算花费 + 实测花费)
    if result.get("ok"):
        with connect() as db:
            # 聚合本次本 script 产生的 token_usage 行 → 实际 USD 花费
            actual_row = db.execute(
                "select coalesce(sum(cost_usd),0) as actual, "
                "coalesce(sum(input_tokens),0) as in_tok, "
                "coalesce(sum(output_tokens),0) as out_tok, "
                "count(*) as calls "
                "from token_usage where user_id=%s "
                "and (metadata->>'script_id')::bigint = %s "
                "and created_at > now() - interval '10 minutes'",
                (user_id, script_id),
            ).fetchone()
            actual_usd = float(actual_row["actual"]) if actual_row else 0.0
            db.execute(
                """
                insert into extraction_quota(user_id, period, books_extracted, est_usd_spent, actual_usd_spent)
                values (%s, %s, 1, %s, %s)
                on conflict(user_id, period) do update set
                  books_extracted = extraction_quota.books_extracted + 1,
                  est_usd_spent = extraction_quota.est_usd_spent + excluded.est_usd_spent,
                  actual_usd_spent = extraction_quota.actual_usd_spent + excluded.actual_usd_spent,
                  updated_at = now()
                """,
                (user_id, period, est["est_usd"], actual_usd),
            )
        result["estimate"] = est
        result["actual_usage"] = {
            "usd": round(actual_usd, 4),
            "input_tokens": int(actual_row["in_tok"]) if actual_row else 0,
            "output_tokens": int(actual_row["out_tok"]) if actual_row else 0,
            "llm_calls": int(actual_row["calls"]) if actual_row else 0,
        }
    return result
