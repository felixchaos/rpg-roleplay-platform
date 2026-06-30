"""RP harness 基准 — LLM 裁判 CLI。

对一批存档做 pairwise 裁判:
  A = 存档里已记录的 GM 回复(RecordedHarness,当前线上基线)
  B = 候选 harness(任意 OpenAI 兼容端点)

用法:
  BENCH_DSN=postgresql://... JUDGE_KEY=sk-... \\
  python -m bench.run_judge \\
    --save-ids 12 34 56 \\
    --model evomap-deepseek-v4-flash \\
    --base-url https://api.evomap.ai/v1 \\
    --limit 30

API key 只从环境变量读取(--api-key-env 指定变量名,默认 JUDGE_KEY)。
结果 JSON 输出到 stdout(可重定向到文件)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg
from psycopg.rows import dict_row

from bench.harness import RecordedHarness, OpenAICompatHarness
from bench.judge import DIMS, batch_judge
from bench.judge_cases import load_judge_cases
from bench.judge_report import build_judge_report
from bench.runner import run_scorecard


def main() -> None:
    ap = argparse.ArgumentParser(
        description="LLM 裁判:对存档 GM 回复做 pairwise 质量评估"
    )
    ap.add_argument("--save-ids", nargs="+", type=int, required=True,
                    help="要裁判的存档 ID 列表")
    ap.add_argument("--limit", type=int, default=30,
                    help="每个存档最多提取多少 case(控制成本,默认 30)")
    ap.add_argument("--model", required=True,
                    help="候选模型 ID(如 evomap-deepseek-v4-flash)")
    ap.add_argument("--base-url", required=True,
                    help="候选模型 API base URL(OpenAI 兼容)")
    ap.add_argument("--api-key-env", default="JUDGE_KEY",
                    help="存放 API key 的环境变量名(默认 JUDGE_KEY)")
    ap.add_argument("--label", default="candidate",
                    help="候选 harness 标签(用于报告)")
    ap.add_argument("--dims", nargs="+", default=DIMS,
                    choices=DIMS,
                    help="要评估的维度(默认全部)")
    ap.add_argument("--max-tokens", type=int, default=120,
                    help="裁判单次 LLM 调用最大 token 数(默认 120)")
    ap.add_argument("--out", default=None,
                    help="把 JSON 报告额外写入该路径(同时也 stdout 输出)")
    a = ap.parse_args()

    api_key = os.environ.get(a.api_key_env, "")
    if not api_key:
        print(f"[ERROR] 环境变量 {a.api_key_env} 未设置或为空", file=sys.stderr)
        sys.exit(1)

    dsn = os.environ.get("BENCH_DSN", "host=localhost port=5432 dbname=rpg_platform")
    c = psycopg.connect(dsn, row_factory=dict_row)
    try:
        all_cases: list[dict] = []
        for sid in a.save_ids:
            try:
                cases = load_judge_cases(c, sid, max_cases=a.limit)
                all_cases.extend(cases)
                print(f"  存档 {sid}: {len(cases)} cases", file=sys.stderr)
            except Exception as e:
                print(f"  存档 {sid} 加载失败: {e}", file=sys.stderr)
    finally:
        c.close()

    if not all_cases:
        print("[ERROR] 未加载到任何 case", file=sys.stderr)
        sys.exit(1)

    print(f"共 {len(all_cases)} cases,开始裁判...", file=sys.stderr)

    # A = 已记录回复(基线), B = 候选 harness 现生成
    recorded = RecordedHarness()
    cand = OpenAICompatHarness(
        a.label, a.model, a.base_url, api_key,
        max_tokens=a.max_tokens,
    )

    resps_a: list[str] = [recorded.generate(case) for case in all_cases]
    resps_b: list[str] = []
    for i, case in enumerate(all_cases):
        resp = cand.generate(case)
        resps_b.append(resp)
        if (i + 1) % 5 == 0:
            print(f"  生成 {i + 1}/{len(all_cases)}", file=sys.stderr)

    print("裁判中...", file=sys.stderr)
    judge_result = batch_judge(
        all_cases, resps_a, resps_b,
        harness=cand,
        dims=a.dims,
        max_cases=len(all_cases),
    )

    # 确定性 scorecard(对 A/基线打分,供对比参考)
    scored_cases = [dict(c, gm_response=ra) for c, ra in zip(all_cases, resps_a)]
    det_scorecard = run_scorecard(scored_cases, label="recorded(prod)")

    report = build_judge_report(
        det_scorecard, judge_result,
        label=a.label,
        n_det_cases=len(scored_cases),
        n_judge_cases=judge_result.get("n_cases", 0),
    )

    out_str = json.dumps(report, ensure_ascii=False, indent=2)
    print(out_str)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(out_str)
        print(f"\n报告 JSON -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
