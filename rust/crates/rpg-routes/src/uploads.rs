//! uploads.rs — base64 分片上传路由
//!
//! 前端 `lib/api.ts` 上传大文件(skill bundle / library 图书等)走 JSON base64 协议:
//!   1. `POST /api/uploads/begin`  → `{upload_id}`
//!   2. `POST /api/uploads/chunk`  body `{upload_id, chunk_index, total_chunks, base64}`
//!   3. `POST /api/uploads/finalize` body `{upload_id, kind:"skill", name?}` → 触发后处理
//!
//! 设计:全内存缓存在 `AppState::chunk_uploads`(DashMap)。本翻译期暂不持久化,
//! finalize 后立即 drain。`kind=skill` 时复用 `rpg_tools_dsl::import_skill_bundle`。

use std::path::PathBuf;

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::json;

use rpg_tools_dsl::skill_executor::import_skill_bundle;

use crate::{require_user, AppState, ChunkUploadState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/uploads/begin", post(api_uploads_begin))
        .route("/api/uploads/chunk", post(api_uploads_chunk))
        .route("/api/uploads/finalize", post(api_uploads_finalize))
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct UploadBeginRequest {
    pub file_name: Option<String>,
    pub name: Option<String>,
    pub kind: Option<String>,
    pub total_chunks: Option<u32>,
}

#[derive(Debug, Deserialize)]
pub struct UploadChunkRequest {
    pub upload_id: String,
    pub chunk_index: u32,
    pub total_chunks: u32,
    pub base64: String,
}

#[derive(Debug, Deserialize, Default)]
pub struct UploadFinalizeRequest {
    pub upload_id: Option<String>,
    /// "skill" 等。未来扩展 "library" / "media"。
    pub kind: Option<String>,
    pub name: Option<String>,
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// POST /api/uploads/begin — 分配 upload_id,允许调用方一次 finalize。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_uploads_begin(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<UploadBeginRequest>>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let body = body.map(|Json(b)| b).unwrap_or_default();
    let upload_id = format!("up-{}", uuid::Uuid::new_v4());
    s.chunk_uploads.insert(
        upload_id.clone(),
        ChunkUploadState {
            total_chunks: body.total_chunks.unwrap_or(0),
            file_name: body.file_name,
            name: body.name,
            kind: body.kind,
            received: Vec::new(),
        },
    );
    Ok(Json(json!({"ok": true, "upload_id": upload_id})).into_response())
}

/// POST /api/uploads/chunk — 接收一片 base64 数据。
///
/// 重复 chunk_index 会覆盖。`total_chunks` 任一片提供即同步到上传状态。
#[tracing::instrument(skip(s, headers, body), fields(user_id, upload_id = %body.upload_id, idx = body.chunk_index))]
async fn api_uploads_chunk(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<UploadChunkRequest>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let decoded = STANDARD
        .decode(body.base64.as_bytes())
        .map_err(|e| ResponseError::bad_request(format!("base64 decode: {e}")))?;
    let mut entry = s
        .chunk_uploads
        .get_mut(&body.upload_id)
        .ok_or_else(|| ResponseError::not_found("upload_id 不存在,需先 /uploads/begin"))?;
    if entry.total_chunks == 0 && body.total_chunks > 0 {
        entry.total_chunks = body.total_chunks;
    }
    // 移除同 idx 旧数据(允许重传)
    entry.received.retain(|(i, _)| *i != body.chunk_index);
    entry.received.push((body.chunk_index, decoded));
    let received = entry.received.len();
    let total = entry.total_chunks;
    drop(entry);
    Ok(Json(json!({
        "ok": true,
        "received": received,
        "total_chunks": total,
    }))
    .into_response())
}

/// POST /api/uploads/finalize — 合并 chunks + 按 `kind` 触发后处理。
///
/// 目前实现 `kind=skill`:把合并后的 zip 走 `import_skill_bundle`。
/// 其它 kind 暂返回 `not_implemented`。
#[tracing::instrument(skip(s, headers), fields(user_id, upload_id))]
async fn api_uploads_finalize(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<UploadFinalizeRequest>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let upload_id = body
        .upload_id
        .ok_or_else(|| ResponseError::bad_request("upload_id required"))?;
    tracing::Span::current().record("upload_id", tracing::field::display(&upload_id));
    let (_, mut entry) = s
        .chunk_uploads
        .remove(&upload_id)
        .ok_or_else(|| ResponseError::not_found("upload_id 不存在或已 finalize"))?;
    // 按 idx 排序,合并
    entry.received.sort_by_key(|(i, _)| *i);
    if entry.total_chunks > 0 && (entry.received.len() as u32) < entry.total_chunks {
        return Err(ResponseError::bad_request(format!(
            "缺片: 已收 {}/{}",
            entry.received.len(),
            entry.total_chunks
        )));
    }
    let mut merged: Vec<u8> = Vec::new();
    for (_, chunk) in &entry.received {
        merged.extend_from_slice(chunk);
    }

    let kind = body
        .kind
        .or(entry.kind.clone())
        .unwrap_or_else(|| "skill".to_string());

    match kind.as_str() {
        "skill" => {
            if user.role != "admin" {
                return Err(ResponseError::forbidden("仅管理员"));
            }
            let name = body
                .name
                .or(entry.name.clone())
                .unwrap_or_else(|| {
                    entry
                        .file_name
                        .as_deref()
                        .unwrap_or("unnamed")
                        .trim_end_matches(".zip")
                        .to_string()
                });
            let skill_dir: PathBuf = std::env::var("SKILL_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(|_| {
                    std::env::current_dir()
                        .unwrap_or_else(|_| PathBuf::from("."))
                        .join("skills")
                });
            let bytes = bytes::Bytes::from(merged);
            let imported = import_skill_bundle(&bytes, &name, &skill_dir)
                .map_err(|e| ResponseError::bad_request(e.to_string()))?;
            Ok(Json(json!({
                "ok": true,
                "kind": "skill",
                "skill_id": imported.id,
                "name": imported.name,
                "path": imported.path,
            }))
            .into_response())
        }
        other => Err(ResponseError::not_implemented(format!(
            "未支持的 kind: {other}"
        ))),
    }
}
