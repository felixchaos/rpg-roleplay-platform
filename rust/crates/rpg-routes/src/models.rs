//! models.py → models.rs — 模型目录与 API 管理路由
//! GET  /api/models                         — 模型列表 + 健康状态
//! POST /api/models/health/refresh-all      — 触发后台 probe
//! GET  /api/models/health                  — 健康缓存快照
//! POST /api/models/select                  — 切换选用模型(admin)
//! POST /api/models/api                     — upsert API(admin)
//! POST /api/models/model                   — upsert model(admin)
//! POST /api/models/model/delete            — 删除 model(admin)
//! GET  /api/models/remote                  — 拉取远端模型清单
//! GET  /api/models/diff                    — 对比本地与远端
//! POST /api/models/probe                   — 发一条最小请求验证可用性
//! GET  /api/models/pricing                 — 查询定价
//! GET  /api/models/report                  — API 综合健康报告
//! GET  /api/models/capabilities            — 查询单个模型能力
//! GET  /api/models/capabilities/labels     — 所有能力标签词典

use axum::{
    extract::{Query, State},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use rpg_llm::{ModelCatalog, Selected};

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/models", get(api_models))
        .route("/api/models/health/refresh-all", post(api_models_health_refresh_all))
        .route("/api/models/health", get(api_models_health))
        .route("/api/models/select", post(api_models_select))
        .route("/api/models/api", post(api_models_upsert_api))
        .route("/api/models/model", post(api_models_upsert_model))
        .route("/api/models/model/delete", post(api_models_delete_model))
        .route("/api/models/remote", get(api_models_remote))
        .route("/api/models/diff", get(api_models_diff))
        .route("/api/models/probe", post(api_models_probe))
        .route("/api/models/pricing", get(api_models_pricing))
        .route("/api/models/report", get(api_models_report))
        .route("/api/models/capabilities", get(api_models_capabilities))
        .route("/api/models/capabilities/labels", get(api_models_capability_labels))
}

// ── request / query types ─────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct ModelsSelectRequest {
    pub api_id: Option<String>,
    pub model_id: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ModelsUpsertApiRequest {
    #[serde(flatten)]
    pub fields: Value,
}

#[derive(Debug, Deserialize, Default)]
pub struct ModelsUpsertModelRequest {
    pub api_id: Option<String>,
    pub model: Option<Value>,
    #[serde(flatten)]
    pub extra: Value,
}

#[derive(Debug, Deserialize, Default)]
pub struct ModelsDeleteModelRequest {
    pub api_id: Option<String>,
    pub model_id: Option<String>,
    pub real_name: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ModelsProbeRequest {
    pub api_id: Option<String>,
    pub model: Option<String>,
    pub timeout: Option<u64>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ModelQueryParams {
    pub api_id: Option<String>,
    pub model: Option<String>,
    pub refresh: Option<String>,
    pub probe: Option<String>,
}

// ── helpers ───────────────────────────────────────────────────────────────────

fn catalog_snapshot(s: &AppState) -> ModelCatalog {
    s.llm_router
        .read()
        .catalog()
        .cloned()
        .unwrap_or_default()
}

fn write_catalog(s: &AppState, catalog: ModelCatalog) {
    s.llm_router.write().set_catalog(catalog);
}

async fn require_admin(s: &AppState, headers: &HeaderMap) -> Result<(), ResponseError> {
    let u = require_user(s, headers).await?;
    if u.role != "admin" {
        return Err(ResponseError::forbidden("仅管理员"));
    }
    Ok(())
}

// ── handlers ──────────────────────────────────────────────────────────────────

#[tracing::instrument(skip_all)]
async fn api_models(State(s): State<AppState>) -> Result<Response, ResponseError> {
    let catalog = catalog_snapshot(&s);
    let selected = catalog.selected.clone();
    let value = serde_json::to_value(&catalog).unwrap_or(json!({}));
    Ok(Json(json!({
        "ok": true,
        "models": value,
        "selected": selected,
    }))
    .into_response())
}

#[tracing::instrument(skip_all)]
async fn api_models_health_refresh_all(
    State(_s): State<AppState>,
    Json(_body): Json<Value>,
) -> impl IntoResponse {
    // TODO: 真后台 probe;翻译期返回 scheduled=0。
    Json(json!({"ok": true, "scheduled": 0}))
}

#[tracing::instrument(skip_all)]
async fn api_models_health(State(_s): State<AppState>) -> impl IntoResponse {
    // TODO: 翻译期没接 probe 缓存,返回空 map。
    Json(json!({"ok": true, "health": {}}))
}

#[tracing::instrument(skip_all)]
async fn api_models_select(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ModelsSelectRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let api_id = body
        .api_id
        .ok_or_else(|| ResponseError::bad_request("api_id required"))?;
    let model_id = body
        .model_id
        .ok_or_else(|| ResponseError::bad_request("model_id required"))?;
    let mut catalog = catalog_snapshot(&s);
    catalog.selected = Selected { api_id, model_id };
    write_catalog(&s, catalog);
    Ok(Json(json!({"ok": true})).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_models_upsert_api(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ModelsUpsertApiRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let id = body
        .fields
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ResponseError::bad_request("id required"))?
        .to_string();
    let mut catalog = catalog_snapshot(&s);
    // 找到现有项,merge 字段
    if let Some(api) = catalog.apis.iter_mut().find(|a| a.id == id) {
        if let Some(v) = body.fields.get("display_name").and_then(|x| x.as_str()) {
            api.display_name = v.to_string();
        }
        if let Some(v) = body.fields.get("enabled").and_then(|x| x.as_bool()) {
            api.enabled = v;
        }
        if let Some(v) = body.fields.get("base_url").and_then(|x| x.as_str()) {
            api.base_url = Some(v.to_string());
        }
    } else {
        // 新增 — 用默认值兜底。
        let v = body.fields.clone();
        if let Ok(entry) = serde_json::from_value::<rpg_llm::ApiEntry>(v) {
            catalog.apis.push(entry);
        }
    }
    write_catalog(&s, catalog);
    Ok(Json(json!({"ok": true})).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_models_upsert_model(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ModelsUpsertModelRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let api_id = body
        .api_id
        .ok_or_else(|| ResponseError::bad_request("api_id required"))?;
    let model_v = body
        .model
        .ok_or_else(|| ResponseError::bad_request("model required"))?;
    let entry: rpg_llm::ModelEntry =
        serde_json::from_value(model_v).map_err(|e| ResponseError::bad_request(e.to_string()))?;
    let mut catalog = catalog_snapshot(&s);
    if let Some(api) = catalog.apis.iter_mut().find(|a| a.id == api_id) {
        if let Some(existing) = api.models.iter_mut().find(|m| m.id == entry.id) {
            *existing = entry;
        } else {
            api.models.push(entry);
        }
    } else {
        return Err(ResponseError::bad_request("api not found"));
    }
    write_catalog(&s, catalog);
    Ok(Json(json!({"ok": true})).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_models_delete_model(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ModelsDeleteModelRequest>,
) -> Result<Response, ResponseError> {
    require_admin(&s, &headers).await?;
    let api_id = body
        .api_id
        .ok_or_else(|| ResponseError::bad_request("api_id required"))?;
    let model_id = body
        .model_id
        .or(body.real_name)
        .ok_or_else(|| ResponseError::bad_request("model_id required"))?;
    let mut catalog = catalog_snapshot(&s);
    if let Some(api) = catalog.apis.iter_mut().find(|a| a.id == api_id) {
        api.models.retain(|m| m.id != model_id);
    }
    write_catalog(&s, catalog);
    Ok(Json(json!({"ok": true})).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_models_remote(
    State(_s): State<AppState>,
    Query(_q): Query<ModelQueryParams>,
) -> impl IntoResponse {
    // TODO: 真远端清单需要在 LlmBackend::list_models 上调度;翻译期返回空。
    Json(json!({"ok": true, "models": []}))
}

#[tracing::instrument(skip_all)]
async fn api_models_diff(
    State(_s): State<AppState>,
    Query(_q): Query<ModelQueryParams>,
) -> impl IntoResponse {
    // TODO: 真 diff 需要远端 + 本地;翻译期返回空。
    Json(json!({"ok": true, "diff": {"added": [], "removed": [], "changed": []}}))
}

#[tracing::instrument(skip_all)]
async fn api_models_probe(
    State(_s): State<AppState>,
    Json(_body): Json<ModelsProbeRequest>,
) -> impl IntoResponse {
    // TODO: 真 probe 需要 backend.stream_chat(...).await,翻译期返回 ok=true stub。
    Json(json!({"ok": true, "result": {"ok": true, "error": null}}))
}

#[tracing::instrument(skip_all)]
async fn api_models_pricing(
    State(_s): State<AppState>,
    Query(_q): Query<ModelQueryParams>,
) -> impl IntoResponse {
    // TODO: 接 pricing catalog;翻译期返回空。
    Json(json!({"ok": true, "pricing": {}}))
}

#[tracing::instrument(skip_all)]
async fn api_models_report(
    State(_s): State<AppState>,
    Query(_q): Query<ModelQueryParams>,
) -> impl IntoResponse {
    Json(json!({"ok": true, "report": {}}))
}

#[tracing::instrument(skip_all)]
async fn api_models_capabilities(
    State(s): State<AppState>,
    Query(q): Query<ModelQueryParams>,
) -> impl IntoResponse {
    let catalog = catalog_snapshot(&s);
    let api_id = q.api_id.unwrap_or_default();
    let model_id = q.model.unwrap_or_default();
    let caps = catalog
        .apis
        .iter()
        .find(|a| a.id == api_id)
        .and_then(|a| a.models.iter().find(|m| m.id == model_id))
        .map(|m| m.capabilities.clone())
        .unwrap_or_default();
    Json(json!({"ok": true, "capabilities": caps}))
}

#[tracing::instrument(skip_all)]
async fn api_models_capability_labels(State(_s): State<AppState>) -> impl IntoResponse {
    Json(json!({
        "ok": true,
        "labels": {
            "text": "文本",
            "streaming": "流式",
            "tools": "工具调用",
            "vision": "视觉",
            "thinking": "扩展思考",
        }
    }))
}
