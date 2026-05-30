//! `/api/library/*` — 库列表/上传/下载/mkdir/delete。
//!
//! 对应 Python: `rpg/platform_app/api/library.py` (70 行)。
//! Service: `rpg_platform::library`。

use axum::{
    extract::{Query, State},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::json;

use rpg_platform::library::{self, UploadItem};

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/library", get(api_library_list))
        .route("/api/library/upload", post(api_library_upload))
        .route("/api/library/mkdir", post(api_library_mkdir))
        .route("/api/library/delete", post(api_library_delete))
        .route("/api/library/download", get(api_library_download))
}

// ── query params ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
struct LibraryQuery {
    #[serde(default)]
    path: String,
    limit: Option<usize>,
    cursor: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
struct DownloadQuery {
    path: String,
}

// ── request bodies ───────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
struct MkdirBody {
    #[serde(default)]
    path: String,
    #[serde(default)]
    name: String,
}

#[derive(Debug, Deserialize, Default)]
struct DeleteBody {
    #[serde(default)]
    path: String,
}

#[derive(Debug, Deserialize, Default)]
struct UploadBody {
    #[serde(default)]
    path: String,
    #[serde(default)]
    files: Vec<UploadItem>,
}

// ── GET /api/library ─────────────────────────────────────────────────────────

async fn api_library_list(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<LibraryQuery>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let listing = library::list_dir(
        &state.db,
        user.id.into(),
        &params.path,
        params.limit,
        params.cursor.as_deref(),
    )
    .await
    .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(flatten_listing(listing)))
}

/// LIBRARY_RESPONSE_FORMAT: 已验证 — 返回 entries 和 items 双字段(与 Python 一致)。
/// Flatten a `LibraryListing` into a top-level JSON object so the frontend
/// can access `r.entries` / `r.items` directly (matching the Python backend).
fn flatten_listing(listing: library::LibraryListing) -> serde_json::Value {
    let items = listing.entries.clone();
    json!({
        "ok": true,
        "engine": listing.engine,
        "path": listing.path,
        "entries": listing.entries,
        "items": items,
        "page": listing.page,
    })
}

// ── POST /api/library/upload — JSON body (base64 files) ─────────────────────

async fn api_library_upload(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<UploadBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let listing = library::upload(&state.db, user.id.into(), &body.path, body.files)
        .await
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(flatten_listing(listing)))
}

// ── POST /api/library/mkdir ───────────────────────────────────────────────────

async fn api_library_mkdir(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<MkdirBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    // path 若含 name 则拼接;Python 端把 name 追加到 path
    let full_path = if body.name.is_empty() {
        body.path.clone()
    } else if body.path.is_empty() {
        body.name.clone()
    } else {
        format!("{}/{}", body.path.trim_end_matches('/'), body.name)
    };
    let listing = library::mkdir(&state.db, user.id.into(), &full_path)
        .await
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(flatten_listing(listing)))
}

// ── POST /api/library/delete ─────────────────────────────────────────────────

async fn api_library_delete(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DeleteBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let listing = library::delete(&state.db, user.id.into(), &body.path)
        .await
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(flatten_listing(listing)))
}

// ── GET /api/library/download ────────────────────────────────────────────────

async fn api_library_download(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<DownloadQuery>,
) -> Result<Response, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let target = library::download_path(user.id.into(), &params.path)
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    if !target.exists() {
        return Err(ResponseError::not_found("文件不存在"));
    }
    let bytes = tokio::fs::read(&target)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    let filename = target
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("download")
        .to_string();
    // 安全：强制 application/octet-stream，防浏览器解析上传内容为 html/svg/js。
    let resp = (
        StatusCode::OK,
        [
            ("Content-Type", "application/octet-stream".to_string()),
            (
                "Content-Disposition",
                format!("attachment; filename=\"{}\"", filename),
            ),
            ("X-Content-Type-Options", "nosniff".to_string()),
            ("Content-Security-Policy", "default-src 'none'; sandbox".to_string()),
            ("X-Frame-Options", "DENY".to_string()),
            ("Referrer-Policy", "no-referrer".to_string()),
        ],
        bytes,
    )
        .into_response();
    Ok(resp)
}
