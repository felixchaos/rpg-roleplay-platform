"""rpg.cron.prune_audit — 审计日志清理 cron (LC-03).

每天跑一次：
  - login_audit:     保留 90 天（隐私合规，参见 LC-03）
  - admin_audit_log: 保留 365 天（合规/运维需要更长审计链）

用法:
    from rpg.cron.prune_audit import run_prune_login_audit, run_prune_admin_audit
    r1 = run_prune_login_audit(db)         # {"pruned": n}
    r2 = run_prune_admin_audit(db)         # {"pruned": n}
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_prune_login_audit(db, days: int = 90) -> dict:
    """删除 login_audit 中超过 `days` 天的行。

    Args:
        db:   psycopg Connection（dict_row）
        days: 保留天数（默认 90）

    Returns:
        {"pruned": int}
    """
    cur = db.execute(
        f"delete from login_audit where created_at < now() - interval '{int(days)} days'"
    )
    n = cur.rowcount
    logger.info("prune_login_audit: pruned=%d rows (threshold=%d days)", n, days)
    return {"pruned": n}


def run_prune_admin_audit(db, days: int = 365) -> dict:
    """删除 admin_audit_log 中超过 `days` 天的行。

    admin 审计日志有合规需求，默认保留 1 年（365 天）。

    Args:
        db:   psycopg Connection（dict_row）
        days: 保留天数（默认 365）

    Returns:
        {"pruned": int}
    """
    cur = db.execute(
        f"delete from admin_audit_log where created_at < now() - interval '{int(days)} days'"
    )
    n = cur.rowcount
    logger.info("prune_admin_audit: pruned=%d rows (threshold=%d days)", n, days)
    return {"pruned": n}
