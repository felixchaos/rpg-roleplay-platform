"""platform_app.dmca — DMCA strike 计数与账户终止辅助逻辑。

依赖表（由 v37 迁移创建）:
  dmca_strikes       — 每条 strike 记录
  banned_users       — 永久封禁名单（email + ip）
  account_delete_queue — 待终止队列
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

# 累犯阈值：达到此次数自动触发账户终止流程
DMCA_STRIKE_THRESHOLD = 3


def increment_strike(db, user_id: int, reason: str) -> dict:
    """为指定用户添加一条 DMCA strike 记录。

    返回:
        dict with keys:
          - strike_count (int): 该用户当前累计 strike 数
          - terminate (bool): 是否已达到终止阈值
          - strike_id (int): 新写入的 strike 记录 ID
    """
    row = db.execute(
        """
        insert into dmca_strikes (user_id, reason, created_at)
        values (%s, %s, now())
        returning id
        """,
        (user_id, reason),
    ).fetchone()
    strike_id = row["id"] if row else None

    count_row = db.execute(
        "select count(*) as cnt from dmca_strikes where user_id = %s",
        (user_id,),
    ).fetchone()
    strike_count = count_row["cnt"] if count_row else 1

    terminate = strike_count >= DMCA_STRIKE_THRESHOLD

    log.info(
        "dmca.strike user_id=%d count=%d/%d terminate=%s",
        user_id, strike_count, DMCA_STRIKE_THRESHOLD, terminate,
    )

    return {
        "strike_id": strike_id,
        "strike_count": int(strike_count),
        "terminate": terminate,
    }


def queue_account_termination(db, user_id: int, reason: str) -> None:
    """将账户加入终止队列，同时写入 banned_users（邮箱 + 注册 IP）。

    TODO: account_delete_queue / banned_users 表名与字段在 v37 落地后核对。
    """
    # 取用户邮箱与注册 IP
    user_row = db.execute(
        "select email, registration_ip from users where id = %s",
        (user_id,),
    ).fetchone()

    email = user_row["email"] if user_row else None
    reg_ip = user_row.get("registration_ip") if user_row else None

    # 写入终止队列
    db.execute(
        """
        insert into account_delete_queue (user_id, reason, queued_at)
        values (%s, %s, now())
        on conflict (user_id) do update
          set reason = excluded.reason, queued_at = now()
        """,
        (user_id, reason),
    )

    # 写入永久封禁名单
    if email:
        db.execute(
            """
            insert into banned_users (email, ip, reason, banned_at)
            values (%s, %s, %s, now())
            on conflict (email) do update
              set reason = excluded.reason, banned_at = now()
            """,
            (email, reg_ip, reason),
        )

    # 立即停用 sessions，阻止继续登录
    db.execute(
        "delete from sessions where user_id = %s",
        (user_id,),
    )

    # 更新用户状态
    db.execute(
        "update users set deactivated_at = now(), ban_reason = %s where id = %s",
        (reason, user_id),
    )

    log.warning(
        "dmca.terminate user_id=%d email=%s reason=%r",
        user_id, email, reason,
    )
