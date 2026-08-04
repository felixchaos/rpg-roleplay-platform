"""RP harness 基准 — replay A/B CLI。

把真实存档上下文喂【当前线上记录(基线)】和【候选 harness(任意 OpenAI 兼容模型/提示词)】,
同一 metrics 打分并排对比。

  BENCH_DSN=... CAND_KEY=sk-... python -m bench.run_replay \
    --model evomap-deepseek-v4-flash --base-url https://api.evomap.ai/v1 \
    --min-turns 10 --limit 20
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
from bench.harness import OpenAICompatHarness, RecordedHarness
from bench.replay import render_compare, run_replay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--key-env", default="CAND_KEY", help="环境变量名(候选模型 key)")
    ap.add_argument("--min-turns", type=int, default=10)
    ap.add_argument("--limit", type=int, default=20, help="最多多少 case(控成本)")
    ap.add_argument("--max-tokens", type=int, default=800)
    ap.add_argument("--label", default="candidate")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    dsn = os.environ.get("BENCH_DSN", "host=localhost port=5432 dbname=rpg_platform")
    c = psycopg.connect(dsn, row_factory=dict_row)
    try:
        ids = select_save_ids(c, min_turns=a.min_turns)
        cases = list(iter_cases(c, ids))[: a.limit]
    finally:
        c.close()

    cand = OpenAICompatHarness(a.label, a.model, a.base_url, os.environ[a.key_env],
                               max_tokens=a.max_tokens)
    res = run_replay(cases, [RecordedHarness(), cand],
                     on_progress=lambda i, n: print(f"  生成 {i}/{n}", file=sys.stderr))
    print(render_compare(res))
    if a.out:
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n→ {a.out}")


if __name__ == "__main__":
    main()
