use thiserror::Error;

/// 公共错误枚举,对应 Python core 层抛出的异常类型。
#[derive(Debug, Error)]
pub enum CoreError {
    #[error("config error: {0}")]
    Config(String),

    #[error("env var error: {0}")]
    Env(#[from] std::env::VarError),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("parse error: {0}")]
    Parse(String),

    #[error("security error: {0}")]
    Security(String),
}
