//! `/api/admin/*` — SMTP 测试 / 部署配置。
//!
//! Python 源: `platform_app/api/platform.py` + `api/settings.py`
//! 端点:
//!   POST /api/admin/smtp/test            — SMTP 连通测试(admin only,stub 返回未配置)
//!   POST /api/admin/deployment-config    — 写部署配置 kv(admin only)
//!   GET  /api/admin/deployment-config    — 读部署配置 kv(admin only)

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::json;

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/admin/smtp/test", post(api_smtp_test))
        .route(
            "/api/admin/deployment-config",
            get(api_deployment_config_get).post(api_deployment_config_set),
        )
}

// ── helpers ───────────────────────────────────────────────────────────────────

/// 检查 admin 身份,不是就返回 Forbidden。
async fn check_admin(state: &AppState, headers: &HeaderMap) -> Result<(), ResponseError> {
    let user = require_user(state, headers).await?;
    if user.role != "admin" {
        return Err(ResponseError::forbidden("仅管理员可访问"));
    }
    Ok(())
}

/// 确保 `app_config` 表存在(handler 内兜底建表;不优雅但够用)。
async fn ensure_app_config_table(state: &AppState) -> Result<(), ResponseError> {
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS app_config (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )",
    )
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(format!("建表失败: {e}")))?;
    Ok(())
}

// ── request types ─────────────────────────────────────────────────────────────

/// POST /api/admin/deployment-config body
#[derive(Debug, Deserialize)]
pub struct DeploymentConfigBody {
    /// 单 kv 写入 —— `{key, value}`
    pub key: Option<String>,
    pub value: Option<serde_json::Value>,
    /// 批量写入 —— `{config: {key: value, ...}}`
    pub config: Option<std::collections::HashMap<String, serde_json::Value>>,
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// POST /api/admin/smtp/test — SMTP 连通测试(stub)
///
/// Python 端会真发测试邮件;Rust 翻译期只返 stub 表示未配置 SMTP。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_smtp_test(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    check_admin(&s, &headers).await?;
    Ok(Json(json!({
        "ok": false,
        "error": "未配置 SMTP",
        "detail": "SMTP stub: 翻译期未接真实邮件发送,请在部署配置中设置 SMTP 参数。"
    }))
    .into_response())
}

/// GET /api/admin/deployment-config — 读取所有部署配置 kv
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_deployment_config_get(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    check_admin(&s, &headers).await?;
    ensure_app_config_table(&s).await?;

    let rows: Vec<(String, String)> = sqlx::query_as(
        "SELECT key, value FROM app_config ORDER BY key",
    )
    .fetch_all(&s.db)
    .await
    .map_err(|e| ResponseError::internal(format!("读取配置失败: {e}")))?;

    let mut config = serde_json::Map::new();
    for (k, v) in rows {
        // value 列存 JSON 字符串;尝试反序列化,失败则当字符串字面量
        let parsed: serde_json::Value = serde_json::from_str(&v).unwrap_or(serde_json::Value::String(v));
        config.insert(k, parsed);
    }

    Ok(Json(json!({
        "ok": true,
        "config": serde_json::Value::Object(config),
    }))
    .into_response())
}

/// POST /api/admin/deployment-config — 写部署配置
///
/// 支持两种 body:
/// 1. `{key, value}` — 单条 upsert
/// 2. `{config: {key: value, ...}}` — 批量 upsert
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_deployment_config_set(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DeploymentConfigBody>,
) -> Result<Response, ResponseError> {
    check_admin(&s, &headers).await?;
    ensure_app_config_table(&s).await?;

    // 组装要写入的 kv 对
    let mut pairs: Vec<(String, String)> = Vec::new();

    if let Some(config) = body.config {
        for (k, v) in config {
            let serialized = serde_json::to_string(&v)
                .map_err(|e| ResponseError::bad_request(format!("序列化失败: {e}")))?;
            pairs.push((k, serialized));
        }
    } else if let (Some(key), Some(value)) = (body.key, body.value) {
        if key.trim().is_empty() {
            return Err(ResponseError::bad_request("key 不能为空"));
        }
        let serialized = serde_json::to_string(&value)
            .map_err(|e| ResponseError::bad_request(format!("序列化失败: {e}")))?;
        pairs.push((key, serialized));
    } else {
        return Err(ResponseError::bad_request(
            "body 需包含 {key, value} 或 {config: {...}}",
        ));
    }

    if pairs.is_empty() {
        return Ok(Json(json!({"ok": true, "updated": 0})).into_response());
    }

    for (k, v) in &pairs {
        sqlx::query(
            "INSERT INTO app_config(key, value, updated_at)
             VALUES ($1, $2, NOW())
             ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        )
        .bind(k)
        .bind(v)
        .execute(&s.db)
        .await
        .map_err(|e| ResponseError::internal(format!("写入配置失败 key={k}: {e}")))?;
    }

    Ok(Json(json!({
        "ok": true,
        "updated": pairs.len(),
    }))
    .into_response())
}
