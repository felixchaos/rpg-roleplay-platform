//! 对应 Python: rpg/core/config.py
//!
//! 所有函数直接读环境变量,调用方在进程启动时先调 load_dotenv_once()。

use std::env;

/// 加载项目根 .env 文件(幂等)。
/// 对应 Python: load_dotenv_once()
pub fn load_dotenv_once() {
    // dotenvy::dotenv() 找不到文件时只返回 Err,不 panic。
    // 与 Python 的 try/except ImportError 同义:找不到就跳过。
    let _ = dotenvy::dotenv();
}

// ── 部署模式 / 鉴权 ──────────────────────────────────────────────────────

pub fn deployment_mode() -> String {
    env::var("RPG_DEPLOYMENT_MODE").unwrap_or_else(|_| "local".to_string())
}

pub fn require_auth() -> bool {
    env::var("RPG_REQUIRE_AUTH").unwrap_or_default() == "1"
}

/// 返回 RPG_REQUIRE_AUTH 原始字符串（含空字符串）。
pub fn require_auth_raw() -> String {
    env::var("RPG_REQUIRE_AUTH").unwrap_or_default()
}

pub fn debug_ui() -> bool {
    env::var("RPG_DEBUG_UI").map(|v| !v.is_empty()).unwrap_or(false)
}

// ── 网络 ─────────────────────────────────────────────────────────────────

pub fn cors_origins() -> Option<String> {
    env::var("RPG_CORS_ORIGINS").ok()
}

pub fn cors_origins_with_default(default: &str) -> String {
    env::var("RPG_CORS_ORIGINS").unwrap_or_else(|_| default.to_string())
}

pub fn cors_max_age() -> i64 {
    env::var("RPG_CORS_MAX_AGE")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(86400)
}

pub fn gzip_min_bytes() -> usize {
    env::var("RPG_GZIP_MIN_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1024)
}

pub fn trusted_proxies() -> Option<String> {
    env::var("RPG_TRUSTED_PROXIES").ok()
}

pub fn trusted_proxies_raw() -> String {
    env::var("RPG_TRUSTED_PROXIES").unwrap_or_default()
}

// ── Cookie ───────────────────────────────────────────────────────────────

pub fn cookie_secure() -> Option<String> {
    env::var("RPG_COOKIE_SECURE").ok()
}

pub fn cookie_samesite() -> String {
    env::var("RPG_COOKIE_SAMESITE").unwrap_or_else(|_| "lax".to_string())
}

// ── 安全 / 密钥 ──────────────────────────────────────────────────────────

pub fn master_key() -> Option<String> {
    env::var("RPG_MASTER_KEY").ok()
}

pub fn admin_password() -> Option<String> {
    env::var("RPG_ADMIN_PASSWORD").ok()
}

// ── 应用标题 ─────────────────────────────────────────────────────────────

pub fn app_title() -> String {
    env::var("RPG_APP_TITLE").unwrap_or_else(|_| "RPG Roleplay".to_string())
}

// ── 运行时 backend ───────────────────────────────────────────────────────

pub fn runtime_backend() -> String {
    env::var("RPG_RUNTIME_BACKEND").unwrap_or_else(|_| "auto".to_string())
}

// ── DB 连接池 ────────────────────────────────────────────────────────────

pub fn db_pool_min() -> u32 {
    env::var("RPG_DB_POOL_MIN")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1)
}

pub fn db_pool_max() -> u32 {
    env::var("RPG_DB_POOL_MAX")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(10)
}

pub fn db_pool_timeout() -> f64 {
    env::var("RPG_DB_POOL_TIMEOUT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8.0)
}

pub fn database_url_override() -> Option<String> {
    env::var("RPG_DATABASE_URL").ok()
}

// ── Migration ────────────────────────────────────────────────────────────

pub fn migration_lock_timeout_ms() -> u64 {
    env::var("RPG_MIGRATION_LOCK_TIMEOUT_MS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(30000)
}

pub fn skip_auto_migrate() -> bool {
    env::var("RPG_SKIP_AUTO_MIGRATE").unwrap_or_default() == "1"
}

// ── Auth / 速率限制 ──────────────────────────────────────────────────────

pub fn min_password_length() -> usize {
    env::var("RPG_MIN_PASSWORD_LENGTH")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8)
}

pub fn login_max_fails() -> u32 {
    env::var("RPG_LOGIN_MAX_FAILS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(5)
}

pub fn login_lockout_sec() -> u64 {
    env::var("RPG_LOGIN_LOCKOUT_SEC")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(60)
}

pub fn login_window_sec() -> u64 {
    env::var("RPG_LOGIN_WINDOW_SEC")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(300)
}

// ── 脚本上传 ─────────────────────────────────────────────────────────────

pub fn script_upload_max_bytes() -> usize {
    env::var("RPG_SCRIPT_UPLOAD_MAX_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(128 * 1024 * 1024)
}

pub fn upload_chunk_max_bytes() -> usize {
    env::var("RPG_UPLOAD_CHUNK_MAX_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8 * 1024 * 1024)
}

pub fn sync_stale_running_seconds() -> u64 {
    env::var("RPG_SYNC_STALE_RUNNING_SECONDS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1800)
}

pub fn sync_heartbeat_seconds() -> u64 {
    env::var("RPG_SYNC_HEARTBEAT_SECONDS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(60)
}

// ── 集群 ─────────────────────────────────────────────────────────────────

pub fn state_cache_ttl() -> u64 {
    env::var("RPG_STATE_CACHE_TTL")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(5)
}

// ── Tools DSL ────────────────────────────────────────────────────────────

pub fn enable_skill_import() -> Option<String> {
    env::var("RPG_ENABLE_SKILL_IMPORT").ok()
}

pub fn enable_mcp_config_write() -> Option<String> {
    env::var("RPG_ENABLE_MCP_CONFIG_WRITE").ok()
}

// ── Phase manager ────────────────────────────────────────────────────────

pub fn phase_turn_threshold() -> u32 {
    env::var("RPG_PHASE_TURN_THRESHOLD")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(30)
}

// ── 黑天鹅子代理 ─────────────────────────────────────────────────────────

/// 是否启用 BlackSwanAgent post-GM hook。默认关闭,需 RPG_ENABLE_BLACK_SWAN=1。
pub fn enable_black_swan() -> bool {
    env::var("RPG_ENABLE_BLACK_SWAN").unwrap_or_default() == "1"
}
