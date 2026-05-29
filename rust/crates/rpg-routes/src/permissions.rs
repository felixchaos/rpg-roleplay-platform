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
use rpg_state::pending::{
    approve_pending_write, clear_pending_question, reject_pending_write, PendingError,
};

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

/// POST /api/permissions/pending-write — 审批待写入(approve / reject + 回放 op)
///
/// 对应 Python `api_pending_write`:
///   - decision = approve → `state.approve_pending_write(id|index)` 走 apply_op force=true
///   - decision = reject  → `state.reject_pending_write(id|index)` 写 audit_log
///
/// id 优先,index 兜底(Python P0 #53 修复:前端发 id+action,后端读 index/decision
/// 旧契约就死)。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_pending_write(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<PendingWriteRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let action = body
        .action
        .as_deref()
        .or(body.decision.as_deref())
        .unwrap_or("")
        .to_ascii_lowercase();
    if action != "approve" && action != "reject" {
        return Err(ResponseError::bad_request(
            "缺少 action/decision（approve|reject）",
        ));
    }
    let id = body.id.as_deref();
    let index = body
        .index
        .and_then(|i| if i >= 0 { Some(i as usize) } else { None });

    let shared = s.state_store.get_or_create(&user_id).await;
    let (snapshot, result_payload) = {
        let mut st = shared.write();
        let payload = match action.as_str() {
            "approve" => match approve_pending_write(&mut st, id, index) {
                Ok(r) => json!({
                    "ok": true,
                    "message": r.message,
                    "applied": matches!(r.outcome.kind, rpg_state::ops::ApplyKind::Applied),
                }),
                Err(PendingError::NotFound(_)) => {
                    return Err(ResponseError::bad_request("待审写入不存在"));
                }
                Err(e) => return Err(ResponseError::internal(e.to_string())),
            },
            _ => match reject_pending_write(&mut st, id, index, None) {
                Ok(r) => json!({
                    "ok": true,
                    "message": r.message,
                    "path": r.path,
                    "applied": false,
                }),
                Err(PendingError::NotFound(_)) => {
                    return Err(ResponseError::bad_request("待审写入不存在"));
                }
                Err(e) => return Err(ResponseError::internal(e.to_string())),
            },
        };
        (st.clone(), payload)
    };
    let mut out = result_payload;
    if let Some(obj) = out.as_object_mut() {
        obj.insert("state".into(), serde_json::to_value(&snapshot.data).unwrap_or(Value::Null));
    }
    Ok(Json(out).into_response())
}

/// POST /api/questions/clear — 回答/跳过 GM 询问
///
/// 对应 Python `api_question_clear` → `state.clear_pending_question(index, id, choice)`。
/// id 优先,index 兜底;choice 为 None 时写 "(skipped)"。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_question_clear(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<QuestionClearRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let id = body.id.as_deref();
    let index = body
        .index
        .and_then(|i| if i >= 0 { Some(i as usize) } else { None });
    let choice_str = body.choice.as_ref().map(|v| match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    });
    let (cleared, snapshot) = {
        let mut st = shared.write();
        let popped = clear_pending_question(&mut st, id, index, choice_str.as_deref());
        (popped.is_some(), st.clone())
    };
    Ok(Json(json!({
        "ok": true,
        "cleared": cleared,
        "state": snapshot.data,
    }))
    .into_response())
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
