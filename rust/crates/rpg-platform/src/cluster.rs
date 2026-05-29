//! cluster —— 多 worker 部署的状态共享层。
//!
//! 对应 Python: `rpg/platform_app/cluster.py`。
//!
//! 提供:
//! - `stop_signals` 表:跨进程取消正在跑的 chat
//! - PG advisory lock:同 job_key 单进程互斥
//! - `STATE_CACHE_TTL_SEC` 常量 + `is_state_stale`:state_repository 缓存失效辅助
//!
//! 不创建 DDL — `stop_signals` 表由 rpg-db migrations 接管(见 TODO)。

use once_cell::sync::Lazy;
use rand::RngCore;
use sqlx::{PgPool, Row};

use crate::error::PlatformResult;

/// 当前进程唯一标识。
pub static WORKER_ID: Lazy<String> = Lazy::new(|| {
    let host = hostname();
    let pid = std::process::id();
    let mut rnd = [0u8; 4];
    rand::thread_rng().fill_bytes(&mut rnd);
    let suffix: String = rnd.iter().map(|b| format!("{:02x}", b)).collect();
    format!("{}-{}-{}", host, pid, suffix)
});

fn hostname() -> String {
    std::env::var("HOSTNAME")
        .ok()
        .or_else(|| std::env::var("COMPUTERNAME").ok())
        .unwrap_or_else(|| "unknown".to_string())
}

// ─── stop_signals ──────────────────────────────────────────────────────

const STOP_TABLE_DDL: &str = "create table if not exists stop_signals (\
      user_id bigint not null,\
      run_id bigint not null,\
      requested_at timestamptz not null default now(),\
      primary key (user_id, run_id)\
    )";

async fn ensure_stop_table(pool: &PgPool) -> PlatformResult<()> {
    sqlx::query(STOP_TABLE_DDL).execute(pool).await?;
    Ok(())
}

/// 兜底初始化 `stop_signals` 表。rpg-db migrations 没接管时由 platform 调用。
/// 供 `app.rs` 启动钩子在 init 阶段调用一次,避免每次 request_stop 都重建。
pub async fn init_stop_signals_table(pool: &PgPool) -> PlatformResult<()> {
    ensure_stop_table(pool).await
}

/// 请求停止 user 当前正在跑的 run。
pub async fn request_stop(pool: &PgPool, user_id: i64, run_id: i64) -> PlatformResult<()> {
    ensure_stop_table(pool).await?;
    sqlx::query(
        "insert into stop_signals(user_id, run_id) values ($1, $2) on conflict do nothing",
    )
    .bind(user_id)
    .bind(run_id)
    .execute(pool)
    .await?;
    Ok(())
}

/// 检查是否被请求停止。任何 DB 异常都吞掉返回 false (与 Python 一致)。
pub async fn is_stop_requested(pool: &PgPool, user_id: i64, run_id: i64) -> bool {
    if user_id == 0 {
        return false;
    }
    if ensure_stop_table(pool).await.is_err() {
        return false;
    }
    sqlx::query("select 1 as ok from stop_signals where user_id = $1 and run_id = $2")
        .bind(user_id)
        .bind(run_id)
        .fetch_optional(pool)
        .await
        .map(|r| r.is_some())
        .unwrap_or(false)
}

/// worker 结束时清理。
pub async fn clear_stop(pool: &PgPool, user_id: i64, run_id: i64) {
    if ensure_stop_table(pool).await.is_err() {
        return;
    }
    let _ = sqlx::query("delete from stop_signals where user_id = $1 and run_id = $2")
        .bind(user_id)
        .bind(run_id)
        .execute(pool)
        .await;
}

/// 定期清理超过 N 秒的孤儿信号。返回删除条数。
pub async fn cleanup_old_stop_signals(pool: &PgPool, max_age_sec: i64) -> u64 {
    if ensure_stop_table(pool).await.is_err() {
        return 0;
    }
    sqlx::query(
        "delete from stop_signals where requested_at < now() - (interval '1 second' * $1)",
    )
    .bind(max_age_sec)
    .execute(pool)
    .await
    .map(|r| r.rows_affected())
    .unwrap_or(0)
}

// ─── advisory lock ─────────────────────────────────────────────────────

fn job_lock_id(job_key: &str) -> i64 {
    // Python `abs(hash(job_key)) % (2**31)`,Python `hash` 不稳定。
    // 这里用稳定的 DJB2 → i32 范围,等价语义 (单进程 + DB 内一致)。
    let mut h: u32 = 5381;
    for b in job_key.as_bytes() {
        h = h.wrapping_mul(33).wrapping_add(*b as u32);
    }
    (h % (1 << 31)) as i64
}

/// 非阻塞 advisory lock。返回 false = 已被其它 worker 占。
pub async fn try_acquire_job_lock(pool: &PgPool, job_key: &str) -> bool {
    let lock_id = job_lock_id(job_key);
    sqlx::query("select pg_try_advisory_lock($1) as ok")
        .bind(lock_id)
        .fetch_one(pool)
        .await
        .ok()
        .and_then(|r| r.try_get::<bool, _>("ok").ok())
        .unwrap_or(false)
}

pub async fn release_job_lock(pool: &PgPool, job_key: &str) {
    let lock_id = job_lock_id(job_key);
    let _ = sqlx::query("select pg_advisory_unlock($1)")
        .bind(lock_id)
        .execute(pool)
        .await;
}

// ─── state cache invalidation ──────────────────────────────────────────

/// state cache TTL,秒。取 `rpg_core::config::state_cache_ttl()`。
pub fn state_cache_ttl_sec() -> u64 {
    rpg_core::config::state_cache_ttl()
}

/// 内存里的 state 是否落后于 DB。基于 `runtime_checkouts.updated_at`。
pub async fn is_state_stale(
    pool: &PgPool,
    save_id: i64,
    cached_updated_at_ns: i64,
) -> bool {
    let row = sqlx::query(
        "select extract(epoch from updated_at) * 1000000000 as ns \
         from runtime_checkouts where save_id = $1",
    )
    .bind(save_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();
    let Some(r) = row else {
        return false;
    };
    let db_ns: f64 = r.try_get("ns").unwrap_or(0.0);
    (db_ns as i64) > cached_updated_at_ns
}

// ─── cluster 树 / 关系 ─────────────────────────────────────────────────
// Python `cluster.py` 没有 tree;任务说明里的"Cluster 集群关系图 + tree 操作"
// 实际指 `branch_*` 分支树,见 `branches/` 模块。这里只占位 re-export 入口。
pub use crate::branches::tree_ops;

// 兜底 DDL:rpg-db migrations 没接管时由 `init_stop_signals_table` 一次性创建;
// CRUD 也仍各自走 ensure_stop_table 以容错 fresh DB(零成本 if exists)。

// ─── worker heartbeat (import_jobs) ───────────────────────────────────────
//
// 对应 Python script_import.py `_heartbeat_loop`:长任务 worker 定期刷新
// import_jobs.heartbeat_at,让 detect_stale_workers 能区分活 worker 与死 worker。
//
// Rust 端跑 Tokio 任务时可 spawn 一个后台 task 调 `worker_heartbeat`,
// `detect_stale_workers` 供周期性巡检(如启动时 / cron)调用以回收脏行。

/// 刷新 `import_jobs.heartbeat_at` — 长任务 worker 定期调用。
///
/// 只更新 status='running' 的行(防止覆盖已完成/失败任务)。
/// 返回更新行数;0 = 任务已不在 running(worker 可自行退出心跳循环)。
///
/// 对应 Python `hb_db.execute("update import_jobs set heartbeat_at = now() ...")`。
pub async fn worker_heartbeat(pool: &PgPool, job_id: &str) -> PlatformResult<u64> {
    let res = sqlx::query(
        "update import_jobs \
         set heartbeat_at = now(), updated_at = now() \
         where job_id = $1 and status = 'running'",
    )
    .bind(job_id)
    .execute(pool)
    .await?;
    Ok(res.rows_affected())
}

/// 把 heartbeat_at 超时的 running 任务回退到 pending,供重新调度。
///
/// 对应 Python `test_durable_sync.py` 里的 stale-running 回收语义:
/// `heartbeat_at < now() - timeout_sec` 且 `status='running'` → 回退为 `pending`。
///
/// 返回回收的任务 job_id 列表。
pub async fn detect_stale_workers(
    pool: &PgPool,
    timeout_sec: i64,
) -> PlatformResult<Vec<String>> {
    // 两步:先查出 stale job_id,再批量回退。用 RETURNING 一步完成。
    let rows = sqlx::query(
        "update import_jobs \
         set status = 'pending', \
             heartbeat_at = null, \
             updated_at = now() \
         where status = 'running' \
           and heartbeat_at < now() - (interval '1 second' * $1) \
         returning job_id",
    )
    .bind(timeout_sec)
    .fetch_all(pool)
    .await?;
    let ids: Vec<String> = rows
        .iter()
        .filter_map(|r| r.try_get::<String, _>("job_id").ok())
        .collect();
    if !ids.is_empty() {
        tracing::warn!(
            "detect_stale_workers: 回收 {} 个超时任务 (timeout={}s): {:?}",
            ids.len(),
            timeout_sec,
            ids
        );
    }
    Ok(ids)
}

// ─── tests ─────────────────────────────────────────────────────────────────
#[cfg(test)]
mod cluster_tests {
    use super::*;

    /// WORKER_ID 必须非空且包含预期格式部分。
    #[test]
    fn worker_id_non_empty() {
        let id = WORKER_ID.as_str();
        assert!(!id.is_empty(), "WORKER_ID 不应为空");
        // 应包含 '-' 分隔符(hostname-pid-hex)
        assert!(id.contains('-'), "WORKER_ID 应含 '-' 分隔符");
    }

    /// job_lock_id 对相同 key 应返回相同结果(稳定哈希)。
    #[test]
    fn job_lock_id_stable() {
        let id1 = job_lock_id("sync:user42:script7");
        let id2 = job_lock_id("sync:user42:script7");
        assert_eq!(id1, id2, "同 key 的 lock_id 必须幂等");
    }

    /// 不同 key 应产生不同 lock_id(基本碰撞检测)。
    #[test]
    fn job_lock_id_differs() {
        let id1 = job_lock_id("job_a");
        let id2 = job_lock_id("job_b");
        assert_ne!(id1, id2, "不同 key 的 lock_id 不应相同");
    }
}
