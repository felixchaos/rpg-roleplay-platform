"""CLI 入口 — 手动触发 cron 任务（供 dev 测试 + docker cron service）.

用法:
    python -m rpg.scripts.run_cron hard_delete
    python -m rpg.scripts.run_cron prune_audit
    python -m rpg.scripts.run_cron all

每次运行后写一行到 admin_audit_log，记录执行结果。
"""
from __future__ import annotations

import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("run_cron")


def _write_audit(db, action: str, details: dict) -> None:
    """把 cron 运行结果写到 admin_audit_log."""
    try:
        db.execute(
            """
            insert into admin_audit_log
              (actor_id, actor_username, action, target_type, target_id, details, ip)
            values
              (null, 'cron', %s, 'system', '', %s, '127.0.0.1')
            """,
            (action, json.dumps(details)),
        )
    except Exception:
        logger.exception("run_cron: failed to write admin_audit_log for action=%s", action)


def cmd_hard_delete(db) -> dict:
    from rpg.cron.hard_delete import run_hard_delete
    result = run_hard_delete(db)
    logger.info("hard_delete: %s", result)
    _write_audit(db, "cron.hard_delete", result)
    return result


def cmd_prune_audit(db) -> dict:
    from rpg.cron.prune_audit import run_prune_login_audit, run_prune_admin_audit
    r1 = run_prune_login_audit(db)
    r2 = run_prune_admin_audit(db)
    result = {"login_audit_pruned": r1["pruned"], "admin_audit_pruned": r2["pruned"]}
    logger.info("prune_audit: %s", result)
    _write_audit(db, "cron.prune_audit", result)
    return result


COMMANDS = {
    "hard_delete": cmd_hard_delete,
    "prune_audit": cmd_prune_audit,
}


def main(argv: list[str] | None = None) -> None:
    args = (argv or sys.argv)[1:]
    if not args:
        print("Usage: python -m rpg.scripts.run_cron <hard_delete|prune_audit|all>", file=sys.stderr)
        sys.exit(1)

    cmd = args[0].strip().lower()
    if cmd not in ("hard_delete", "prune_audit", "all"):
        print(f"Unknown command: {cmd!r}", file=sys.stderr)
        sys.exit(1)

    from rpg.platform_app.db import connect, init_db
    init_db()

    with connect() as db:
        if cmd == "all":
            for name, fn in COMMANDS.items():
                logger.info("=== running %s ===", name)
                fn(db)
        else:
            COMMANDS[cmd](db)

    logger.info("run_cron done.")


if __name__ == "__main__":
    main()
