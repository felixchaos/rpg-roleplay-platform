//! pool.rs — 对应 rpg/platform_app/db/connection.py
//!
//! 核心函数：
//!   init_pool(database_url, max_size) → Result<PgPool, DbError>
//!
//! Python 原版用 psycopg_pool.ConnectionPool(min_size, max_size, timeout)；
//! 这里用 sqlx::postgres::PgPoolOptions 对等实现。

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

/// 构建一个 Postgres 连接池。
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
    let pool = PgPoolOptions::new()
        .max_connections(max_size)
        // 等价于 Python pool 的 timeout（默认 30 s）
        .acquire_timeout(Duration::from_secs(30))
        .connect(database_url)
        .await?;
    Ok(pool)
}
