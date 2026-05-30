//! `/api/uploads/*` 新协议 + `/api/me/import-jobs` + import-jobs 流式状态。
//!
//! 对应 Python: `rpg/platform_app/api/imports.py` (150 行)。
//! Service: `rpg_platform::script_import`。
//!
//! 端点:
//! - POST /api/uploads/init                         — 创建分片上传任务
//! - POST /api/uploads/{upload_id}/chunk            — 单分片上传 (multipart)
//! - POST /api/uploads/{upload_id}/finish           — 完成,返最终文件信息
//! - POST /api/uploads/{upload_id}/cancel           — 取消并清理
//! - GET  /api/me/import-jobs                       — 列本人 import jobs
//! - GET  /api/scripts/import-jobs/{job_id}         — 单 job 详情
//! - POST /api/scripts/import-jobs/{job_id}/cancel  — 取消 job
//! - GET  /api/scripts/import-jobs/{job_id}/stream  — SSE 流

use std::convert::Infallible;

use axum::{
    extract::{FromRequest, Path, Query, State},
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse,
    },
    routing::{get, post},
    Json, Router,
};
use futures_util::stream;
use http::HeaderMap;
use base64::Engine as _;
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row;

use rpg_platform::script_import::{self, upload as upload_svc};

use crate::{named_sse_event, require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        // 分片上传
        .route("/api/uploads/init", post(api_uploads_init))
        .route("/api/uploads/:upload_id/chunk", post(api_uploads_chunk))
        .route("/api/uploads/:upload_id/finish", post(api_uploads_finish))
        .route("/api/uploads/:upload_id/cancel", post(api_uploads_cancel))
        // import-jobs
        .route("/api/me/import-jobs", get(api_me_import_jobs))
        .route("/api/scripts/import-jobs/:job_id", get(api_import_job_get))
        .route(
            "/api/scripts/import-jobs/{job_id}/cancel",
            post(api_import_job_cancel),
        )
        .route(
            "/api/scripts/import-jobs/{job_id}/stream",
            get(api_import_job_stream),
        )
}

// ── request bodies ───────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct InitUploadBody {
    filename: Option<String>,
    total_bytes: usize,
    total_chunks: usize,
}

#[derive(Debug, Deserialize, Default)]
struct ImportJobsQuery {
    #[serde(default = "default_limit")]
    limit: i64,
}

fn default_limit() -> i64 {
    20
}

// ── POST /api/uploads/init ───────────────────────────────────────────────────

async fn api_uploads_init(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<InitUploadBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let filename = body.filename.as_deref().unwrap_or("upload.bin");
    let meta =
        upload_svc::init_upload(user.id.into(), filename, body.total_bytes, body.total_chunks)
            .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(json!({
        "ok": true,
        "upload_id": meta.upload_id,
        "filename": meta.filename,
        "total_bytes": meta.total_bytes,
        "total_chunks": meta.total_chunks,
        "received_chunks": meta.received_chunks,
        "received_bytes": meta.received_bytes,
    })))
}

// ── POST /api/uploads/{upload_id}/chunk ──────────────────────────────────────
//
// UPLOADS_BODY_MISMATCH: 前端发 JSON {chunk_index, base64}(与 uploads.rs 协议一致），
// 同时兼容 multipart（旧协议）。优先尝试 JSON 解析,失败再 fallback multipart。

#[derive(Debug, Deserialize)]
struct ChunkJsonBody {
    chunk_index: usize,
    base64: String,
    #[allow(dead_code)]
    total_chunks: Option<usize>,
}

async fn api_uploads_chunk(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(upload_id): Path<String>,
    body: axum::body::Bytes,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // 尝试 JSON 解析(前端走 base64 协议)
    let (chunk_index, blob) = if let Ok(json_body) = serde_json::from_slice::<ChunkJsonBody>(&body) {
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(json_body.base64.as_bytes())
            .map_err(|e| ResponseError::bad_request(format!("base64 decode: {e}")))?;
        (json_body.chunk_index, decoded)
    } else {
        // Fallback: multipart
        let mut mp = axum::extract::Multipart::from_request(
            axum::http::Request::builder()
                .header("content-type", headers.get("content-type").and_then(|v| v.to_str().ok()).unwrap_or("multipart/form-data"))
                .body(axum::body::Body::from(body))
                .unwrap(),
            &state,
        ).await.map_err(|e| ResponseError::bad_request(e.to_string()))?;

        let mut cidx: Option<usize> = None;
        let mut data: Option<Vec<u8>> = None;
        while let Some(field) = mp.next_field().await.map_err(|e| ResponseError::bad_request(e.to_string()))? {
            let field_name = field.name().unwrap_or("").to_string();
            match field_name.as_str() {
                "chunk_index" => {
                    let txt = field.text().await.map_err(|e| ResponseError::bad_request(e.to_string()))?;
                    cidx = Some(txt.trim().parse::<usize>().map_err(|_| ResponseError::bad_request("chunk_index 必须是非负整数"))?);
                }
                "file" | "chunk" | "data" => {
                    let bytes = field.bytes().await.map_err(|e| ResponseError::bad_request(e.to_string()))?;
                    data = Some(bytes.to_vec());
                }
                _ => {}
            }
        }
        let cidx = cidx.ok_or_else(|| ResponseError::bad_request("缺少 chunk_index 字段"))?;
        let data = data.ok_or_else(|| ResponseError::bad_request("缺少 file/chunk/data 字段"))?;
        (cidx, data)
    };

    let meta = upload_svc::put_chunk(user.id.into(), &upload_id, chunk_index, &blob)
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "upload_id": meta.upload_id,
        "chunk_index": chunk_index,
        "received_chunks": meta.received_chunks,
        "received_bytes": meta.received_bytes,
        "total_chunks": meta.total_chunks,
        "total_bytes": meta.total_bytes,
    })))
}

// ── POST /api/uploads/{upload_id}/finish ─────────────────────────────────────

async fn api_uploads_finish(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(upload_id): Path<String>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let (meta, _bytes) = upload_svc::finish_upload(user.id.into(), &upload_id)
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(json!({
        "ok": true,
        "upload_id": meta.upload_id,
        "filename": meta.filename,
        "total_bytes": meta.total_bytes,
        "total_chunks": meta.total_chunks,
        "received_chunks": meta.received_chunks,
        "received_bytes": meta.received_bytes,
    })))
}

// ── POST /api/uploads/{upload_id}/cancel ─────────────────────────────────────

async fn api_uploads_cancel(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(upload_id): Path<String>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    upload_svc::cancel_upload(user.id.into(), &upload_id)
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    Ok(Json(json!({ "ok": true })))
}

// ── GET /api/me/import-jobs ───────────────────────────────────────────────────

async fn api_me_import_jobs(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ImportJobsQuery>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let limit = params.limit.clamp(1, 100);
    let rows = sqlx::query(
        r#"
        select id, job_id, user_id, script_id, kind, status, stage,
               stage_progress, stage_total, overall_progress, overall_total,
               error, created_at, started_at, finished_at
        from import_jobs
        where user_id = $1
        order by created_at desc
        limit $2
        "#,
    )
    .bind(i64::from(user.id))
    .bind(limit)
    .fetch_all(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let jobs: Vec<Value> = rows
        .iter()
        .map(|r| {
            json!({
                "id": r.try_get::<i64, _>("id").unwrap_or(0),
                "job_id": r.try_get::<String, _>("job_id").unwrap_or_default(),
                "user_id": r.try_get::<i64, _>("user_id").unwrap_or(0),
                "script_id": r.try_get::<Option<i64>, _>("script_id").ok().flatten(),
                "kind": r.try_get::<String, _>("kind").unwrap_or_default(),
                "status": r.try_get::<String, _>("status").unwrap_or_default(),
                "stage": r.try_get::<String, _>("stage").unwrap_or_default(),
                "stage_progress": r.try_get::<i32, _>("stage_progress").unwrap_or(0),
                "stage_total": r.try_get::<i32, _>("stage_total").unwrap_or(0),
                "overall_progress": r.try_get::<i32, _>("overall_progress").unwrap_or(0),
                "overall_total": r.try_get::<i32, _>("overall_total").unwrap_or(0),
                "error": r.try_get::<String, _>("error").unwrap_or_default(),
                "created_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("created_at").ok().flatten(),
                "started_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("started_at").ok().flatten(),
                "finished_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("finished_at").ok().flatten(),
            })
        })
        .collect();

    Ok(Json(json!({ "ok": true, "jobs": jobs })))
}

// ── GET /api/scripts/import-jobs/{job_id} ────────────────────────────────────

async fn api_import_job_get(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(job_id): Path<String>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let job = script_import::get_job(&state.db, &job_id)
        .await
        .map_err(|_| ResponseError::not_found("import job 不存在"))?;
    if job.user_id != i64::from(user.id) {
        return Err(ResponseError::forbidden("无权访问该 job"));
    }
    Ok(Json(json!({ "ok": true, "found": true, "job": job })))
}

// ── POST /api/scripts/import-jobs/{job_id}/cancel ────────────────────────────

async fn api_import_job_cancel(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(job_id): Path<String>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    // 先校验归属
    let job = script_import::get_job(&state.db, &job_id)
        .await
        .map_err(|_| ResponseError::not_found("import job 不存在"))?;
    if job.user_id != i64::from(user.id) {
        return Err(ResponseError::forbidden("无权操作该 job"));
    }
    // 只允许对 pending/running 取消
    if !matches!(job.status.as_str(), "pending" | "running") {
        return Err(ResponseError::bad_request(format!(
            "job 状态为 {} ,无法取消",
            job.status
        )));
    }
    sqlx::query(
        r#"
        update import_jobs
        set status = 'cancelled', finished_at = coalesce(finished_at, now()), updated_at = now()
        where job_id = $1 and status in ('pending', 'running')
        "#,
    )
    .bind(&job_id)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(Json(json!({ "ok": true, "job_id": job_id, "status": "cancelled" })))
}

// ── GET /api/scripts/import-jobs/{job_id}/stream — SSE ───────────────────────
//
// 每秒轮询 DB,状态/阶段/进度有变化时推 update 事件;
// 任务终止(done/failed/cancelled)后推 done 事件并关流。
// 最小心跳:每 15s 推一次 comment 保持 nginx/cloudflare 连接。

async fn api_import_job_stream(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(job_id): Path<String>,
) -> Result<Sse<impl futures_util::Stream<Item = Result<Event, Infallible>>>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // 先做一次权属校验(不存在直接 404)
    let job = script_import::get_job(&state.db, &job_id)
        .await
        .map_err(|_| ResponseError::not_found("import job 不存在"))?;
    if job.user_id != i64::from(user.id) {
        return Err(ResponseError::forbidden("无权访问该 job"));
    }

    let db = state.db.clone();
    let job_id_owned = job_id.clone();

    // 用 async_stream 风格的 channel-less 实现:
    // 把整个 generator 放进 stream::unfold,每次产出一个 Event 或 None(结束)。
    #[derive(Default)]
    struct StreamState {
        last_snap: Option<(String, String, i32, i32)>,
        idle_loops: u32,
        done: bool,
    }

    let init_state = StreamState::default();

    let event_stream = stream::unfold(
        (db, job_id_owned, init_state),
        |(db, job_id, mut st)| async move {
            if st.done {
                return None;
            }
            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;

            let row = sqlx::query(
                r#"
                select status, stage, stage_progress, overall_progress,
                       overall_total, stage_total, error, script_id,
                       created_at, started_at, finished_at
                from import_jobs
                where job_id = $1
                "#,
            )
            .bind(&job_id)
            .fetch_optional(&db)
            .await;

            let row = match row {
                Ok(Some(r)) => r,
                Ok(None) => {
                    let ev = named_sse_event("error", json!({ "error": "job not found" }));
                    st.done = true;
                    return Some((Ok(ev), (db, job_id, st)));
                }
                Err(e) => {
                    let ev = named_sse_event("error", json!({ "error": e.to_string() }));
                    st.done = true;
                    return Some((Ok(ev), (db, job_id, st)));
                }
            };

            let status: String = row.try_get::<String, _>("status").unwrap_or_default();
            let stage: String = row.try_get::<String, _>("stage").unwrap_or_default();
            let stage_progress: i32 = row.try_get("stage_progress").unwrap_or(0);
            let overall_progress: i32 = row.try_get("overall_progress").unwrap_or(0);

            let snap = (
                status.clone(),
                stage.clone(),
                stage_progress,
                overall_progress,
            );

            let is_terminal = matches!(status.as_str(), "done" | "failed" | "cancelled");

            let ev = if Some(&snap) != st.last_snap.as_ref() {
                st.last_snap = Some(snap);
                st.idle_loops = 0;
                let payload = json!({
                    "status": status,
                    "stage": stage,
                    "stage_progress": stage_progress,
                    "overall_progress": overall_progress,
                    "overall_total": row.try_get::<i32, _>("overall_total").unwrap_or(0),
                    "stage_total": row.try_get::<i32, _>("stage_total").unwrap_or(0),
                    "error": row.try_get::<String, _>("error").unwrap_or_default(),
                    "script_id": row.try_get::<Option<i64>, _>("script_id").ok().flatten(),
                });
                if is_terminal {
                    st.done = true;
                    // 先推 update,下一轮会发 done 事件 —— 但 done=true 会退出。
                    // 改为直接推 done event(含最终状态)。
                    named_sse_event("done", payload)
                } else {
                    named_sse_event("update", payload)
                }
            } else {
                st.idle_loops += 1;
                if is_terminal {
                    st.done = true;
                    named_sse_event("done", json!({ "status": status }))
                } else if st.idle_loops % 15 == 0 {
                    // 心跳 comment
                    Event::default().comment("heartbeat")
                } else {
                    // 无变化,不产出任何 event —— 但 unfold 必须产出 Some
                    // 用一个空 comment 占位,前端会忽略
                    Event::default().comment("")
                }
            };

            Some((Ok(ev), (db, job_id, st)))
        },
    );

    Ok(Sse::new(event_stream).keep_alive(KeepAlive::default()))
}
