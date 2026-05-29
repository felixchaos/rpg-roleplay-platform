//! rpg-routes — Axum 路由层
//! 对应 Python: rpg/routes/ 14 文件

pub mod console_assistant;
pub mod core;
pub mod game;
pub mod mcp;
pub mod memory;
pub mod models;
pub mod permissions;
pub mod rules;
pub mod skills;
pub mod timeline;
pub mod worldline;

use std::sync::Arc;

use axum::{
    response::{IntoResponse, Response},
    Json, Router,
};
use dashmap::DashMap;
use http::{HeaderMap, StatusCode};
use parking_lot::RwLock;
use serde_json::json;
use tokio::sync::Notify;

use rpg_llm::LlmRouter;
use rpg_platform::auth::User;
use rpg_state::StateStore;
use rpg_tools_dsl::ToolRegistry;

// ── AppState ─────────────────────────────────────────────────────────────────
//
// 持有真实业务需要的句柄。字段都包了 Arc / Clone,axum 用 `Clone` extractor 共享。

/// `tool_registry` / `llm_router` 等内部可变结构用 parking_lot::RwLock 包,
/// 跨 await 不持锁:大部分调用拿到 read snapshot 后立即释放。
#[derive(Clone)]
pub struct AppState {
    pub db: sqlx::PgPool,
    pub state_store: Arc<StateStore>,
    pub llm_router: Arc<RwLock<LlmRouter>>,
    pub tool_registry: Arc<RwLock<ToolRegistry>>,
    /// 每个 user 一个 Notify,用于 /api/stop 打断当前 chat。
    pub stop_events: Arc<DashMap<String, Arc<Notify>>>,
    /// 控制台助手对话(简版:全内存)。Vec<(role, text)>。
    pub console_conversations:
        Arc<DashMap<String, Vec<ConsoleMessage>>>,
}

impl AppState {
    /// 默认构造,只需 pool。其它字段全用空骨架。
    pub fn new(db: sqlx::PgPool) -> Self {
        Self {
            db,
            state_store: Arc::new(StateStore::new()),
            llm_router: Arc::new(RwLock::new(LlmRouter::new())),
            tool_registry: Arc::new(RwLock::new(ToolRegistry::new())),
            stop_events: Arc::new(DashMap::new()),
            console_conversations: Arc::new(DashMap::new()),
        }
    }

    /// 取/建一个 user 的 stop Notify。
    pub fn stop_notify(&self, user_id: &str) -> Arc<Notify> {
        self.stop_events
            .entry(user_id.to_string())
            .or_insert_with(|| Arc::new(Notify::new()))
            .clone()
    }
}

#[derive(Clone, Debug)]
pub struct ConsoleMessage {
    pub role: String,
    pub text: String,
    pub at: chrono::DateTime<chrono::Utc>,
}

// ── 错误类型 ─────────────────────────────────────────────────────────────────

/// 统一返回的 Response 错误,带 JSON `{ok:false, error:...}` 体。
#[derive(Debug)]
pub struct ResponseError {
    pub status: StatusCode,
    pub message: String,
}

impl ResponseError {
    pub fn new(status: StatusCode, msg: impl Into<String>) -> Self {
        Self {
            status,
            message: msg.into(),
        }
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
            Json(json!({ "ok": false, "error": self.message })),
        )
            .into_response()
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

/// 从 cookie / Authorization header 提 token。
fn token_from_headers(headers: &HeaderMap) -> Option<String> {
    if let Some(auth) = headers.get(http::header::AUTHORIZATION) {
        if let Ok(s) = auth.to_str() {
            if let Some(rest) = s.strip_prefix("Bearer ") {
                return Some(rest.trim().to_string());
            }
        }
    }
    if let Some(cookie) = headers.get(http::header::COOKIE) {
        if let Ok(s) = cookie.to_str() {
            for part in s.split(';') {
                let p = part.trim();
                if let Some(v) = p.strip_prefix("session_token=") {
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

// ── Router 构造 ──────────────────────────────────────────────────────────────

pub fn build_routes() -> Router<AppState> {
    Router::new().nest("/", api_router())
}

fn api_router() -> Router<AppState> {
    Router::new()
        .merge(core::router())
        .merge(game::router())
        .merge(memory::router())
        .merge(permissions::router())
        .merge(rules::router())
        .merge(skills::router())
        .merge(timeline::router())
        .merge(worldline::router())
        .merge(mcp::router())
        .merge(models::router())
        .merge(console_assistant::router())
}
