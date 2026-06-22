"""
cluster.py — 多机部署的状态共享层

进程级内存（_state_by_user / _stop_events_by_user / import_pipeline._RUNNING /
model_probe._LIST_CACHE 等）在多 worker 部署下会串档。把关键的两类状态下沉到 DB：

1. stop_signal：用一张 stop_signals(user_id, run_id, requested_at) 表，
   chat handler 每 N 个 token 查一次。
2. job lock：advisory lock 防止同 job_id 被多 worker 重复跑。
3. state cache invalidation：state_repository 加可选的"最后修改时间"检查，
   N 秒回退到 DB。

worker_id：每个 uvicorn 进程启动时分配一个 UUID，用来区分谁在跑哪个 job。
"""
from __future__ import annotations

import hashlib
import os
import secrets
import socket

from .db import connect, init_db

# 进程唯一标识：hostname + pid + 启动时的随机数
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(4)}"
try:
    STOP_SIGNAL_MAX_AGE_SEC = max(1, int(os.getenv("RPG_STOP_SIGNAL_MAX_AGE_SEC", "900")))
except ValueError:
    STOP_SIGNAL_MAX_AGE_SEC = 900


def _stable_lock_id(job_key: str) -> int:
    """job_key → 稳定 int8 advisory lock id (跨进程一致)。

    旧实现 abs(hash(key)) 受 Python 默认 hash 随机化影响,
    不同 worker 同一 key 算出不同 lock_id, 互不排斥, advisory lock 形同虚设。
    用 sha256 取前 8 字节转 signed int8 (PG bigint range)。
    """
    digest = hashlib.sha256(job_key.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


# ══════════════════════════════════════════════════════════════════════
#  Stop signal: 跨进程取消正在跑的 chat
# ══════════════════════════════════════════════════════════════════════
# 注: user_id 加 FK + cascade, 防止用户被删后 stop_signals 残留孤儿行。
# run_id 仍是 bigint; 调用方必须传入进程重启后也不重复的 run_id。
_STOP_TABLE_DDL = """
create table if not exists stop_signals (
  user_id bigint not null references users(id) on delete cascade,
  run_id bigint not null,
  requested_at timestamptz not null default now(),
  primary key (user_id, run_id)
)
"""


# [round-4-P2] 建表只跑一次:原来 is_stop_requested 每个流式 token 都触发 CREATE TABLE
#   IF NOT EXISTS DDL + 一次连接池 acquire,几个并发 streaming 用户即可耗尽连接池(每请求 8s 超时)。
_STOP_TABLE_READY = False
_last_stop_cleanup_at = 0.0  # 过期行清理节流(秒)
_STOP_CLEANUP_INTERVAL = 30.0


def _ensure_stop_table() -> None:
    global _STOP_TABLE_READY
    if _STOP_TABLE_READY:
        return
    init_db()
    with connect() as db:
        db.execute(_STOP_TABLE_DDL)
    _STOP_TABLE_READY = True


def request_stop(user_id: int, run_id: int) -> None:
    """请求停止 user 当前正在跑的 run。worker 下次检查时会看到。"""
    _ensure_stop_table()
    with connect() as db:
        db.execute(
            """
            insert into stop_signals(user_id, run_id) values (%s, %s)
            on conflict (user_id, run_id) do update set requested_at = now()
            """,
            (int(user_id), int(run_id)),
        )


def is_stop_requested(user_id: int, run_id: int) -> bool:
    """检查是否被请求停止。worker 每 N 个 token 调一次。"""
    if not user_id:
        return False
    try:
        _ensure_stop_table()
        global _last_stop_cleanup_at
        import time as _t
        _now = _t.monotonic()
        _do_cleanup = (_now - _last_stop_cleanup_at) >= _STOP_CLEANUP_INTERVAL
        with connect() as db:
            if _do_cleanup:
                # [round-4-P2] 过期行清理从「每 token 都删」改为节流 ≥30s 一次,避免热路径无谓 DELETE。
                _last_stop_cleanup_at = _now
                db.execute(
                    "delete from stop_signals where requested_at < now() - (interval '1 second' * %s)",
                    (int(STOP_SIGNAL_MAX_AGE_SEC),),
                )
            row = db.execute(
                """
                select 1
                from stop_signals
                where user_id = %s
                  and run_id = %s
                  and requested_at >= now() - (interval '1 second' * %s)
                """,
                (int(user_id), int(run_id), int(STOP_SIGNAL_MAX_AGE_SEC)),
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def clear_stop(user_id: int, run_id: int) -> None:
    """worker 结束时清理。"""
    try:
        _ensure_stop_table()
        with connect() as db:
            db.execute(
                "delete from stop_signals where user_id = %s and run_id = %s",
                (int(user_id), int(run_id)),
            )
    except Exception:
        pass


def cleanup_old_stop_signals(max_age_sec: int = 3600) -> int:
    """定期清理超过 1 小时的孤儿信号。"""
    try:
        _ensure_stop_table()
        with connect() as db:
            cur = db.execute(
                "delete from stop_signals where requested_at < now() - (interval '1 second' * %s)",
                (int(max_age_sec),),
            )
        return cur.rowcount if cur else 0
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════════════
#  Job lock: 防止同 job_id 被多 worker 重复跑
# ══════════════════════════════════════════════════════════════════════
# ⚠️ 原实现用 session 级 pg_advisory_lock:连接经 PgBouncer 事务池借还,acquire 与 release 落在
#    不同后端会话 → unlock 永远 no-op、锁泄漏;且锁随连接归还/重置即失效 → 形同虚设,多 worker
#    可同跑同一 import job(数据重复/损坏)。改用 DB 行锁(与 stop_signals 同套路,跨连接/事务池可靠),
#    带 stale 接管做崩溃恢复(持有者崩溃后超时可被另一 worker 接管)。
try:
    _JOB_LOCK_STALE_SEC = max(300, int(os.getenv("RPG_JOB_LOCK_STALE_SEC", "7200")))
except ValueError:
    _JOB_LOCK_STALE_SEC = 7200

_JOB_LOCK_DDL = """
create table if not exists job_locks (
  job_key text primary key,
  worker_id text not null,
  acquired_at timestamptz not null default now()
)
"""


def _ensure_job_lock_table() -> None:
    init_db()
    with connect() as db:
        db.execute(_JOB_LOCK_DDL)


def try_acquire_job_lock(job_key: str, worker_id: str = WORKER_ID) -> bool:
    """非阻塞抢锁。返回 False = 已被其他 worker 持有(且未 stale)。"""
    _ensure_job_lock_table()
    with connect() as db:
        # 新建即抢到;冲突仅当旧锁 stale(持有者疑似崩溃)才接管;否则 RETURNING 空 → 抢锁失败。
        row = db.execute(
            """
            insert into job_locks(job_key, worker_id, acquired_at) values (%s, %s, now())
            on conflict (job_key) do update
              set worker_id = excluded.worker_id, acquired_at = now()
              where job_locks.acquired_at < now() - (interval '1 second' * %s)
            returning job_key
            """,
            (job_key, worker_id, int(_JOB_LOCK_STALE_SEC)),
        ).fetchone()
    return bool(row)


def release_job_lock(job_key: str, worker_id: str = WORKER_ID) -> None:
    """释放本 worker 持有的锁(只删自己的,不动别人 stale 接管后的锁)。"""
    try:
        _ensure_job_lock_table()
        with connect() as db:
            db.execute(
                "delete from job_locks where job_key = %s and worker_id = %s",
                (job_key, worker_id),
            )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
#  state cache invalidation
# ══════════════════════════════════════════════════════════════════════
# 思路：state_repository 缓存 state 时记一个 last_db_check_ts，
# 每 STATE_CACHE_TTL 秒后再回 DB 查 runtime_checkouts.updated_at，
# 比内存版新就丢缓存。这个逻辑在 state_repository 里实现，cluster.py 只提供 TTL 常量。
from core.config import state_cache_ttl as _state_cache_ttl

STATE_CACHE_TTL_SEC = _state_cache_ttl()


def is_state_stale(save_id: int, cached_updated_at_ns: int) -> bool:
    """检查内存缓存的 state 是否落后于 DB。"""
    try:
        init_db()
        with connect() as db:
            row = db.execute(
                "select extract(epoch from updated_at) * 1000000000 as ns "
                "from runtime_checkouts where save_id = %s",
                (int(save_id),),
            ).fetchone()
        if not row:
            return False
        db_ns = int(row["ns"] or 0)
        return db_ns > cached_updated_at_ns
    except Exception:
        return False
