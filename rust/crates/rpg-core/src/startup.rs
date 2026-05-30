//! 对应 Python: rpg/core/startup.py
//!
//! 纯函数部分已翻译;FastAPI middleware/lifespan 不适用 Rust,留 TODO。

use std::env;

pub const API_VERSION: &str = "1";

/// 计算允许的 CORS origins 列表,对应 Python _cors_origins()。
pub fn cors_origins() -> (Vec<String>, bool) {
    let default_origins = concat!(
        "http://127.0.0.1:7860,http://localhost:7860,",
        "http://127.0.0.1:5173,http://localhost:5173,",
        "http://127.0.0.1:5174,http://localhost:5174,",
        "http://127.0.0.1:3000,http://localhost:3000"
    );
    let raw = env::var("RPG_CORS_ORIGINS").unwrap_or_else(|_| default_origins.to_string());
    let mut origins: Vec<String> = raw
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    if origins.is_empty() {
        origins = vec![
            "http://127.0.0.1:7860".to_string(),
            "http://localhost:7860".to_string(),
        ];
    }
    let allow_all = origins.contains(&"*".to_string());
    if allow_all {
        (vec!["*".to_string()], false)
    } else {
        (origins, true)
    }
}

/// 检查某个 origin 是否在允许列表中,对应 Python _origin_allowed()。
pub fn origin_allowed(origins: &[String], origin: Option<&str>) -> bool {
    match origin {
        None => true,
        Some(o) => origins.iter().any(|x| x == "*") || origins.iter().any(|x| x == o),
    }
}

/// 当前部署模式(strip + lower),对应 Python _deployment_mode()。
pub fn deployment_mode() -> String {
    env::var("RPG_DEPLOYMENT_MODE")
        .unwrap_or_else(|_| "local".to_string())
        .trim()
        .to_lowercase()
}

// TODO: lifespan (startup / shutdown) — 依赖 mcp_broker / tools_dsl / platform_app,
//       待各对应 Rust crate 就绪后翻译。
// TODO: configure_app (axum Router middleware) — 待 rpg-routes crate 中实现。
// TODO: api_contract_middleware — 待 rpg-routes crate 中实现。
// TODO: Exception handlers (_value_error_handler 等) — Axum 错误层待实现。
