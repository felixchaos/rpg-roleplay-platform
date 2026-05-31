"""
usage.py — token_usage 表写入 + 聚合查询

数据流：
1. chat 结束后调用 record_usage(...) 把 backend.last_usage 写进 token_usage
2. 前端 dashboard 调 list_usage / aggregate_usage 看图表
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, expose, init_db


def compute_cost(api_id: str, model_real_name: str, usage: dict[str, int]) -> Decimal:
    """根据 model_probe 静态价格表计算单轮成本（USD）"""
    try:
        from model_probe import get_pricing
        pricing = get_pricing(api_id, model_real_name) or {}
    except Exception:
        pricing = {}
    if not pricing:
        return Decimal("0")
    input_price = Decimal(str(pricing.get("input", 0)))
    output_price = Decimal(str(pricing.get("output", 0)))
    # cached input 通常打折，简化：按 25% 输入价
    cached_price = input_price * Decimal("0.25")
    input_tok = int(usage.get("input_tokens", 0))
    output_tok = int(usage.get("output_tokens", 0))
    cached_tok = int(usage.get("cached_input_tokens", 0))
    billable_input = max(0, input_tok - cached_tok)
    million = Decimal("1000000")
    cost = (
        (Decimal(billable_input) * input_price / million)
        + (Decimal(cached_tok) * cached_price / million)
        + (Decimal(output_tok) * output_price / million)
    )
    return cost.quantize(Decimal("0.000001"))


def record_usage(
    user_id: int,
    save_id: int | None,
    context_run_id: int | None,
    api_id: str,
    model_real_name: str,
    usage: dict[str, int],
    context_used: int = 0,
    context_max: int = 0,
    metadata: dict[str, Any] | None = None,
    scenario: str = "chat",
) -> dict[str, Any]:
    """把一轮 backend.last_usage 写入 token_usage 表。

    scenario 枚举: chat / opening / extract / embedding / assistant / tool
    """
    init_db()
    cost = compute_cost(api_id, model_real_name, usage or {})
    with connect() as db:
        row = db.execute(
            """
            insert into token_usage(
              user_id, save_id, context_run_id, api_id, model_real_name,
              input_tokens, output_tokens, cached_input_tokens, reasoning_tokens, total_tokens,
              cost_usd, context_used, context_max, metadata, scenario
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning *
            """,
            (
                user_id, save_id, context_run_id, api_id, model_real_name,
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
                int(usage.get("cached_input_tokens", 0)),
                int(usage.get("reasoning_tokens", 0)),
                int(usage.get("total_tokens", 0)),
                cost,
                int(context_used),
                int(context_max),
                Jsonb(metadata or {}),
                scenario,
            ),
        ).fetchone()
    return expose(row) or {}


def forecast_daily_burn(user_id: int, days_back: int = 7) -> dict:
    """基于过去 N 天的平均日消耗，预测当月耗尽阈值。

    返回:
    {
      "avg_daily_cost_usd": float,  # 过去 N 天平均日 cost
      "avg_daily_tokens": int,
      "projected_30d_cost": float,  # 假设维持当前速率，30 天总 cost
      "trend_7d_vs_prev_7d_pct": float,  # 速率是涨是跌，正数=涨
    }
    """
    init_db()
    days_back = max(1, min(int(days_back), 90))
    with connect() as db:
        # 当前窗口：group by date
        current_rows = db.execute(
            """
            select date_trunc('day', created_at)::date as d,
                   sum(cost_usd)::float as day_cost,
                   sum(total_tokens)::bigint as day_tokens
            from token_usage
            where user_id = %s
              and created_at >= now() - (interval '1 day' * %s)
              and created_at < now()
            group by d
            """,
            (user_id, days_back),
        ).fetchall()
        # 前一窗口（用于 trend）
        prev_rows = db.execute(
            """
            select date_trunc('day', created_at)::date as d,
                   sum(cost_usd)::float as day_cost
            from token_usage
            where user_id = %s
              and created_at >= now() - (interval '1 day' * %s)
              and created_at < now() - (interval '1 day' * %s)
            group by d
            """,
            (user_id, days_back * 2, days_back),
        ).fetchall()

    # 当前窗口平均
    if current_rows:
        avg_cost = sum(float(r["day_cost"] or 0) for r in current_rows) / days_back
        avg_tokens = int(sum(int(r["day_tokens"] or 0) for r in current_rows) / days_back)
    else:
        avg_cost = 0.0
        avg_tokens = 0

    # 前一窗口平均
    if prev_rows:
        prev_avg_cost = sum(float(r["day_cost"] or 0) for r in prev_rows) / days_back
    else:
        prev_avg_cost = 0.0

    # trend 百分比（当前 vs 前期，基准为前期；前期为 0 时 trend=0）
    if prev_avg_cost > 0:
        trend_pct = round((avg_cost - prev_avg_cost) / prev_avg_cost * 100, 1)
    else:
        trend_pct = 0.0

    return {
        "avg_daily_cost_usd": round(avg_cost, 6),
        "avg_daily_tokens": avg_tokens,
        "projected_30d_cost": round(avg_cost * 30, 4),
        "trend_7d_vs_prev_7d_pct": trend_pct,
    }


def aggregate_usage(
    user_id: int,
    days: int = 30,
    recent_offset: int = 0,
    recent_limit: int = 20,
) -> dict[str, Any]:
    """汇总：本人 N 天累计 input/output/cost，按模型分组"""
    init_db()
    recent_offset = max(0, int(recent_offset))
    recent_limit = max(1, min(int(recent_limit), 100))
    with connect() as db:
        total = db.execute(
            """
            select
              coalesce(sum(input_tokens), 0) as input_tokens,
              coalesce(sum(output_tokens), 0) as output_tokens,
              coalesce(sum(cached_input_tokens), 0) as cached_input_tokens,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(cost_usd), 0) as cost_usd,
              count(*) as turns
            from token_usage
            where user_id = %s and created_at >= now() - (interval '1 day' * %s)
            """,
            (user_id, days),
        ).fetchone()
        by_model = db.execute(
            """
            select api_id, model_real_name,
                   sum(input_tokens) as input_tokens,
                   sum(output_tokens) as output_tokens,
                   sum(cost_usd) as cost_usd,
                   count(*) as turns
            from token_usage
            where user_id = %s and created_at >= now() - (interval '1 day' * %s)
            group by api_id, model_real_name
            order by cost_usd desc
            """,
            (user_id, days),
        ).fetchall()
        by_scenario = db.execute(
            """
            select scenario,
                   sum(input_tokens) as input_tokens,
                   sum(output_tokens) as output_tokens,
                   sum(cached_input_tokens) as cached_input_tokens,
                   sum(cost_usd) as cost_usd,
                   count(*) as turns
            from token_usage
            where user_id = %s and created_at >= now() - (interval '1 day' * %s)
            group by scenario
            order by cost_usd desc
            """,
            (user_id, days),
        ).fetchall()
        # B4: 带 offset/limit 的 recent_turns，total 用于前端翻页
        recent_total_row = db.execute(
            """
            select count(*) as n
            from token_usage
            where user_id = %s and created_at >= now() - (interval '1 day' * %s)
            """,
            (user_id, days),
        ).fetchone()
        recent = db.execute(
            """
            select created_at, api_id, model_real_name, input_tokens, output_tokens,
                   cost_usd, context_used, context_max, scenario
            from token_usage
            where user_id = %s and created_at >= now() - (interval '1 day' * %s)
            order by id desc
            limit %s offset %s
            """,
            (user_id, days, recent_limit, recent_offset),
        ).fetchall()
    recent_total = int(recent_total_row["n"]) if recent_total_row else 0
    return {
        "ok": True,
        "window_days": days,
        "totals": {k: (float(v) if hasattr(v, "as_tuple") else int(v or 0)) for k, v in (total or {}).items()},
        "by_model": [
            {
                "api_id": r["api_id"],
                "model": r["model_real_name"],
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "cost_usd": float(r["cost_usd"]),
                "turns": int(r["turns"]),
            }
            for r in by_model
        ],
        "by_scenario": [
            {
                "scenario": r["scenario"],
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "cached_input_tokens": int(r["cached_input_tokens"]),
                "cost_usd": float(r["cost_usd"]),
                "turns": int(r["turns"]),
            }
            for r in by_scenario
        ],
        "recent_turns": [
            {
                "at": str(r["created_at"]),
                "api_id": r["api_id"],
                "model": r["model_real_name"],
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "cost_usd": float(r["cost_usd"]),
                "context_used": int(r["context_used"]),
                "context_max": int(r["context_max"]),
                "scenario": r["scenario"] if "scenario" in r.keys() else "chat",
            }
            for r in recent
        ],
        "recent_total": recent_total,
        "recent_offset": recent_offset,
        "recent_limit": recent_limit,
    }


def context_window_for(api_id: str, model_real_name: str) -> int:
    """从定价表里取该模型的 context_window"""
    try:
        from model_probe import get_pricing
        pricing = get_pricing(api_id, model_real_name) or {}
        return int(pricing.get("context", 0))
    except Exception:
        return 0


def estimate_input_tokens(text: str) -> int:
    """粗略估算：中文按字数 *0.6，英文按 4 字符/token"""
    if not text:
        return 0
    cn_chars = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cn_chars
    return int(cn_chars * 0.6 + other / 4)


def timeline_usage(user_id: int, days: int = 30, group_by: str = "day") -> dict[str, Any]:
    """时间序列用量（dashboard 图表用）。

    group_by: "day" / "model"
    返回 [{bucket, input_tokens, output_tokens, cost_usd, turns}, ...]
    """
    init_db()
    if group_by not in ("day", "model"):
        raise ValueError("group_by 只支持 day / model")
    days = max(1, min(int(days), 365))
    with connect() as db:
        if group_by == "day":
            rows = db.execute(
                """
                select date_trunc('day', created_at) as bucket,
                       sum(input_tokens)::int as input_tokens,
                       sum(output_tokens)::int as output_tokens,
                       sum(cost_usd)::float as cost_usd,
                       count(*)::int as turns
                from token_usage
                where user_id = %s and created_at >= now() - (interval '1 day' * %s)
                group by bucket order by bucket
                """,
                (user_id, days),
            ).fetchall()
        else:  # model
            rows = db.execute(
                """
                select (api_id || '/' || model_real_name) as bucket,
                       sum(input_tokens)::int as input_tokens,
                       sum(output_tokens)::int as output_tokens,
                       sum(cost_usd)::float as cost_usd,
                       count(*)::int as turns
                from token_usage
                where user_id = %s and created_at >= now() - (interval '1 day' * %s)
                group by bucket order by cost_usd desc
                """,
                (user_id, days),
            ).fetchall()
    return {
        "ok": True,
        "group_by": group_by,
        "days": days,
        "series": [
            {
                "bucket": str(r["bucket"]),
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "cost_usd": float(r["cost_usd"]),
                "turns": int(r["turns"]),
            }
            for r in rows
        ],
    }


def average_output_tokens(user_id: int, model_real_name: str = "", last_n: int = 10) -> int:
    """最近 N 轮该模型的平均 output tokens，用于估算"""
    init_db()
    with connect() as db:
        if model_real_name:
            row = db.execute(
                """
                select coalesce(avg(output_tokens), 0)::int as avg
                from (
                    select output_tokens from token_usage
                    where user_id = %s and model_real_name = %s
                    order by id desc limit %s
                ) t
                """,
                (user_id, model_real_name, last_n),
            ).fetchone()
        else:
            row = db.execute(
                """
                select coalesce(avg(output_tokens), 0)::int as avg
                from (
                    select output_tokens from token_usage
                    where user_id = %s order by id desc limit %s
                ) t
                """,
                (user_id, last_n),
            ).fetchone()
    return int(row["avg"]) if row else 0
