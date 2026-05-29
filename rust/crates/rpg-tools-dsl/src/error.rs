//! DslError — crate 级错误类型

use thiserror::Error;

#[derive(Debug, Error)]
pub enum DslError {
    #[error("skill not found: {0}")]
    SkillNotFound(String),

    #[error("skill execution timed out after {0}s")]
    Timeout(u64),

    #[error("skill exited with code {0}")]
    NonZeroExit(i32),

    #[error("spawn failed: {0}")]
    SpawnError(String),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("permission denied: {0}")]
    PermissionDenied(String),

    #[error("{0}")]
    Other(String),
}

impl From<anyhow::Error> for DslError {
    fn from(e: anyhow::Error) -> Self {
        DslError::Other(e.to_string())
    }
}
