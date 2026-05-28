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

from .db import connect, init_db, expose


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
) -> dict[str, Any]:
    """把一轮 backend.last_usage 写入 token_usage 表"""
    init_db()
    cost = compute_cost(api_id, model_real_name, usage or {})
    with connect() as db:
        row = db.execute(
            """
            insert into token_usage(
              user_id, save_id, context_run_id, api_id, model_real_name,
              input_tokens, output_tokens, cached_input_tokens, reasoning_tokens, total_tokens,
              cost_usd, context_used, context_max, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ),
        ).fetchone()
    return expose(row) or {}


def aggregate_usage(user_id: int, days: int = 30) -> dict[str, Any]:
    """汇总：本人 N 天累计 input/output/cost，按模型分组"""
    init_db()
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
        recent = db.execute(
            """
            select created_at, api_id, model_real_name, input_tokens, output_tokens,
                   cost_usd, context_used, context_max
            from token_usage
            where user_id = %s
            order by id desc
            limit 20
            """,
            (user_id,),
        ).fetchall()
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
            }
            for r in recent
        ],
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
