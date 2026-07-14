"""import_pipeline.control — 预算预估 + Job 控制(DB 状态读写/取消信号)

来源: 原 rpg/platform_app/import_pipeline.py estimate_budget / JobController(原 L139-295) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from ..db import connect, init_db
from core.llm_backend import DEFAULT_FALLBACK_API, DEFAULT_FALLBACK_MODEL


# ══════════════════════════════════════════════════════════════════════
#  预算预估（不入库，仅估算）
# ══════════════════════════════════════════════════════════════════════
def estimate_budget(
    chapter_count: int,
    total_words: int,
    *,
    enable_cards: bool = True,
    enable_worldbook: bool = True,
    cards_top_n: int = 30,
    model_api_id: str = DEFAULT_FALLBACK_API,
    model_real_name: str = DEFAULT_FALLBACK_MODEL,
) -> dict[str, Any]:
    """开始导入前的预算。

    估算依据：
    - chunks: 0 token（确定性，只切块）
    - facts: 0 token（确定性，规则匹配）
    - entities: 0 token（确定性词频）
    - cards: top_n 个角色 × 每个 ~3000 token in + 800 out
    - worldbook: ~20 条目 × 每条 ~2000 token in + 400 out

    时间估算：
    - 确定性阶段：100 章/秒
    - LLM 阶段：每次请求 ~3s
    """
    try:
        from model_probe import get_pricing
        pricing = get_pricing(model_api_id, model_real_name) or {}
    except Exception:
        pricing = {}
    input_price = float(pricing.get("input", 1.0))   # USD per million
    output_price = float(pricing.get("output", 5.0))

    cards_calls = cards_top_n if enable_cards else 0
    worldbook_calls = 20 if enable_worldbook else 0
    cards_input = cards_calls * 3000
    cards_output = cards_calls * 800
    wb_input = worldbook_calls * 2000
    wb_output = worldbook_calls * 400

    total_input = cards_input + wb_input
    total_output = cards_output + wb_output
    cost_usd = (total_input * input_price + total_output * output_price) / 1_000_000

    eta_sec = (
        chapter_count / 100              # chunks
        + chapter_count / 100            # facts
        + 0.5                            # entities (instant)
        + cards_calls * 3                # cards
        + worldbook_calls * 3            # worldbook
    )

    # 字段名对齐 extract/budget.estimate + llm-extract/estimate 用的
    # est_input_tokens / est_output_tokens / est_usd / tokens_est / time_est_sec
    # (前端 Wizard ImportEstimateView 期望 tokens_est+time_est_sec)。
    # 同时保留旧 tokens_in/out/cost_usd 别名给老调用方,这版双写一段时间。
    def _stage(id_, label, ti, to, cost, eta, **extra):
        return {
            "id": id_, "label": label,
            "tokens_est": ti + to,
            "est_input_tokens": ti, "est_output_tokens": to,
            "est_usd": cost,
            "time_est_sec": eta,
            # 旧字段别名,兼容现有读取
            "tokens_in": ti, "tokens_out": to, "cost_usd": cost, "eta_sec": eta,
            **extra,
        }
    cards_cost = round((cards_input * input_price + cards_output * output_price) / 1_000_000, 4)
    wb_cost = round((wb_input * input_price + wb_output * output_price) / 1_000_000, 4)
    return {
        "ok": True,
        "model": {"api_id": model_api_id, "real_name": model_real_name, "pricing": pricing},
        "stages": [
            _stage("chunks",    "切块入库", 0, 0, 0.0, chapter_count / 100, deterministic=True),
            _stage("facts",     "章节事实", 0, 0, 0.0, chapter_count / 100, deterministic=True),
            _stage("entities",  "人物提取", 0, 0, 0.0, 0.5,                deterministic=True),
            _stage("cards",     "人设卡生成", cards_input, cards_output, cards_cost,
                   cards_calls * 3, enabled=enable_cards, calls=cards_calls),
            _stage("worldbook", "世界书建立", wb_input, wb_output, wb_cost,
                   worldbook_calls * 3, enabled=enable_worldbook, calls=worldbook_calls),
        ],
        # 全局聚合 — 同时给新名 + 老名
        "est_input_tokens": total_input,
        "est_output_tokens": total_output,
        "est_usd": round(cost_usd, 4),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": round(cost_usd, 4),
        "total_eta_sec": int(eta_sec),
        "chapter_count": chapter_count,
        "total_words": total_words,
    }


# ══════════════════════════════════════════════════════════════════════
#  Job 控制：DB 状态读写 + 取消信号
# ══════════════════════════════════════════════════════════════════════
class JobController:
    """封装单个 import_job 的 DB 状态操作。worker 用 self.update() 写进度，
    self.is_cancelled() 检查是否被用户取消。"""

    def __init__(self, job_id: str):
        self.job_id = job_id

    def _exec(self, sql: str, params: tuple) -> None:
        init_db()
        with connect() as db:
            db.execute(sql, params)

    def update(self, **fields) -> None:
        """部分更新当前 job 的字段（status/stage/stage_progress/...）

        phase_backend: 新增 warnings(jsonb), module/source/before_count/after_count/sub_kind 字段。
        """
        if not fields:
            return
        sets = []
        params: list[Any] = []
        for k, v in fields.items():
            if k in ("budget_estimate", "usage_actual", "stages", "warnings"):
                sets.append(f"{k} = %s")
                params.append(Jsonb(v))
            else:
                sets.append(f"{k} = %s")
                params.append(v)
        sets.append("updated_at = now()")
        params.append(self.job_id)
        self._exec(f"update import_jobs set {', '.join(sets)} where job_id = %s", tuple(params))

    def is_cancelled(self) -> bool:
        init_db()
        with connect() as db:
            row = db.execute(
                "select cancel_requested, status from import_jobs where job_id = %s",
                (self.job_id,),
            ).fetchone()
        return bool(row and (row.get("cancel_requested") or row.get("status") == "cancelled"))

    def add_usage(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        init_db()
        with connect() as db:
            db.execute(
                """
                update import_jobs
                   set usage_actual = jsonb_set(
                       jsonb_set(
                           jsonb_set(usage_actual,
                               '{input_tokens}', to_jsonb(coalesce((usage_actual->>'input_tokens')::int,0) + %s)),
                           '{output_tokens}', to_jsonb(coalesce((usage_actual->>'output_tokens')::int,0) + %s)),
                       '{cost_usd}', to_jsonb(coalesce((usage_actual->>'cost_usd')::float,0) + %s)),
                       updated_at = now()
                 where job_id = %s
                """,
                (input_tokens, output_tokens, cost_usd, self.job_id),
            )
