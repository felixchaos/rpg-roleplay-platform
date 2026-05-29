//! rpg-routes — Axum 路由层
//! 对应 Python: rpg/routes/ 14 文件
//!
//! ## W4-1 协议对齐(2026-05-29)
//! 前端 `lib/api.ts` 走 `/api/v1/*` 并期望 `{detail, code, ok:false}` 错误体、
//! 命名 SSE 事件(`hello` / `state_change` / `chunk` / `done` / `error`)。
//! 这层做了:
//!   1. `build_routes()` 同时挂 `/api/*`(旧)与 `/api/v1/*`(新),旧路径保留兼容。
//!   2. `ResponseError` 输出 `{detail, code, ok:false}`,并定义 [`ApiError`] 同义结构。
//!   3. 提供 [`sse::named_event`] helper,所有 SSE handler 用 `.json_data` 输出 JSON。
//!   4. 新增 `uploads` 模块 — base64 分片上传(`/api/uploads/chunk`、`/api/uploads/finalize`)。

pub mod admin;
pub mod auth;
pub mod branches;
pub mod console_assistant;
pub mod core;
pub mod db_metrics;
pub mod game;
pub mod imports;
pub mod library;
pub mod me;
pub mod metrics;
pub mod platform;
pub mod saves;
pub mod scripts;
pub mod settings;
pub mod sse_events;
pub mod sse_metrics;
pub mod mcp;
pub mod memory;
pub mod models;
pub mod permissions;
pub mod rules;
pub mod skills;
pub mod timeline;
pub mod uploads;
pub mod worldline;
pub mod ws;

use std::ops::Deref;
use std::sync::Arc;

use axum::{
    extract::Request,
    middleware::{self, Next},
    response::{sse::Event as SseEvent, IntoResponse, Response},
    Json, Router,
};
use dashmap::DashMap;
use http::{HeaderMap, StatusCode, Uri};
use parking_lot::RwLock;
use serde::Serialize;
use serde_json::json;
use tokio::sync::Notify;
use tokio_util::sync::CancellationToken;
use tokio_util::task::TaskTracker;

use rpg_agents::gm::GameMaster;
use rpg_core::UserId;
use rpg_llm::LlmRouter;
use rpg_platform::auth::User;
use rpg_state::StateStore;
use rpg_tools_dsl::{McpBroker, ToolRegistry};

// ── AppState ─────────────────────────────────────────────────────────────────
//
// 单一权威 AppState(6B-1 合一)。此前 server / routes 各持一份、build_router 里
// 逐字段 9×Arc clone 重建 routes 版,极易漂移(W3/W4 多次因字段不同步编译失败)。
// 现统一为 `AppState(Arc<AppStateInner>)`:
//   - 所有句柄收进 [`AppStateInner`],只在 main 装配一次;
//   - axum `State<AppState>` extractor 每次 clone 仅 inc 1 次外层 Arc 引用计数
//     (而非旧版逐字段 clone 9 个内部 Arc);
//   - `Deref<Target = AppStateInner>` 让 handler 里 `s.db` / `s.state_store` 等写法不变。

/// 进程级共享句柄容器。包成 `Arc<AppStateInner>`(见 [`AppState`]),
/// 内部可变结构(`llm_router` / `tool_registry`)用 parking_lot::RwLock,
/// 跨 await 不持锁:大部分调用拿到 read snapshot 后立即释放。
pub struct AppStateInner {
    pub db: sqlx::PgPool,
    pub state_store: Arc<StateStore>,
    /// 按 user_id 分片的 GameMaster 池(从 server 并入)。
    /// 对应 Python `_gm_by_user` + `_sub_gm_by_user`。
    /// key 用强类型 [`UserId`]:此池只服务已登录用户(GM 必有 DB user)。
    pub gm_pool: DashMap<UserId, Arc<RwLock<GameMaster>>>,
    pub llm_router: Arc<RwLock<LlmRouter>>,
    pub tool_registry: Arc<RwLock<ToolRegistry>>,
    /// MCP broker — 管理子进程 MCP server + 工具调用。
    pub mcp_broker: Arc<McpBroker>,
    /// 每个 user 一个 Notify,用于 /api/stop 打断当前 chat。
    pub stop_events: DashMap<String, Arc<Notify>>,
    /// 按 user_id 分片的 run_id 计数器(从 server 并入)。对应 Python `_run_id_by_user`。
    /// key 用强类型 [`UserId`](已登录用户)。
    pub run_ids: DashMap<UserId, u64>,
    /// 控制台助手对话(简版:全内存)。Vec<(role, text)>。
    pub console_conversations: DashMap<String, Vec<ConsoleMessage>>,
    /// 分片上传缓存:`upload_id → ChunkUploadState`。由 `uploads.rs` 写入。
    pub chunk_uploads: DashMap<String, ChunkUploadState>,
    /// 进程级配置快照(env 变量已 freeze,从 server 并入)。
    pub config: Arc<AppConfig>,
    /// 用于通知所有 spawned task 退出的取消令牌(从 server 并入)。
    pub shutdown_token: CancellationToken,
    /// 追踪所有 spawned task,graceful shutdown 时等待全部完成(从 server 并入)。
    pub task_tracker: TaskTracker,
}

/// 单一权威 AppState,`Arc<AppStateInner>` 的 newtype。
///
/// `#[derive(Clone)]` 只 clone 外层 `Arc`(1 次 refcount inc),不再逐字段 clone。
#[derive(Clone)]
pub struct AppState(pub Arc<AppStateInner>);

impl Deref for AppState {
    type Target = AppStateInner;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

/// 启动期 freeze 的配置,避免 handler 反复 `env::var`。
///
/// 6B-1:从 rpg-server 移到 routes,使单一 AppState 能直接持有它
/// (server 依赖 routes,无循环依赖)。
#[derive(Debug, Clone)]
pub struct AppConfig {
    pub app_title: String,
    pub deployment_mode: String,
    pub require_auth: bool,
    pub cors_origins: Vec<String>,
    pub cors_allow_credentials: bool,
    pub cors_max_age: i64,
    pub gzip_min_bytes: usize,
    pub host: String,
    pub port: u16,
}

/// 单次分片上传的累积状态(全内存,翻译期实现)。
#[derive(Debug, Default)]
pub struct ChunkUploadState {
    pub total_chunks: u32,
    pub file_name: Option<String>,
    pub name: Option<String>,
    pub kind: Option<String>,
    /// 已收到的 chunk(`idx → bytes`)。
    pub received: Vec<(u32, Vec<u8>)>,
}

impl AppState {
    /// 默认构造,只需 pool。其它字段全用空骨架。
    ///
    /// `config` 用 [`AppConfig::default`] 占位;真实进程由 main 用 `from_env` 装配
    /// 后通过 [`AppState::from_inner`] / 直接构造 [`AppStateInner`] 传入。
    pub fn new(db: sqlx::PgPool) -> Self {
        Self(Arc::new(AppStateInner {
            db,
            state_store: Arc::new(StateStore::new()),
            gm_pool: DashMap::new(),
            llm_router: Arc::new(RwLock::new(LlmRouter::new())),
            tool_registry: Arc::new(RwLock::new(ToolRegistry::new())),
            mcp_broker: Arc::new(McpBroker::default()),
            stop_events: DashMap::new(),
            run_ids: DashMap::new(),
            console_conversations: DashMap::new(),
            chunk_uploads: DashMap::new(),
            config: Arc::new(AppConfig::default()),
            shutdown_token: CancellationToken::new(),
            task_tracker: TaskTracker::new(),
        }))
    }

    /// 从已装配好的 [`AppStateInner`] 构造。main 用它把所有句柄一次性收口。
    pub fn from_inner(inner: AppStateInner) -> Self {
        Self(Arc::new(inner))
    }

    /// 取/建一个 user 的 stop Notify。
    pub fn stop_notify(&self, user_id: &str) -> Arc<Notify> {
        self.stop_events
            .entry(user_id.to_string())
            .or_insert_with(|| Arc::new(Notify::new()))
            .clone()
    }

    /// 为某 user 分配下一个 run_id(自增计数器,从 1 开始)。
    ///
    /// 6C-1:run_id 用于跨 pod stop —— `/api/stop` 据此往 `stop_signals` 写信号,
    /// chat 循环按 (user_id, run_id) 轮询 `cluster::is_stop_requested`。
    /// 返回 `i64`(`cluster` 用 i64 user_id/run_id;此处 u64 计数器转 i64)。
    pub fn next_run_id(&self, user_id: UserId) -> i64 {
        let mut entry = self.run_ids.entry(user_id).or_insert(0);
        *entry += 1;
        *entry as i64
    }

    /// 读取某 user 当前 run_id(无进行中 run 时返回 0)。
    pub fn current_run_id(&self, user_id: UserId) -> i64 {
        self.run_ids.get(&user_id).map(|r| *r as i64).unwrap_or(0)
    }
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            app_title: String::new(),
            deployment_mode: String::new(),
            require_auth: true,
            cors_origins: Vec::new(),
            cors_allow_credentials: false,
            cors_max_age: 600,
            gzip_min_bytes: 0,
            host: "0.0.0.0".to_string(),
            port: 7860,
        }
    }
}

#[derive(Clone, Debug)]
pub struct ConsoleMessage {
    pub role: String,
    pub text: String,
    pub at: chrono::DateTime<chrono::Utc>,
}

// ── 错误类型 ─────────────────────────────────────────────────────────────────

/// 标准错误 code 表,与前端 `lib/api.ts` ErrorCode 对齐。
pub mod error_codes {
    pub const BAD_REQUEST: &str = "bad_request";
    pub const UNAUTHORIZED: &str = "unauthorized";
    pub const FORBIDDEN: &str = "forbidden";
    pub const NOT_FOUND: &str = "not_found";
    pub const CONFLICT: &str = "conflict";
    pub const NOT_IMPLEMENTED: &str = "not_implemented";
    pub const INTERNAL_ERROR: &str = "internal_error";
}

/// 统一返回的 Response 错误,JSON 体格式 `{ok:false, detail, code}`。
///
/// 与前端 `lib/api.ts` 约定一致:错误 payload 含人读 `detail` + 机器 `code`。
#[derive(Debug)]
pub struct ResponseError {
    pub status: StatusCode,
    pub message: String,
    pub code: &'static str,
}

impl ResponseError {
    pub fn new(status: StatusCode, msg: impl Into<String>) -> Self {
        let code = match status {
            StatusCode::BAD_REQUEST => error_codes::BAD_REQUEST,
            StatusCode::UNAUTHORIZED => error_codes::UNAUTHORIZED,
            StatusCode::FORBIDDEN => error_codes::FORBIDDEN,
            StatusCode::NOT_FOUND => error_codes::NOT_FOUND,
            StatusCode::CONFLICT => error_codes::CONFLICT,
            StatusCode::NOT_IMPLEMENTED => error_codes::NOT_IMPLEMENTED,
            _ => error_codes::INTERNAL_ERROR,
        };
        Self {
            status,
            message: msg.into(),
            code,
        }
    }

    /// 显式覆盖 code(默认按 status 推断)。
    pub fn with_code(mut self, code: &'static str) -> Self {
        self.code = code;
        self
    }

    pub fn unauthorized(msg: impl Into<String>) -> Self {
        Self::new(StatusCode::UNAUTHORIZED, msg)
    }

    pub fn forbidden(msg: impl Into<String>) -> Self {
        Self::new(StatusCode::FORBIDDEN, msg)
    }

    pub fn bad_request(msg: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, msg)
    }

    pub fn not_found(msg: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, msg)
    }

    pub fn conflict(msg: impl Into<String>) -> Self {
        Self::new(StatusCode::CONFLICT, msg)
    }

    pub fn internal(msg: impl Into<String>) -> Self {
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, msg)
    }

    pub fn not_implemented(msg: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_IMPLEMENTED, msg)
    }
}

impl IntoResponse for ResponseError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(json!({
                "ok": false,
                "detail": self.message,
                "code": self.code,
            })),
        )
            .into_response()
    }
}

/// `ApiError` — 错误协议结构体,前端 fetch 失败时反序列化它。
///
/// 主要供文档 / 测试用;实际响应由 [`ResponseError::into_response`] 直接渲染。
#[derive(Debug, Clone, Serialize)]
pub struct ApiError {
    pub detail: String,
    pub code: String,
    pub ok: bool,
}

impl ApiError {
    pub fn new(detail: impl Into<String>, code: impl Into<String>) -> Self {
        Self {
            detail: detail.into(),
            code: code.into(),
            ok: false,
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = match self.code.as_str() {
            error_codes::BAD_REQUEST => StatusCode::BAD_REQUEST,
            error_codes::UNAUTHORIZED => StatusCode::UNAUTHORIZED,
            error_codes::FORBIDDEN => StatusCode::FORBIDDEN,
            error_codes::NOT_FOUND => StatusCode::NOT_FOUND,
            error_codes::CONFLICT => StatusCode::CONFLICT,
            error_codes::NOT_IMPLEMENTED => StatusCode::NOT_IMPLEMENTED,
            _ => StatusCode::INTERNAL_SERVER_ERROR,
        };
        (status, Json(self)).into_response()
    }
}

impl From<rpg_platform::PlatformError> for ResponseError {
    fn from(err: rpg_platform::PlatformError) -> Self {
        // PlatformError 没有公开变体,统一映射为 500/400(透传 message)。
        Self::internal(err.to_string())
    }
}

impl From<sqlx::Error> for ResponseError {
    fn from(err: sqlx::Error) -> Self {
        Self::internal(format!("db: {err}"))
    }
}

impl From<rpg_state::ops::OpError> for ResponseError {
    fn from(err: rpg_state::ops::OpError) -> Self {
        Self::bad_request(err.to_string())
    }
}

impl From<rpg_state::StateError> for ResponseError {
    fn from(err: rpg_state::StateError) -> Self {
        Self::bad_request(err.to_string())
    }
}

impl From<anyhow::Error> for ResponseError {
    fn from(err: anyhow::Error) -> Self {
        Self::internal(err.to_string())
    }
}

// ── 鉴权 middleware ──────────────────────────────────────────────────────────

/// 与 Python `_deps.py:SESSION_COOKIE` 对齐:前端 / Python 后端 / Rust 后端必须用同一名。
pub const SESSION_COOKIE: &str = "rpg_session";

/// 从 cookie / Authorization header 提 token。
pub fn token_from_headers(headers: &HeaderMap) -> Option<String> {
    if let Some(auth) = headers.get(http::header::AUTHORIZATION) {
        if let Ok(s) = auth.to_str() {
            if let Some(rest) = s.strip_prefix("Bearer ") {
                return Some(rest.trim().to_string());
            }
        }
    }
    if let Some(cookie) = headers.get(http::header::COOKIE) {
        if let Ok(s) = cookie.to_str() {
            let needle = format!("{SESSION_COOKIE}=");
            for part in s.split(';') {
                let p = part.trim();
                if let Some(v) = p.strip_prefix(&needle) {
                    return Some(v.trim().to_string());
                }
            }
        }
    }
    None
}

/// 取 user。失败 → 401。
///
/// 注:Python 端 `require_auth()` 关时允许匿名;此处保持简化语义,
/// 没 token 就 401,由上层选择性跳过(本翻译期非 SSO 路径)。
pub async fn require_user(state: &AppState, headers: &HeaderMap) -> Result<User, ResponseError> {
    let token = token_from_headers(headers);
    let user_opt = rpg_platform::auth::user_from_token(&state.db, token.as_deref()).await?;
    user_opt.ok_or_else(|| ResponseError::unauthorized("未登录"))
}

/// 取 user_id 字符串,匿名也允许(用 "anonymous" 兜底)。
/// 用于 state_store 索引,避免登录强约束。
pub async fn user_id_or_anon(state: &AppState, headers: &HeaderMap) -> String {
    let token = token_from_headers(headers);
    match rpg_platform::auth::user_from_token(&state.db, token.as_deref()).await {
        Ok(Some(u)) => u.id.to_string(),
        _ => "anonymous".to_string(),
    }
}

// ── Cookie helpers ──────────────────────────────────────────────────────────
//
// 对齐 Python `_deps.py:_set_session_cookie / _delete_session_cookie`。
//   - SameSite 从 env `RPG_COOKIE_SAMESITE` 读,默认 "lax"。
//   - Secure 从 env `RPG_COOKIE_SECURE` 读(1/0),未设按 scheme 推断。
//   - SameSite=None 时强制 Secure=true(Chrome 100+ 拒绝 SameSite=None;!Secure)。
//   - 删 cookie 必须用同一组 attrs,否则浏览器视作另一条 cookie 残留。

fn cookie_samesite_env() -> String {
    std::env::var("RPG_COOKIE_SAMESITE")
        .unwrap_or_else(|_| "lax".to_string())
        .trim()
        .to_lowercase()
}

fn cookie_secure_env(scheme_is_https: bool) -> bool {
    match std::env::var("RPG_COOKIE_SECURE")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
    {
        Some(v) if v == "1" => true,
        Some(v) if v == "0" => false,
        _ => scheme_is_https,
    }
}

/// 构造 `Set-Cookie` 头值,带 max_age 秒。
///
/// 用于登录/注册成功后写 `rpg_session`。`scheme_is_https` 来自请求 URI scheme,
/// 或调用方根据 `x-forwarded-proto` 判定;开发环境通常为 false。
pub fn build_session_set_cookie(token: &str, max_age_secs: i64, scheme_is_https: bool) -> String {
    let samesite = cookie_samesite_env();
    let mut secure = cookie_secure_env(scheme_is_https);
    if samesite == "none" {
        secure = true; // 浏览器规范硬要求
    }
    let mut parts = vec![
        format!("{SESSION_COOKIE}={token}"),
        "Path=/".to_string(),
        "HttpOnly".to_string(),
        format!(
            "SameSite={}",
            match samesite.as_str() {
                "none" => "None",
                "strict" => "Strict",
                _ => "Lax",
            }
        ),
        format!("Max-Age={max_age_secs}"),
    ];
    if secure {
        parts.push("Secure".to_string());
    }
    parts.join("; ")
}

/// 构造删除 cookie 的 `Set-Cookie` 头值。必须与 set 用同一组 SameSite / Secure。
pub fn build_session_delete_cookie(scheme_is_https: bool) -> String {
    let samesite = cookie_samesite_env();
    let mut secure = cookie_secure_env(scheme_is_https);
    if samesite == "none" {
        secure = true;
    }
    let mut parts = vec![
        format!("{SESSION_COOKIE}="),
        "Path=/".to_string(),
        "HttpOnly".to_string(),
        format!(
            "SameSite={}",
            match samesite.as_str() {
                "none" => "None",
                "strict" => "Strict",
                _ => "Lax",
            }
        ),
        "Max-Age=0".to_string(),
    ];
    if secure {
        parts.push("Secure".to_string());
    }
    parts.join("; ")
}

/// 判定请求来自 https(用于 cookie Secure 推断)。
/// 优先看 X-Forwarded-Proto(反代场景),再看 URI scheme。
pub fn request_is_https(headers: &HeaderMap, uri: &http::Uri) -> bool {
    if let Some(v) = headers.get("x-forwarded-proto") {
        if let Ok(s) = v.to_str() {
            return s.eq_ignore_ascii_case("https");
        }
    }
    uri.scheme_str().map(|s| s == "https").unwrap_or(false)
}

// ── SSE helpers ──────────────────────────────────────────────────────────────

/// 统一构造命名 SSE event(自动 `.json_data`,失败回退到 string)。
///
/// 与前端约定:首条必发 `hello`,后续 `state_change` / `chunk` / `done` / `error`。
/// 任何接 SSE 的 handler 都应通过本 helper 写事件,保证 JSON 编码与命名一致。
pub fn named_sse_event(name: &str, payload: serde_json::Value) -> SseEvent {
    match SseEvent::default().event(name).json_data(payload.clone()) {
        Ok(e) => e,
        Err(_) => SseEvent::default().event(name).data(payload.to_string()),
    }
}

/// `hello` 事件的常规 payload(`user_id + ts`)。
pub fn hello_payload(user_id: &str) -> serde_json::Value {
    json!({
        "user_id": user_id,
        "ts": chrono::Utc::now().timestamp(),
        "protocol": "v1",
    })
}

// ── Router 构造 ──────────────────────────────────────────────────────────────

/// 构造完整 API Router。
///
/// 同时挂在 `/api/*`(旧版兼容)与 `/api/v1/*`(前端 `lib/api.ts` 主路径)。
/// 实现:axum middleware 在请求进入前把 `/api/v1/...` 改写成 `/api/...`,
/// 然后落到同一份内部 router。这样无需复制路由表 + handlers 完全共享。
pub fn build_routes() -> Router<AppState> {
    let inner = api_router();
    Router::new()
        .merge(inner)
        .layer(middleware::from_fn(rewrite_v1_prefix))
}

/// SSE 长连接路由(豁免 timeout + governor)。
///
/// 包括所有会产生 Server-Sent Events 的端点:
/// - `/api/chat` — 主聊天 SSE
/// - `/api/opening` — 开场白 SSE
/// - `/api/state_events` — 状态变更推送
/// - `/api/console_assistant/chat` — 控制台助手 SSE
/// - `/api/console_assistant/confirm` — 确认操作 SSE
///
/// server 侧对这些路由不挂 TimeoutLayer 和 GovernorLayer,
/// 避免长连接被超时切断 / 被流量计数器误判。
pub fn build_sse_routes() -> Router<AppState> {
    use axum::routing::{get, post};
    // 直接引用同 crate 内 pub(crate) handler(不走 pub re-export)。
    Router::new()
        .route("/api/chat", post(game::api_chat))
        .route("/api/opening", post(game::api_opening))
        .route("/api/state_events", get(core::api_state_events))
        .route(
            "/api/console_assistant/chat",
            post(console_assistant::api_console_assistant_chat),
        )
        .route(
            "/api/console_assistant/confirm",
            post(console_assistant::api_console_assistant_confirm),
        )
        // Wave 10-B:`/api/ws` 是 WebSocket 长连接,也走豁免 timeout 的 SSE 路由组。
        .merge(ws::router())
        .layer(middleware::from_fn(rewrite_v1_prefix))
}

/// 上传路由(需要放宽 body limit,其余中间件与普通路由一致)。
///
/// `/api/uploads/*` — base64 分片上传。server 侧对此组路由替换成更大的 body limit。
pub fn build_upload_routes() -> Router<AppState> {
    Router::new()
        .merge(uploads::router())
        .layer(middleware::from_fn(rewrite_v1_prefix))
}

/// 普通业务路由(全套中间件:governor + timeout + 全局 body limit)。
///
/// 排除 SSE 路由和上传路由。
pub fn build_regular_routes() -> Router<AppState> {
    let inner = regular_api_router();
    Router::new()
        .merge(inner)
        .layer(middleware::from_fn(rewrite_v1_prefix))
}

/// middleware:把 `/api/v1/...` 路径改写为 `/api/...`,其它路径直通。
///
/// 兼容前端 `lib/api.ts` 走 `/api/v1/*` 调用,而后端 handler 注册的是 `/api/*`。
async fn rewrite_v1_prefix(mut req: Request, next: Next) -> Response {
    const V1_PREFIX: &str = "/api/v1/";
    const V1_EXACT: &str = "/api/v1";
    let path = req.uri().path();
    let new_path: Option<String> = if let Some(rest) = path.strip_prefix(V1_PREFIX) {
        Some(format!("/api/{rest}"))
    } else if path == V1_EXACT {
        Some("/api".to_string())
    } else {
        None
    };
    if let Some(new_path) = new_path {
        let query = req.uri().query().map(|q| format!("?{q}")).unwrap_or_default();
        let new_uri_str = format!("{new_path}{query}");
        if let Ok(new_uri) = new_uri_str.parse::<Uri>() {
            *req.uri_mut() = new_uri;
        }
    }
    next.run(req).await
}

fn api_router() -> Router<AppState> {
    Router::new()
        .merge(core::router())
        .merge(game::router())
        .merge(memory::router())
        .merge(metrics::router())
        .merge(permissions::router())
        .merge(rules::router())
        .merge(skills::router())
        .merge(timeline::router())
        .merge(worldline::router())
        .merge(mcp::router())
        .merge(models::router())
        .merge(console_assistant::router())
        .merge(uploads::router())
        .merge(ws::router())
        // Wave 12 新增 9 个 HTTP handler 域:
        .merge(admin::router())
        .merge(auth::router())
        .merge(branches::router())
        .merge(imports::router())
        .merge(library::router())
        .merge(me::router())
        .merge(platform::router())
        .merge(saves::router())
        .merge(scripts::router())
        .merge(settings::router())
}

/// 普通业务路由(去掉 SSE 路由 + 上传路由)。
fn regular_api_router() -> Router<AppState> {
    Router::new()
        // core.rs 中的非 SSE 路由:/ 和 /api/state
        .merge(core::regular_router())
        // game.rs 中的非 SSE 路由
        .merge(game::regular_router())
        .merge(memory::router())
        .merge(metrics::router())
        .merge(permissions::router())
        .merge(rules::router())
        .merge(skills::router())
        .merge(timeline::router())
        .merge(worldline::router())
        .merge(mcp::router())
        .merge(models::router())
        // console_assistant 的非 SSE 路由
        .merge(console_assistant::regular_router())
        // uploads 已单独拎出,不再包含
        // Wave 12 新增 9 个 HTTP handler 域(全部 non-SSE,挂在 regular):
        .merge(admin::router())
        .merge(auth::router())
        .merge(branches::router())
        .merge(imports::router())
        .merge(library::router())
        .merge(me::router())
        .merge(platform::router())
        .merge(saves::router())
        .merge(scripts::router())
        .merge(settings::router())
}
