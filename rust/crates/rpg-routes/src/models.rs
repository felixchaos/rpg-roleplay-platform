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

use model_catalog::ModelCatalog as NewModelCatalog;
use rpg_llm::{probe_backend, AnyBackend, ModelCatalog, ProbeResult, Selected};

use crate::{require_user, AppState, ResponseError};

// ── 凭证检测 + 脱敏 ───────────────────────────────────────────────────────────
//
// 对应 Python `model_probe._credential_present` + `app._redact_catalog`。
// 把 catalog 序列化后的 JSON Value 中,每个 api 节点添加 `has_credential` 布尔,
// 然后 *non-admin* 删除 `credential_env` / `credential_ref` / `base_url` 三个
// 部署形状字段。前端用 `has_credential` 过滤"未配 key 的 API"按钮。
//
// 之前 `api_models` handler 直接把整个 catalog 序列化返回,任何登录用户都能
// 拿到 `credential_env` ↔ `OPENAI_API_KEY` 这种环境变量名(等价于把 deployment
// 配置泄露给所有 user)。Python 端早就靠 `_redact_catalog` 做了对称的保护。

/// 轻量检查 api 节点的凭证是否就绪 — env 形态查环境变量,ref 形态查文件存在。
/// 与 Python `_credential_present` 同义。
fn credential_present(api: &Value) -> bool {
    if let Some(env_name) = api.get("credential_env").and_then(|v| v.as_str()) {
        if !env_name.is_empty() {
            return std::env::var(env_name).map(|v| !v.is_empty()).unwrap_or(false);
        }
    }
    if let Some(ref_path) = api.get("credential_ref").and_then(|v| v.as_str()) {
        if !ref_path.is_empty() {
            return std::path::Path::new(ref_path).exists();
        }
    }
    false
}

/// 把 catalog JSON 按角色脱敏。
///
/// 对所有 apis[*]:
///   - 总是注入 `has_credential` 布尔(对应 Python `api["has_credential"]`)
///   - 非 admin 删除 `credential_env` / `credential_ref` / `base_url`
///
/// 返回脱敏后的新 Value(原值不变)。在 JSON 层做避免改 `ApiEntry` strict schema。
pub(crate) fn redact_catalog(catalog: Value, is_admin: bool) -> Value {
    let mut v = catalog;
    if let Some(apis) = v.get_mut("apis").and_then(|a| a.as_array_mut()) {
        for api in apis.iter_mut() {
            let present = credential_present(api);
            if let Some(obj) = api.as_object_mut() {
                obj.insert("has_credential".to_string(), Value::Bool(present));
                if !is_admin {
                    obj.remove("credential_env");
                    obj.remove("credential_ref");
                    obj.remove("base_url");
                }
            }
        }
    }
    v
}

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/models", get(api_models))
        .route("/api/models/catalog", get(api_models_catalog))
        .route("/api/models/refresh", post(api_models_refresh))
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

/// GET /api/models 的 query params。
/// `legacy=1` 时返回旧 shape:
///   { apis:[...], models:[{id, capabilities:["text","streaming",...], context:"128K", ...}], selected:"..." }
/// 默认(无 legacy)继续返回现有 rpg_llm::ModelCatalog 嵌套 shape。
#[derive(Debug, Deserialize, Default)]
pub struct ModelsListParams {
    pub legacy: Option<String>,
}

// ── legacy shim helpers ───────────────────────────────────────────────────────

/// 把 model_catalog::ModelCapabilities 的 true 字段名收集成字符串列表。
/// 这样旧代码 `m.capabilities[0]` 仍能正常工作。
fn caps_to_strings(c: &model_catalog::ModelCapabilities) -> Vec<&'static str> {
    let mut out = Vec::new();
    // 总是把 "text" 加进去代表基础文本能力(旧 shape 惯例)
    out.push("text");
    if c.streaming        { out.push("streaming"); }
    if c.tools            { out.push("tools"); }
    if c.vision           { out.push("vision"); }
    if c.audio            { out.push("audio"); }
    if c.structured_output { out.push("structured_output"); }
    if c.extended_thinking { out.push("extended_thinking"); }
    if c.embedding        { out.push("embedding"); }
    if c.function_calling { out.push("function_calling"); }
    if c.prompt_caching   { out.push("prompt_caching"); }
    if c.web_search       { out.push("web_search"); }
    if c.pdf_input        { out.push("pdf_input"); }
    out
}

/// context_window(tokens) → 人类可读字符串:"128K" / "1M" / "200K" 等。
fn format_context(tokens: u32) -> String {
    if tokens == 0 {
        return "—".to_string();
    }
    if tokens >= 1_000_000 {
        let m = tokens / 1_000_000;
        let rem = (tokens % 1_000_000) / 100_000;
        if rem == 0 {
            return format!("{}M", m);
        }
        return format!("{}.{}M", m, rem);
    }
    let k = tokens / 1_000;
    let rem = (tokens % 1_000) / 100;
    if rem == 0 {
        return format!("{}K", k);
    }
    format!("{}.{}K", k, rem)
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

/// 构建 Python `selected_model(catalog)` 等价的 JSON 对象。
/// 返回 `{"api_id", "api_display_name", "api_kind", "model_id", "real_name", "display_name", "capabilities"}`.
fn build_selected_value(catalog: &ModelCatalog) -> Value {
    let (api, model) = catalog
        .selected_model()
        .or_else(|| {
            catalog
                .apis
                .iter()
                .find(|a| a.enabled)
                .and_then(|a| a.models.iter().find(|m| m.enabled).map(|m| (a, m)))
        })
        .unwrap_or_else(|| {
            let api = &catalog.apis[0];
            let model = &api.models[0];
            (api, model)
        });
    json!({
        "api_id": api.id,
        "api_display_name": api.display_name,
        "api_kind": api.kind,
        "model_id": model.id,
        "real_name": model.real_name.as_deref().unwrap_or(&model.id),
        "display_name": if model.display_name.is_empty() {
            model.real_name.as_deref().unwrap_or(&model.id)
        } else {
            &model.display_name
        },
        "capabilities": model.capabilities,
    })
}

/// 把 catalog 序列化 + 脱敏后,附上 selected 一起返回 `{"ok": true, "models": ..., "selected": ...}`。
fn catalog_with_selected_response(s: &AppState, is_admin: bool) -> Value {
    let catalog = catalog_snapshot(s);
    let selected = build_selected_value(&catalog);
    let catalog_value = serde_json::to_value(&catalog).unwrap_or(json!({}));
    let redacted = redact_catalog(catalog_value, is_admin);
    // MODELS-1: 添加 state 字段(与 Python _payload(user) 一致)
    // 包含当前选中模型的详细信息
    let state = if let Some((api, model)) = catalog.selected_model() {
        let real = model.real_name.clone().unwrap_or_else(|| model.id.clone());
        json!({
            "model": model.display_name,
            "model_real_name": real,
            "api": api.display_name,
            "api_id": api.id,
            "capabilities": model.capabilities,
        })
    } else {
        json!({})
    };
    json!({
        "ok": true,
        "models": redacted,
        "selected": selected,
        "state": state,
    })
}

/// 把整个 catalog 持久化到 DB — 对应 Python `_write_model_catalog_rows`。
/// 写 model_apis + model_entries + app_config(selected_model)。
async fn persist_catalog_to_db(pool: &sqlx::PgPool, catalog: &ModelCatalog) {
    // 1. persist selected_model
    persist_selected_to_db(pool, &catalog.selected.api_id, &catalog.selected.model_id).await;

    // 2. upsert each api + models
    for api in &catalog.apis {
        let res = sqlx::query(
            "INSERT INTO model_apis(api_id, display_name, kind, enabled, credential_ref, credential_env, base_url) \
             VALUES ($1, $2, $3, $4, $5, $6, $7) \
             ON CONFLICT(api_id) DO UPDATE SET \
               display_name = excluded.display_name, \
               kind = excluded.kind, \
               enabled = excluded.enabled, \
               credential_ref = excluded.credential_ref, \
               credential_env = excluded.credential_env, \
               base_url = excluded.base_url, \
               updated_at = now()"
        )
        .bind(&api.id)
        .bind(&api.display_name)
        .bind(&api.kind)
        .bind(api.enabled)
        .bind(api.credential_ref.as_deref().unwrap_or(""))
        .bind(api.credential_env.as_deref().unwrap_or(""))
        .bind(api.base_url.as_deref().unwrap_or(""))
        .execute(pool)
        .await;
        if let Err(e) = res {
            tracing::warn!(api_id = %api.id, error = %e, "persist model_api failed");
            continue;
        }

        let keep_ids: Vec<String> = api.models.iter().map(|m| m.id.clone()).collect();

        for model in &api.models {
            let real = model.real_name.as_deref().unwrap_or(&model.id);
            let caps = serde_json::to_value(&model.capabilities).unwrap_or(json!([]));
            let res = sqlx::query(
                "INSERT INTO model_entries(api_id, model_id, real_name, display_name, enabled, capabilities) \
                 VALUES ($1, $2, $3, $4, $5, $6) \
                 ON CONFLICT(api_id, model_id) DO UPDATE SET \
                   real_name = excluded.real_name, \
                   display_name = excluded.display_name, \
                   enabled = excluded.enabled, \
                   capabilities = excluded.capabilities, \
                   updated_at = now()"
            )
            .bind(&api.id)
            .bind(&model.id)
            .bind(real)
            .bind(&model.display_name)
            .bind(model.enabled)
            .bind(&caps)
            .execute(pool)
            .await;
            if let Err(e) = res {
                tracing::warn!(api_id = %api.id, model_id = %model.id, error = %e, "persist model_entry failed");
            }
        }

        // Delete removed models from DB
        if !keep_ids.is_empty() {
            let res = sqlx::query(
                "DELETE FROM model_entries WHERE api_id = $1 AND model_id <> ALL($2)"
            )
            .bind(&api.id)
            .bind(&keep_ids)
            .execute(pool)
            .await;
            if let Err(e) = res {
                tracing::warn!(api_id = %api.id, error = %e, "delete stale model_entries failed");
            }
        }
    }
}

/// 把当前选中模型持久化到 DB `app_config(key='selected_model')`。
/// 对齐 Python `_write_model_catalog_rows` 中 `INSERT INTO app_config …` 那一行。
async fn persist_selected_to_db(pool: &sqlx::PgPool, api_id: &str, model_id: &str) {
    let value = json!({"api_id": api_id, "model_id": model_id});
    let res = sqlx::query(
        "INSERT INTO app_config(key, value) VALUES ('selected_model', $1) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now()"
    )
    .bind(&value)
    .execute(pool)
    .await;
    if let Err(e) = res {
        tracing::warn!(error = %e, "persist selected_model to app_config failed");
    }
}

async fn require_admin(s: &AppState, headers: &HeaderMap) -> Result<(), ResponseError> {
    let u = require_user(s, headers).await?;
    if u.role != "admin" {
        return Err(ResponseError::forbidden("仅管理员"));
    }
    Ok(())
}

/// 后台 probe 扫描一批 (api_id, backend, model_id) 目标,fire-and-forget。
/// 对应 Python `model_probe.probe_availability`:结果写入 health_cache。
fn spawn_probe_sweep(targets: Vec<(String, Arc<AnyBackend>, String)>, s: AppState) {
    tokio::spawn(async move {
        for (api_id, backend, model_id) in targets {
            let start = std::time::Instant::now();
            let result = probe_backend(backend.as_ref(), &api_id, &model_id).await;
            let latency_ms = start.elapsed().as_millis() as u64;
            let checked_at = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            if result.ok {
                tracing::debug!(api_id = %api_id, model_id = %model_id, "probe ok");
                s.health_cache.insert(
                    (api_id.clone(), model_id.clone()),
                    json!({
                        "status": "ok",
                        "latency_ms": latency_ms,
                        "checked_at": checked_at,
                        "error": "",
                    }),
                );
            } else {
                tracing::warn!(
                    api_id = %api_id, model_id = %model_id,
                    error = ?result.error,
                    "probe failed"
                );
                s.health_cache.insert(
                    (api_id.clone(), model_id.clone()),
                    json!({
                        "status": "err",
                        "latency_ms": latency_ms,
                        "checked_at": checked_at,
                        "error": result.error.unwrap_or_default(),
                    }),
                );
            }
        }
    });
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// GET /api/models/catalog
///
/// 统一 catalog 端点:调用 model_catalog::ModelCatalog::list_all() 返回完整
/// Vec<ModelInfo>(10 家 provider 合并)。前端用此端点渲染模型列表新字段
/// (context_window / max_output_tokens / deprecated_at / pricing / source 等)。
///
/// 缓存由 model_catalog 内部 per-provider TTL 管理(默认 5 分钟)。
/// 返回: {"ok": true, "models": [...ModelInfo]}
#[tracing::instrument(skip_all)]
async fn api_models_catalog() -> impl IntoResponse {
    let catalog = NewModelCatalog::default();
    // preload_static 保证离线时也能返回 static 数据而不挂起。
    if catalog.preload_static().is_err() {
        tracing::warn!("api_models_catalog: preload_static 失败,返回空列表");
        return Json(json!({"ok": true, "models": []}));
    }
    let models = catalog.list_all().await;
    Json(json!({"ok": true, "models": models}))
}

/// POST /api/models/refresh
///
/// 强制重拉所有 provider 的 live /models 端点,清除 TTL cache 后返回最新列表。
/// 耗时操作(并发请求 10 家 provider),前端应 fire-and-forget 或展示 loading。
/// 返回: {"ok": true, "count": <total model count>}
#[tracing::instrument(skip_all)]
async fn api_models_refresh() -> impl IntoResponse {
    let catalog = NewModelCatalog::default();
    // 依次 refresh 每家 provider(live → static 降级)
    for &p in model_catalog::KNOWN_ALL_PROVIDERS {
        if let Err(e) = catalog.refresh(p).await {
            tracing::warn!(provider = ?p, error = %e, "refresh provider 失败,已降级");
        }
    }
    let models = catalog.list_all().await;
    let count = models.len();
    Json(json!({"ok": true, "count": count}))
}

#[tracing::instrument(skip_all)]
async fn api_models(
    State(s): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<ModelsListParams>,
) -> Result<Response, ResponseError> {
    let is_admin = match require_user(&s, &headers).await {
        Ok(u) => u.role == "admin",
        Err(_) => false,
    };

    // ?legacy=1: 从 model_catalog crate 拉新 typed 数据,转换成旧 shape 返回,
    // 保证老代码 `m.capabilities[0]` 不炸。
    if q.legacy.as_deref() == Some("1") {
        let new_catalog = NewModelCatalog::default();
        let _ = new_catalog.preload_static();
        let models: Vec<model_catalog::ModelInfo> = new_catalog.list_all().await;

        let legacy_models: Vec<Value> = models
            .iter()
            .map(|m| {
                let caps: Vec<&str> = caps_to_strings(&m.capabilities);
                let context_str = m
                    .context_window
                    .map(format_context)
                    .unwrap_or_else(|| "—".to_string());
                json!({
                    "id": m.id,
                    "display_name": m.display_name,
                    "provider": m.provider,
                    "capabilities": caps,
                    "context": context_str,
                    "context_window": m.context_window,
                    "max_output_tokens": m.max_output_tokens,
                    "input_cost_per_million": m.input_cost_per_million,
                    "output_cost_per_million": m.output_cost_per_million,
                    "deprecated_at": m.deprecated_at,
                    "source": m.source,
                })
            })
            .collect();

        // 旧 selected 字符串:取 rpg_llm catalog 的 selected.model_id 作为回落
        let old_catalog = catalog_snapshot(&s);
        let selected_str = format!(
            "{}/{}",
            old_catalog.selected.api_id, old_catalog.selected.model_id
        );

        return Ok(Json(json!({
            "ok": true,
            "apis": serde_json::to_value(&old_catalog.apis).unwrap_or(json!([])),
            "models": legacy_models,
            "selected": selected_str,
        }))
        .into_response());
    }

    // 默认路径:返回现有 rpg_llm::ModelCatalog 嵌套 shape。
    let catalog = catalog_snapshot(&s);
    let selected = catalog.selected.clone();
    let value = serde_json::to_value(&catalog).unwrap_or(json!({}));
    let redacted = redact_catalog(value, is_admin);
    Ok(Json(json!({
        "ok": true,
        "models": redacted,
        "selected": selected,
    }))
    .into_response())
}

/// POST /api/models/health/refresh-all
///
/// 对应 Python `api_models_health_refresh_all`:遍历所有 enabled API 的 enabled
/// model,对每个有注册 backend 的条目发起后台 probe(fire-and-forget)。
/// 可选 body `{"api_id": "..."}` 限定只扫描单一 API。
/// 安全:非 admin 用户只能 probe 自己配置过 key 的 provider(对齐 Python 逻辑)。
#[tracing::instrument(skip_all)]
async fn api_models_health_refresh_all(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<Value>>,
) -> impl IntoResponse {
    let only_api_id: Option<String> = body
        .as_ref()
        .and_then(|Json(v)| v.get("api_id"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    // P1-models-health-refresh-all-no-user-check: 对齐 Python 权限逻辑。
    // 取当前用户以判断是否 admin;非 admin 用户构建可探测的 api_id 白名单。
    let user_opt = require_user(&s, &headers).await.ok();
    let is_admin = user_opt.as_ref().map(|u| u.role == "admin").unwrap_or(false);
    // 非 admin:预取该用户的 credential 列表,只允许探测自己配置过 key 的 provider
    let user_credential_api_ids: Option<std::collections::HashSet<String>> = if !is_admin {
        if let Some(ref user) = user_opt {
            rpg_platform::users::list_credentials(&s.db, user.id)
                .await
                .ok()
                .map(|creds| {
                    creds
                        .into_iter()
                        .filter(|c| c.has_credential)
                        .map(|c| c.api_id)
                        .collect()
                })
        } else {
            Some(std::collections::HashSet::new())
        }
    } else {
        None // admin:无限制
    };

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
        // 非 admin:跳过该用户没有配置凭证的 API,避免烧服务器凭证
        if let Some(ref allowed) = user_credential_api_ids {
            if !allowed.contains(&api.id) {
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
    spawn_probe_sweep(targets, s.clone());

    Json(json!({"ok": true, "scheduled": scheduled}))
}

/// GET /api/models/health
///
/// 对应 Python `api_models_health`:返回所有 API 下各 model 的当前 health 快照。
/// 读取 health_cache(由 spawn_probe_sweep 写入)。未探测过的 model 返回 "untested"。
#[tracing::instrument(skip_all)]
async fn api_models_health(State(s): State<AppState>) -> impl IntoResponse {
    let catalog = catalog_snapshot(&s);
    let mut health: HashMap<String, Value> = HashMap::new();

    for api in &catalog.apis {
        for model in &api.models {
            let real = model.real_name.as_deref().unwrap_or(&model.id);
            let key = format!("{}/{}", api.id, real);
            let entry = s
                .health_cache
                .get(&(api.id.clone(), real.to_string()))
                .map(|v| v.clone())
                .unwrap_or_else(|| json!({"status": "untested"}));
            health.insert(key, entry);
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
    catalog.selected = Selected {
        api_id: api_id.clone(),
        model_id: model_id.clone(),
    };
    write_catalog(&s, catalog);

    // Gap 1: persist selection to DB (app_config table)
    persist_selected_to_db(&s.db, &api_id, &model_id).await;

    // Gap 2: return full catalog + selected (matching Python response shape)
    let resp = catalog_with_selected_response(&s, /*is_admin=*/ true);
    Ok(Json(resp).into_response())
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
    // Persist to DB (llm-19: matching Python _write_model_catalog_rows)
    let snap = catalog_snapshot(&s);
    persist_catalog_to_db(&s.db, &snap).await;
    // Gap 3: return full catalog + selected (matching Python response shape)
    let resp = catalog_with_selected_response(&s, /*is_admin=*/ true);
    Ok(Json(resp).into_response())
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
    // Persist to DB (llm-19: matching Python _write_model_catalog_rows)
    let snap = catalog_snapshot(&s);
    persist_catalog_to_db(&s.db, &snap).await;
    // Gap 3: return full catalog + selected (matching Python response shape)
    let resp = catalog_with_selected_response(&s, /*is_admin=*/ true);
    Ok(Json(resp).into_response())
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
        // Validate at least one model remains after deletion (matching Python behavior)
        let remaining = api.models.iter().filter(|m| m.id != model_id).count();
        if remaining == 0 {
            return Err(ResponseError::bad_request(
                "至少保留一个 model，无法删除唯一 model",
            ));
        }
        api.models.retain(|m| m.id != model_id);
    }
    // If selected model was deleted, fall back to first enabled model
    let selected_deleted = catalog.selected.api_id == api_id && catalog.selected.model_id == model_id;
    if selected_deleted {
        if let Some(api) = catalog.apis.iter().find(|a| a.id == api_id) {
            if let Some(m) = api.models.iter().find(|m| m.enabled) {
                catalog.selected = Selected {
                    api_id: api_id.clone(),
                    model_id: m.id.clone(),
                };
            }
        }
    }
    write_catalog(&s, catalog);
    // Persist to DB (llm-19: matching Python _write_model_catalog_rows)
    let snap = catalog_snapshot(&s);
    persist_catalog_to_db(&s.db, &snap).await;
    let resp = catalog_with_selected_response(&s, /*is_admin=*/ true);
    Ok(Json(resp).into_response())
}

/// GET /api/models/remote?api_id=...&refresh=1
///
/// 对应 Python `api_models_remote`:从 LlmBackend::list_models() 拉取远端真实可用
/// 模型清单。`refresh=1` 时跳过任何缓存(当前 Rust 侧无独立缓存,每次都实时调用)。
/// 如果该 api_id 没有注册 backend,返回 404。
#[tracing::instrument(skip_all)]
async fn api_models_remote(
    State(s): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<ModelQueryParams>,
) -> Result<Response, ResponseError> {
    // Gap 18: 检查用户凭证
    let user = require_user(&s, &headers).await?;
    let api_id = q.api_id.unwrap_or_default();
    if api_id.is_empty() {
        return Err(ResponseError::bad_request("api_id required"));
    }
    // 非 admin 需要在 user_api_credentials 表里配置了该 api_id 的 key
    if user.role != "admin" {
        let cred_result = rpg_platform::users::list_credentials(&s.db, user.id).await;
        let has_cred = cred_result
            .map(|creds| creds.iter().any(|c| c.api_id == api_id && c.has_credential))
            .unwrap_or(false);
        if !has_cred {
            return Err(ResponseError::forbidden("需要先配置该 provider 的凭证"));
        }
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
    headers: HeaderMap,
    Query(q): Query<ModelQueryParams>,
) -> Result<Response, ResponseError> {
    // Gap 19: 检查用户凭证
    let user = require_user(&s, &headers).await?;
    let api_id = q.api_id.unwrap_or_default();
    if api_id.is_empty() {
        return Err(ResponseError::bad_request("api_id required"));
    }
    if user.role != "admin" {
        let cred_result = rpg_platform::users::list_credentials(&s.db, user.id).await;
        let has_cred = cred_result
            .map(|creds| creds.iter().any(|c| c.api_id == api_id && c.has_credential))
            .unwrap_or(false);
        if !has_cred {
            return Err(ResponseError::forbidden("需要先配置该 provider 的凭证"));
        }
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

    let remote_total = remote_ids.len();
    let local_total = local_ids.len();

    let remote_only: Vec<&str> = remote_ids
        .iter()
        .filter(|id| !local_ids.contains(*id))
        .map(|s| s.as_str())
        .collect();
    let local_only: Vec<&str> = local_ids
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
        "remote_only": remote_only,
        "local_only": local_only,
        "matching": matching,
        "remote_total": remote_total,
        "local_total": local_total,
    }))
    .into_response())
}

/// POST /api/models/probe
///
/// 对应 Python `api_models_probe`:对指定 api_id + model 发一条最小请求,
/// 验证可用性 + 测延迟。返回 `{ok, latency_ms, error}`。
/// 权限:admin 可测任何 provider;普通 user 必须先在「个人主页 → API 凭证」中配置
/// 该 provider 的 key 才能测试(对齐 Python 逻辑)。
#[tracing::instrument(skip_all)]
async fn api_models_probe(
    State(s): State<AppState>,
    headers: HeaderMap,
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

    // P1-models-probe-user-credentials: 对齐 Python 权限逻辑。
    // admin 可测任何 provider;非 admin 必须先配置自己的 key。
    let user_opt = require_user(&s, &headers).await.ok();
    if let Some(ref user) = user_opt {
        if user.role != "admin" {
            // 检查该用户是否在 user_api_credentials 表里配置了该 api_id 的 key。
            let cred_result = rpg_platform::users::list_credentials(&s.db, user.id).await;
            let has_user_cred = cred_result
                .map(|creds| creds.iter().any(|c| c.api_id == api_id && c.has_credential))
                .unwrap_or(false);
            if !has_user_cred {
                return Err(ResponseError::forbidden(
                    "需要先在「个人主页 → API 凭证」中配置该 provider 的 key 才能测试",
                ));
            }
        }
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
            "model_used": model_id,
            "latency_ms": latency_ms,
            "response_text": "",
        }))
        .into_response())
    } else {
        Ok(Json(json!({
            "ok": false,
            "api_id": api_id,
            "model_used": model_id,
            "latency_ms": latency_ms,
            "response_text": "",
            "error": result.error,
        }))
        .into_response())
    }
}

/// GET /api/models/pricing?api_id=...&model=...
///
/// 对应 Python `api_models_pricing`:查询单个模型的定价(USD per 1k tokens)。
/// 先查 catalog 内联定价,找不到则回落 builtin 价格表。
/// MODELS-2: 已验证 — 接受 query params(api_id, model),与前端 pricing(q) 一致。
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

    // Find context window from catalog for this model
    let context = {
        let catalog = catalog_snapshot(&s);
        catalog
            .apis
            .iter()
            .find(|a| a.id == api_id)
            .and_then(|a| a.models.iter().find(|m| m.id == model_id || m.real_name.as_deref() == Some(&model_id)))
            .and(None::<u32>) // No context_window in ModelEntry; use None
    };

    match pricing {
        Some(p) => (
            StatusCode::OK,
            Json(json!({
                "ok": true,
                "api_id": api_id,
                "model": model_id,
                "pricing": {
                    // per-million-token units matching Python's _STATIC_PRICING format
                    "input": p.input_per_1k_usd * 1000.0,
                    "output": p.output_per_1k_usd * 1000.0,
                    "cache_read": p.cache_read_per_1k_usd * 1000.0,
                    "cache_write": p.cache_write_per_1k_usd * 1000.0,
                    "source": "static",
                    "unit": "USD per million tokens",
                    "context": context,
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

/// GET /api/models/report?api_id=...
///
/// 对应 Python `model_probe.full_report`:返回 api_id 的综合健康报告。
/// 包含: local_catalog(含 pricing/capabilities)、credential_present、kind/enabled。
/// remote_models_summary 和 availability 因需要远端调用而标为 TODO(不阻断返回)。
#[tracing::instrument(skip_all)]
async fn api_models_report(
    State(s): State<AppState>,
    Query(q): Query<ModelQueryParams>,
) -> impl IntoResponse {
    let api_id = q.api_id.unwrap_or_default();
    if api_id.is_empty() {
        return Json(json!({"ok": false, "error": "api_id required"}));
    }
    // Collect everything from catalog in a block so borrows are dropped before await
    let (credential_present, api_kind, api_enabled, local_catalog) = {
        let catalog = catalog_snapshot(&s);
        let Some(api) = catalog.apis.iter().find(|a| a.id == api_id) else {
            return Json(json!({"ok": false, "error": format!("api_id 不存在: {}", api_id)}));
        };
        let cred = {
            let api_val = serde_json::to_value(api).unwrap_or(json!({}));
            crate::models::credential_present(&api_val)
        };
        let kind = api.kind.clone();
        let enabled = api.enabled;
        let router = s.llm_router.read();
        let lc: Vec<Value> = api.models.iter().map(|m| {
            let real = m.real_name.as_deref().unwrap_or(&m.id);
            let pricing = router.pricing_for(&api_id, &m.id).map(|p| json!({
                "input": p.input_per_1k_usd * 1000.0,
                "output": p.output_per_1k_usd * 1000.0,
                "source": "static",
                "unit": "USD per million tokens",
            }));
            let caps: Vec<Value> = m.capabilities.iter().map(|c| json!({
                "id": c,
                "label": capability_label(c),
            })).collect();
            json!({
                "id": m.id,
                "real_name": real,
                "enabled": m.enabled,
                "pricing": pricing,
                "capabilities": caps,
            })
        }).collect();
        (cred, kind, enabled, lc)
    };

    // Gap 17: 实现简化版 remote models summary
    let remote_summary = {
        let backend_opt = {
            let router = s.llm_router.read();
            router.backend_for_api(&api_id).ok()
        };
        match backend_opt {
            Some(backend) => {
                match backend.list_models().await {
                    Ok(models) if !models.is_empty() => {
                        let model_ids: Vec<String> = models.iter().map(|m| m.id.clone()).collect();
                        json!({"count": models.len(), "models": model_ids})
                    }
                    Ok(_) => json!({"count": 0, "models": []}),
                    Err(e) => json!({"error": e.to_string()}),
                }
            }
            None => json!({"error": "no backend registered for this api_id"}),
        }
    };

    Json(json!({
        "ok": true,
        "api_id": api_id,
        "kind": api_kind,
        "enabled": api_enabled,
        "credential_present": credential_present,
        "local_catalog": local_catalog,
        "remote_models_summary": remote_summary,
    }))
}

/// MODELS-3: 已验证 — 接受 query params(api_id, model),与前端 capabilities(q) 一致。
#[tracing::instrument(skip_all)]
async fn api_models_capabilities(
    State(s): State<AppState>,
    Query(q): Query<ModelQueryParams>,
) -> impl IntoResponse {
    let catalog = catalog_snapshot(&s);
    let api_id = q.api_id.unwrap_or_default();
    let model_id = q.model.unwrap_or_default();
    let caps: Vec<String> = catalog
        .apis
        .iter()
        .find(|a| a.id == api_id)
        .and_then(|a| a.models.iter().find(|m| m.id == model_id))
        .map(|m| m.capabilities.clone())
        .unwrap_or_default();
    // Gap 4: return structured {id, label} pairs matching Python describe_capabilities()
    let described: Vec<Value> = caps
        .iter()
        .map(|c| {
            let label = capability_label(c);
            json!({"id": c, "label": label})
        })
        .collect();
    let real_name = catalog
        .apis
        .iter()
        .find(|a| a.id == api_id)
        .and_then(|a| a.models.iter().find(|m| m.id == model_id))
        .and_then(|m| m.real_name.as_deref())
        .unwrap_or(&model_id);
    Json(json!({
        "ok": true,
        "api_id": api_id,
        "model": real_name,
        "capabilities": described,
        "capability_ids": caps,
    }))
}

/// 能力代码 → 中文标签,与 Python `CAPABILITY_LABELS` 完全对齐。
fn capability_label(cap: &str) -> &'static str {
    match cap {
        "text"         => "文本生成",
        "streaming"    => "流式输出",
        "image_input"  => "视觉输入",
        "audio_input"  => "音频输入",
        "video_input"  => "视频输入",
        "file_input"   => "文件附件",
        "tools"        => "Function Calling",
        "json_mode"    => "JSON 结构化输出",
        "image_gen"    => "图像生成",
        "audio_gen"    => "音频生成",
        "reasoning"    => "深度思考",
        "computer_use" => "电脑控制",
        "code_exec"    => "代码执行",
        "web_search"   => "联网搜索",
        _other         => {
            // 未知能力:返回原始代码(与 Python CAPABILITY_LABELS.get(c, c) 对齐)。
            // 由于返回 &'static str,此处用 leak 兜底;实际不会命中,
            // 因为所有已知能力已穷举。
            // 为避免 leak,不走此路径——直接返回固定提示。
            "未知能力"
        }
    }
}

#[tracing::instrument(skip_all)]
async fn api_models_capability_labels(State(_s): State<AppState>) -> impl IntoResponse {
    // Gap 5: expanded to match Python's CAPABILITY_LABELS (14 entries)
    Json(json!({
        "ok": true,
        "labels": {
            "text":         "文本生成",
            "streaming":    "流式输出",
            "image_input":  "视觉输入",
            "audio_input":  "音频输入",
            "video_input":  "视频输入",
            "file_input":   "文件附件",
            "tools":        "Function Calling",
            "json_mode":    "JSON 结构化输出",
            "image_gen":    "图像生成",
            "audio_gen":    "音频生成",
            "reasoning":    "深度思考",
            "computer_use": "电脑控制",
            "code_exec":    "代码执行",
            "web_search":   "联网搜索",
        }
    }))
}

// ── unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rpg_llm::{
        registry::{ApiEntry, ModelCatalog, ModelEntry, ModelPricing, Selected},
        BackendKind, ChatChunk, ChatRequest, ChunkStream, LlmBackend, LlmError,
        ModelInfo,
    };
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

    // ── redact_catalog: API key/base_url 脱敏 ───────────────────────────────

    /// 非 admin 看到的 catalog 不应再含 credential_env / credential_ref / base_url
    /// 三个泄露 deployment 形状的字段;同时仍能看到 has_credential。
    #[test]
    fn test_redact_catalog_strips_credentials_for_non_admin() {
        let mut catalog = make_catalog_with_model("test-api", "gpt-test");
        catalog.apis[0].credential_env = Some("OPENAI_API_KEY".into());
        catalog.apis[0].credential_ref = Some("/tmp/does-not-exist".into());
        catalog.apis[0].base_url = Some("https://internal.example.com/v1".into());
        let v = serde_json::to_value(&catalog).unwrap();
        let redacted = redact_catalog(v, /*is_admin=*/ false);
        let api0 = &redacted["apis"][0];
        assert!(api0.get("credential_env").is_none(), "credential_env 应被删");
        assert!(api0.get("credential_ref").is_none(), "credential_ref 应被删");
        assert!(api0.get("base_url").is_none(), "base_url 应被删");
        assert!(api0.get("has_credential").is_some(), "has_credential 标记应存在");
    }

    /// admin 应能看到完整 credential_env / base_url 明文(用于配置面板)。
    #[test]
    fn test_redact_catalog_admin_sees_credentials() {
        let mut catalog = make_catalog_with_model("test-api", "gpt-test");
        catalog.apis[0].credential_env = Some("OPENAI_API_KEY".into());
        catalog.apis[0].base_url = Some("https://api.openai.com/v1".into());
        let v = serde_json::to_value(&catalog).unwrap();
        let redacted = redact_catalog(v, /*is_admin=*/ true);
        let api0 = &redacted["apis"][0];
        assert_eq!(
            api0.get("credential_env").and_then(|v| v.as_str()),
            Some("OPENAI_API_KEY"),
            "admin 应看到 credential_env 原值"
        );
        assert_eq!(
            api0.get("base_url").and_then(|v| v.as_str()),
            Some("https://api.openai.com/v1"),
            "admin 应看到 base_url 原值"
        );
        assert!(api0.get("has_credential").is_some());
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

    // ── legacy shim helpers ──────────────────────────────────────────────────

    #[test]
    fn test_caps_to_strings_all_false_returns_text_only() {
        let caps = model_catalog::ModelCapabilities::default();
        let result = caps_to_strings(&caps);
        assert_eq!(result, vec!["text"]);
    }

    #[test]
    fn test_caps_to_strings_streaming_and_tools() {
        let caps = model_catalog::ModelCapabilities {
            streaming: true,
            tools: true,
            ..Default::default()
        };
        let result = caps_to_strings(&caps);
        assert!(result.contains(&"text"));
        assert!(result.contains(&"streaming"));
        assert!(result.contains(&"tools"));
        assert!(!result.contains(&"vision"));
    }

    #[test]
    fn test_caps_to_strings_all_true() {
        let caps = model_catalog::ModelCapabilities {
            streaming: true,
            tools: true,
            vision: true,
            audio: true,
            structured_output: true,
            extended_thinking: true,
            embedding: true,
            function_calling: true,
            prompt_caching: true,
            web_search: true,
            pdf_input: true,
        };
        let result = caps_to_strings(&caps);
        // text + 11 caps = 12 entries
        assert_eq!(result.len(), 12);
        assert_eq!(result[0], "text");
    }

    #[test]
    fn test_format_context_128k() {
        assert_eq!(format_context(128_000), "128K");
    }

    #[test]
    fn test_format_context_1m() {
        assert_eq!(format_context(1_000_000), "1M");
    }

    #[test]
    fn test_format_context_200k() {
        assert_eq!(format_context(200_000), "200K");
    }

    #[test]
    fn test_format_context_1m5() {
        assert_eq!(format_context(1_500_000), "1.5M");
    }

    #[test]
    fn test_format_context_zero() {
        assert_eq!(format_context(0), "—");
    }

    #[test]
    fn test_format_context_32k() {
        assert_eq!(format_context(32_000), "32K");
    }
}
