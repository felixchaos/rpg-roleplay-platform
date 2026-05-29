//! pool.rs — 对应 rpg/platform_app/db/connection.py
//!
//! 核心函数：
//!   init_pool(database_url, max_size)           → Result<PgPool, DbError>  (向后兼容)
//!   init_pool_with_opts(database_url, PoolOpts)  → Result<PgPool, DbError>  (参数化版)
//!
//! Python 原版用 psycopg_pool.ConnectionPool(min_size, max_size, timeout)；
//! 这里用 sqlx::postgres::PgPoolOptions 对等实现,并补全防雪崩参数。
//!
//! ## 雪崩防护设计
//!
//! | 参数                | 默认值  | 说明                                               |
//! |---------------------|---------|----------------------------------------------------|
//! | `min_connections`   | 2       | 始终保持热连接,避免冷启动抖动                      |
//! | `idle_timeout`      | 600 s   | 空闲连接 10 min 后回收,节省 DB server 文件描述符  |
//! | `max_lifetime`      | 1800 s  | 30 min 强制回收,防止连接泄漏 / DNS staleness       |
//! | `acquire_timeout`   | **5 s** | 池满时快速失败而非堆积排队,防止请求雪崩            |
//! | `statement_timeout` | 5000 ms | 每连接 SET,限制慢查询(pgvector 检索)占用时长     |
//!
//! ## 生产多 Pod 建议
//!
//! 在多 Pod 部署场景下,建议在应用侧连接池之前放置 **pgbouncer(transaction 模式)**:
//! - 每 pod 的 `max_connections` 调低到 5–8(pgbouncer 复用连接)
//! - pgbouncer `pool_size` = pod 数 × per-pod max,控制总并发
//! - `statement_timeout` 可由 pgbouncer 侧统一设置,避免 after_connect 开销
//!
//! 这样即使某个 Pod 被慢查询打满,pgbouncer 层仍能保护 Postgres 不被耗尽。

use sqlx::postgres::{PgPool, PgPoolOptions};
use std::time::Duration;
use thiserror::Error;

/// rpg-db 统一错误类型。
#[derive(Debug, Error)]
pub enum DbError {
    #[error("sqlx error: {0}")]
    Sqlx(#[from] sqlx::Error),

    #[error("migration advisory lock timeout after {timeout_ms}ms")]
    LockTimeout { timeout_ms: u64 },

    #[error("migration error: {0}")]
    Migration(String),
}

// ─── 默认参数常量 ──────────────────────────────────────────────────────────

/// 池始终保持的最小热连接数。
const DEFAULT_MIN_CONNECTIONS: u32 = 2;
/// 空闲连接超时(秒);超时后回收。
const DEFAULT_IDLE_TIMEOUT_SECS: u64 = 600; // 10 min
/// 连接最大存活时间(秒);到期强制回收。
const DEFAULT_MAX_LIFETIME_SECS: u64 = 1800; // 30 min
/// 等待获取连接的超时(秒);超时则快速失败。
const DEFAULT_ACQUIRE_TIMEOUT_SECS: u64 = 5;
/// 每连接 SET statement_timeout 值(毫秒)。
const DEFAULT_STATEMENT_TIMEOUT_MS: u64 = 5_000;

// ─── PoolOpts ──────────────────────────────────────────────────────────────

/// `init_pool_with_opts` 的参数结构体。
///
/// 所有字段均为 `Option`;`None` 则使用模块级默认常量。
#[derive(Debug, Clone)]
pub struct PoolOpts {
    /// 最大连接数。
    pub max_connections: u32,
    /// 始终保持的最小热连接数(默认 `DEFAULT_MIN_CONNECTIONS`)。
    pub min_connections: Option<u32>,
    /// 获取连接超时(秒,默认 `DEFAULT_ACQUIRE_TIMEOUT_SECS`)。
    pub acquire_timeout_secs: Option<u64>,
    /// 空闲回收超时(秒,默认 `DEFAULT_IDLE_TIMEOUT_SECS`)。
    pub idle_timeout_secs: Option<u64>,
    /// 连接最大存活时间(秒,默认 `DEFAULT_MAX_LIFETIME_SECS`)。
    pub max_lifetime_secs: Option<u64>,
    /// `statement_timeout` 毫秒数(默认 `DEFAULT_STATEMENT_TIMEOUT_MS`)。
    /// 传 `Some(0)` 可禁用 statement_timeout。
    pub statement_timeout_ms: Option<u64>,
}

impl PoolOpts {
    /// 使用 `max_connections` 和其余全部默认值构造。
    pub fn with_max(max_connections: u32) -> Self {
        Self {
            max_connections,
            min_connections: None,
            acquire_timeout_secs: None,
            idle_timeout_secs: None,
            max_lifetime_secs: None,
            statement_timeout_ms: None,
        }
    }
}

// ─── 公开 API ──────────────────────────────────────────────────────────────

/// 构建一个 Postgres 连接池(向后兼容签名)。
///
/// 内部转调 [`init_pool_with_opts`],使用所有防雪崩默认参数。
///
/// 对应 Python: `ConnectionPool(conninfo=..., min_size=..., max_size=..., timeout=...)`
///
/// # 参数
/// - `database_url`: Postgres 连接串，例如 `postgresql://user:pass@host/db`
/// - `max_size`: 最大连接数（对应 Python `max_size`）
///
/// # 示例
/// ```ignore
/// let pool = init_pool("postgresql:///rpg_platform", 10).await?;
/// ```
pub async fn init_pool(database_url: &str, max_size: u32) -> Result<PgPool, DbError> {
    init_pool_with_opts(database_url, PoolOpts::with_max(max_size)).await
}

/// 构建一个可完整配置防雪崩参数的 Postgres 连接池。
///
/// 相比 [`init_pool`],此版本允许调用方按需覆盖各项超时与连接数。
/// `opts` 中所有 `None` 字段都会回落到模块级默认常量。
///
/// # 示例
/// ```ignore
/// let pool = init_pool_with_opts(
///     "postgresql:///rpg_platform",
///     PoolOpts {
///         max_connections: 10,
///         acquire_timeout_secs: Some(3),
///         statement_timeout_ms: Some(10_000),
///         ..PoolOpts::with_max(10)
///     },
/// ).await?;
/// ```
pub async fn init_pool_with_opts(
    database_url: &str,
    opts: PoolOpts,
) -> Result<PgPool, DbError> {
    let min_connections = opts.min_connections.unwrap_or(DEFAULT_MIN_CONNECTIONS);
    let acquire_timeout = Duration::from_secs(
        opts.acquire_timeout_secs.unwrap_or(DEFAULT_ACQUIRE_TIMEOUT_SECS),
    );
    let idle_timeout = Duration::from_secs(
        opts.idle_timeout_secs.unwrap_or(DEFAULT_IDLE_TIMEOUT_SECS),
    );
    let max_lifetime = Duration::from_secs(
        opts.max_lifetime_secs.unwrap_or(DEFAULT_MAX_LIFETIME_SECS),
    );
    let statement_timeout_ms = opts
        .statement_timeout_ms
        .unwrap_or(DEFAULT_STATEMENT_TIMEOUT_MS);

    let pool = PgPoolOptions::new()
        .max_connections(opts.max_connections)
        .min_connections(min_connections)
        .acquire_timeout(acquire_timeout)
        .idle_timeout(idle_timeout)
        .max_lifetime(max_lifetime)
        // 每个新连接建立后立即设置语句级超时,限制慢查询(pgvector 检索等)占用时长。
        // 使用 Box::pin + async move 满足 sqlx after_connect 的生命周期要求。
        .after_connect(move |conn, _meta| {
            Box::pin(async move {
                if statement_timeout_ms > 0 {
                    let sql =
                        format!("SET statement_timeout = '{statement_timeout_ms}ms'");
                    sqlx::Executor::execute(conn, sql.as_str()).await?;
                }
                Ok(())
            })
        })
        .connect(database_url)
        .await?;

    Ok(pool)
}
