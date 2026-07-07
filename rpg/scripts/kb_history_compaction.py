"""世界树历史祖先裁剪 compaction(见 branches/history_elide.py 文档)。

用法:
  python -m scripts.kb_history_compaction --save 268 [--dry-run]
  python -m scripts.kb_history_compaction --all [--dry-run]

每 save 一个事务+advisory lock(与回合/分支操作互斥),失败单档跳过不影响其他。"""
import argparse
import sys

from platform_app.db import connect, init_db
from platform_app.branches._helpers import acquire_save_advisory_lock
from platform_app.branches.history_elide import elide_save


def run_one(save_id: int, dry_run: bool) -> dict:
    with connect() as db:
        row = db.execute("select user_id from game_saves where id=%s", (save_id,)).fetchone()
        if not row:
            return {"save_id": save_id, "skipped": "no such save"}
        acquire_save_advisory_lock(db, save_id, int(row["user_id"]))
        return elide_save(db, save_id, dry_run=dry_run)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--save", type=int)
    g.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    init_db()
    if args.save:
        ids = [args.save]
    else:
        with connect() as db:
            ids = [r["id"] for r in db.execute(
                "select distinct save_id as id from branch_commits order by 1").fetchall()]
    total_elided = total_bytes = 0
    for sid in ids:
        try:
            r = run_one(sid, args.dry_run)
        except Exception as exc:
            print(f"save {sid}: FAILED {type(exc).__name__}: {exc}", flush=True)
            continue
        if r.get("elided") or r.get("candidates"):
            print(f"save {sid}: elided={r.get('elided')} candidates={r.get('candidates')} "
                  f"bytes_before={r.get('bytes_before')}", flush=True)
        total_elided += int(r.get("elided") or 0)
        total_bytes += int(r.get("bytes_before") or 0)
    print(f"TOTAL: elided={total_elided} bytes_reclaim~={total_bytes}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
