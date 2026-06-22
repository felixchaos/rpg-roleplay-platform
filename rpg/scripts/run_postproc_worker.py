"""run_postproc_worker.py — W1 容量优化: 独立 Phase 4 后处理 worker。

不能走 PgBouncer(LISTEN/NOTIFY 会话级)。
必须直连 Postgres :5432。DATABASE_URL 不含 :5432 时启动即崩,明确报错。

启动:
    DATABASE_URL=postgresql://rpg:PASS@127.0.0.1:5432/rpg \\
        .venv/bin/python -m rpg.scripts.run_postproc_worker

或由 systemd rpg-postproc.service 管理(见 deploy/bare-metal/README.md §7.5)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import select
import sys
from pathlib import Path
from typing import Any

# 保证 rpg/ 在 sys.path(以便 import agents.* 等)
_RPG_DIR = Path(__file__).resolve().parent.parent
if str(_RPG_DIR) not in sys.path:
    sys.path.insert(0, str(_RPG_DIR))

import psycopg
import psycopg.rows

log = logging.getLogger("rpg.postproc_worker")

# ---------------------------------------------------------------------------
# 最大并发任务数 & 重试限制
# ---------------------------------------------------------------------------
MAX_TASKS_PER_POLL = 5
MAX_ATTEMPTS = 3
POLL_TIMEOUT_SEC = 30  # NOTIFY 没来时最多等 30s 再 poll 一次
_REAP_INTERVAL = 300.0  # _reap_stuck_running 最多每 5 分钟调一次

_last_reap_at: float = 0.0


# ---------------------------------------------------------------------------
# 任务 handler 注册表
# ---------------------------------------------------------------------------

async def _handle_extractor(payload: dict[str, Any]) -> None:
    """调 extractor.extract_state_ops 抽 JSON ops。"""
    from agents import extractor as _extractor
    gm_output = payload.get("gm_output") or ""
    if not gm_output.strip():
        return
    ops = await asyncio.to_thread(
        _extractor.extract_state_ops,
        narrative_text=gm_output,
        state_data={},  # worker 无法拿到实时 state,只做 no-op 安全 fallback
        user_id=payload.get("user_id"),
        timeout_sec=15,
    )
    log.debug("[postproc] extractor got %d ops for user=%s", len(ops or []), payload.get("user_id"))


async def _handle_phase_digest(payload: dict[str, Any]) -> None:
    """调 phase_digest 记录本轮摘要到 KB。"""
    try:
        from agents import phase_digest_agent as _pda
        await asyncio.to_thread(
            _pda.maybe_record_phase_digest,
            user_id=payload.get("user_id"),
            save_id=payload.get("save_id"),
            gm_output=payload.get("gm_output") or "",
            player_input=payload.get("player_input") or "",
        )
    except AttributeError:
        # phase_digest_agent 可能尚未实现 maybe_record_phase_digest;静默跳过
        log.debug("[postproc] phase_digest_agent.maybe_record_phase_digest not found, skipping")


async def _handle_acceptance_verifier(payload: dict[str, Any]) -> None:
    """跑 acceptance verifier,把 unmet 写到 audit_log。
    用函数内延迟 import 避免 worker 启动时拉入整个 FastAPI app 对象树（重量级）。"""
    try:
        # 延迟 import：app.py 依赖 FastAPI/路由/中间件，worker 只需其中两个函数。
        # 在调用时才 import 可将 worker 启动内存开销降到最低，且不影响功能正确性。
        from app import _acceptance_verifier_mode as _avm, _verify_acceptance as _va  # noqa: PLC0415
        curator_plan = payload.get("curator_plan") or {}
        acceptance = curator_plan.get("acceptance") or []
        gm_output = payload.get("gm_output") or ""
        if not acceptance or not gm_output.strip():
            return
        uid = payload.get("user_id")
        mode = _avm({"id": uid} if uid else None)
        _va(acceptance, gm_output, [], mode=mode, user_id=uid)
    except Exception as exc:
        log.warning("[postproc] acceptance_verifier skipped: %s", exc)


async def _handle_black_swan(payload: dict[str, Any]) -> None:
    """调 black_swan_agent.maybe_trigger。"""
    from agents.black_swan_agent import maybe_trigger as _maybe_trigger
    await asyncio.to_thread(
        _maybe_trigger,
        None,  # state — worker 没有实时 state,只做 enable_llm=False 安全 fallback
        user_id=payload.get("user_id") or 0,
        save_id=int(payload.get("save_id") or 0),
        script_id=payload.get("script_id"),
        api_id_override=payload.get("api_id_override"),
        model_override=payload.get("model_override"),
        enable_llm=False,  # worker 无法安全访问实时 state;LLM path 暂禁用
    )


async def _handle_image_gen(payload: dict[str, Any]) -> None:
    """生图 job：调 image_jobs.handle_image_gen（内部已 asyncio.to_thread 卸载阻塞的
    provider 调用/轮询，不会冻住单线程 worker 事件循环）。"""
    from platform_app.image_jobs import handle_image_gen as _h
    await _h(payload)


TASK_HANDLERS = {
    "extractor": _handle_extractor,
    "phase_digest": _handle_phase_digest,
    "acceptance_verifier": _handle_acceptance_verifier,
    "black_swan": _handle_black_swan,
    "image_gen": _handle_image_gen,
}


# ---------------------------------------------------------------------------
# 核心消费循环
# ---------------------------------------------------------------------------

async def _process_one(conn: psycopg.Connection, row: dict[str, Any]) -> None:
    """拿到一行任务,跑 handler,更新 status。"""
    task_id = row["id"]
    task_kind = row["task_kind"]
    attempts = row["attempts"] + 1

    # 原子认领:autocommit 下 SELECT ... FOR UPDATE SKIP LOCKED 的行锁在 execute 返回即释放(no-op),
    # 多 worker 会捞到同一行重复处理。改用条件 UPDATE...RETURNING 做 CAS:只有 status 仍是 pending/failed
    # 才置 running,RETURNING 空 = 已被别的 worker 抢走 → 跳过。
    claimed = conn.execute(
        "UPDATE chat_postproc_tasks SET status='running', started_at=now(), attempts=%s "
        "WHERE id=%s AND status IN ('pending','failed') RETURNING id",
        (attempts, task_id),
    ).fetchone()
    if not claimed:
        log.debug("[postproc] task %s 已被其他 worker 认领,跳过", task_id)
        return

    handler = TASK_HANDLERS.get(task_kind)
    if handler is None:
        log.warning("[postproc] unknown task_kind=%s id=%s, marking done", task_kind, task_id)
        conn.execute(
            "UPDATE chat_postproc_tasks SET status='done', completed_at=now() WHERE id=%s",
            (task_id,),
        )
        return

    try:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}")
        await handler(payload)
        conn.execute(
            "UPDATE chat_postproc_tasks SET status='done', completed_at=now() WHERE id=%s",
            (task_id,),
        )
        log.info("[postproc] task %s kind=%s done (attempt %d)", task_id, task_kind, attempts)
    except Exception as exc:
        log.exception("[postproc] task %s kind=%s failed (attempt %d)", task_id, task_kind, attempts)
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE chat_postproc_tasks SET status='failed', completed_at=now(), "
                "error_message=%s WHERE id=%s",
                (str(exc)[:500], task_id),
            )
        else:
            backoff_sec = 2 ** attempts * 10
            # paramstyle: 全位置占位符。原写法把命名 %(backoff)s 塞进 SQL 字符串字面量
            # 又传 dict,psycopg3 对"位置 %s + mapping 参数"直接抛错 → 退避 UPDATE 每次必崩,
            # 异常冒泡掀翻 consume 主循环 → 整个 worker 进程挂掉。改 make_interval 绑定。
            conn.execute(
                "UPDATE chat_postproc_tasks SET status='pending', attempts=%s, "
                "scheduled_at=now() + make_interval(secs => %s) WHERE id=%s",
                (attempts, backoff_sec, task_id),
            )


def _reap_stuck_running(conn: psycopg.Connection) -> None:
    """回收僵死任务:被置 running 后 worker 崩溃/被 kill,该行永卡 running,
    而消费查询只捞 pending/failed → 永不重投、静默丢失(用户反馈"丢事件"的后处理侧)。
    把 running 超过 5 分钟的行视为僵死:attempts >= MAX_ATTEMPTS 直接标 failed,
    否则降级回 pending 重投(防止 attempts 已饱和时永久僵尸循环)。"""
    try:
        n = conn.execute(
            "UPDATE chat_postproc_tasks "
            "SET status = CASE WHEN attempts >= %s THEN 'failed' ELSE 'pending' END, "
            "    error_message = CASE WHEN attempts >= %s THEN 'reaped after max attempts' ELSE error_message END "
            "WHERE status='running' AND started_at < now() - interval '5 minutes'",
            (MAX_ATTEMPTS, MAX_ATTEMPTS),
        ).rowcount
        if n:
            log.warning("[postproc] reaped %d stuck-running task(s) (failed or back to pending)", n)
    except Exception:
        log.exception("[postproc] reap stuck-running failed")


async def consume(conn: psycopg.Connection) -> None:
    """主循环:LISTEN/NOTIFY + 兜底 30s poll。autocommit 连接,每次 DML 单句提交。"""
    global _last_reap_at
    import time as _time
    conn.execute("LISTEN chat_postproc_new")
    log.info("[postproc] worker ready, LISTEN chat_postproc_new")
    _reap_stuck_running(conn)  # 启动即回收上次崩溃残留的 running
    _last_reap_at = _time.monotonic()

    while True:
        now = _time.monotonic()
        if now - _last_reap_at >= _REAP_INTERVAL:
            _reap_stuck_running(conn)
            _last_reap_at = _time.monotonic()
        # 不用 FOR UPDATE SKIP LOCKED — 认领路径已改为 _process_one 里的 CAS UPDATE...RETURNING。
        # SELECT 只是候选行扫描，实际认领由 CAS 原子完成；多 worker 同时读到同一行时，
        # CAS 保证只有一个 worker 能置 running，其余跳过（claimed is None）。
        rows = conn.execute(
            "SELECT id, user_id, save_id, commit_id, task_kind, payload, attempts "
            "FROM chat_postproc_tasks "
            "WHERE status IN ('pending', 'failed') AND attempts < %s "
            "AND scheduled_at <= now() "
            "ORDER BY scheduled_at "
            "LIMIT %s",
            (MAX_ATTEMPTS, MAX_TASKS_PER_POLL),
        ).fetchall()

        if not rows:
            # 等 NOTIFY 或超时后再 poll
            raw = conn.fileno()
            readable, _, _ = await asyncio.get_event_loop().run_in_executor(
                None, select.select, [raw], [], [], POLL_TIMEOUT_SEC
            )
            if readable:
                # 真正消费已缓冲的 NOTIFY(仅作唤醒提示,内容不用)。原写法 `conn.notifies` 只引用方法
                # 不调用 → libpq 通知积压不消费。timeout=0 = 排空当前缓冲后立即返回。
                try:
                    for _ in conn.notifies(timeout=0):
                        pass
                except TypeError:
                    for _ in conn.notifies():
                        break
                except Exception:
                    pass
            continue

        for row in rows:
            # 单任务异常隔离:绝不让一行的失败冒泡掀翻整个 worker 进程
            try:
                await _process_one(conn, row)
            except Exception:
                log.exception("[postproc] _process_one crashed for id=%s, skipping", row.get("id"))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    conn_str = os.environ.get("DATABASE_URL") or ""
    if not conn_str:
        raise RuntimeError("DATABASE_URL not set")

    # 强制直连 5432 — LISTEN/NOTIFY 不能过 PgBouncer transaction pool
    if ":6432/" in conn_str or ":6432?" in conn_str:
        raise RuntimeError(
            "postproc worker must use direct Postgres :5432, not PgBouncer :6432.\n"
            "Set DATABASE_URL=postgresql://rpg:PASS@127.0.0.1:5432/rpg in the service env."
        )

    log.info("[postproc] connecting to Postgres (direct :5432 required)")
    conn = psycopg.connect(
        conn_str,
        autocommit=True,
        row_factory=psycopg.rows.dict_row,
    )
    log.info("[postproc] worker started")

    try:
        asyncio.run(consume(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
