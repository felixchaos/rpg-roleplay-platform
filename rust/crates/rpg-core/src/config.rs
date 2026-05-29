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

/// 单次上传(总)最大字节数。对应 Python `MAX_SCRIPT_UPLOAD_BYTES`/`script_upload_max_bytes`。
/// 默认 256 MiB(与 Python 端 core.config 默认一致)。
/// 覆盖: `RPG_SCRIPT_UPLOAD_MAX_BYTES=<bytes>`
pub fn script_upload_max_bytes() -> usize {
    env::var("RPG_SCRIPT_UPLOAD_MAX_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(256 * 1024 * 1024)
}

/// 单 chunk 最大字节数。对应 Python `MAX_UPLOAD_CHUNK_BYTES`。
/// 默认 8 MiB。
/// 覆盖: `RPG_UPLOAD_CHUNK_MAX_BYTES=<bytes>`
pub fn upload_chunk_max_bytes() -> usize {
    env::var("RPG_UPLOAD_CHUNK_MAX_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8 * 1024 * 1024)
}

/// 单次上传最多分片数。对应 Python `MAX_CHUNKS`。
/// 默认 4096。
/// 覆盖: `RPG_MAX_UPLOAD_CHUNKS=<n>`
pub fn max_upload_chunks() -> usize {
    env::var("RPG_MAX_UPLOAD_CHUNKS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(4096)
}

/// upload chunk 磁盘根目录。
/// 覆盖: `RPG_UPLOAD_CHUNK_DIR=<path>` ;默认 `platform_data/upload_chunks`。
pub fn upload_chunk_dir() -> String {
    env::var("RPG_UPLOAD_CHUNK_DIR")
        .unwrap_or_else(|_| "platform_data/upload_chunks".to_string())
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

// ── Extended Thinking / Reasoning 预算 ──────────────────────────────────
//
// Wave 10-A:统一抽象 LLM 的"思考预算"。Anthropic Claude 4+ 用 `thinking_budget`
// (token 数),Vertex Gemini 用 `thinkingBudget`(token 数),OpenAI o-系列 / Responses
// 用 `reasoning_effort`(枚举 low/medium/high)。
//
// 三个调用入口各自独立 env 默认,**默认全部为 0**(关闭),保持与 Python 端
// 一致的费用与延迟行为。运营按需通过 env 开启:
//   * `RPG_OPENING_THINKING_BUDGET=5000`   — GM 开场预算
//   * `RPG_CHAT_THINKING_BUDGET=2000`      — 主聊天回合预算
//   * `RPG_CONSOLE_THINKING_BUDGET=0`      — console_assistant(默认仍关)
//   * `ANTHROPIC_THINKING_BUDGET=3000`     — 全局兜底(若上面未设置)
//
// 这是一组"路由层 policy"配置:LLM crate 本身不读 env,只接收 `extra.thinking_budget`
// 由 caller 注入(rpg-routes 在构建 ChatRequest 时合并 extra,见 [`build_thinking_extra`])。

const ENV_OPENING_THINKING_BUDGET: &str = "RPG_OPENING_THINKING_BUDGET";
const ENV_CHAT_THINKING_BUDGET: &str = "RPG_CHAT_THINKING_BUDGET";
const ENV_CONSOLE_THINKING_BUDGET: &str = "RPG_CONSOLE_THINKING_BUDGET";
const ENV_GLOBAL_THINKING_BUDGET: &str = "ANTHROPIC_THINKING_BUDGET";

fn parse_budget(name: &str) -> Option<u32> {
    env::var(name).ok().and_then(|v| v.trim().parse::<u32>().ok())
}

fn parse_budget_with_fallback(primary: &str) -> u32 {
    parse_budget(primary)
        .or_else(|| parse_budget(ENV_GLOBAL_THINKING_BUDGET))
        .unwrap_or(0)
}

/// GM 开场剧情生成时使用的 extended thinking 预算(token 数)。
///
/// 默认 0(禁用);`RPG_OPENING_THINKING_BUDGET` 优先,缺失则回落 `ANTHROPIC_THINKING_BUDGET`。
pub fn opening_thinking_budget() -> u32 {
    parse_budget_with_fallback(ENV_OPENING_THINKING_BUDGET)
}

/// 主聊天回合(/api/chat)使用的 extended thinking 预算(token 数)。
pub fn chat_thinking_budget() -> u32 {
    parse_budget_with_fallback(ENV_CHAT_THINKING_BUDGET)
}

/// console_assistant 调试通道使用的 extended thinking 预算(token 数)。
///
/// 默认 0:控制台一般是工具/导航类任务,thinking 额外成本不值得。
pub fn console_thinking_budget() -> u32 {
    parse_budget_with_fallback(ENV_CONSOLE_THINKING_BUDGET)
}

// ── Settings struct ───────────────────────────────────────────────────────

/// 上传子系统运行时设置。
/// 由 `Settings::from_env()` 在进程启动时一次性读取,随后以 `Arc<Settings>` 注入
/// AppState。各处不再调用裸 `std::env::var`,而是直接读字段。
///
/// 字段命名与 Python `core.config` 中对应配置项保持一致。
#[derive(Debug, Clone)]
pub struct Settings {
    /// 单次上传(总)最大字节数。对应 `RPG_SCRIPT_UPLOAD_MAX_BYTES`。
    pub max_script_upload_bytes: usize,
    /// 单 chunk 最大字节数。对应 `RPG_UPLOAD_CHUNK_MAX_BYTES`。
    pub max_upload_chunk_bytes: usize,
    /// 单次上传最多分片数。对应 `RPG_MAX_UPLOAD_CHUNKS`。
    pub max_chunks: usize,
    /// upload chunk 磁盘根目录。对应 `RPG_UPLOAD_CHUNK_DIR`。
    pub upload_chunk_dir: String,
    /// KMS provider 类型字符串(透传 Wave 8-A KEY_PROVIDER env)。
    /// 仅存入 Settings 以便 AppState 集中分发;实际解析由 rpg-platform key_provider 负责。
    pub kms_provider: Option<String>,
}

impl Settings {
    /// 从当前进程环境变量构造 Settings。
    /// 应在 `load_dotenv_once()` 之后调用。
    pub fn from_env() -> Self {
        Settings {
            max_script_upload_bytes: script_upload_max_bytes(),
            max_upload_chunk_bytes: upload_chunk_max_bytes(),
            max_chunks: max_upload_chunks(),
            upload_chunk_dir: upload_chunk_dir(),
            kms_provider: env::var("KEY_PROVIDER").ok(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 串行保护:Settings::from_env 读的是 live env var,并行测试修改同一变量会 race。
    static SETTINGS_ENV_MUTEX: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn test_settings_defaults() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        // 清掉可能残留的覆盖
        std::env::remove_var("RPG_SCRIPT_UPLOAD_MAX_BYTES");
        std::env::remove_var("RPG_UPLOAD_CHUNK_MAX_BYTES");
        std::env::remove_var("RPG_MAX_UPLOAD_CHUNKS");
        std::env::remove_var("RPG_UPLOAD_CHUNK_DIR");
        std::env::remove_var("KEY_PROVIDER");

        let s = Settings::from_env();
        assert_eq!(s.max_script_upload_bytes, 256 * 1024 * 1024);
        assert_eq!(s.max_upload_chunk_bytes, 8 * 1024 * 1024);
        assert_eq!(s.max_chunks, 4096);
        assert_eq!(s.upload_chunk_dir, "platform_data/upload_chunks");
        assert!(s.kms_provider.is_none());
    }

    #[test]
    fn test_settings_from_env_overrides() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("RPG_SCRIPT_UPLOAD_MAX_BYTES", "1048576");
        std::env::set_var("RPG_UPLOAD_CHUNK_MAX_BYTES", "131072");
        std::env::set_var("RPG_MAX_UPLOAD_CHUNKS", "128");
        std::env::set_var("RPG_UPLOAD_CHUNK_DIR", "/tmp/chunks");
        std::env::set_var("KEY_PROVIDER", "gcp");

        let s = Settings::from_env();
        assert_eq!(s.max_script_upload_bytes, 1_048_576);
        assert_eq!(s.max_upload_chunk_bytes, 131_072);
        assert_eq!(s.max_chunks, 128);
        assert_eq!(s.upload_chunk_dir, "/tmp/chunks");
        assert_eq!(s.kms_provider.as_deref(), Some("gcp"));

        // 清理
        std::env::remove_var("RPG_SCRIPT_UPLOAD_MAX_BYTES");
        std::env::remove_var("RPG_UPLOAD_CHUNK_MAX_BYTES");
        std::env::remove_var("RPG_MAX_UPLOAD_CHUNKS");
        std::env::remove_var("RPG_UPLOAD_CHUNK_DIR");
        std::env::remove_var("KEY_PROVIDER");
    }

    #[test]
    fn test_settings_invalid_env_falls_back_to_default() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("RPG_SCRIPT_UPLOAD_MAX_BYTES", "not_a_number");
        std::env::set_var("RPG_MAX_UPLOAD_CHUNKS", "xyz");

        let s = Settings::from_env();
        // 无法解析 → 退回默认
        assert_eq!(s.max_script_upload_bytes, 256 * 1024 * 1024);
        assert_eq!(s.max_chunks, 4096);

        std::env::remove_var("RPG_SCRIPT_UPLOAD_MAX_BYTES");
        std::env::remove_var("RPG_MAX_UPLOAD_CHUNKS");
    }

    #[test]
    fn test_upload_chunk_dir_fn_default() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var("RPG_UPLOAD_CHUNK_DIR");
        assert_eq!(upload_chunk_dir(), "platform_data/upload_chunks");
    }

    #[test]
    fn test_upload_chunk_dir_fn_override() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("RPG_UPLOAD_CHUNK_DIR", "/data/uploads");
        assert_eq!(upload_chunk_dir(), "/data/uploads");
        std::env::remove_var("RPG_UPLOAD_CHUNK_DIR");
    }

    #[test]
    fn test_max_upload_chunks_default() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var("RPG_MAX_UPLOAD_CHUNKS");
        assert_eq!(max_upload_chunks(), 4096);
    }

    // Wave 10-A:thinking 预算解析 ────────────────────────────────────────

    #[test]
    fn test_thinking_budget_defaults_zero() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var(ENV_OPENING_THINKING_BUDGET);
        std::env::remove_var(ENV_CHAT_THINKING_BUDGET);
        std::env::remove_var(ENV_CONSOLE_THINKING_BUDGET);
        std::env::remove_var(ENV_GLOBAL_THINKING_BUDGET);
        assert_eq!(opening_thinking_budget(), 0);
        assert_eq!(chat_thinking_budget(), 0);
        assert_eq!(console_thinking_budget(), 0);
    }

    #[test]
    fn test_thinking_budget_primary_wins_over_global() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var(ENV_OPENING_THINKING_BUDGET, "7000");
        std::env::set_var(ENV_GLOBAL_THINKING_BUDGET, "1000");
        assert_eq!(opening_thinking_budget(), 7000);
        std::env::remove_var(ENV_OPENING_THINKING_BUDGET);
        std::env::remove_var(ENV_GLOBAL_THINKING_BUDGET);
    }

    #[test]
    fn test_thinking_budget_global_fallback() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var(ENV_CHAT_THINKING_BUDGET);
        std::env::set_var(ENV_GLOBAL_THINKING_BUDGET, "2500");
        assert_eq!(chat_thinking_budget(), 2500);
        std::env::remove_var(ENV_GLOBAL_THINKING_BUDGET);
    }

    #[test]
    fn test_thinking_budget_invalid_falls_back_to_zero() {
        let _g = SETTINGS_ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var(ENV_CONSOLE_THINKING_BUDGET, "not-a-num");
        std::env::remove_var(ENV_GLOBAL_THINKING_BUDGET);
        assert_eq!(console_thinking_budget(), 0);
        std::env::remove_var(ENV_CONSOLE_THINKING_BUDGET);
    }
}
