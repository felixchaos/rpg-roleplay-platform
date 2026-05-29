//! `/api/settings` — 当前用户 settings read/write。
//!
//! Python 源: `rpg/platform_app/api/settings.py` (20 行)
//! 表: `settings(user_id BIGINT, key TEXT, value JSONB, updated_at TIMESTAMPTZ)`
//! 端点:
//!   GET  /api/settings — 当前用户全部 settings(返回 map)
//!   POST /api/settings — 写 setting,body `{key, value}` 或 `{settings: {...}}`

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::json;

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/settings", get(api_settings_get).post(api_settings_set))
}

// ── request types ─────────────────────────────────────────────────────────────

/// POST /api/settings body — 支持两种写法
#[derive(Debug, Deserialize)]
pub struct SettingsSetBody {
    /// 单条写入
    pub key: Option<String>,
    pub value: Option<serde_json::Value>,
    /// 批量写入 `{settings: {key: value, ...}}`
    pub settings: Option<std::collections::HashMap<String, serde_json::Value>>,
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// GET /api/settings — 返回当前用户的全部 settings kv
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_settings_get(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));

    let rows: Vec<(String, serde_json::Value)> = sqlx::query_as(
        "SELECT key, value FROM settings WHERE user_id = $1 ORDER BY key",
    )
    .bind(user.id)
    .fetch_all(&s.db)
    .await
    .map_err(|e| ResponseError::internal(format!("读取 settings 失败: {e}")))?;

    let mut map = serde_json::Map::new();
    for (k, v) in rows {
        map.insert(k, v);
    }

    Ok(Json(json!({
        "ok": true,
        "settings": serde_json::Value::Object(map),
    }))
    .into_response())
}

/// POST /api/settings — 写 setting(单条或批量)
///
/// 支持两种 body:
/// 1. `{key, value}` — 单条 upsert
/// 2. `{settings: {key: value, ...}}` — 批量 upsert
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_settings_set(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<SettingsSetBody>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));

    // 组装要写入的 kv 对
    let mut pairs: Vec<(String, serde_json::Value)> = Vec::new();

    if let Some(settings) = body.settings {
        for (k, v) in settings {
            pairs.push((k, v));
        }
    } else if let (Some(key), Some(value)) = (body.key, body.value) {
        if key.trim().is_empty() {
            return Err(ResponseError::bad_request("key 不能为空"));
        }
        pairs.push((key, value));
    } else {
        return Err(ResponseError::bad_request(
            "body 需包含 {key, value} 或 {settings: {...}}",
        ));
    }

    if pairs.is_empty() {
        return Ok(Json(json!({"ok": true, "updated": 0})).into_response());
    }

    for (k, v) in &pairs {
        sqlx::query(
            "INSERT INTO settings(user_id, key, value, updated_at)
             VALUES ($1, $2, $3::jsonb, NOW())
             ON CONFLICT (user_id, key) DO UPDATE
             SET value = EXCLUDED.value, updated_at = NOW()",
        )
        .bind(user.id)
        .bind(k)
        .bind(v)
        .execute(&s.db)
        .await
        .map_err(|e| {
            ResponseError::internal(format!("写入 settings 失败 key={k}: {e}"))
        })?;
    }

    Ok(Json(json!({
        "ok": true,
        "updated": pairs.len(),
    }))
    .into_response())
}
