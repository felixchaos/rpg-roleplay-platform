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

use rpg_platform::auth::user_from_token;

use crate::{token_from_headers, user_id_or_anon, AppState, ResponseError};

/// 对应 Python `platform_knowledge.set_worldline_variable(user_id, save_id, key, value, source)`.
/// 解析活跃 save → 找 game_sessions → upsert worldline_variables。失败只 warn,不打断响应。
async fn persist_worldline_variable(s: &AppState, headers: &HeaderMap, key: &str, value: &str) {
    let token = token_from_headers(headers);
    let user_opt = match user_from_token(&s.db, token.as_deref()).await {
        Ok(u) => u,
        Err(_) => return,
    };
    let Some(user) = user_opt else { return };
    let Some(save_id) = rpg_platform::save_io::resolve_active_save_id(&s.db, user.id).await else {
        return;
    };
    // game_sessions 里找 session_id
    let session_id: Option<i64> = sqlx::query(
        "select id from game_sessions where save_id = $1 order by updated_at desc limit 1",
    )
    .bind(save_id)
    .fetch_optional(&s.db)
    .await
    .ok()
    .flatten()
    .and_then(|r| sqlx::Row::try_get::<i64, _>(&r, "id").ok());
    let Some(sid) = session_id else { return };
    // WORLDLINE-DB-PERSIST-TIMING: DB 持久化失败时静默 warn(与 Python 行为一致)。
    // Python 也不会因 DB 写入失败而中断响应,state 内存中的值仍然有效。
    if let Err(e) = rpg_platform::runtime::worldline::set_user_worldline_variable(
        &s.db, sid, key, value, "user", true, None,
    )
    .await
    {
        tracing::warn!("worldline variable DB persist failed ({key}): {e}");
    }
}

/// 对应 Python `platform_knowledge.remove_worldline_variable(user_id, save_id, key)`.
async fn persist_worldline_variable_remove(s: &AppState, headers: &HeaderMap, key: &str) {
    let token = token_from_headers(headers);
    let user_opt = match user_from_token(&s.db, token.as_deref()).await {
        Ok(u) => u,
        Err(_) => return,
    };
    let Some(user) = user_opt else { return };
    let Some(save_id) = rpg_platform::save_io::resolve_active_save_id(&s.db, user.id).await else {
        return;
    };
    let session_id: Option<i64> = sqlx::query_scalar(
        "select id from game_sessions where save_id = $1 order by updated_at desc limit 1",
    )
    .bind(save_id)
    .fetch_optional(&s.db)
    .await
    .ok()
    .flatten();
    let Some(sid) = session_id else { return };
    if let Err(e) =
        rpg_platform::runtime::worldline::remove_user_worldline_variable(&s.db, sid, key).await
    {
        tracing::warn!("worldline variable DB remove failed ({key}): {e}");
    }
}

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
/// WORLDLINE-DISPATCHER-MISMATCH: 设计差异,行为一致。
/// Python 走 dispatch_ui_tool,Rust 直接操作 state_store + DB persist — 最终效果相同。
///
/// 写到 state.worldline.user_variables.{key} = value + DB 持久化。
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
    let (snapshot, value_cloned) = {
        let mut st = shared.write();
        st.set_path(
            &format!("worldline.user_variables.{key}"),
            Value::String(value.clone()),
        )?;
        (st.clone(), value.clone())
    };
    // 对应 Python platform_knowledge.set_worldline_variable(user_id, save_id, key, value, source='user')
    persist_worldline_variable(&s, &headers, &key, &value_cloned).await;
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
    // 对应 Python platform_knowledge.remove_worldline_variable(user_id, save_id, key)
    persist_worldline_variable_remove(&s, &headers, &key).await;
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}
