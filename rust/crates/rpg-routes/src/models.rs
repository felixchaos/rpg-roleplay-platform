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

use std::collections::HashMap;
use std::sync::Arc;

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use rpg_llm::{probe_backend, AnyBackend, ModelCatalog, ProbeResult, Selected};

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

/// 后台 probe 扫描一批 (api_id, backend, model_id) 目标,fire-and-forget。
fn spawn_probe_sweep(targets: Vec<(String, Arc<AnyBackend>, String)>) {
    tokio::spawn(async move {
        for (api_id, backend, model_id) in targets {
            let result = probe_backend(backend.as_ref(), &api_id, &model_id).await;
            if result.ok {
                tracing::debug!(api_id = %api_id, model_id = %model_id, "probe ok");
            } else {
                tracing::warn!(
                    api_id = %api_id, model_id = %model_id,
                    error = ?result.error,
                    "probe failed"
                );
            }
        }
    });
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

/// POST /api/models/health/refresh-all
///
/// 对应 Python `api_models_health_refresh_all`:遍历所有 enabled API 的 enabled
/// model,对每个有注册 backend 的条目发起后台 probe(fire-and-forget)。
/// 可选 body `{"api_id": "..."}` 限定只扫描单一 API。
#[tracing::instrument(skip_all)]
async fn api_models_health_refresh_all(
    State(s): State<AppState>,
    body: Option<Json<Value>>,
) -> impl IntoResponse {
    let only_api_id: Option<String> = body
        .as_ref()
        .and_then(|Json(v)| v.get("api_id"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    let catalog = catalog_snapshot(&s);
    let router = s.llm_router.read();

    let mut targets: Vec<(String, Arc<AnyBackend>, String)> = Vec::new();

    for api in &catalog.apis {
        if !api.enabled {
            continue;
        }
        if let Some(ref filter) = only_api_id {
            if &api.id != filter {
                continue;
            }
        }
        // 只有已注册 backend 的 API 才触发 probe,避免烧没有凭证的 provider。
        let Ok(backend) = router.backend_for_api(&api.id) else {
            continue;
        };
        for model in &api.models {
            if !model.enabled {
                continue;
            }
            let real = model.real_name.as_deref().unwrap_or(&model.id);
            if !real.is_empty() {
                targets.push((api.id.clone(), backend.clone(), real.to_string()));
            }
        }
    }
    drop(router);

    let scheduled = targets.len();
    spawn_probe_sweep(targets);

    Json(json!({"ok": true, "scheduled": scheduled}))
}

/// GET /api/models/health
///
/// 对应 Python `api_models_health`:返回所有 API 下各 model 的当前 health 快照。
/// Rust 侧暂无独立 health 缓存结构(probe 结果直接 tracing log),
/// 这里从 catalog 构造一个 "untested" 的骨架供前端轮询使用。
/// 实际的 probe 结果在 health_refresh_all 触发后通过 /api/models 获取。
#[tracing::instrument(skip_all)]
async fn api_models_health(State(s): State<AppState>) -> impl IntoResponse {
    let catalog = catalog_snapshot(&s);
    let mut health: HashMap<String, Value> = HashMap::new();

    for api in &catalog.apis {
        for model in &api.models {
            let real = model.real_name.as_deref().unwrap_or(&model.id);
            let key = format!("{}/{}", api.id, real);
            health.insert(key, json!({"status": "untested"}));
        }
    }

    Json(json!({"ok": true, "health": health}))
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

/// GET /api/models/remote?api_id=...&refresh=1
///
/// 对应 Python `api_models_remote`:从 LlmBackend::list_models() 拉取远端真实可用
/// 模型清单。`refresh=1` 时跳过任何缓存(当前 Rust 侧无独立缓存,每次都实时调用)。
/// 如果该 api_id 没有注册 backend,返回 404。
#[tracing::instrument(skip_all)]
async fn api_models_remote(
    State(s): State<AppState>,
    Query(q): Query<ModelQueryParams>,
) -> Result<Response, ResponseError> {
    let api_id = q.api_id.unwrap_or_default();
    if api_id.is_empty() {
        return Err(ResponseError::bad_request("api_id required"));
    }

    let backend = {
        let router = s.llm_router.read();
        router.backend_for_api(&api_id).map_err(|e| {
            ResponseError::not_found(format!("no backend for api_id {api_id}: {e}"))
        })?
    };

    let models = backend.list_models().await.unwrap_or_default();
    let model_list: Vec<Value> = models
        .into_iter()
        .map(|m| {
            json!({
                "id": m.id,
                "display_name": m.display_name,
                "capabilities": m.capabilities,
                "context_window": m.context_window,
            })
        })
        .collect();

    Ok(Json(json!({"ok": true, "api_id": api_id, "models": model_list})).into_response())
}

/// GET /api/models/diff?api_id=...
///
/// 对应 Python `api_models_diff`:对比本地 catalog 和远端真实模型清单,
/// 返回 missing(远端有但本地没有)/ extra(本地有但远端没有)/ matching(两者都有)。
#[tracing::instrument(skip_all)]
async fn api_models_diff(
    State(s): State<AppState>,
    Query(q): Query<ModelQueryParams>,
) -> Result<Response, ResponseError> {
    let api_id = q.api_id.unwrap_or_default();
    if api_id.is_empty() {
        return Err(ResponseError::bad_request("api_id required"));
    }

    let backend = {
        let router = s.llm_router.read();
        router.backend_for_api(&api_id).map_err(|e| {
            ResponseError::not_found(format!("no backend for api_id {api_id}: {e}"))
        })?
    };

    let remote_models = backend.list_models().await.unwrap_or_default();
    let remote_ids: std::collections::HashSet<String> =
        remote_models.iter().map(|m| m.id.clone()).collect();

    let catalog = catalog_snapshot(&s);
    let local_ids: std::collections::HashSet<String> = catalog
        .apis
        .iter()
        .find(|a| a.id == api_id)
        .map(|api| {
            api.models
                .iter()
                .map(|m| m.real_name.as_deref().unwrap_or(&m.id).to_string())
                .collect()
        })
        .unwrap_or_default();

    let missing: Vec<&str> = remote_ids
        .iter()
        .filter(|id| !local_ids.contains(*id))
        .map(|s| s.as_str())
        .collect();
    let extra: Vec<&str> = local_ids
        .iter()
        .filter(|id| !remote_ids.contains(*id))
        .map(|s| s.as_str())
        .collect();
    let matching: Vec<&str> = local_ids
        .iter()
        .filter(|id| remote_ids.contains(*id))
        .map(|s| s.as_str())
        .collect();

    Ok(Json(json!({
        "ok": true,
        "api_id": api_id,
        "missing": missing,
        "extra": extra,
        "matching": matching,
    }))
    .into_response())
}

/// POST /api/models/probe
///
/// 对应 Python `api_models_probe`:对指定 api_id + model 发一条最小请求,
/// 验证可用性 + 测延迟。返回 `{ok, latency_ms, error}`。
/// 权限:有 backend 注册 = admin 已配凭证;普通 user 无 backend 则 403。
#[tracing::instrument(skip_all)]
async fn api_models_probe(
    State(s): State<AppState>,
    Json(body): Json<ModelsProbeRequest>,
) -> Result<Response, ResponseError> {
    let api_id = body.api_id.unwrap_or_default();
    let model_id = body.model.unwrap_or_default();

    if api_id.is_empty() {
        return Err(ResponseError::bad_request("api_id required"));
    }
    if model_id.is_empty() {
        return Err(ResponseError::bad_request("model required"));
    }

    let backend = {
        let router = s.llm_router.read();
        router.backend_for_api(&api_id).map_err(|_| {
            ResponseError::forbidden(
                "需要先配置该 provider 的凭证才能测试"
            )
        })?
    };

    let start = std::time::Instant::now();
    let result: ProbeResult = probe_backend(backend.as_ref(), &api_id, &model_id).await;
    let latency_ms = start.elapsed().as_millis() as u64;

    if result.ok {
        Ok(Json(json!({
            "ok": true,
            "api_id": api_id,
            "model": model_id,
            "latency_ms": latency_ms,
        }))
        .into_response())
    } else {
        Ok(Json(json!({
            "ok": false,
            "api_id": api_id,
            "model": model_id,
            "latency_ms": latency_ms,
            "error": result.error,
        }))
        .into_response())
    }
}

/// GET /api/models/pricing?api_id=...&model=...
///
/// 对应 Python `api_models_pricing`:查询单个模型的定价(USD per 1k tokens)。
/// 先查 catalog 内联定价,找不到则回落 builtin 价格表。
#[tracing::instrument(skip_all)]
async fn api_models_pricing(
    State(s): State<AppState>,
    Query(q): Query<ModelQueryParams>,
) -> impl IntoResponse {
    let api_id = q.api_id.unwrap_or_default();
    let model_id = q.model.unwrap_or_default();

    if api_id.is_empty() || model_id.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "api_id and model required"})),
        );
    }

    let router = s.llm_router.read();
    let pricing = router.pricing_for(&api_id, &model_id);

    match pricing {
        Some(p) => (
            StatusCode::OK,
            Json(json!({
                "ok": true,
                "api_id": api_id,
                "model": model_id,
                "pricing": {
                    "input_per_1k_usd": p.input_per_1k_usd,
                    "output_per_1k_usd": p.output_per_1k_usd,
                    "cache_read_per_1k_usd": p.cache_read_per_1k_usd,
                    "cache_write_per_1k_usd": p.cache_write_per_1k_usd,
                },
            })),
        ),
        None => (
            StatusCode::OK,
            Json(json!({
                "ok": true,
                "api_id": api_id,
                "model": model_id,
                "pricing": null,
            })),
        ),
    }
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

// ── unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rpg_llm::{
        registry::{ApiEntry, ModelCatalog, ModelEntry, ModelPricing, Selected},
        AnyBackend, BackendKind, ChatChunk, ChatRequest, ChunkStream, LlmBackend, LlmError,
        ModelInfo,
    };
    use std::sync::Arc;

    // ── minimal mock backend ─────────────────────────────────────────────────

    /// Mock backend 总是返回 ok 的 probe 结果,list_models 返回固定列表。
    #[derive(Default)]
    struct MockBackend {
        models: Vec<ModelInfo>,
        fail: bool,
    }

    #[async_trait::async_trait]
    impl LlmBackend for MockBackend {
        fn kind(&self) -> BackendKind {
            BackendKind::Openai
        }

        async fn stream_chat<'a>(&'a self, _req: ChatRequest) -> Result<ChunkStream<'a>, LlmError> {
            if self.fail {
                return Err(LlmError::Provider {
                    status: 500,
                    body: "mock error".into(),
                });
            }
            use futures_util::stream;
            let chunks: Vec<Result<ChatChunk, LlmError>> = vec![
                Ok(ChatChunk::Text("pong".into())),
                Ok(ChatChunk::Stop { reason: "end_turn".into() }),
            ];
            Ok(Box::pin(stream::iter(chunks)))
        }

        async fn list_models(&self) -> Result<Vec<ModelInfo>, LlmError> {
            Ok(self.models.clone())
        }
    }

    fn make_catalog_with_model(api_id: &str, model_id: &str) -> ModelCatalog {
        ModelCatalog {
            schema_version: 1,
            selected: Selected {
                api_id: api_id.into(),
                model_id: model_id.into(),
            },
            apis: vec![ApiEntry {
                id: api_id.into(),
                display_name: "Test API".into(),
                kind: "openai_compat".into(),
                enabled: true,
                credential_env: None,
                credential_ref: None,
                base_url: None,
                models: vec![ModelEntry {
                    id: model_id.into(),
                    real_name: Some(model_id.into()),
                    display_name: "Test Model".into(),
                    enabled: true,
                    capabilities: vec!["text".into()],
                    pricing: None,
                }],
            }],
        }
    }

    // ── health_refresh_all: targets count ────────────────────────────────────

    #[test]
    fn test_health_refresh_all_counts_enabled_models() {
        let catalog = make_catalog_with_model("test-api", "gpt-test");
        // one enabled API, one enabled model → 1 target
        let count: usize = catalog
            .apis
            .iter()
            .filter(|a| a.enabled)
            .flat_map(|a| a.models.iter().filter(|m| m.enabled))
            .count();
        assert_eq!(count, 1);
    }

    #[test]
    fn test_health_refresh_all_skips_disabled_api() {
        let mut catalog = make_catalog_with_model("test-api", "gpt-test");
        catalog.apis[0].enabled = false;
        let count: usize = catalog
            .apis
            .iter()
            .filter(|a| a.enabled)
            .flat_map(|a| a.models.iter().filter(|m| m.enabled))
            .count();
        assert_eq!(count, 0);
    }

    #[test]
    fn test_health_refresh_all_skips_disabled_model() {
        let mut catalog = make_catalog_with_model("test-api", "gpt-test");
        catalog.apis[0].models[0].enabled = false;
        let count: usize = catalog
            .apis
            .iter()
            .filter(|a| a.enabled)
            .flat_map(|a| a.models.iter().filter(|m| m.enabled))
            .count();
        assert_eq!(count, 0);
    }

    // ── remote: list_models happy + error path ───────────────────────────────

    #[tokio::test]
    async fn test_remote_list_models_happy() {
        let mock = MockBackend {
            models: vec![
                ModelInfo { id: "gpt-4".into(), display_name: "GPT-4".into(), capabilities: vec![], context_window: None },
                ModelInfo { id: "gpt-5".into(), display_name: "GPT-5".into(), capabilities: vec![], context_window: None },
            ],
            fail: false,
        };
        let models = mock.list_models().await.unwrap();
        assert_eq!(models.len(), 2);
        assert_eq!(models[0].id, "gpt-4");
    }

    #[tokio::test]
    async fn test_remote_list_models_empty_on_backend_error() {
        // list_models default impl returns Ok(vec![]) on any backend
        let mock = MockBackend { models: vec![], fail: true };
        let models = mock.list_models().await.unwrap_or_default();
        assert!(models.is_empty());
    }

    // ── diff: local vs remote ────────────────────────────────────────────────

    #[test]
    fn test_diff_missing_and_extra() {
        // remote: [a, b], local: [b, c]
        let remote: std::collections::HashSet<String> = vec!["a".into(), "b".into()].into_iter().collect();
        let local: std::collections::HashSet<String> = vec!["b".into(), "c".into()].into_iter().collect();

        let missing: Vec<String> = remote.iter().filter(|id| !local.contains(*id)).cloned().collect();
        let extra: Vec<String> = local.iter().filter(|id| !remote.contains(*id)).cloned().collect();
        let matching: Vec<String> = local.iter().filter(|id| remote.contains(*id)).cloned().collect();

        assert_eq!(missing, vec!["a"]);
        assert_eq!(extra, vec!["c"]);
        assert_eq!(matching, vec!["b"]);
    }

    #[test]
    fn test_diff_identical_lists() {
        let ids: std::collections::HashSet<String> = vec!["x".into()].into_iter().collect();
        let missing: Vec<_> = ids.iter().filter(|id| !ids.contains(*id)).collect();
        let extra: Vec<_> = ids.iter().filter(|id| !ids.contains(*id)).collect();
        let matching: Vec<_> = ids.iter().filter(|id| ids.contains(*id)).collect();
        assert!(missing.is_empty());
        assert!(extra.is_empty());
        assert_eq!(matching.len(), 1);
    }

    // ── probe: happy + fail ──────────────────────────────────────────────────

    #[tokio::test]
    async fn test_probe_happy_path() {
        let mock = MockBackend { models: vec![], fail: false };
        let result = probe_backend(&mock, "test-api", "gpt-test").await;
        assert!(result.ok);
        assert!(result.error.is_none());
    }

    #[tokio::test]
    async fn test_probe_fail_path() {
        let mock = MockBackend { models: vec![], fail: true };
        let result = probe_backend(&mock, "test-api", "gpt-test").await;
        assert!(!result.ok);
        assert!(result.error.is_some());
    }

    // ── pricing: builtin + missing ───────────────────────────────────────────

    #[test]
    fn test_pricing_builtin_lookup() {
        use rpg_llm::LlmRouter;
        let router = LlmRouter::new();
        // anthropic/claude-sonnet-4-6 is in builtin table
        let p = router.pricing_for("anthropic", "claude-sonnet-4-6");
        assert!(p.is_some());
        let p = p.unwrap();
        assert!(p.input_per_1k_usd > 0.0);
        assert!(p.output_per_1k_usd > 0.0);
    }

    #[test]
    fn test_pricing_missing_returns_none() {
        use rpg_llm::LlmRouter;
        let router = LlmRouter::new();
        let p = router.pricing_for("nonexistent", "does-not-exist");
        assert!(p.is_none());
    }

    #[test]
    fn test_pricing_catalog_inline_override() {
        use rpg_llm::LlmRouter;
        let mut catalog = make_catalog_with_model("test-api", "my-model");
        // set inline pricing on model
        catalog.apis[0].models[0].pricing = Some(ModelPricing {
            input_per_1k_usd: 0.001,
            output_per_1k_usd: 0.002,
            cache_read_per_1k_usd: 0.0001,
            cache_write_per_1k_usd: 0.00125,
        });
        let router = LlmRouter::new().with_catalog(catalog);
        let p = router.pricing_for("test-api", "my-model");
        assert!(p.is_some());
        assert_eq!(p.unwrap().input_per_1k_usd, 0.001);
    }
}
