#!/usr/bin/env python3
"""phase_digest_worker.py — task 107D 附属。

扫所有 save_phase_digests 里"待重摘"的行,逐个调 compact_phase。

谁来调:
  1. cron (每 N 分钟跑一次, 后台 backfill)
  2. chat_pipeline 在 phase 切换后 fire-and-forget 触发一次
  3. 运维手动: /phase rebuild N 标记后跑一次

"待重摘" 条件 (任意一条满足都会被处理):
  A. metadata.needs_rebuild=true        — 用户/GM 显式标记
  B. status='closed' and summary='' and generated_by='backfill'
                                        — 107F backfill 占了行但 LLM 还没跑
  C. status='closed' and summary=''     — 上次摘要失败 (异常吞了),要重试

用法:
  rpg_env/bin/python rpg/scripts/phase_digest_worker.py
  rpg_env/bin/python rpg/scripts/phase_digest_worker.py --save-id 7916
  rpg_env/bin/python rpg/scripts/phase_digest_worker.py --max 20  # 最多 20 个就停
  rpg_env/bin/python rpg/scripts/phase_digest_worker.py --once   # 不循环, 跑完一轮就退
  rpg_env/bin/python rpg/scripts/phase_digest_worker.py --dry-run

退出码: 0=正常,N=失败数。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from platform_app.db import connect, init_db


def find_pending(
    save_id: int | None = None, limit: int = 50,
) -> list[dict]:
    """返回待重摘的 (save_id, phase_index, user_id, reason) 列表。"""
    init_db()
    with connect() as db:
        rows = db.execute(
            """
            select spd.save_id,
                   spd.phase_index,
                   gs.user_id,
                   case
                     when (spd.metadata->>'needs_rebuild')::bool then 'flagged'
                     when spd.status = 'closed' and coalesce(spd.summary,'') = ''
                          and spd.generated_by = 'backfill' then 'backfill'
                     when spd.status = 'closed' and coalesce(spd.summary,'') = '' then 'retry'
                     else 'unknown'
                   end as reason
              from save_phase_digests spd
              join game_saves gs on gs.id = spd.save_id
             where (
                 (spd.metadata->>'needs_rebuild')::bool
                 or (spd.status = 'closed' and coalesce(spd.summary,'') = '')
               )
               and (%s::bigint is null or spd.save_id = %s)
             order by spd.save_id, spd.phase_index
             limit %s
            """,
            (save_id, save_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-id", type=int, default=None,
                    help="只处理指定 save (默认全部)")
    ap.add_argument("--max", type=int, default=50,
                    help="每轮最多处理几个 phase")
    ap.add_argument("--once", action="store_true",
                    help="不循环, 跑完一轮就退 (默认 --once)")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="持续模式下两轮之间 sleep 多少秒")
    ap.add_argument("--continuous", action="store_true",
                    help="持续运行 (默认 --once 模式)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印计划, 不调 LLM")
    args = ap.parse_args()

    try:
        from phase_digest_agent import compact_phase
    except ImportError as exc:
        print(f"ERROR: 无法 import phase_digest_agent: {exc}", file=sys.stderr)
        return 99

    total_failed = 0
    total_done = 0
    round_idx = 0

    while True:
        round_idx += 1
        pending = find_pending(save_id=args.save_id, limit=args.max)
        if not pending:
            print(f"[round {round_idx}] no pending phase, exit.")
            break

        print(f"[round {round_idx}] {len(pending)} pending phase(s):")
        for p in pending:
            print(f"  save {p['save_id']:>6} phase {p['phase_index']:>3} "
                  f"(reason={p['reason']}, user={p['user_id']})")

        if args.dry_run:
            print("[dry-run] 不调 LLM, 退出")
            return 0

        for p in pending:
            tag = f"save {p['save_id']} phase {p['phase_index']}"
            t0 = time.time()
            try:
                result = compact_phase(
                    save_id=int(p["save_id"]),
                    phase_index=int(p["phase_index"]),
                    user_id=int(p["user_id"]),
                    force=True,
                )
                err = (result or {}).get("error")
                if err:
                    print(f"  [FAIL] {tag} — {err}")
                    total_failed += 1
                else:
                    print(f"  [OK]   {tag} ({time.time()-t0:.1f}s)  "
                          f"summary={(result or {}).get('summary','')[:60]!r}")
                    total_done += 1
            except Exception as exc:
                print(f"  [FAIL] {tag} — {type(exc).__name__}: {exc}")
                total_failed += 1

        if args.once or not args.continuous:
            break
        print(f"[round {round_idx}] sleep {args.interval}s ...")
        time.sleep(args.interval)

    print(f"\n==== done={total_done} failed={total_failed} ====")
    return total_failed


if __name__ == "__main__":
    raise SystemExit(main())
