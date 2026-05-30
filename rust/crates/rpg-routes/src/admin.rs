//! `/api/admin/*` — SMTP 测试 / 部署配置。
//!
//! Python 源: `platform_app/frontend_routes.py`
//! 端点:
//!   POST /api/admin/smtp/test            — SMTP 连通测试(admin only,stub 返回未配置)
//!   POST /api/admin/deployment-config    — 写部署配置(admin only, patch 合并语义)
//!   GET  /api/admin/deployment-config    — 读部署配置(admin only)
//!
//! 存储模型: 所有部署配置存储为 app_config 表中 key='admin.deployment_config' 的单条 JSONB 记录,
//! 与 Python 实现完全一致。

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use http::{HeaderMap, StatusCode};

use serde_json::json;

use crate::{require_user, AppState, ResponseError};

/// Python 端使用的配置键名。
const DEPLOY_CFG_KEY: &str = "admin.deployment_config";

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/admin/smtp/test", post(api_smtp_test))
        .route(
            "/api/admin/deployment-config",
            get(api_deployment_config_get).post(api_deployment_config_set),
        )
        .route("/api/admin/branches/gc", post(api_branches_gc))
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
/// value 列使用 JSONB 以与 Python 端一致。
async fn ensure_app_config_table(state: &AppState) -> Result<(), ResponseError> {
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS app_config (
            key        TEXT PRIMARY KEY,
            value      JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )",
    )
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(format!("建表失败: {e}")))?;
    Ok(())
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// POST /api/admin/smtp/test — SMTP 连通测试(stub)
///
/// 对应 Python: 返回 503 + {ok:false, error:..., configured:false}
/// Rust 翻译期只返 stub 表示未配置 SMTP。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_smtp_test(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    check_admin(&s, &headers).await?;
    Ok((
        StatusCode::SERVICE_UNAVAILABLE,
        Json(json!({
            "ok": false,
            "error": "未配置 SMTP",
            "configured": false,
        })),
    )
        .into_response())
}

/// GET /api/admin/deployment-config — 读取部署配置
///
/// 与 Python 一致:从 app_config 表读取 key='admin.deployment_config' 的单条 JSONB 记录。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_deployment_config_get(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    check_admin(&s, &headers).await?;
    ensure_app_config_table(&s).await?;

    let row: Option<(serde_json::Value,)> = sqlx::query_as(
        "SELECT value FROM app_config WHERE key = $1",
    )
    .bind(DEPLOY_CFG_KEY)
    .fetch_optional(&s.db)
    .await
    .map_err(|e| ResponseError::internal(format!("读取配置失败: {e}")))?;

    let config = match row {
        Some((v,)) => v,
        None => json!({}),
    };

    Ok(Json(json!({
        "ok": true,
        "config": config,
    }))
    .into_response())
}

/// POST /api/admin/deployment-config — 写部署配置(patch 合并语义)
///
/// 与 Python 一致:读取现有 JSONB 对象,与 body 合并后 upsert 回同一 key。
/// body 直接作为 JSON 对象,每个顶层 key 覆盖对应配置项,不影响未出现的键。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_deployment_config_set(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Response, ResponseError> {
    check_admin(&s, &headers).await?;
    ensure_app_config_table(&s).await?;

    let incoming = match body.as_object() {
        Some(obj) => obj.clone(),
        None => {
            return Err(ResponseError::bad_request("请求体必须是对象"));
        }
    };

    // 读取现有配置
    let row: Option<(serde_json::Value,)> = sqlx::query_as(
        "SELECT value FROM app_config WHERE key = $1",
    )
    .bind(DEPLOY_CFG_KEY)
    .fetch_optional(&s.db)
    .await
    .map_err(|e| ResponseError::internal(format!("读取配置失败: {e}")))?;

    let mut existing = match row {
        Some((serde_json::Value::Object(map),)) => map,
        _ => serde_json::Map::new(),
    };

    // patch 合并:incoming 覆盖 existing 中的同名键
    for (k, v) in incoming {
        existing.insert(k, v);
    }

    let merged = serde_json::Value::Object(existing);

    // upsert 回 app_config,value 列为 JSONB
    sqlx::query(
        "INSERT INTO app_config(key, value, updated_at)
         VALUES ($1, $2, NOW())
         ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
    )
    .bind(DEPLOY_CFG_KEY)
    .bind(&merged)
    .execute(&s.db)
    .await
    .map_err(|e| ResponseError::internal(format!("写入配置失败: {e}")))?;

    Ok(Json(json!({
        "ok": true,
        "config": merged,
        "note": "listen_address / cors_origins 等网络配置需重启服务才能生效",
    }))
    .into_response())
}

// ── branches GC ──────────────────────────────────────────────────────────────

/// POST /api/admin/branches/gc — 清理孤儿 branch commits
///
/// body: { "save_id": i64, "max_age_days": i64 (可选, 默认 30) }
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_branches_gc(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Response, ResponseError> {
    check_admin(&s, &headers).await?;

    let save_id = body
        .get("save_id")
        .and_then(|v| v.as_i64())
        .ok_or_else(|| ResponseError::bad_request("缺少 save_id"))?;
    let max_age_days = body
        .get("max_age_days")
        .and_then(|v| v.as_i64())
        .unwrap_or(30);

    let deleted = rpg_platform::branches::gc::gc_orphaned_commits(&s.db, save_id, max_age_days)
        .await
        .map_err(|e| ResponseError::internal(format!("gc 失败: {e}")))?;

    Ok(Json(json!({
        "ok": true,
        "deleted": deleted,
        "save_id": save_id,
        "max_age_days": max_age_days,
    }))
    .into_response())
}
