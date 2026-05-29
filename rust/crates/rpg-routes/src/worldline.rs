//! worldline.py → worldline.rs — 世界线变量管理路由
//! POST /api/worldline/variable        — 设置世界线变量
//! POST /api/worldline/variable/remove — 删除世界线变量

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::{user_id_or_anon, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/worldline/variable", post(api_worldline_variable))
        .route(
            "/api/worldline/variable/remove",
            post(api_worldline_variable_remove),
        )
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct WorldlineVariableRequest {
    pub key: Option<String>,
    pub value: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct WorldlineVariableRemoveRequest {
    pub key: Option<String>,
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// POST /api/worldline/variable — 设置世界线变量
///
/// 写到 state.worldline.user_variables.{key} = value。
/// Python 端还会同步写 platform_knowledge.worldline_variable 表(用于 DB
/// 持久化),Rust 翻译期暂略。
#[tracing::instrument(skip_all)]
async fn api_worldline_variable(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<WorldlineVariableRequest>,
) -> Result<Response, ResponseError> {
    let key = body
        .key
        .map(|k| k.trim().to_string())
        .filter(|k| !k.is_empty())
        .ok_or_else(|| ResponseError::bad_request("key required"))?;
    let value = body.value.unwrap_or_default();
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        st.set_path(
            &format!("worldline.user_variables.{key}"),
            Value::String(value),
        )?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}

/// POST /api/worldline/variable/remove — 删除世界线变量
#[tracing::instrument(skip_all)]
async fn api_worldline_variable_remove(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<WorldlineVariableRemoveRequest>,
) -> Result<Response, ResponseError> {
    let key = body
        .key
        .map(|k| k.trim().to_string())
        .filter(|k| !k.is_empty())
        .ok_or_else(|| ResponseError::bad_request("key required"))?;
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        let _ = st.delete_path(&format!("worldline.user_variables.{key}"))?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}
