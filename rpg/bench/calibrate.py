"""RP harness 基准 — LLM 裁判校准。

对一组 golden_pairs(人工标注预期胜者)做 anti-position-bias 双向裁判:
  forward : judge(resp_a, resp_b)  → 原始结论
  swapped : judge(resp_b, resp_a)  → 交换 A/B 后的结论(再还原:B_swapped=A_original)

per-dim 统计:
  accuracy                — 裁判结论与 expected 一致率
  position_flip_consistency — forward 与 swapped(还原后)一致率(值越高=越不受位置偏差影响)
  n                       — golden pair 数
"""
from __future__ import annotations

from typing import Any

from bench.judge import DIMS, judge_pair

# 反转 winner(用于 swapped 还原):A↔B,tie 不变
_FLIP = {"A": "B", "B": "A", "tie": "tie"}


def _flip_winner(w: str) -> str:
    return _FLIP.get(w, "tie")


def run_calibration(golden_pairs: list[dict], harness,
                    dims: list[str] = DIMS) -> dict[str, Any]:
    """对 golden_pairs 做双向裁判并计算校准指标。

    golden_pairs 格式:
      [{"case": dict, "resp_a": str, "resp_b": str, "expected": "A"|"B"|"tie"}, ...]

    返回:
      {
        dim: {"accuracy": float, "flip_consistency": float, "n": int},
        "overall": {"accuracy": float, "flip_consistency": float, "n": int},
      }
    """
    # per-dim 累加器
    dim_correct: dict[str, int] = {d: 0 for d in dims}
    dim_consistent: dict[str, int] = {d: 0 for d in dims}
    dim_n: dict[str, int] = {d: 0 for d in dims}

    for gp in golden_pairs:
        case = gp.get("case") or {}
        resp_a = gp.get("resp_a") or ""
        resp_b = gp.get("resp_b") or ""
        expected = gp.get("expected", "tie")

        for dim in dims:
            # 正向裁判
            fwd = judge_pair(case, resp_a, resp_b, dim, harness)
            fwd_winner = fwd.get("winner", "tie")

            # 交换裁判(swap A/B)
            swp = judge_pair(case, resp_b, resp_a, dim, harness)
            # 还原:swp 里的 B 赢 = 原来的 A 赢
            swp_winner_restored = _flip_winner(swp.get("winner", "tie"))

            dim_n[dim] += 1
            if fwd_winner == expected:
                dim_correct[dim] += 1
            if fwd_winner == swp_winner_restored:
                dim_consistent[dim] += 1

    result: dict[str, Any] = {}
    total_correct = total_consistent = total_n = 0

    for dim in dims:
        n = dim_n[dim]
        acc = round(dim_correct[dim] / n, 4) if n else 0.0
        cons = round(dim_consistent[dim] / n, 4) if n else 0.0
        result[dim] = {"accuracy": acc, "flip_consistency": cons, "n": n}
        total_correct += dim_correct[dim]
        total_consistent += dim_consistent[dim]
        total_n += n

    overall_acc = round(total_correct / total_n, 4) if total_n else 0.0
    overall_cons = round(total_consistent / total_n, 4) if total_n else 0.0
    result["overall"] = {"accuracy": overall_acc, "flip_consistency": overall_cons, "n": total_n}
    return result
