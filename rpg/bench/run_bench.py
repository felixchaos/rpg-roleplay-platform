"""RP harness 基准 — CLI 入口。

  BENCH_DSN=postgresql://... python -m bench.run_bench --min-turns 20 --out scorecard.json

评估【当前 harness】(直接给真实存档里已记录的 GM 回复打分)→ 基线 scorecard。
后续 replay 模式(把上下文喂候选 harness 现生成再打分)接同一 metrics/runner。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg
from psycopg.rows import dict_row

from bench.cases import iter_cases, select_save_ids
from bench.runner import run_scorecard


def _bar(rate: float, width: int = 20) -> str:
    f = int(round(rate * width))
    return "█" * f + "·" * (width - f)


def render(sc: dict) -> str:
    L = [f"== RP harness 基线 scorecard · label={sc['label']} =="]
    L.append(f"样本: {sc['n_cases']} 回合 / {sc['n_saves']} 存档\n")
    L.append("坏指标命中率(越低越好):")
    for f, a in sc["fields"].items():
        if a.get("kind") == "bad_rate" and "rate" in a:
            L.append(f"  {f:<14} {a['rate']*100:5.1f}%  {_bar(a['rate'])}  ({a['hits']}/{a['n']})")
    L.append("\n观测率(中性):")
    for f, a in sc["fields"].items():
        if a.get("kind") != "bad_rate" and "rate" in a:
            L.append(f"  {f:<14} {a['rate']*100:5.1f}%  ({a['hits']}/{a['n']})")
    L.append("\n连续指标(mean / median / p90):")
    for f, a in sc["fields"].items():
        if "mean" in a and a.get("n"):
            arrow = {"lower": "↓优", "higher": "↑优", "info": "  "}.get(a["kind"], "  ")
            L.append(f"  {f:<14} {a['mean']:>8} / {a['median']:>8} / {a['p90']:>8}  {arrow}")
    if sc["offenders"]:
        L.append(f"\nworst offenders(命中坏指标的回合,前 {min(10,len(sc['offenders']))}):")
        for o in sc["offenders"][:10]:
            L.append(f"  存档{o['save_id']} 回合{o['turn']} [{','.join(o['bad'])}]: {o['excerpt'][:60]}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-turns", type=int, default=20)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--kb-native", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--label", default="current")
    a = ap.parse_args()

    dsn = os.environ.get("BENCH_DSN", "host=localhost port=5432 dbname=rpg_platform")
    c = psycopg.connect(dsn, row_factory=dict_row)
    try:
        ids = select_save_ids(c, min_turns=a.min_turns, limit=a.limit, only_kb_native=a.kb_native)
        cases = list(iter_cases(c, ids))
        sc = run_scorecard(cases, label=a.label)
    finally:
        c.close()

    print(render(sc))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(sc, f, ensure_ascii=False, indent=2)
        print(f"\nscorecard JSON → {a.out}")


if __name__ == "__main__":
    main()
