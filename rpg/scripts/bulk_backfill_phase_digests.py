#!/usr/bin/env python3
"""bulk_backfill_phase_digests.py — task 107F

老存档 backfill 工具: 扫所有有 branch_commits 的 save, 按 30 turn 粗切 phase,
逐个调 phase_digest_agent.compact_phase() 生成 LLM 摘要。

依赖: 107D (rpg/phase_digest_agent.py 必须已就绪)。

用法:
  rpg_env/bin/python rpg/scripts/bulk_backfill_phase_digests.py             # 全部 user, 全部 save
  rpg_env/bin/python rpg/scripts/bulk_backfill_phase_digests.py --user-id 6 # 只 user 6
  rpg_env/bin/python rpg/scripts/bulk_backfill_phase_digests.py --save-id 7916  # 只这个 save
  rpg_env/bin/python rpg/scripts/bulk_backfill_phase_digests.py --dry-run   # 只打印计划不执行
  rpg_env/bin/python rpg/scripts/bulk_backfill_phase_digests.py --phase-size 30  # 每 phase 30 turn

退出码: 0=全部成功, N=失败的 phase 数
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# 加 rpg/ 到 import path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from platform_app.db import connect, init_db


def find_target_saves(user_id: int | None, save_id: int | None) -> list[dict]:
    """返回 [{save_id, user_id, title, commit_count, max_turn, has_any_digest}]."""
    init_db()
    with connect() as db:
        rows = db.execute(
            """
            select gs.id as save_id, gs.user_id, gs.title,
                   (select count(*) from branch_commits bc where bc.save_id=gs.id) as commit_count,
                   coalesce((select max(turn_index) from branch_commits bc where bc.save_id=gs.id), 0) as max_turn,
                   exists(select 1 from save_phase_digests spd where spd.save_id=gs.id) as has_any_digest
            from game_saves gs
            where (%s::bigint is null or gs.user_id = %s)
              and (%s::bigint is null or gs.id = %s)
            order by gs.updated_at desc
            """,
            (user_id, user_id, save_id, save_id),
        ).fetchall()
    return [dict(r) for r in rows]


def chunk_commits(save_id: int, phase_size: int) -> list[tuple[int, int]]:
    """把 save 的 commits 按 phase_size 切成 (turn_start, turn_end) 段。

    例如 max_turn=85, phase_size=30 → [(0,29), (30,59), (60,85)]
    turn 0 是 root commit (kind='root'), 也参与 phase 0。
    """
    init_db()
    with connect() as db:
        rows = db.execute(
            "select distinct turn_index from branch_commits where save_id=%s order by turn_index",
            (save_id,),
        ).fetchall()
    turns = sorted({r["turn_index"] for r in rows})
    if not turns:
        return []
    chunks = []
    i = 0
    while i < len(turns):
        start = turns[i]
        end_target = start + phase_size - 1
        # 找到最大的 turn ≤ end_target
        j = i
        while j + 1 < len(turns) and turns[j + 1] <= end_target:
            j += 1
        chunks.append((start, turns[j]))
        i = j + 1
    return chunks


def ensure_phase_row(save_id: int, phase_index: int, turn_start: int, turn_end: int) -> int:
    """确保 save_phase_digests 有这一行 (status='open' 待摘要), 返回 id。"""
    init_db()
    with connect() as db:
        row = db.execute(
            "select id from save_phase_digests where save_id=%s and phase_index=%s",
            (save_id, phase_index),
        ).fetchone()
        if row:
            db.execute(
                """update save_phase_digests set turn_start=%s, turn_end=%s, updated_at=now()
                   where id=%s""",
                (turn_start, turn_end, row["id"]),
            )
            return int(row["id"])
        row = db.execute(
            """insert into save_phase_digests
               (save_id, phase_index, turn_start, turn_end, status, generated_by)
               values (%s, %s, %s, %s, 'open', 'backfill')
               returning id""",
            (save_id, phase_index, turn_start, turn_end),
        ).fetchone()
        return int(row["id"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", type=int, default=None)
    ap.add_argument("--save-id", type=int, default=None)
    ap.add_argument("--phase-size", type=int, default=30, help="每 phase 多少 turn (默认 30)")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不执行 LLM")
    ap.add_argument("--skip-existing", action="store_true",
                    help="跳过已有 phase digests 的 save (默认重新跑覆盖)")
    args = ap.parse_args()

    saves = find_target_saves(args.user_id, args.save_id)
    print(f"找到 {len(saves)} 个候选 save")
    if not saves:
        return 0

    # 过滤
    if args.skip_existing:
        saves = [s for s in saves if not s["has_any_digest"]]
        print(f"过滤已有 digest 后剩 {len(saves)}")

    saves = [s for s in saves if s["commit_count"] > 0]
    print(f"过滤无 commit 的 save 后剩 {len(saves)}\n")

    # 计划
    plans = []
    for s in saves:
        chunks = chunk_commits(s["save_id"], args.phase_size)
        plans.append((s, chunks))
        print(f"  save {s['save_id']:>6} ({s['title'][:30]:30}) "
              f"commits={s['commit_count']:>3} max_turn={s['max_turn']:>3} → {len(chunks)} phases")

    if args.dry_run:
        print("\n[DRY RUN] 不实际执行,退出")
        return 0

    # 实际执行: 调 phase_digest_agent.compact_phase
    try:
        from agents.phase_digest_agent import compact_phase  # 107D 提供
    except ImportError:
        print("\nERROR: rpg/phase_digest_agent.py 不存在或未实现 compact_phase()。"
              "请先完成 task 107D 再跑 backfill。", file=sys.stderr)
        return 99

    total_phases = sum(len(p[1]) for p in plans)
    print(f"\n开始 backfill: 共 {total_phases} 个 phase 需要摘要")
    started = time.time()
    failed = 0
    done = 0

    for s, chunks in plans:
        save_id = s["save_id"]
        for phase_index, (turn_start, turn_end) in enumerate(chunks):
            ensure_phase_row(save_id, phase_index, turn_start, turn_end)
            tag = f"save {save_id} phase {phase_index} (turn {turn_start}-{turn_end})"
            try:
                t0 = time.time()
                result = compact_phase(save_id, phase_index, user_id=s["user_id"], force=True)
                err = (result or {}).get("error")
                if err:
                    print(f"  [FAIL] {tag} — LLM error: {err}")
                    failed += 1
                else:
                    print(f"  [OK]   {tag}  ({time.time()-t0:.1f}s)  "
                          f"summary={(result or {}).get('summary','')[:60]!r}")
                    done += 1
            except Exception as exc:
                print(f"  [FAIL] {tag} — {type(exc).__name__}: {exc}")
                failed += 1

    elapsed = time.time() - started
    print(f"\n==== {done}/{total_phases} 完成, {failed} 失败, 用时 {elapsed:.1f}s ====")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
