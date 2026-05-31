"""extract/budget.py — Phase G 提取精确预算估算器。

提取走 BYOK(用户自己 key 付费),平台只给算法 + **跑前精确报价**。
确定性估算:可提取章数 × 每章估算 token × 用户选的模型单价。设计 NEXT_PHASE_PLAN W4-a。
"""
from __future__ import annotations

# 每 1M token 单价(美元)。便宜模型铁律:只列 flash/haiku 级 + 给个 frontier 警示价。
# 数字按各家 flash/haiku 档公开量级取保守值;可随实际调价更新。
MODEL_PRICING: dict[str, dict] = {
    "gemini-3.5-flash": {"in": 0.10, "out": 0.40, "tier": "flash"},
    "gemini-2.5-flash": {"in": 0.075, "out": 0.30, "tier": "flash"},
    "deepseek-v4-flash": {"in": 0.10, "out": 0.40, "tier": "flash"},  # 官方 V4 flash
    "deepseek-v4-pro":   {"in": 0.30, "out": 1.20, "tier": "flash"},  # 官方 V4 pro
    "claude-haiku-4-5": {"in": 0.80, "out": 4.00, "tier": "haiku"},
    # 仅作对比警示——不建议全程用:
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00, "tier": "frontier"},
}

# Pass1 每章估算 — **已用真实 gemini-flash 调用校准(二战书 3 章实测)**:
#   实测 输入均值 2930/章(正文截6000字符≈2900 + 词表/摘要)、输出均值 2069/章
#   (固定schema三元组JSON比预想大得多)。取实测 + 小头寸,宁可略高不低估(BYOK 用户付费,
#   报价宁高勿低,避免超账单)。全书重算 ≈ $1.0,贴实测 $0.98。
_PER_CH_INPUT = 3200    # 实测 2930 + 词表随书增长头寸
_PER_CH_OUTPUT = 2200   # 实测 2069 + 头寸(原 800 严重低估)
# Pass0 自举:采样 ~min(12, chapters) 章 NER
_SEED_SAMPLE = 12
_SEED_PER_CALL_INPUT = 2800
_SEED_PER_CALL_OUTPUT = 1200
# 嵌入(Vertex text-embedding-004)≈ 平台承担/极廉,不计入 BYOK 报价


def _model_price(model: str) -> dict:
    return MODEL_PRICING.get(model, MODEL_PRICING["gemini-3.5-flash"])


def estimate(db, script_id: int, *, model: str = "gemini-3.5-flash",
             sample_chapters: int | None = None, batch_discount: bool = False) -> dict:
    """估算一次提取的成本(确定性,跑前可知)。

    sample_chapters: 只提前 N 章(懒/增量提取场景);None=全可提取章。
    batch_discount: Batch API 五折(若接)。
    """
    row = db.execute(
        "select count(*) c from script_chapters where script_id=%s and exclude_from_extraction=false",
        (script_id,),
    ).fetchone()
    total = int(row["c"]) if row else 0
    chapters = min(total, sample_chapters) if sample_chapters else total
    if chapters <= 0:
        return {"ok": False, "error": "无可提取章节", "chapters": 0}

    price = _model_price(model)
    seed_calls = min(_SEED_SAMPLE, chapters)

    in_tok = chapters * _PER_CH_INPUT + seed_calls * _SEED_PER_CALL_INPUT
    out_tok = chapters * _PER_CH_OUTPUT + seed_calls * _SEED_PER_CALL_OUTPUT

    usd = (in_tok / 1_000_000) * price["in"] + (out_tok / 1_000_000) * price["out"]
    if batch_discount:
        usd *= 0.5

    return {
        "ok": True,
        "script_id": script_id,
        "model": model,
        "model_tier": price["tier"],
        "chapters": chapters,
        "total_extractable": total,
        "est_input_tokens": in_tok,
        "est_output_tokens": out_tok,
        "est_usd": round(usd, 3),
        "batch_discount": batch_discount,
        "note": (
            f"约 ${round(usd,2)}(用你自己的 {model} key 付费)。"
            + ("⚠️ frontier 档,建议换 flash/haiku" if price["tier"] == "frontier" else "")
        ),
    }


def cheapest_models() -> list[str]:
    """推荐的便宜模型(按 in 单价升序),给前端下拉默认。"""
    flash = [(m, p) for m, p in MODEL_PRICING.items() if p["tier"] in ("flash", "haiku")]
    return [m for m, _ in sorted(flash, key=lambda x: x[1]["in"])]
