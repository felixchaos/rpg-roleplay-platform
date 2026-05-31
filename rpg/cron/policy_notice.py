"""rpg.cron.policy_notice — 政策通知定时任务 (DOC-02 / AUP-03).

两个任务:
  run_dispatch_due(db)  扫描 effective_at - now() < 30d 且 dispatched_at IS NULL 的通知,
                        调用 dispatch_notice 发邮件。
  run_activate_due(db)  扫描 effective_at <= now() 且 dispatched_at IS NOT NULL 的通知,
                        调用 activate_notice 更新 policy_versions。

用法:
    from rpg.cron.policy_notice import run_dispatch_due, run_activate_due
    result = run_dispatch_due(db)
    result = run_activate_due(db)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from platform_app.policy_notice import (
    NOTICE_LEAD_TIME_DAYS,
    activate_notice,
    dispatch_notice,
    list_pending_notices,
)

logger = logging.getLogger(__name__)


def run_dispatch_due(db) -> dict:
    """发送还未触发邮件但 effective_at 距今 <= 30d 的 pending 通知。

    Returns:
        {"dispatched": int, "checked": int}
    """
    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=NOTICE_LEAD_TIME_DAYS)

    notices = list_pending_notices(db)
    checked = len(notices)
    dispatched = 0

    for notice in notices:
        if notice.get("dispatched_at"):
            continue  # 已发过
        try:
            effective_at = datetime.fromisoformat(notice["effective_at"])
            if effective_at.tzinfo is None:
                effective_at = effective_at.replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            logger.warning("policy_notice cron: bad effective_at in notice %s", notice.get("id"))
            continue

        if effective_at <= threshold:
            logger.info(
                "policy_notice cron: dispatching notice %s (effective_at=%s)",
                notice["id"], notice["effective_at"],
            )
            try:
                dispatch_notice(db, notice["id"])
                dispatched += 1
            except Exception:
                logger.exception(
                    "policy_notice cron: dispatch failed for notice %s", notice["id"]
                )

    return {"dispatched": dispatched, "checked": checked}


def run_activate_due(db) -> dict:
    """激活 effective_at 已到且邮件已发的 pending 通知。

    Returns:
        {"activated": int, "checked": int}
    """
    now = datetime.now(timezone.utc)

    notices = list_pending_notices(db)
    checked = len(notices)
    activated = 0

    for notice in notices:
        if not notice.get("dispatched_at"):
            continue  # 邮件还没发,等待下次 dispatch cron
        try:
            effective_at = datetime.fromisoformat(notice["effective_at"])
            if effective_at.tzinfo is None:
                effective_at = effective_at.replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue

        if effective_at <= now:
            logger.info(
                "policy_notice cron: activating notice %s (effective_at=%s)",
                notice["id"], notice["effective_at"],
            )
            try:
                activate_notice(db, notice["id"])
                activated += 1
            except Exception:
                logger.exception(
                    "policy_notice cron: activate failed for notice %s", notice["id"]
                )

    return {"activated": activated, "checked": checked}
