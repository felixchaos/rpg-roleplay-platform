"""CLI 入口 — 手动触发 cron 任务（供 dev 测试 + docker cron service）.

用法（cwd 必须在 rpg/，与后端 uvicorn app:app 一致的裸模块约定）:
    python -m scripts.run_cron hard_delete
    python -m scripts.run_cron prune_audit
    python -m scripts.run_cron all

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
    from cron.hard_delete import run_hard_delete
    result = run_hard_delete(db)
    logger.info("hard_delete: %s", result)
    _write_audit(db, "cron.hard_delete", result)
    return result


def cmd_prune_audit(db) -> dict:
    from cron.prune_audit import run_prune_admin_audit, run_prune_login_audit
    r1 = run_prune_login_audit(db)
    r2 = run_prune_admin_audit(db)
    result = {"login_audit_pruned": r1["pruned"], "admin_audit_pruned": r2["pruned"]}
    logger.info("prune_audit: %s", result)
    _write_audit(db, "cron.prune_audit", result)
    return result


def cmd_policy_dispatch(db) -> dict:
    """扫描并发送待发政策变更通知邮件 (DOC-02/AUP-03)."""
    from cron.policy_notice import run_dispatch_due
    result = run_dispatch_due(db)
    logger.info("policy_dispatch: %s", result)
    _write_audit(db, "cron.policy_dispatch", result)
    return result


def cmd_policy_activate(db) -> dict:
    """激活 effective_at 已到的政策版本 (DOC-02/AUP-03)."""
    from cron.policy_notice import run_activate_due
    result = run_activate_due(db)
    logger.info("policy_activate: %s", result)
    _write_audit(db, "cron.policy_activate", result)
    return result


def cmd_prune_feedback(db) -> dict:
    """删 24 月前的反馈行,保留 nsfw_terminate 证据 (FB-09)."""
    from cron.prune_feedback import run_prune_feedback
    result = run_prune_feedback(db)
    logger.info("prune_feedback: %s", result)
    _write_audit(db, "cron.prune_feedback", result)
    return result


def cmd_phase_digest_backfill(db) -> dict:
    """重试卡住的 phase 浓缩(status='closed' summary='':异步 compact 失败/进程重启留下)。

    背景:phase 切换时 chat_pipeline fire-and-forget 触发一次 compact_phase;若那次因
    异常/重启没成,该 phase 行停在 closed+空 summary,这段约 24 回合对 GM 不可见(原文在
    DB 不丢,但浓缩缺失)。此前 `scripts/phase_digest_worker.py` 写了重试逻辑却没挂任何
    cron → 永不自动重试。本命令把它接进每日 cron(`run_cron all`)做后台 backfill。

    compact_phase 内部按 worker 查出的 user_id 走 BYOK 凭证(user_api_credentials 表,
    无 HTTP/ContextVar 依赖),cron 进程可正常取 key;用户没配 key 的行只是再次失败计入
    failed,无副作用。每轮有界(MAX),逐行 try 隔离,本命令绝不抛异常(否则会中断
    `run_cron all` 后续任务)。
    """
    result = {"done": 0, "failed": 0, "skipped_no_key": 0, "pending": 0}
    PHASE_BACKFILL_MAX = 20  # 单次 cron 最多重试几个,控时长 + BYOK 调用成本
    try:
        # 生产以 `-m rpg.scripts.run_cron` 跑(rpg.* 可导);测试/直接调以 rpg/ 为根(顶层可导)。
        try:
            from rpg.agents.phase_digest_agent import compact_phase
            from rpg.scripts.phase_digest_worker import find_pending
        except ModuleNotFoundError:
            from agents.phase_digest_agent import compact_phase
            from scripts.phase_digest_worker import find_pending

        pending = find_pending(limit=PHASE_BACKFILL_MAX)
        result["pending"] = len(pending)
        for p in pending:
            try:
                r = compact_phase(
                    save_id=int(p["save_id"]),
                    phase_index=int(p["phase_index"]),
                    user_id=int(p["user_id"]),
                    force=True,
                ) or {}
                err = r.get("error")
                if err:
                    # 无凭证类失败单独计数,便于辨别"卡住"vs"用户没配 key"
                    if "key" in str(err).lower() or "credential" in str(err).lower():
                        result["skipped_no_key"] += 1
                    else:
                        result["failed"] += 1
                        # 记错因(截断),否则失败只剩计数、下次没法 debug 为何卡住
                        logger.warning(
                            "phase_digest_backfill: compact returned error save=%s phase=%s err=%s",
                            p.get("save_id"), p.get("phase_index"), str(err)[:200],
                        )
                else:
                    result["done"] += 1
            except Exception:
                logger.exception(
                    "phase_digest_backfill: compact_phase failed save=%s phase=%s",
                    p.get("save_id"), p.get("phase_index"),
                )
                result["failed"] += 1
    except Exception:
        logger.exception("phase_digest_backfill: setup/find_pending failed")
    logger.info("phase_digest_backfill: %s", result)
    _write_audit(db, "cron.phase_digest_backfill", result)
    return result


COMMANDS = {
    "hard_delete": cmd_hard_delete,
    "prune_audit": cmd_prune_audit,
    "policy_dispatch": cmd_policy_dispatch,
    "policy_activate": cmd_policy_activate,
    "prune_feedback": cmd_prune_feedback,
    "phase_digest_backfill": cmd_phase_digest_backfill,
}

_ALL_COMMAND_NAMES = "|".join(COMMANDS.keys())


def main(argv: list[str] | None = None) -> None:
    args = (argv or sys.argv)[1:]
    if not args:
        print(
            f"Usage: python -m scripts.run_cron <{_ALL_COMMAND_NAMES}|all>",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = args[0].strip().lower()
    if cmd not in (*COMMANDS, "all"):
        print(f"Unknown command: {cmd!r}", file=sys.stderr)
        sys.exit(1)

    from platform_app.db import connect, init_db
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
