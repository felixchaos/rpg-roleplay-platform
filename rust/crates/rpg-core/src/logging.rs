//! 对应 Python: rpg/core/logging.py
//!
//! get_logger() → tracing 宏 + 模块路径 span
//! setup_default_logging() → tracing_subscriber 初始化

use tracing_subscriber::{fmt, EnvFilter};

/// 初始化默认 tracing subscriber (basicConfig 等价)。
/// 一般在 main() 最早处调用一次。
/// 对应 Python: setup_default_logging()
pub fn setup_default_logging() {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info"));

    fmt::Subscriber::builder()
        .with_env_filter(filter)
        .with_target(true)
        .with_timer(fmt::time::SystemTime)
        // 对应 Python format: "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        .compact()
        .try_init()
        .ok(); // 幂等:已初始化时忽略 SetLoggerError
}

/// 返回一个命名的 tracing span,对应 Python logging.getLogger(name)。
///
/// 用法:
/// ```ignore
/// let _guard = get_logger("rpg::mymodule").entered();
/// tracing::info!("hello");
/// ```
pub fn get_logger(name: &'static str) -> tracing::Span {
    tracing::info_span!("logger", name = name)
}
