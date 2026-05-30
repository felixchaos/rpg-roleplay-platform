//! `/api/branches/*` — 分支树读取/派生/激活/删除/回滚。
//!
//! 对应 Python: `rpg/platform_app/api/saves.py` 中的 branches 端点。
//! Service: `rpg_platform::branches::{tree_ops, activation, deletion}`。

use axum::{
    extract::{Path, Query, State},
    http::HeaderMap,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/branches/:save_id", get(api_branches_tree))
        .route("/api/branches/continue", post(api_branches_continue))
        .route("/api/branches/activate", post(api_branches_activate))
        .route("/api/branches/delete", post(api_branches_delete))
        .route("/api/branches/rollback", post(api_branches_rollback))
}

// ── Query / Body ──────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct BranchesQuery {
    #[allow(dead_code)]
    limit: Option<i64>,
    #[allow(dead_code)]
    cursor: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ContinueBody {
    node_id: Option<Value>,
    save_id: Option<Value>,
    message_index: Option<Value>,
}

#[derive(Debug, Deserialize)]
struct ActivateBody {
    node_id: Option<Value>,
}

#[derive(Debug, Deserialize)]
struct DeleteBody {
    node_id: Option<Value>,
}

#[derive(Debug, Deserialize)]
struct RollbackBody {
    save_id: Option<Value>,
    message_index: Option<Value>,
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn parse_i64_field(val: &Option<Value>, field: &str) -> Result<Option<i64>, ResponseError> {
    match val {
        None => Ok(None),
        Some(Value::Number(n)) => n
            .as_i64()
            .map(Some)
            .ok_or_else(|| ResponseError::bad_request(format!("{field} 不是整数"))),
        Some(Value::String(s)) if !s.is_empty() => s
            .parse::<i64>()
            .map(Some)
            .map_err(|_| ResponseError::bad_request(format!("{field} 不是整数"))),
        Some(Value::Null) => Ok(None),
        Some(Value::String(s)) if s.is_empty() => Ok(None),
        _ => Err(ResponseError::bad_request(format!("{field} 不是整数"))),
    }
}

// ── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/branches/{save_id}?limit=&cursor= — 分支树读取
async fn api_branches_tree(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
    Query(_q): Query<BranchesQuery>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // tree() 内部已校验 save 归属，返回 403-style PlatformError 时映射到 forbidden
    let result = rpg_platform::branches::tree_ops::tree(&state.db, user.id.into(), save_id)
        .await
        .map_err(|e| ResponseError::forbidden(e.to_string()))?;

    // 完整对齐 Python tree() 返回格式
    Ok(Json(json!({
        "ok": result.ok,
        "save_id": result.save_id,
        "save": result.save,
        "nodes": result.nodes,
        "refs": result.refs,
        "active_commit_id": result.active_commit_id,
        "active_branch_node_id": result.active_branch_node_id,
        "active_ref_id": result.active_ref_id,
        "page": result.page,
    })))
}

/// POST /api/branches/continue — 从节点派生新分支
///
/// 接受两种 body:
///   A) { node_id }
///   B) { save_id, message_index } → 后端解析 message_index → commit_id
async fn api_branches_continue(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ContinueBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // 解析 node_id (形态 A)
    let node_id_opt = match &body.node_id {
        None => None,
        Some(Value::Null) => None,
        Some(Value::String(s)) if s.is_empty() => None,
        Some(v) => parse_i64_field(&Some(v.clone()), "node_id")?,
    };

    // 若没有 node_id，尝试形态 B
    let node_id: i64 = if let Some(nid) = node_id_opt {
        nid
    } else {
        let save_id = parse_i64_field(&body.save_id, "save_id")?
            .ok_or_else(|| {
                ResponseError::bad_request(
                    "缺字段：需要 node_id 或 (save_id + message_index)",
                )
            })?;
        let msg_idx = parse_i64_field(&body.message_index, "message_index")?
            .ok_or_else(|| {
                ResponseError::bad_request(
                    "缺字段：需要 node_id 或 (save_id + message_index)",
                )
            })?;

        let resolved = rpg_platform::branches::tree_ops::resolve_commit_id_by_message(
            &state.db,
            user.id.into(),
            save_id,
            msg_idx,
        )
        .await
        .map_err(ResponseError::from)?;

        resolved.ok_or_else(|| {
            ResponseError::bad_request(format!(
                "无法在 save={save_id} 找到 message_index={msg_idx} 对应的提交"
            ))
        })?
    };

    let result = rpg_platform::branches::activation::continue_from(&state.db, user.id.into(), node_id)
        .await
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": result.ok,
        "save_id": result.save_id,
        "save": result.save,
        "nodes": result.nodes,
        "refs": result.refs,
        "active_commit_id": result.active_commit_id,
        "active_branch_node_id": result.active_branch_node_id,
        "active_ref_id": result.active_ref_id,
        "page": result.page,
        "runtime": result.runtime,
        "game_url": result.game_url,
        "runtime_url": result.runtime_url,
        "active_ref": result.active_ref,
    })))
}

/// POST /api/branches/activate — 直接激活某节点
async fn api_branches_activate(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ActivateBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let node_id = parse_i64_field(&body.node_id, "node_id")?
        .ok_or_else(|| ResponseError::bad_request("node_id 不是整数"))?;

    let result = rpg_platform::branches::activation::activate_node(&state.db, user.id.into(), node_id)
        .await
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": result.ok,
        "save_id": result.save_id,
        "save": result.save,
        "nodes": result.nodes,
        "refs": result.refs,
        "active_commit_id": result.active_commit_id,
        "active_branch_node_id": result.active_branch_node_id,
        "active_ref_id": result.active_ref_id,
        "page": result.page,
        "runtime": result.runtime,
        "game_url": result.game_url,
        "runtime_url": result.runtime_url,
    })))
}

/// POST /api/branches/delete — 删某条分支子树
async fn api_branches_delete(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DeleteBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let node_id = parse_i64_field(&body.node_id, "node_id")?
        .ok_or_else(|| ResponseError::bad_request("node_id 不是整数"))?;

    let result = rpg_platform::branches::deletion::delete_subtree(&state.db, user.id.into(), node_id)
        .await
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": result.ok,
        "save_id": result.save_id,
        "save": result.save,
        "nodes": result.nodes,
        "refs": result.refs,
        "active_commit_id": result.active_commit_id,
        "active_branch_node_id": result.active_branch_node_id,
        "active_ref_id": result.active_ref_id,
        "page": result.page,
        "runtime": result.runtime,
        "game_url": result.game_url,
    })))
}

/// POST /api/branches/rollback — 软回滚到指定 message_index
///
/// 入参: { save_id, message_index }
/// 出参: { ok, target_commit_id, restored_turn, messages, timeline_anchors, context_runs }
async fn api_branches_rollback(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RollbackBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let save_id = parse_i64_field(&body.save_id, "save_id")?
        .ok_or_else(|| ResponseError::bad_request("save_id 和 message_index 都必须是整数"))?;
    let message_index = parse_i64_field(&body.message_index, "message_index")?
        .ok_or_else(|| ResponseError::bad_request("save_id 和 message_index 都必须是整数"))?;

    let result =
        rpg_platform::branches::deletion::rollback_to_message(&state.db, user.id.into(), save_id, message_index)
            .await
            .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": result.ok,
        "save_id": result.save_id,
        "save": result.save,
        "nodes": result.nodes,
        "refs": result.refs,
        "active_commit_id": result.active_commit_id,
        "active_branch_node_id": result.active_branch_node_id,
        "active_ref_id": result.active_ref_id,
        "page": result.page,
        "runtime": result.runtime,
        "game_url": result.game_url,
        "restored_turn": result.restored_turn,
        "deleted": result.deleted,
        "trash_ref": result.trash_ref,
    })))
}
