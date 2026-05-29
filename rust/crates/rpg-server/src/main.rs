//! rpg-server — 主入口(对应 Python `rpg/app.py` + `rpg/core/startup.py`)
//!
//! ## 翻译思路
//!
//! Python 侧通过模块级全局 dict + 反模式 `from app import _state_xxx` 共享
//! per-user 运行态(`_state_by_user` / `_gm_by_user` / `_run_id_by_user` 等)。
//! Rust 端拒绝这种隐式耦合,统一收进显式 [`AppState`],由 Axum `State<AppState>`
//! extractor 通过编译期类型把依赖注入到 handler 里。
//!
//! ## 现状
//!
//! 依赖的内部 crate 已就绪:
//! - `rpg_state::StateStore` — 按 user_id 分片的 GameState 持有者
//! - `rpg_llm::LlmRouter` + `rpg_llm::registry::ModelCatalog`
//! - `rpg_agents::gm::GameMaster`
//! - `rpg_tools_dsl::McpBroker`(re-export 自 `rpg_tools_dsl::mcp_broker`)
//!
//! `rpg-routes` 的 `build_routes()` 在本文件 [`build_router`] 中 merge 进来,
//! routes 自己持有 `rpg_routes::AppState { db }`(只需要 pool),所以
//! `with_state(routes_state)` 即可把它降级成 `Router<()>` 再合入 server router。
//!
//! ## main 主流程
//!
//! ```text
//!   1. dotenv 加载
//!   2. tracing 初始化(env-filter)
//!   3. 配置 / 鉴权 banner
//!   4. Postgres 连接池
//!   5. run_migrations(版本化 advisory-lock)
//!   6. 装配 AppState
//!   7. lifespan startup hooks(MCP 健康 loop / command tools / durable jobs / model catalog)
//!   8. build_router → CORS + Brotli/Gzip(SSE 排除) + Trace + api_contract_middleware
//!   9. bind 0.0.0.0:7860 axum::serve
//!  10. 优雅 shutdown(ctrl-c / SIGTERM → lifespan shutdown hooks)
//! ```

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use axum::{
    extract::State,
    http::{header, HeaderName, HeaderValue, Method, Request, StatusCode},
    middleware::Next,
    response::{IntoResponse, Json, Response},
    routing::get,
    Router,
};
use axum_prometheus::PrometheusMetricLayer;
use tower_governor::{
    governor::GovernorConfigBuilder,
    GovernorLayer,
};
use dashmap::DashMap;
use http::HeaderMap;
use parking_lot::RwLock;
use sqlx::Row;
use serde::Serialize;
use serde_json::json;
use thiserror::Error as ThisError;
use tokio::signal;
use tokio_util::sync::CancellationToken;
use tokio_util::task::TaskTracker;
use tower_http::{
    compression::{
        CompressionLayer,
        predicate::{NotForContentType, Predicate, SizeAbove},
    },
    cors::{AllowOrigin, CorsLayer},
    timeout::TimeoutLayer,
    trace::TraceLayer,
};
use tracing::{error as log_error, info, warn};
use uuid::Uuid;

use rpg_core::{config as core_config, startup as core_startup};
use rpg_llm::registry::ModelCatalog;
use rpg_llm::LlmRouter;
use rpg_routes::{AppConfig, AppState, AppStateInner};
use rpg_state::StateStore;
use rpg_tools_dsl::{McpBroker, ToolRegistry};

// ─── AppState ──────────────────────────────────────────────────────────────
//
// 6B-1:server 不再自持 AppState/AppConfig。两者已上移到 `rpg-routes`(单一权威),
// 这里只 `use rpg_routes::{AppState, AppStateInner, AppConfig}` 并在 main 装配一次。
// 旧版在 build_router 里逐字段 9×Arc clone 重建一份 routes::AppState 的反模式已删除。

/// 从 env 装配 [`AppConfig`]。
///
/// `AppConfig` 现定义在 rpg-routes(外部类型,Rust 不允许在此写 inherent impl),
/// 故由本自由函数承担原 `AppConfig::from_env`。
fn app_config_from_env() -> AppConfig {
    let (cors_origins, cors_allow_credentials) = core_startup::cors_origins();
    let port: u16 = std::env::var("RPG_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(7860);
    let host = std::env::var("RPG_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    AppConfig {
        app_title: core_config::app_title(),
        deployment_mode: core_config::deployment_mode().trim().to_lowercase(),
        require_auth: resolve_require_auth(),
        cors_origins,
        cors_allow_credentials,
        cors_max_age: core_config::cors_max_age(),
        gzip_min_bytes: core_config::gzip_min_bytes(),
        host,
        port,
    }
}

/// 装配带 DB read-through / flush 的 [`StateStore`](6C-1 状态外置)。
///
/// 这是打破 `rpg-state → rpg-platform` 循环的接缝:`rpg-state` 只持有不认 platform
/// 类型的闭包,真实的 `save_io` 调用绑定在这里(rpg-server 同时依赖二者)。
///   - **loader**:`String user_id → Option<GameState>`。跳过匿名哨兵;已登录用户经
///     `save_io::load_active_state_snapshot` 拉活跃存档的 `state_snapshot`,
///     用 `GameState::from_value` 包成运行态。无存档 / 解析失败 → `None`(退化建空白档)。
///   - **saver**:`(String user_id, GameState) → ()`。经 `save_io::write_active_state_snapshot`
///     把 `state.data` 写回 `game_saves.state_snapshot` 并刷新 `runtime_checkouts` 时间戳。
fn build_state_store(pool: sqlx::PgPool) -> StateStore {
    use rpg_state::GameState;

    /// 把 store 的 String user_id 解析成已登录用户的 `UserId`;匿名 / 非数字 → None。
    fn parse_user_id(user_id: &str) -> Option<rpg_core::UserId> {
        if user_id == "anonymous" {
            return None;
        }
        user_id.parse::<i64>().ok().map(rpg_core::UserId::from)
    }

    let load_pool = pool.clone();
    let loader: rpg_state::store::StateLoader = Arc::new(move |user_id: String| {
        let pool = load_pool.clone();
        Box::pin(async move {
            let uid = parse_user_id(&user_id)?;
            let (_, snapshot) = rpg_platform::save_io::load_active_state_snapshot(&pool, uid).await?;
            // 空对象 snapshot 视为无有效存档,退化建空白档(GameState::from_value 对
            // 非 object 会兜底,但空 object 会被当成合法空存档 —— 这里显式过滤)。
            if snapshot.as_object().map(|m| m.is_empty()).unwrap_or(true) {
                return None;
            }
            Some(GameState::from_value(user_id, snapshot))
        })
    });

    let save_pool = pool;
    let saver: rpg_state::store::StateSaver = Arc::new(move |user_id: String, state: GameState| {
        let pool = save_pool.clone();
        Box::pin(async move {
            let Some(uid) = parse_user_id(&user_id) else {
                return; // 匿名不落库
            };
            match rpg_platform::save_io::write_active_state_snapshot(&pool, uid, &state.data).await {
                Ok(true) => {}
                Ok(false) => {
                    tracing::debug!(user_id = %user_id, "state flush: 无活跃存档,跳过落库");
                }
                Err(e) => {
                    tracing::warn!(user_id = %user_id, error = %e, "state flush: 落库失败");
                }
            }
        })
    });

    StateStore::new().with_persistence(loader, saver)
}

/// 严格对应 Python `_api_auth_required()`(`app.py:355-375`)。
fn resolve_require_auth() -> bool {
    let explicit = core_config::require_auth_raw().trim().to_string();
    if explicit == "1" {
        return true;
    }
    if explicit == "0" {
        return false;
    }
    let mode = core_config::deployment_mode().trim().to_lowercase();
    const LOCAL_MODES: [&str; 4] = ["local", "desktop", "self_hosted", "self-hosted"];
    const SERVER_MODES: [&str; 4] = ["server", "production", "prod", "cloud"];
    if SERVER_MODES.iter().any(|m| *m == mode) {
        return true;
    }
    if LOCAL_MODES.iter().any(|m| *m == mode) {
        return false;
    }
    // 未知部署模式:保守起见强制鉴权。
    true
}

// ─── AppError(统一错误响应,对应 5 个 Python exception_handler)───────────

#[derive(Debug, ThisError)]
pub enum AppError {
    /// 对应 Python `ValueError` / `JSONDecodeError`。
    #[error("{0}")]
    BadRequest(String),

    /// 对应 Python `KeyError`。
    #[error("missing field: {0}")]
    MissingField(String),

    /// 对应 Python `TypeError`。
    #[error("invalid input type: {0}")]
    InvalidType(String),

    /// 对应 Python `PermissionError`。
    #[error("{0}")]
    Forbidden(String),

    /// 对应 Python `FileNotFoundError`。
    #[error("{0}")]
    NotFound(String),

    /// 兜底 5xx(对应 Python 未捕获异常 → 500)。
    #[error(transparent)]
    Internal(#[from] anyhow::Error),
}

impl AppError {
    fn status(&self) -> StatusCode {
        match self {
            Self::BadRequest(_) | Self::MissingField(_) | Self::InvalidType(_) => {
                StatusCode::BAD_REQUEST
            }
            Self::Forbidden(_) => StatusCode::FORBIDDEN,
            Self::NotFound(_) => StatusCode::NOT_FOUND,
            Self::Internal(_) => StatusCode::INTERNAL_SERVER_ERROR,
        }
    }

    fn user_message(&self) -> String {
        let raw = self.to_string();
        // 对齐 Python TypeError handler 的截断逻辑(200 字符)。
        if matches!(self, AppError::InvalidType(_)) && raw.len() > 200 {
            raw.chars().take(200).collect()
        } else {
            raw
        }
    }
}

#[derive(Serialize)]
struct ErrorBody {
    ok: bool,
    error: String,
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let status = self.status();
        if status.is_server_error() {
            log_error!(error = ?self, "request failed");
        } else {
            warn!(error = ?self, "request rejected");
        }
        let body = Json(ErrorBody {
            ok: false,
            error: self.user_message(),
        });
        (status, body).into_response()
    }
}

// ─── api_contract_middleware(对应 Python `core/startup.py:158`)─────────────

const API_VERSION: &str = "1";

/// /api/v1/* → /api/* 重写 + Origin 校验 + X-Request-ID + X-API-Version。
///
/// 对应 Python `api_contract_middleware`,行为对齐:
///   - mutating method(POST/PUT/PATCH/DELETE)若 Origin 不在白名单 → 403
///   - 重写后续 Router 看到的 path
///   - 响应头补 `X-API-Version` / `X-Request-ID` / `Cache-Control: no-store`
async fn api_contract_middleware(
    State(state): State<AppState>,
    request: Request<axum::body::Body>,
    next: Next,
) -> Response {
    let request_id = request
        .headers()
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| Uuid::new_v4().simple().to_string());

    let original_path = request.uri().path().to_string();
    let method = request.method().clone();
    let origin = request
        .headers()
        .get(header::ORIGIN)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    // 路径重写:/api/v1/* → /api/*
    let mut request = request;
    let prefix = format!("/api/v{API_VERSION}");
    if original_path == prefix {
        rewrite_path(&mut request, "/api");
    } else if let Some(rest) = original_path.strip_prefix(&format!("{prefix}/")) {
        rewrite_path(&mut request, &format!("/api/{rest}"));
    }

    // Origin 校验(只针对 /api/* 的 mutating method)
    if original_path.starts_with("/api") && is_mutating(&method) {
        let allowed =
            core_startup::origin_allowed(&state.config.cors_origins, origin.as_deref());
        if !allowed {
            let mut resp = (
                StatusCode::FORBIDDEN,
                Json(json!({
                    "ok": false,
                    "error": "Origin 不在允许列表",
                    "request_id": request_id,
                })),
            )
                .into_response();
            inject_api_headers(resp.headers_mut(), &request_id);
            return resp;
        }
    }

    let mut response = next.run(request).await;

    if original_path.starts_with("/api") {
        inject_api_headers(response.headers_mut(), &request_id);
    }

    response
}

fn is_mutating(method: &Method) -> bool {
    matches!(
        *method,
        Method::POST | Method::PUT | Method::PATCH | Method::DELETE
    )
}

fn rewrite_path(request: &mut Request<axum::body::Body>, new_path: &str) {
    let uri = request.uri().clone();
    let pq = uri
        .query()
        .map(|q| format!("{new_path}?{q}"))
        .unwrap_or_else(|| new_path.to_string());
    let mut parts = uri.into_parts();
    if let Ok(p) = http::uri::PathAndQuery::from_maybe_shared(pq.into_bytes()) {
        parts.path_and_query = Some(p);
    }
    if let Ok(new_uri) = http::Uri::from_parts(parts) {
        *request.uri_mut() = new_uri;
    }
}

fn inject_api_headers(headers: &mut HeaderMap, request_id: &str) {
    headers
        .entry(HeaderName::from_static("cache-control"))
        .or_insert(HeaderValue::from_static("no-store"));
    if let Ok(v) = HeaderValue::from_str(API_VERSION) {
        headers.insert(HeaderName::from_static("x-api-version"), v);
    }
    if let Ok(v) = HeaderValue::from_str(request_id) {
        headers.insert(HeaderName::from_static("x-request-id"), v);
    }
    headers
        .entry(HeaderName::from_static("vary"))
        .or_insert(HeaderValue::from_static("Origin"));
}

// ─── lifespan hooks(对应 Python `core/startup.py:lifespan`)────────────────

/// startup 阶段:MCP 健康 loop / command tools / durable job 恢复 / model catalog 加载。
async fn lifespan_startup(state: &AppState) {
    // 1. MCP 健康 loop — 用 task_tracker 追踪,使 graceful shutdown 能等待其退出
    {
        let broker = state.mcp_broker.clone();
        let token = state.shutdown_token.clone();
        state.task_tracker.spawn(async move {
            // start_health_loop 是同步启动内部 tokio task,在 tracker 的 task 内运行,
            // 并监听 shutdown_token,一旦取消则等待 health_loop 停止。
            broker.start_health_loop();
            token.cancelled().await;
            broker.stop_health_loop().await;
        });
    }
    info!("startup: mcp_broker health loop started");

    // 2. 注册默认 plugin 工具(对应 Python `command_tools_register.ensure_registered`)
    rpg_tools_dsl::tool_registry::register_default_plugins();
    let count = state.tool_registry.read().list().len();
    info!(tool_count = count, "startup: tool registry primed");

    // 3. durable import job 恢复
    //    Python: `platform_app.script_import.recover_pending_sync_jobs(pool)`
    //    表名对齐:Rust migrations 建的是 `import_jobs`(见 migrations.rs SQL_009),
    //    旧代码误写 `script_import_jobs`(Python 时代表名)在 fresh DB 上会直接报错。
    //    in_progress 状态对齐 `import_jobs` 的 'pending'/'running'(见 SQL_013 单活跃索引)。
    //    这里加载 in_progress 明细并 log 告警;真正 requeue/transition 留 TODO。
    match sqlx::query(
        r#"select id, job_id, kind, status, stage, user_id, script_id
           from import_jobs
           where status in ('pending','running')
           order by created_at"#,
    )
    .fetch_all(&state.db)
    .await
    {
        Ok(rows) if !rows.is_empty() => {
            warn!(
                in_progress_jobs = rows.len(),
                "startup: durable import jobs in progress on boot (requeue logic TODO)"
            );
            for r in &rows {
                let job_id: String = r.try_get("job_id").unwrap_or_default();
                let kind: String = r.try_get("kind").unwrap_or_default();
                let status: String = r.try_get("status").unwrap_or_default();
                let stage: String = r.try_get("stage").unwrap_or_default();
                warn!(
                    %job_id, %kind, %status, %stage,
                    "startup: orphaned import job (was running when worker stopped)"
                );
            }
            // TODO[rpg-platform]: 真正 requeue/transition 等 `recover_pending_sync_jobs` 落地。
        }
        Ok(_) => info!("startup: no in-progress import jobs"),
        Err(e) => warn!(error = %e, "startup: probe import_jobs failed"),
    }

    // 4. 模型目录加载(rpg-llm registry)
    //    Python: `model_registry.load_model_catalog`。目前 LlmRouter 已通过
    //    `with_catalog` 初始化(见 main),此处仅 log 当前 selected 信息。
    {
        let router = state.llm_router.read();
        if let Some(catalog) = router.catalog() {
            info!(
                api_id = %catalog.selected.api_id,
                model_id = %catalog.selected.model_id,
                "startup: model catalog ready"
            );
        } else {
            warn!("startup: llm_router has no catalog");
        }
    }

    // 5. TODO: cleanup stale upload chunks(等 rpg-platform 提供入口)
    //    对应 Python `platform_app.script_import.cleanup_stale_upload_chunks(ttl_hours=24)`
}

/// shutdown 阶段:cancel → 等所有 task 完成 → 停 MCP 子进程 → 关 pool。
async fn lifespan_shutdown(state: &AppState) {
    // 1. 广播取消信号 — 所有持有 shutdown_token.clone() 的 spawned task 都会感知
    state.shutdown_token.cancel();
    info!("shutdown: cancellation token broadcast");

    // 2. 等所有已追踪 task 退出(包括 in-flight SSE / LLM stream 写日志任务)
    state.task_tracker.close();
    state.task_tracker.wait().await;
    info!("shutdown: all spawned tasks drained");

    // 3. 把所有 dirty 的内存 state 落库(6C-1),避免 graceful 重启丢未保存写入。
    //    必须在关 pool 之前;saver 闭包持有的是 pool clone,close 后写会失败。
    let flushed = state.state_store.flush_all_dirty().await;
    if flushed > 0 {
        info!(flushed_states = flushed, "shutdown: dirty game states flushed to DB");
    }

    // 4. 停止 MCP 子进程(health_loop 已由 task 内部处理,此处补充保险)
    state.mcp_broker.stop_all().await;
    info!("shutdown: mcp_broker stopped");

    // 5. 关闭数据库连接池
    state.db.close().await;
    info!("lifespan shutdown done");
}

// ─── Router 装配 ───────────────────────────────────────────────────────────

async fn health() -> impl IntoResponse {
    Json(json!({
        "ok": true,
        "service": "RPG backend (Rust/Axum)",
    }))
}

/// GET /livez — k8s liveness probe。进程活着即返回 200。
async fn livez() -> impl IntoResponse {
    (StatusCode::OK, Json(json!({"ok": true, "status": "alive"})))
}

/// GET /readyz — k8s readiness probe。探 DB 连通性,通过 → 200,失败 → 503。
///
/// 跳过:SSO / auth token 鉴权(探针本身不需要用户 token),
/// 探针失败时 k8s 停止向此 pod 分流流量。
async fn readyz(State(state): State<AppState>) -> impl IntoResponse {
    match sqlx::query("SELECT 1").execute(&state.db).await {
        Ok(_) => (StatusCode::OK, Json(json!({"ok": true, "db": "ready"}))),
        Err(e) => {
            warn!(error = %e, "readyz: DB probe failed");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"ok": false, "db": "unavailable", "error": e.to_string()})),
            )
        }
    }
}

/// 从 env 读取限流配置(可通过 RPG_RATE_LIMIT_PER_MIN 覆盖,默认 100)。
fn rate_limit_per_minute() -> u32 {
    std::env::var("RPG_RATE_LIMIT_PER_MIN")
        .ok()
        .and_then(|v| v.parse::<u32>().ok())
        .filter(|&n| n > 0)
        .unwrap_or(100)
}

/// 从 env 读取请求超时(秒,可通过 RPG_REQUEST_TIMEOUT_SECS 覆盖,默认 30)。
fn request_timeout_secs() -> Duration {
    let secs = std::env::var("RPG_REQUEST_TIMEOUT_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(30);
    Duration::from_secs(secs)
}

/// 全局请求体大小限制(字节,可通过 RPG_BODY_LIMIT_BYTES 覆盖,默认 2 MB)。
fn body_limit_bytes() -> usize {
    std::env::var("RPG_BODY_LIMIT_BYTES")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(2 * 1024 * 1024) // 2 MB
}

/// 上传路由专用大 body limit(字节,可通过 RPG_UPLOAD_BODY_LIMIT_BYTES 覆盖,默认 50 MB)。
fn upload_body_limit_bytes() -> usize {
    std::env::var("RPG_UPLOAD_BODY_LIMIT_BYTES")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(50 * 1024 * 1024) // 50 MB
}

/// 组装完整 Router 与中间件栈。
///
/// ## 中间件分层设计
///
/// ### 观测层(全局,含 SSE)
///   Prometheus metrics → TraceLayer → CompressionLayer
///
/// ### 非 SSE 业务层(普通 API)
///   body limit(2 MB) + timeout(30 s) + governor 限流(100 req/min per-IP)
///   → api_contract_middleware → handler
///
/// ### SSE 路由层(豁免 timeout + governor)
///   /api/chat  /api/opening  /api/state_events — 只走 body limit,
///   不走 TimeoutLayer(避免长连接被切断)和 governor(流式连接本身无刷量风险)。
///
/// ### 上传路由层
///   /api/uploads/* — body limit 放宽至 50 MB(per-route 覆盖全局 2 MB)。
///
/// ### 探针/metrics 端点(完全在限流之外)
///   /health  /livez  /readyz  /metrics — 不走 governor / body limit / timeout
fn build_router(state: AppState) -> Router {
    let cors = build_cors_layer(&state.config);

    // ── Prometheus metrics ──────────────────────────────────────────────────
    let (prometheus_layer, metric_handle) = PrometheusMetricLayer::pair();

    // ── governor 限流配置(per-IP) ───────────────────────────────────────────
    // tower_governor 0.4 API:
    //   - per_second(n) 表示每 n 秒补充 1 token(令牌桶速率 = 60/rpm per-second)
    //   - burst_size(b) 允许 b 个请求的短时突发
    // 100 req/min → 每 60/100=0.6s 补充 1 个令牌(以毫秒表示)。
    let rpm = rate_limit_per_minute();
    let period_ms = (60_000u64 / rpm as u64).max(1); // 每 token 补充时间(ms)
    let burst = (rpm / 10).max(1);
    let governor_cfg = GovernorConfigBuilder::default()
        .per_millisecond(period_ms)
        .burst_size(burst)
        .finish()
        .expect("governor config should be valid");
    let governor_layer = GovernorLayer {
        config: std::sync::Arc::new(governor_cfg),
    };

    // ── timeout ─────────────────────────────────────────────────────────────
    // tower_http 0.6 推荐 with_status_code (TimeoutLayer::new 已标 deprecated)。
    // 超时时返回 504 Gateway Timeout。
    #[allow(deprecated)]
    let timeout = TimeoutLayer::new(request_timeout_secs());

    // ── body limit ──────────────────────────────────────────────────────────
    let global_body_limit = axum::extract::DefaultBodyLimit::max(body_limit_bytes());

    // ── SSE 路由(豁免 governor + timeout) ──────────────────────────────────
    // 这三条路由产生长连接流,不设超时也不限流。
    // body limit 仍保留(SSE 请求本身体积小,全局 2 MB 够用)。
    let sse_routes = rpg_routes::build_sse_routes()
        .with_state(state.clone())
        .layer(global_body_limit);

    // ── 上传路由(放宽 body limit) ───────────────────────────────────────────
    let upload_routes = rpg_routes::build_upload_routes()
        .with_state(state.clone())
        .layer(axum::extract::DefaultBodyLimit::max(upload_body_limit_bytes()))
        .layer(timeout)
        .layer(governor_layer.clone());

    // ── 普通业务路由(全套中间件) ────────────────────────────────────────────
    let api_routes = rpg_routes::build_regular_routes()
        .with_state(state.clone())
        .layer(global_body_limit)
        .layer(timeout)
        .layer(governor_layer);

    // ── 探针端点(完全无限流/超时/body limit,运维专用) ──────────────────────
    let probe_routes = Router::new()
        .route("/health", get(health))
        .route("/livez", get(livez))
        .route("/readyz", get(readyz))
        .route(
            "/metrics",
            get(move || {
                let h = metric_handle.clone();
                async move { h.render() }
            }),
        )
        .with_state(state.clone());

    // ── 拼装完整路由树 ──────────────────────────────────────────────────────
    //
    // axum 的 .layer() **从内向外** 包裹,书写顺序与执行顺序相反。
    // 期望外→内顺序: CORS → Prometheus → Trace → Compression → contract → [各子树自己的层]
    Router::new()
        .merge(probe_routes)
        .merge(sse_routes)
        .merge(upload_routes)
        .merge(api_routes)
        .with_state(state.clone())
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            api_contract_middleware,
        ))
        .layer(
            CompressionLayer::new()
                .compress_when(
                    SizeAbove::new(512)
                        .and(NotForContentType::const_new("text/event-stream")),
                ),
        )
        .layer(TraceLayer::new_for_http())
        .layer(prometheus_layer)
        .layer(cors)
}

fn build_cors_layer(cfg: &AppConfig) -> CorsLayer {
    let mut layer = CorsLayer::new()
        .allow_methods([
            Method::GET,
            Method::POST,
            Method::PUT,
            Method::PATCH,
            Method::DELETE,
            Method::OPTIONS,
        ])
        .allow_headers([
            header::CONTENT_TYPE,
            header::AUTHORIZATION,
            header::ACCEPT,
            HeaderName::from_static("x-request-id"),
        ])
        .expose_headers([
            HeaderName::from_static("x-api-version"),
            HeaderName::from_static("x-request-id"),
        ])
        .max_age(Duration::from_secs(cfg.cors_max_age.max(0) as u64));

    let has_wildcard = cfg.cors_origins.iter().any(|o| o == "*");

    if has_wildcard {
        // `credentials: include` 不可与 `*` 同用;改为 Mirror — 回显请求 Origin,
        // 这样既满足 allow_credentials(true) 的要求,又不硬写来源列表。
        warn!(
            "cors_origins 包含 '*',已自动切换为 AllowOrigin::mirror_request() \
             以兼容 credentials: include (RFC 要求明确 origin)"
        );
        layer = layer
            .allow_origin(AllowOrigin::mirror_request())
            .allow_credentials(true);
    } else if cfg.cors_origins.is_empty() {
        // 生产环境 cors_origins 为空 → 无任何跨域请求会被放行,记 warn 便于排查
        if cfg.deployment_mode == "production"
            || cfg.deployment_mode == "prod"
            || cfg.deployment_mode == "cloud"
        {
            warn!(
                deployment_mode = %cfg.deployment_mode,
                "cors_origins 为空,生产环境所有跨域请求将被拒绝,请在 RPG_CORS_ORIGINS 配置允许来源"
            );
        }
        // 不设 allow_origin → tower-http 默认拒绝所有跨域
    } else {
        // 明确 origin 列表:不含 `*`,可安全启用 allow_credentials
        let parsed: Vec<HeaderValue> = cfg
            .cors_origins
            .iter()
            .filter_map(|o| HeaderValue::from_str(o).ok())
            .collect();
        layer = layer.allow_origin(parsed);
        if cfg.cors_allow_credentials {
            layer = layer.allow_credentials(true);
        }
    }
    layer
}

// ─── Cookie helper ─────────────────────────────────────────────────────────

/// 构造符合安全规范的 session cookie 头值字符串。
///
/// 属性约定:
/// - `HttpOnly` — 防止 XSS 脚本读取
/// - `Path=/` — 整个站点有效
/// - `SameSite=Lax` — 防止 CSRF 同时允许普通跨站跳转携带
/// - `Secure` — prod 环境强制 HTTPS(开发环境可传 `false`)
///
/// 用法示例(handler 侧,TODO: future routes wire):
/// ```ignore
/// let cookie = build_session_cookie("session", &token, cfg.deployment_mode == "production");
/// response.headers_mut().insert(header::SET_COOKIE, HeaderValue::from_str(&cookie)?);
/// ```
pub fn build_session_cookie(name: &str, token: &str, secure: bool) -> String {
    let mut parts = vec![
        format!("{}={}", name, token),
        "Path=/".to_string(),
        "HttpOnly".to_string(),
        "SameSite=Lax".to_string(),
    ];
    if secure {
        parts.push("Secure".to_string());
    }
    parts.join("; ")
}

// ─── main ──────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> Result<()> {
    // 1. dotenv:优先读项目根的 .env(对应 Python `Path(__file__).parent.parent / ".env"`)
    let _ = dotenvy::from_filename("../.env");
    let _ = dotenvy::dotenv(); // 当前目录兜底

    // 2. tracing
    let env_filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info,rpg=debug"));
    tracing_subscriber::fmt()
        .with_env_filter(env_filter)
        .with_target(true)
        .init();

    // 启动日志含 commit hash,便于日志追踪(可后续通过 BUILD_COMMIT env 覆盖)
    let commit_hash = option_env!("BUILD_COMMIT").unwrap_or("rust-migration");
    info!(commit = commit_hash, "rpg-server starting");

    // 3. 配置 + 鉴权 banner(对应 Python `_startup_auth_banner`)
    let config = Arc::new(app_config_from_env());
    info!(
        deployment_mode = %config.deployment_mode,
        require_auth = config.require_auth,
        cors_origins = ?config.cors_origins,
        "startup: config loaded"
    );

    // 4. 数据库连接池(对应 Python `platform_app.db.init_db()`)
    let database_url = std::env::var("DATABASE_URL")
        .or_else(|_| std::env::var("RPG_DATABASE_URL"))
        .context("DATABASE_URL / RPG_DATABASE_URL 至少需要一个,无法启动")?;
    let pool_max = core_config::db_pool_max();
    let db = rpg_db::pool::init_pool(&database_url, pool_max)
        .await
        .context("init pg pool failed")?;

    // 5. 运行迁移(对应 Python `_bootstrap_init_db`)
    if !core_config::skip_auto_migrate() {
        rpg_db::migrations::run_migrations(&db)
            .await
            .context("run_migrations failed")?;
    } else {
        warn!("RPG_SKIP_AUTO_MIGRATE=1,跳过自动迁移");
    }

    // 6. 装配 AppState(6B-1:单一 `AppStateInner`,一次性收口全部句柄)
    //    - LlmRouter:目前用 `ModelCatalog::default()` 兜底,后续 `model_registry.load_model_catalog`
    //      会从 DB / 文件读取 catalog 并 set_catalog;backend 注册由 rpg-llm 上层负责。
    //    - StateStore:按 user_id(String key)分片 GameState。
    //    - GameMaster pool / stop_events / run_ids:进程内,lazy 填充(bare DashMap,
    //      外层 `AppState(Arc<_>)` 已提供共享语义,无需各自再包 Arc)。
    let tool_registry = Arc::new(RwLock::new(ToolRegistry::new()));
    let llm_router = Arc::new(RwLock::new(
        LlmRouter::new().with_catalog(ModelCatalog::default()),
    ));
    // 6C-1:StateStore 接 DB read-through / flush(loader/saver 闭包注入,打破
    // rpg-state→rpg-platform 循环)。pool 先 clone 给闭包,再把 db 本体移进 AppStateInner。
    let state_store = Arc::new(build_state_store(db.clone()));
    let state = AppState::from_inner(AppStateInner {
        db,
        state_store,
        gm_pool: DashMap::new(),
        llm_router,
        tool_registry,
        mcp_broker: Arc::new(McpBroker::default()),
        stop_events: DashMap::new(),
        run_ids: DashMap::new(),
        console_conversations: DashMap::new(),
        chunk_uploads: DashMap::new(),
        config: config.clone(),
        shutdown_token: CancellationToken::new(),
        task_tracker: TaskTracker::new(),
    });

    // 7. lifespan startup
    lifespan_startup(&state).await;

    // 8. Router
    let app = build_router(state.clone());

    // 9. Bind & serve
    let addr: SocketAddr = format!("{}:{}", config.host, config.port)
        .parse()
        .context("invalid host/port")?;
    info!(%addr, "rpg-server listening");
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .context("bind tcp listener failed")?;

    // 10. 优雅 shutdown
    // `into_make_service_with_connect_info::<SocketAddr>()` 使 axum-governor PeerIp extractor
    // 能读取 ConnectInfo<SocketAddr>。若不加此项,governor 会因 MissingConnectInfo 而 panic。
    let shutdown_state = state.clone();
    let serve_result = axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(async move {
        shutdown_signal().await;
        lifespan_shutdown(&shutdown_state).await;
    })
    .await;

    if let Err(err) = serve_result {
        log_error!(?err, "axum::serve exited with error");
        return Err(anyhow::anyhow!(err));
    }
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("install ctrl-c handler failed");
    };

    #[cfg(unix)]
    let terminate = async {
        signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("install SIGTERM handler failed")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => info!("ctrl-c received, draining"),
        _ = terminate => info!("SIGTERM received, draining"),
    }
}
