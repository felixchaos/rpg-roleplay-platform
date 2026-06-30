"""RP harness 基准 — 合并报告生成(确定性 + LLM 裁判)。

纯函数,不做任何 I/O。将 run_scorecard 输出(确定性)和 batch_judge 输出(LLM 裁判)
合并为统一报告 JSON。

schema_version=1 字段:
  schema_version    int   — 报告格式版本
  label             str   — 候选 harness 标签
  n_det_cases       int   — 确定性指标 case 数
  n_judge_cases     int   — LLM 裁判 case 数
  deterministic     dict  — run_scorecard 原始结果
  llm_judge         dict  — batch_judge 结果 + calibration_note + per-dim flip_inconsistency
"""
from __future__ import annotations

from typing import Any


def build_judge_report(
    deterministic: dict,
    llm_judge: dict,
    *,
    label: str,
    n_det_cases: int,
    n_judge_cases: int,
    calibration: dict | None = None,
) -> dict[str, Any]:
    """合并确定性 scorecard 与 LLM 裁判结果,返回统一报告 dict(不做 I/O)。

    参数:
      deterministic  — run_scorecard() 的返回值
      llm_judge      — batch_judge() 的返回值
      label          — 候选 harness 标签(如 "evomap-v4-flash")
      n_det_cases    — 确定性指标跑了多少 case
      n_judge_cases  — LLM 裁判跑了多少 case
      calibration    — (可选) run_calibration() 的返回值,如有则嵌入
    """
    # 提取 per-dim flip_inconsistency(1 - flip_consistency)供报告展示
    dim_meta: dict[str, Any] = {}
    if calibration:
        for dim, cal in calibration.items():
            if dim == "overall" or not isinstance(cal, dict):
                continue
            cons = cal.get("flip_consistency")
            if cons is not None:
                dim_meta[dim] = {"position_flip_inconsistency": round(1.0 - cons, 4)}

    # 将 dim_meta 合并到 llm_judge per-dim 统计里
    judge_section: dict[str, Any] = {}
    from bench.judge import DIMS
    for dim in DIMS:
        base = (llm_judge or {}).get(dim) or {}
        entry: dict[str, Any] = dict(base)
        if dim in dim_meta:
            entry.update(dim_meta[dim])
        judge_section[dim] = entry

    overall = (llm_judge or {}).get("overall") or {}
    judge_section["overall"] = overall
    judge_section["n_cases"] = (llm_judge or {}).get("n_cases", n_judge_cases)

    # calibration_note
    cal_note = ""
    if calibration:
        ov = calibration.get("overall") or {}
        acc = ov.get("accuracy")
        cons = ov.get("flip_consistency")
        n_cal = ov.get("n", 0)
        if acc is not None:
            cal_note = (
                f"校准集 n={n_cal}: accuracy={acc:.2%}, "
                f"position_flip_consistency={cons:.2%}"
            )
    judge_section["calibration_note"] = cal_note

    return {
        "schema_version": 1,
        "label": label,
        "n_det_cases": n_det_cases,
        "n_judge_cases": n_judge_cases,
        "deterministic": deterministic,
        "llm_judge": judge_section,
    }
