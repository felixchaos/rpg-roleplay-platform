//! memory.py → memory.rs — 记忆管理路由
//! POST /api/memory/mode   — 切换记忆模式
//! POST /api/memory/add    — 添加记忆条目
//! POST /api/memory/remove — 删除记忆条目
//!
//! Python 端走 dispatcher 路由到 add_memory_fact / add_memory_note 等工具,
//! Rust 翻译期 dispatcher 还没全接 → 直接 op_set/op_append 写到 state.memory.{bucket},
//! 走 rpg_state 的 path API。

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
        .route("/api/memory/mode", post(api_memory_mode))
        .route("/api/memory/add", post(api_memory_add))
        .route("/api/memory/remove", post(api_memory_remove))
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct MemoryModeRequest {
    pub mode: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct MemoryAddRequest {
    pub bucket: Option<String>,
    pub text: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct MemoryRemoveRequest {
    pub bucket: Option<String>,
    pub index: Option<i64>,
}

// ── handlers ──────────────────────────────────────────────────────────────────

fn allowed_mode(m: &str) -> &'static str {
    match m {
        "lite" => "lite",
        "deep" => "deep",
        _ => "normal",
    }
}

fn allowed_bucket(b: &str) -> &'static str {
    match b {
        "facts" => "facts",
        "resources" => "resources",
        "abilities" => "abilities",
        "pinned" => "pinned",
        _ => "notes",
    }
}

/// POST /api/memory/mode
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_memory_mode(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<MemoryModeRequest>,
) -> Result<Response, ResponseError> {
    let mode = allowed_mode(body.mode.as_deref().unwrap_or("normal"));
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        st.set_path("memory.mode", Value::String(mode.to_string()))?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}

/// POST /api/memory/add
#[tracing::instrument(skip(s, headers, body), fields(user_id, bucket))]
async fn api_memory_add(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<MemoryAddRequest>,
) -> Result<Response, ResponseError> {
    let bucket = allowed_bucket(body.bucket.as_deref().unwrap_or("notes"));
    tracing::Span::current().record("bucket", tracing::field::display(bucket));
    let text = body.text.unwrap_or_default();
    if text.trim().is_empty() {
        return Err(ResponseError::bad_request("text 不能为空"));
    }
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        st.append_to_path(&format!("memory.{bucket}"), Value::String(text))?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}

/// POST /api/memory/remove
#[tracing::instrument(skip(s, headers, body), fields(user_id, bucket, idx))]
async fn api_memory_remove(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<MemoryRemoveRequest>,
) -> Result<Response, ResponseError> {
    let bucket = allowed_bucket(body.bucket.as_deref().unwrap_or("notes"));
    tracing::Span::current().record("bucket", tracing::field::display(bucket));
    let idx = body.index.unwrap_or(-1);
    tracing::Span::current().record("idx", idx);
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        // 取出当前数组
        let cur = st
            .get_path(&format!("memory.{bucket}"))
            .cloned()
            .unwrap_or(Value::Array(vec![]));
        if let Value::Array(mut arr) = cur {
            if idx < 0 || (idx as usize) >= arr.len() {
                return Err(ResponseError::bad_request("index 越界"));
            }
            arr.remove(idx as usize);
            st.set_path(&format!("memory.{bucket}"), Value::Array(arr))?;
        }
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}
