from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


# task 23：knowledge.sync_script_knowledge 的返回结果里常常嵌套 backend Row（dict-like）+ datetime
# 字段（created_at/updated_at）+ Decimal/UUID/bytes 等 jsonb 直接不能吃的类型。
# psycopg 的 Jsonb 默认走 json.dumps，遇到这些类型抛 TypeError，让整个 _run_sync_job 静默失败，
# 用户看到 import 200 OK 却没建知识库。这里统一兜底：递归走一遍替换为 JSON-safe 原语。
def _jsonify(value):
    """递归把不能直接 json.dumps 的类型转成 JSON-safe 原语。"""
    import datetime as _dt
    import decimal as _dec
    import uuid as _uuid
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    if isinstance(value, _dec.Decimal):
        # float 失真但 jsonb 不区分；如果要精确，改成 str(value)
        return float(value)
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            import base64 as _b64
            return {"__bytes_b64__": _b64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonify(v) for v in value]
    # psycopg Row / 其他 dict-like
    if hasattr(value, "keys") and callable(value.keys):
        try:
            return {str(k): _jsonify(value[k]) for k in value.keys()}
        except Exception:
            pass
    # 兜底：repr 而不是 raise，让 jsonb 至少能写
    return repr(value)


# ── 后台同步任务（DB 持久化 + 进程内执行）─────────────────────────
# B5: 状态从 import_jobs 表读写，避免 worker 重启或多进程下 _SYNC_STATE 丢失。
# 单一权威源：DB。in-process ThreadPoolExecutor 只是执行器。
#
# 三层保护防止重复跑同一任务：
# 1) 唯一索引 uq_import_jobs_active_per_script（migration v13）保证
#    (user_id, script_id, kind) 在 pending/running 状态下只能有一行
# 2) _schedule_knowledge_sync 用 INSERT ... ON CONFLICT DO NOTHING + RETURNING，
#    任何竞争方插入失败都回退到读 DB 拿现有 job_id
# 3) _run_sync_job 用 UPDATE ... WHERE status='pending' RETURNING 原子领取；
#    领取失败说明别的 worker 已经在跑（或已 done/failed），直接退出
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_SYNC_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="script-sync")


MAX_ACTIVE_JOBS_PER_USER = 1
# 超过这个时长还在 running 视为 worker 崩溃，启动 recover 时回收
from core.config import (
    sync_heartbeat_seconds as _sync_heartbeat_seconds,
)
from core.config import (
    sync_stale_running_seconds as _sync_stale_running_seconds,
)

STALE_RUNNING_SECONDS = _sync_stale_running_seconds()
# heartbeat 刷新间隔（worker 跑长任务时定期更新 heartbeat_at）
SYNC_HEARTBEAT_SECONDS = _sync_heartbeat_seconds()


def _schedule_knowledge_sync(user_id: int, script_id: int) -> str:
    """触发后台同步（DB 持久化）。

    去重 + 限流：
    - 同 (user, script) 已有 pending/running → 返回老 job_id（依赖 uq_import_jobs_active_per_script 唯一索引兜底）
    - 同 user 跨 script 的活跃任务数 >= MAX_ACTIVE_JOBS_PER_USER → 拒绝

    并发安全：INSERT ... ON CONFLICT DO NOTHING + RETURNING 让两个进程同时进入也只能成功一个插入；
    失败方回查同 (user, script) 拿到对方的 job_id 返回。
    """
    import secrets

    from ..db import connect, init_db
    init_db()
    job_id = f"ks_{script_id}_{secrets.token_hex(6)}"
    with connect() as db:
        # 限流（注意：此查询不在唯一索引保护内，是 advisory 的；竞争窗口的代价就是
        # 多挤进 1 个 job，对单用户场景可忽略；真要严格可用 advisory_lock）
        active_count_row = db.execute(
            """
            select count(*) as n from import_jobs
            where user_id = %s and kind = 'knowledge_sync'
              and status in ('pending', 'running')
              and (user_id, script_id) != (%s, %s)
            """,
            (user_id, user_id, script_id),
        ).fetchone()
        if int(active_count_row["n"]) >= MAX_ACTIVE_JOBS_PER_USER:
            raise ValueError(
                f"已有 {active_count_row['n']} 个同步任务在跑，"
                f"请等已有任务完成（每用户最多 {MAX_ACTIVE_JOBS_PER_USER} 个并发）"
            )

        # 原子去重：唯一索引 uq_import_jobs_active_per_script（partial unique index）
        # 保证同 (user_id, script_id, kind) 在 pending/running 状态下只能有一行。
        # PG 对 partial unique index 的 ON CONFLICT 需写 (cols) + WHERE 谓词（必须与索引谓词一致）。
        inserted = db.execute(
            """
            insert into import_jobs(job_id, user_id, script_id, kind, status, stage,
                                    stage_progress, stage_total, overall_progress, overall_total)
            values (%s, %s, %s, 'knowledge_sync', 'pending', 'pending', 0, 1, 0, 1)
            on conflict (user_id, script_id, kind)
              where status in ('pending', 'running')
              do nothing
            returning job_id
            """,
            (job_id, user_id, script_id),
        ).fetchone()
        if inserted:
            actual_job_id = inserted["job_id"]
        else:
            # 撞了：去查现有 active job
            row = db.execute(
                """
                select job_id from import_jobs
                where user_id = %s and script_id = %s and kind = 'knowledge_sync'
                  and status in ('pending', 'running')
                order by created_at desc limit 1
                """,
                (user_id, script_id),
            ).fetchone()
            if not row:
                # 极端竞争：唯一索引拒绝但 active 行又消失（被同时 done 了）。重试一次。
                inserted = db.execute(
                    """
                    insert into import_jobs(job_id, user_id, script_id, kind, status, stage,
                                            stage_progress, stage_total, overall_progress, overall_total)
                    values (%s, %s, %s, 'knowledge_sync', 'pending', 'pending', 0, 1, 0, 1)
                    on conflict (user_id, script_id, kind)
                      where status in ('pending', 'running')
                      do nothing
                    returning job_id
                    """,
                    (job_id, user_id, script_id),
                ).fetchone()
                if not inserted:
                    raise RuntimeError("无法插入 sync job 也无法读取已存在 job_id（请重试）")
                actual_job_id = inserted["job_id"]
            else:
                actual_job_id = row["job_id"]
    _SYNC_POOL.submit(_run_sync_job, actual_job_id)
    return actual_job_id


def _claim_pending_job(job_id: str) -> dict[str, Any] | None:
    """原子领取一个 pending 任务。
    UPDATE ... WHERE status='pending' RETURNING 一次完成判定 + 标记 + 取 owner 信息。
    返回 None 说明：任务不存在 / 已被别的 worker 领走 / 已 done/failed/cancelled。
    """
    from ..db import connect
    with connect() as db:
        row = db.execute(
            """
            update import_jobs
            set status = 'running',
                started_at = coalesce(started_at, now()),
                heartbeat_at = now(),
                updated_at = now()
            where job_id = %s and status = 'pending'
            returning user_id, script_id, kind
            """,
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


def _run_sync_job(job_id: str) -> None:
    """worker 入口：必须先 _claim_pending_job 原子领取，领不到直接退出。"""
    from psycopg.types.json import Jsonb

    from .. import knowledge
    from ..db import connect, init_db
    init_db()

    claim = _claim_pending_job(job_id)
    if not claim:
        # 已被别的 worker 领走 / 已结束 / 不存在；幂等返回
        logger.debug("sync job %s not pending, skip", job_id)
        return
    user_id = int(claim["user_id"])
    script_id = int(claim["script_id"])

    # 长任务 heartbeat：开一根后台线程，每 SYNC_HEARTBEAT_SECONDS 更新 heartbeat_at，
    # 让 stale-running 回收逻辑能区分活 worker 和死 worker。
    stop_heartbeat = threading.Event()

    # phase_backend: heartbeat 连续 3 次失败主动 abort,不再留 stale recover 兜底重跑。
    # 旧逻辑只 log.warning,worker 死了 DB 看不出来,recover 30 分钟后才回收。
    consecutive_hb_failures = {"n": 0}

    def _heartbeat_loop() -> None:
        while not stop_heartbeat.is_set():
            stop_heartbeat.wait(timeout=SYNC_HEARTBEAT_SECONDS)
            if stop_heartbeat.is_set():
                break
            try:
                with connect() as hb_db:
                    hb_db.execute(
                        "update import_jobs set heartbeat_at = now(), updated_at = now() "
                        "where job_id = %s and status = 'running'",
                        (job_id,),
                    )
                consecutive_hb_failures["n"] = 0
            except Exception:
                consecutive_hb_failures["n"] += 1
                logger.warning(
                    "heartbeat update failed for %s (consecutive=%d)",
                    job_id, consecutive_hb_failures["n"], exc_info=True,
                )
                if consecutive_hb_failures["n"] >= 3:
                    # DB 出问题超过 3 次,主动让主任务退出而不是 silently 跑下去
                    # (主任务的 ctl.update 也会跟着失败,后续 cancel/SSE 完全看不到)
                    logger.error(
                        "heartbeat consecutive 3 failures, abort job %s", job_id,
                    )
                    stop_heartbeat.set()
                    break

    hb_thread = threading.Thread(target=_heartbeat_loop, name=f"sync-hb-{job_id}", daemon=True)
    hb_thread.start()
    try:
        result = knowledge.sync_script_knowledge(user_id, script_id, rebuild=True)
        # phase_backend: result.partial_failures 非空 → done_with_errors,而非"假成功"。
        partial_failures = []
        if isinstance(result, dict):
            partial_failures = list(result.get("partial_failures") or [])
        final_status = "done_with_errors" if partial_failures else "done"
        error_text = ""
        if partial_failures:
            error_text = "; ".join(
                f"{p.get('stage', '?')}: {str(p.get('error', ''))[:100]}"
                for p in partial_failures
            )[:500]
        with connect() as db:
            db.execute(
                """
                update import_jobs
                set status = %s, stage = 'done',
                    stage_progress = 1, overall_progress = 1,
                    finished_at = now(), updated_at = now(),
                    usage_actual = %s,
                    warnings = %s,
                    error = case when %s = '' then error else %s end
                where job_id = %s
                """,
                # task 23：result 里可能含 datetime/date/Decimal/UUID/Row 等 jsonb 不能直接吃的对象
                # （如 sync_script_knowledge 把 book row 整个塞进结果时，含 created_at: datetime）。
                # 用 _jsonify 走一遍把它们转成 JSON-safe 字符串/原语，再喂 Jsonb。
                # 否则 psycopg 序列化时抛 TypeError，import 主路径已 200 但 sync 静默 failed → 用户以为知识库 OK 实际没建。
                (
                    final_status,
                    Jsonb(_jsonify({"result": result})),
                    Jsonb(_jsonify(partial_failures)),
                    error_text, error_text,
                    job_id,
                ),
            )
    except Exception as exc:
        logger.exception("sync job %s failed", job_id)
        with connect() as db:
            db.execute(
                """
                update import_jobs
                set status = 'failed', error = %s,
                    finished_at = now(), updated_at = now()
                where job_id = %s
                """,
                (str(exc)[:500], job_id),
            )
    finally:
        stop_heartbeat.set()


def recover_pending_sync_jobs(stale_running_seconds: int | None = None) -> dict[str, Any]:
    """启动时恢复 durable jobs。

    两类需要重新提交进线程池：
    1) status='pending' 但没有任何 worker 领走的（很可能是上次 crash 前已 schedule 但 submit 没完成）
    2) status='running' 但 heartbeat_at（或 started_at）超过 STALE_RUNNING_SECONDS 没更新的
       → 视为 worker 已死，原子回退到 pending，再丢回线程池
    返回：{recovered_pending: n, reclaimed_stale: n, resubmitted: [job_id...]}
    """
    from ..db import connect, init_db
    init_db()
    stale_seconds = stale_running_seconds if stale_running_seconds is not None else STALE_RUNNING_SECONDS
    resubmitted: list[str] = []
    with connect() as db:
        # 1) stale running → 原子回 pending
        stale_rows = db.execute(
            """
            update import_jobs
            set status = 'pending',
                error = case when error = '' then 'reclaimed_after_stale' else error end,
                heartbeat_at = null,
                updated_at = now()
            where kind = 'knowledge_sync'
              and status = 'running'
              and coalesce(heartbeat_at, started_at, created_at)
                  < now() - make_interval(secs => %s)
            returning job_id
            """,
            (stale_seconds,),
        ).fetchall()
        reclaimed_stale = [r["job_id"] for r in stale_rows]
        # 2) 取所有 pending（含刚刚回退的）
        pending_rows = db.execute(
            """
            select job_id from import_jobs
            where kind = 'knowledge_sync' and status = 'pending'
            order by created_at asc
            """,
        ).fetchall()
        pending_job_ids = [r["job_id"] for r in pending_rows]

    # 在 with 外面 submit，避免持着 DB 连接 submit
    for jid in pending_job_ids:
        try:
            _SYNC_POOL.submit(_run_sync_job, jid)
            resubmitted.append(jid)
        except Exception:
            logger.warning("resubmit pending sync job %s failed", jid, exc_info=True)
    return {
        "ok": True,
        "recovered_pending": len(pending_job_ids) - len(reclaimed_stale),
        "reclaimed_stale": len(reclaimed_stale),
        "stale_job_ids": reclaimed_stale,
        "resubmitted": resubmitted,
    }


def get_sync_status(user_id: int, script_id: int) -> dict[str, Any]:
    """返回该剧本最近一次同步任务的状态（DB 单一源）。"""
    from ..db import connect, init_db
    init_db()
    with connect() as db:
        row = db.execute(
            """
            select job_id, status, stage_progress, stage_total, overall_progress, overall_total,
                   started_at, finished_at, error, usage_actual, created_at
            from import_jobs
            where user_id = %s and script_id = %s and kind = 'knowledge_sync'
            order by created_at desc limit 1
            """,
            (user_id, script_id),
        ).fetchone()
    if not row:
        return {"ok": True, "status": "none", "script_id": script_id}
    progress_pct = 0
    if row["overall_total"] and row["overall_progress"] is not None:
        progress_pct = int(100 * int(row["overall_progress"]) / max(1, int(row["overall_total"])))
    out = {
        "job_id": row["job_id"],
        "user_id": user_id,
        "script_id": script_id,
        "status": row["status"],
        "progress": progress_pct,
        "started_at": row["started_at"].timestamp() if row["started_at"] else None,
        "finished_at": row["finished_at"].timestamp() if row["finished_at"] else None,
        "error": row["error"] or None,
    }
    usage = row.get("usage_actual") or {}
    if isinstance(usage, dict) and usage.get("result"):
        out["result_summary"] = {
            k: usage["result"].get(k)
            for k in ("documents", "chunks", "facts", "characters", "worldbook")
            if k in usage["result"]
        }
    return {"ok": True, **out}
