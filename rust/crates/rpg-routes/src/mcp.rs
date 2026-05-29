//! mcp.py → mcp.rs — MCP server 管理与工具调用路由
//! GET  /api/tools                — 工具清单
//! POST /api/mcp/server           — 新增/更新 MCP server(admin)
//! POST /api/mcp/server/enabled   — 启用/禁用(admin)
//! POST /api/mcp/server/delete    — 删除(admin)
//! POST /api/mcp/server/validate  — 校验(admin)
//! POST /api/mcp/server/start     — 启动(admin)
//! POST /api/mcp/server/stop      — 停止(admin)
//! GET  /api/mcp/runtime          — 运行时状态 + audit
//! POST /api/mcp/tool/call        — 直接调用 MCP 工具
//! GET  /api/mcp/tools            — 已启动 server 的工具清单

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use rpg_tools_dsl::mcp::{list_audit_entries, validate_server, McpCatalog, McpServer};

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/tools", get(api_tools))
        .route("/api/mcp/server", post(api_mcp_server))
        .route("/api/mcp/server/enabled", post(api_mcp_server_enabled))
        .route("/api/mcp/server/delete", post(api_mcp_server_delete))
        .route("/api/mcp/server/validate", post(api_mcp_server_validate))
        .route("/api/mcp/server/start", post(api_mcp_server_start))
        .route("/api/mcp/server/stop", post(api_mcp_server_stop))
        .route("/api/mcp/runtime", get(api_mcp_runtime))
        .route("/api/mcp/tool/call", post(api_mcp_tool_call))
        .route("/api/mcp/tools", get(api_mcp_tools))
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct McpServerRequest {
    #[serde(flatten)]
    pub fields: Value,
}

#[derive(Debug, Deserialize, Default)]
pub struct McpServerEnabledRequest {
    pub id: Option<String>,
    pub enabled: Option<bool>,
}

#[derive(Debug, Deserialize, Default)]
pub struct McpServerDeleteRequest {
    pub id: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct McpServerValidateRequest {
    pub id: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct McpServerStartRequest {
    pub id: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct McpServerStopRequest {
    pub id: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct McpToolCallRequest {
    pub server_id: Option<String>,
    pub tool: Option<String>,
    pub arguments: Option<Value>,
    pub timeout: Option<u64>,
}

// ── helpers ───────────────────────────────────────────────────────────────────

async fn require_admin(s: &AppState, headers: &HeaderMap) -> Result<(), ResponseError> {
    let u = require_user(s, headers).await?;
    if u.role != "admin" {
        return Err(ResponseError::forbidden("仅管理员"));
    }
    Ok(())
}

async fn load_catalog(s: &AppState) -> McpCatalog {
    McpCatalog::load(&s.db).await.unwrap_or_default()
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// GET /api/tools — 工具清单(本地 tool registry)
#[tracing::instrument(skip_all)]
async fn api_tools(State(s): State<AppState>) -> impl IntoResponse {
    let reg = s.tool_registry.read();
    let tools: Vec<Value> = reg
        .list()
        .into_iter()
        .map(|t| serde_json::to_value(t).unwrap_or(json!({})))
        .collect();
    Json(json!({"ok": true, "tools": tools}))
}

#[tracing::instrument(skip_all)]
async fn api_mcp_server(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<McpServerRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let server: McpServer = serde_json::from_value(body.fields)
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    validate_server(&server).map_err(|e| ResponseError::bad_request(e.to_string()))?;
    let mut catalog = load_catalog(&s).await;
    catalog.upsert_server(server);
    catalog
        .save(&s.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    Ok(Json(json!({"ok": true})).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_mcp_server_enabled(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<McpServerEnabledRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let id = body
        .id
        .ok_or_else(|| ResponseError::bad_request("id required"))?;
    let enabled = body.enabled.unwrap_or(false);
    let mut catalog = load_catalog(&s).await;
    let changed = catalog.set_enabled(&id, enabled);
    if changed {
        catalog
            .save(&s.db)
            .await
            .map_err(|e| ResponseError::internal(e.to_string()))?;
    }
    Ok(Json(json!({"ok": true, "changed": changed})).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_mcp_server_delete(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<McpServerDeleteRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let id = body
        .id
        .ok_or_else(|| ResponseError::bad_request("id required"))?;
    let mut catalog = load_catalog(&s).await;
    catalog.delete_server(&id);
    catalog
        .save(&s.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    Ok(Json(json!({"ok": true})).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_mcp_server_validate(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<McpServerValidateRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let id = body.id.unwrap_or_default();
    let catalog = load_catalog(&s).await;
    let server = catalog
        .servers
        .iter()
        .find(|s| s.id == id)
        .ok_or_else(|| ResponseError::bad_request("server not found"))?;
    validate_server(server).map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(json!({"ok": true, "valid": true})).into_response())
}

/// POST /api/mcp/server/start — 启动指定 MCP server 子进程。
///
/// 对应 Python `api_mcp_server_start` → `mcp_broker.start_server(id)`。
/// 从 catalog 取出 server spec,委托 `McpBroker::start_server`(已实现真起 child
/// process,见 `rpg-tools-dsl::mcp_broker`)。
#[tracing::instrument(skip_all)]
async fn api_mcp_server_start(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<McpServerStartRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let id = body
        .id
        .ok_or_else(|| ResponseError::bad_request("id required"))?;
    let catalog = load_catalog(&s).await;
    let spec = catalog
        .servers
        .iter()
        .find(|x| x.id == id)
        .cloned()
        .ok_or_else(|| ResponseError::bad_request(format!("未知 server: {id}")))?;
    let result = s
        .mcp_broker
        .start_server(spec)
        .await
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(result).into_response())
}

/// POST /api/mcp/server/stop — 停止指定 MCP server 子进程。
///
/// 对应 Python `api_mcp_server_stop` → `mcp_broker.stop_server(id)`。
#[tracing::instrument(skip_all)]
async fn api_mcp_server_stop(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<McpServerStopRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let id = body
        .id
        .ok_or_else(|| ResponseError::bad_request("id required"))?;
    let result = s.mcp_broker.stop_server(&id).await;
    Ok(Json(result).into_response())
}

/// GET /api/mcp/runtime — 列出 server / running 进程 / audit_log。
///
/// 对应 Python `api_mcp_runtime` → `mcp_broker.status()` + `get_audit_log`。
/// 非 admin 时 strip 掉 `last_stderr`(可能含 token / 路径),与 Python 行为一致。
#[tracing::instrument(skip_all)]
async fn api_mcp_runtime(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let is_admin = crate::require_user(&s, &headers)
        .await
        .ok()
        .map(|u| u.role == "admin")
        .unwrap_or(false);
    let catalog = load_catalog(&s).await;
    let mut running: Vec<Value> =
        s.mcp_broker.status().into_iter().map(|st| serde_json::to_value(st).unwrap_or(json!({}))).collect();
    if !is_admin {
        for entry in running.iter_mut() {
            if let Some(obj) = entry.as_object_mut() {
                obj.remove("last_stderr");
            }
        }
    }
    let audit = list_audit_entries(200);
    Json(json!({
        "ok": true,
        "servers": catalog.servers,
        "running": running,
        "audit_log": audit,
    }))
}

#[tracing::instrument(skip_all)]
async fn api_mcp_tool_call(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<McpToolCallRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;

    let server_id = body
        .server_id
        .ok_or_else(|| ResponseError::bad_request("server_id required"))?;
    let tool_name = body
        .tool
        .ok_or_else(|| ResponseError::bad_request("tool required"))?;
    let arguments = body.arguments.unwrap_or(serde_json::Value::Object(Default::default()));
    let timeout_secs = body.timeout.unwrap_or(30).clamp(1, 300);

    let result = s
        .mcp_broker
        .call_tool(&server_id, &tool_name, arguments, timeout_secs)
        .await;

    Ok(Json(result).into_response())
}

/// GET /api/mcp/tools — 列出所有已启动 MCP server 的工具清单(前端加号菜单用)。
///
/// 对应 Python `api_mcp_tools` → `mcp_broker.discover_all_tools()`。
#[tracing::instrument(skip_all)]
async fn api_mcp_tools(State(s): State<AppState>) -> impl IntoResponse {
    let tools = s.mcp_broker.discover_all_tools();
    Json(json!({"ok": true, "tools": tools}))
}
