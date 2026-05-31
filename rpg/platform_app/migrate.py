"""
migrate.py — 独立数据库迁移 CLI

用法（CI/deploy 脚本）：
  python -m platform_app.migrate status        # 看当前 schema 版本和待应用列表
  python -m platform_app.migrate up            # 应用所有待应用 migration（增量升级）
  python -m platform_app.migrate baseline      # 仅跑基线 CREATE TABLE（首次部署，低层调用）
  python -m platform_app.migrate full          # baseline + up + pgvector（首次 fresh DB 推荐）
  python -m platform_app.migrate check         # 仅做版本检查，落后则 exit(1)

!! 必须在 rpg/ 目录下运行（模块查找依赖工作目录），不是仓库根 !!
  正确: cd rpg/ && .venv/bin/python -m platform_app.migrate full
  错误: python -m platform_app.migrate full  # 从仓库根跑 → ModuleNotFoundError

生产部署最佳实践：
  - 首次部署（fresh DB）:  .venv/bin/python -m platform_app.migrate full
  - 后续升级（已有 DB）:   .venv/bin/python -m platform_app.migrate up
  - 升级前备份:            pg_dump -Fc rpg > backup-pre-migrate-$(date +%F).dump
  - worker 跳过自动迁移:   设 RPG_SKIP_AUTO_MIGRATE=1，由 deploy 脚本提前跑 migrate

!! migration 必须走直连 Postgres（5432），不能走 PgBouncer（6432）!!
  PgBouncer transaction 模式不支持 pg_advisory_lock（此 CLI 内部用了 advisory lock）。
  DATABASE_URL 应指向 postgresql://user:pass@127.0.0.1:5432/rpg（直连）跑 migrate，
  运行时 uvicorn 才走 PgBouncer 6432。

设计要点：
- 整个迁移过程包裹 pg_advisory_lock，串行化多进程并发部署
- 设置 lock_timeout 防止 ALTER TABLE 撞到长事务无限等
- 与运行时 `init_db()` 共用同一套 MIGRATIONS 列表（platform_app.db）
- up 子命令要求 migration_history 表已存在；fresh DB 必须先跑 full
"""
from __future__ import annotations

import argparse
import sys

from . import db as _db


def cmd_status(args) -> int:
    info = _db.list_migrations()
    if not info.get("ok"):
        print(f"[ERR] {info.get('error')}", file=sys.stderr)
        return 2
    items = info["migrations"]
    if info.get("fresh_database"):
        print("[fresh] schema_migrations 表不存在；DB 尚未跑过任何 migration")
    print(f"已知 migration: {info['total_known']}  已应用: {info['total_applied']}")
    for it in items:
        mark = "✓" if it["applied"] else "·"
        when = it["applied_at"] or "-"
        print(f"  {mark} v{it['version']:<3} {it['name']:<40} {when}")
    return 0


def cmd_up(args) -> int:
    with _db._migration_advisory_lock():
        _db._apply_versioned_migrations()
    print("[ok] 应用 migration 完成")
    return cmd_status(args)


def cmd_baseline(args) -> int:
    with _db._migration_advisory_lock():
        _db._do_init_db()
    print("[ok] 基线 schema 已建立")
    return 0


def cmd_full(args) -> int:
    with _db._migration_advisory_lock():
        _db._do_init_db()
        _db._apply_versioned_migrations()
        pg = _db.try_enable_pgvector()
    print(f"[ok] 基线 + migration 完成；pgvector: {pg}")
    return cmd_status(args)


def cmd_check(args) -> int:
    try:
        _db._assert_schema_up_to_date()
    except Exception as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1
    print("[ok] schema 已与代码登记的 migration 列表一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m platform_app.migrate")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="列出 migration 状态").set_defaults(func=cmd_status)
    sub.add_parser("up", help="应用所有待应用 migration").set_defaults(func=cmd_up)
    sub.add_parser("baseline", help="仅跑基线 CREATE TABLE").set_defaults(func=cmd_baseline)
    sub.add_parser("full", help="baseline + up + pgvector").set_defaults(func=cmd_full)
    sub.add_parser("check", help="检查 schema 是否落后，落后 exit(1)").set_defaults(func=cmd_check)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
