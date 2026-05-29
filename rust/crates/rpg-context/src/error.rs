//! 上下文管线错误类型。

use thiserror::Error;

#[derive(Debug, Error)]
pub enum ContextError {
    #[error("provider failed: {0}")]
    Provider(String),

    #[error("invalid manifest: {0}")]
    Manifest(String),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),

    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

pub type ContextResult<T> = std::result::Result<T, ContextError>;
