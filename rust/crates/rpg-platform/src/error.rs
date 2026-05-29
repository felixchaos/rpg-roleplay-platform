//! PlatformError —— platform_app 系统统一错误类型。
//!
//! 对应 Python 中四类抛出:
//! - `ValueError`        → `Validation`
//! - `RateLimited`       → `RateLimited`
//! - DB / psycopg 异常   → `Db`
//! - 任意其他 anyhow      → `Other`

use thiserror::Error;

/// Platform 错误。
#[derive(Debug, Error)]
pub enum PlatformError {
    #[error("validation: {0}")]
    Validation(String),

    #[error("not found: {0}")]
    NotFound(String),

    #[error("conflict: {0}")]
    Conflict(String),

    #[error("forbidden: {0}")]
    Forbidden(String),

    #[error("unauthorized")]
    Unauthorized,

    /// 登录被速率限制(对应 Python `RateLimited`)。
    #[error("rate limited; retry after {retry_after_sec}s (key={key})")]
    RateLimited { retry_after_sec: u64, key: String },

    #[error("db error: {0}")]
    Db(#[from] sqlx::Error),

    #[error("serde: {0}")]
    Serde(#[from] serde_json::Error),

    #[error("io: {0}")]
    Io(#[from] std::io::Error),

    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

impl PlatformError {
    pub fn validation(msg: impl Into<String>) -> Self {
        Self::Validation(msg.into())
    }
    pub fn not_found(msg: impl Into<String>) -> Self {
        Self::NotFound(msg.into())
    }
    pub fn conflict(msg: impl Into<String>) -> Self {
        Self::Conflict(msg.into())
    }
    pub fn forbidden(msg: impl Into<String>) -> Self {
        Self::Forbidden(msg.into())
    }
}

/// 便捷别名,所有 platform 函数统一返回。
pub type PlatformResult<T> = Result<T, PlatformError>;
