"""rpg.cron.hard_delete — 账号硬删 cron (LC-01/LC-02).

每天 03:00 跑一次。把 account_delete_queue.scheduled_hard_delete_at <= now()
且 completed_at IS NULL 的账号物理删除（级联所有用户数据）。

用法:
    from rpg.cron.hard_delete import run_hard_delete
    result = run_hard_delete(db)   # db = psycopg.Connection (dict_row)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 按 FK 依赖顺序排列（先删依赖方，再删被依赖方；最终删 users 主行）。
# 规则：引用 game_saves 的表先删；引用 users 但不引用 game_saves 的后删。
# branch_commits / branch_nodes 的 FK 指向 game_saves → 随 game_saves CASCADE，
# 但 DB 是 ON DELETE CASCADE，所以删 game_saves 行时 DB 自动级联——
# 此处显式删以便在连接池 transaction 模式下安全操作（pgbouncer transaction mode
# 不支持 SET CONSTRAINTS DEFERRED，所以用手工顺序）。
#
# 注意：
#   - user_runtime         → user_id PK，CASCADE
#   - user_preferences     → user_id PK，CASCADE
#   - profile_extras       → user_id PK，CASCADE
#   - dmca_strikes         → user_id PK，CASCADE
#   - game_saves           → user_id，CASCADE（子表 branch_commits/branch_nodes/
#                            runtime_checkouts/save_phase_digests/game_sessions/
#                            messages/memories/context_runs 全部 ON DELETE CASCADE）
#   - worldbook_entries    → script_id（NOT user_id），不在此清单
#   - memories             → user_id nullable，CASCADE
#   - script_overrides     → script_id PK（NOT user_id），不在此清单
#   - account_delete_queue → user_id PK，CASCADE（随 users 删自动清，但先标 completed_at）

TABLES_USER_OWNED_ORDER: list[str] = [
    # --- 轻量 per-user 元数据（无子 FK 依赖）---
    "feedback_consent_log",
    "feedback",
    "splash_acks",
    "email_verifications",
    "token_usage",
    "user_api_credentials",
    "user_preferences",
    "extraction_quota",
    "import_jobs",
    # --- 人物卡 / 身份卡（合并多态表，user_id nullable）---
    "character_cards",         # user_id nullable，PC/persona 卡
    "user_character_cards",    # 旧分立表（v28 前），保留兜底
    "user_personas",           # 旧分立表（v28 前），保留兜底
    # --- 存档体系（game_saves 级联子表）---
    "user_runtime",            # user_id PK → CASCADE
    "game_saves",              # user_id → CASCADE（branch_commits/nodes/sessions/memories 全跟）
    # --- 独立 user_id 引用（不挂 game_saves）---
    "memories",                # user_id nullable，CASCADE
    "profile_extras",          # user_id PK，CASCADE
    "dmca_strikes",            # user_id PK，CASCADE
    # --- 审计日志 ---
    "login_audit",             # 无 user_id FK，但记录 username；按 username 删即可（见注释）
]

# login_audit 没有 user_id FK（只有 username text 列），无法按 user_id 删。
# 硬删时跳过（username 已匿名/用户已不存在，90 天后会被 prune_audit 清理）。
_SKIP_FOR_HARD_DELETE = {"login_audit"}


def run_hard_delete(db) -> dict:
    """物理删除所有到期账号。

    Args:
        db: psycopg Connection（dict_row），调用方负责事务管理。

    Returns:
        {"deleted": int, "due_at_run": int}
    """
    due_rows = db.execute(
        """
        select user_id
        from account_delete_queue
        where scheduled_hard_delete_at <= now()
          and completed_at is null
        """
    ).fetchall()

    due_at_run = len(due_rows)
    deleted = 0

    for row in due_rows:
        user_id = row["user_id"]
        logger.info("hard_delete: starting deletion for user_id=%s", user_id)

        try:
            # 先标 completed_at，避免幂等重复执行（即使 users 行已不存在也安全）
            db.execute(
                "update account_delete_queue set completed_at = now() where user_id = %s",
                (user_id,),
            )

            # 显式按依赖顺序删（pgbouncer transaction 模式下安全）
            for table in TABLES_USER_OWNED_ORDER:
                if table in _SKIP_FOR_HARD_DELETE:
                    continue
                db.execute(f"delete from {table} where user_id = %s", (user_id,))

            # 最后删主行（触发 ON DELETE CASCADE 清理剩余 FK）
            db.execute("delete from users where id = %s", (user_id,))

            deleted += 1
            logger.info("hard_delete: done user_id=%s", user_id)
        except Exception:
            logger.exception("hard_delete: failed for user_id=%s", user_id)
            # 继续处理其他用户，不中止整批

    return {"deleted": deleted, "due_at_run": due_at_run}
