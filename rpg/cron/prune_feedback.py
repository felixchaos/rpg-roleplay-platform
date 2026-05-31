"""rpg.cron.prune_feedback — 反馈数据清理 cron (FB-09).

策略:
  - 删 created_at < now() - 24 months 的 feedback 行
  - 例外: review_decision = 'nsfw_terminate' 永久保留（合规证据）
  - feedback_consent_log 不删（所有同意记录永久留存，供监管 audit）

用法:
    from rpg.cron.prune_feedback import run_prune_feedback
    result = run_prune_feedback(db)          # {"pruned": n, "kept_nsfw": n}
    result = run_prune_feedback(db, months=36)

可通过 scripts/run_cron.py prune-feedback 触发（见该文件 subcommand 注册）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_prune_feedback(db, months: int = 24) -> dict:
    """删 created_at < now() - {months} months 的反馈行。

    nsfw_terminate 行保留（合规证据），consent_log 行永不删除。

    Args:
        db:     psycopg Connection（dict_row）
        months: 保留月数（默认 24）

    Returns:
        {"pruned": int, "kept_nsfw": int}
    """
    months = max(1, int(months))

    # 先统计会被保留的 nsfw_terminate 超期行（只用于日志，不删）
    kept_row = db.execute(
        f"""
        select count(*) as n from feedback
        where created_at < now() - interval '{months} months'
          and review_decision = 'nsfw_terminate'
        """
    ).fetchone()
    kept_nsfw = int(kept_row["n"]) if kept_row else 0

    cur = db.execute(
        f"""
        delete from feedback
        where created_at < now() - interval '{months} months'
          and (review_decision is null or review_decision != 'nsfw_terminate')
        """
    )
    pruned = cur.rowcount

    logger.info(
        "prune_feedback: pruned=%d rows (threshold=%d months), kept nsfw_terminate=%d",
        pruned, months, kept_nsfw,
    )
    return {"pruned": pruned, "kept_nsfw": kept_nsfw}
