//! permissions.py → permissions.rs — 权限/确认管理路由
//! POST /api/permissions              — 切换权限模式
//! POST /api/permissions/pending-write — 审批待写入
//! POST /api/questions/clear          — 回答/跳过 GM 询问
//! POST /api/debug/pending-question   — [debug] 注入待处理问题

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use rpg_state::ops::normalize_permission_mode;

use crate::{user_id_or_anon, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/permissions", post(api_permissions))
        .route("/api/permissions/pending-write", post(api_pending_write))
        .route("/api/questions/clear", post(api_question_clear))
        .route("/api/debug/pending-question", post(api_debug_pending_question))
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct PermissionsRequest {
    pub mode: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct PendingWriteRequest {
    pub id: Option<String>,
    pub index: Option<i64>,
    /// "approve" | "reject"
    pub action: Option<String>,
    pub decision: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct QuestionClearRequest {
    pub id: Option<String>,
    pub index: Option<i64>,
    pub choice: Option<Value>,
}

#[derive(Debug, Deserialize, Default)]
pub struct DebugPendingQuestionRequest {
    pub text: Option<String>,
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// POST /api/permissions — 切换权限模式
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_permissions(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<PermissionsRequest>,
) -> Result<Response, ResponseError> {
    let mode = normalize_permission_mode(body.mode.as_deref().unwrap_or(""));
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        st.set_path("permissions.mode", Value::String(mode.to_string()))?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data, "mode": mode})).into_response())
}

/// POST /api/permissions/pending-write — 审批待写入
///
/// 通过 index 删除 state.permissions.pending_writes 里指定项。
/// 真正"批准后回放 op"留 TODO,接 rpg_state pending writes mixin。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_pending_write(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<PendingWriteRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        let pending = st
            .get_path("permissions.pending_writes")
            .cloned()
            .unwrap_or(Value::Array(vec![]));
        if let Value::Array(mut arr) = pending {
            let by_id = body.id.as_deref();
            let by_idx = body.index;
            let pos = if let Some(id) = by_id {
                arr.iter()
                    .position(|x| x.get("id").and_then(|v| v.as_str()) == Some(id))
            } else if let Some(i) = by_idx {
                if i >= 0 && (i as usize) < arr.len() {
                    Some(i as usize)
                } else {
                    None
                }
            } else {
                None
            };
            if let Some(p) = pos {
                let removed = arr.remove(p);
                st.set_path("permissions.pending_writes", Value::Array(arr))?;
                let action = body.action.or(body.decision).unwrap_or_default();
                let mut audit = st
                    .get_path("permissions.audit_log")
                    .cloned()
                    .unwrap_or(Value::Array(vec![]));
                if let Value::Array(ref mut a) = audit {
                    a.push(
                        json!({"action": action, "entry": removed, "at": chrono::Utc::now().to_rfc3339()}),
                    );
                }
                st.set_path("permissions.audit_log", audit)?;
            }
        }
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}

/// POST /api/questions/clear — 回答/跳过 GM 询问
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_question_clear(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<QuestionClearRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        let cur = st
            .get_path("permissions.pending_questions")
            .cloned()
            .unwrap_or(Value::Array(vec![]));
        if let Value::Array(mut arr) = cur {
            let pos = if let Some(id) = body.id.as_deref() {
                arr.iter()
                    .position(|x| x.get("id").and_then(|v| v.as_str()) == Some(id))
            } else if let Some(i) = body.index {
                if i >= 0 && (i as usize) < arr.len() {
                    Some(i as usize)
                } else {
                    None
                }
            } else {
                None
            };
            if let Some(p) = pos {
                arr.remove(p);
                st.set_path("permissions.pending_questions", Value::Array(arr))?;
            }
        }
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}

/// POST /api/debug/pending-question — [debug] 注入待处理问题
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_debug_pending_question(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DebugPendingQuestionRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let text = body.text.unwrap_or_default();
    let snapshot = {
        let mut st = shared.write();
        st.append_to_path(
            "permissions.pending_questions",
            json!({
                "id": format!("debug-{}", chrono::Utc::now().timestamp_millis()),
                "text": text,
                "at": chrono::Utc::now().to_rfc3339(),
            }),
        )?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}
