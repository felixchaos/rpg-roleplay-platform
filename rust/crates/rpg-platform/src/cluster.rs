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

// TODO[Sonnet]: DDL 迁到 rpg-db migrations,而非每次调用 create table if not exists。
// TODO[Sonnet]: import_pipeline 那边的 worker heartbeat,跨进程超时检测。
