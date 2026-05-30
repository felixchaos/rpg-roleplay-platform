//! `/api/scripts/*` — 剧本列表 / 章节 / 知识库同步 / overrides 等。
//!
//! 对应 Python: `rpg/platform_app/api/scripts.py` (574 行)。
//! Service: `rpg_platform::script_import`、`rpg_platform::library`。
//!
//! # 审计注:UPLOAD-001 — 误报
//! 审计报告称"Upload chunked file endpoints missing",但这些端点已在
//! `crates/rpg-routes/src/imports.rs` 的 `router()` 中注册:
//!   - POST /api/uploads/init
//!   - POST /api/uploads/:upload_id/chunk
//!   - POST /api/uploads/:upload_id/finish
//!   - POST /api/uploads/:upload_id/cancel
//!
//! 该 router 在 `rpg-server/src/main.rs` 通过 `.merge(imports::router())` 挂载。
//! UPLOAD-001 为误报,无需修复。

use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row;
use crate::{require_user, AppState, ResponseError};

// ── 公开 router ──────────────────────────────────────────────────────────────

pub fn router() -> Router<AppState> {
    Router::new()
        // 核心 GET 列表
        .route("/api/scripts", get(api_scripts))
        // 重要:preview 和 import 必须在 :script_id 之前注册,否则 axum 路径匹配会把
        // 字面量段当参数。axum 0.7 优先精确匹配字面量,但加在前面更安全。
        .route("/api/scripts/preview", post(api_script_preview))
        .route("/api/scripts/import", post(api_import_script))
        .route("/api/scripts/batch-import", post(api_scripts_batch_import))
        .route("/api/scripts/import-pack", post(api_import_script_pack))
        // 剧本章节 (注意 merge 和 chapter_index 的顺序)
        .route("/api/scripts/:script_id/chapters", get(api_script_chapters))
        .route("/api/scripts/:script_id/chapters/merge", post(api_chapter_merge))
        .route("/api/scripts/:script_id/chapters/:chapter_index", post(api_chapter_update))
        .route("/api/scripts/:script_id/chapters/:chapter_index/split", post(api_chapter_split))
        // 知识库
        .route("/api/scripts/:script_id/chapter-facts", get(api_script_chapter_facts))
        .route("/api/scripts/:script_id/character-cards", get(api_script_character_cards).post(api_script_upsert_character_card))
        .route("/api/scripts/:script_id/character-cards/:card_id", get(api_script_character_card))
        .route("/api/scripts/:script_id/character-cards/:card_id/delete", post(api_script_delete_character_card))
        .route("/api/scripts/:script_id/character-cards/:card_id/enabled", post(api_script_card_enabled))
        .route("/api/scripts/:script_id/worldbook", get(api_script_worldbook))
        // 出生点 / 推荐身份
        .route("/api/scripts/:script_id/birthpoints", get(api_script_birthpoints))
        .route("/api/scripts/:script_id/recommend-identity", post(api_script_recommend_identity))
        // overrides
        .route("/api/scripts/:script_id/overrides", get(api_get_script_overrides).post(api_update_script_overrides))
        // 操作
        .route("/api/scripts/:script_id/delete", post(api_script_delete))
        .route("/api/scripts/:script_id/resplit", post(api_script_resplit))
        .route("/api/scripts/:script_id/knowledge/sync", post(api_knowledge_sync))
        // embed (stub)
        .route("/api/scripts/:script_id/embed", get(api_script_embed_get).post(api_script_embed_post))
        .route("/api/scripts/:script_id/embed/status", get(api_script_embed_status))
        // import 状态
        .route("/api/scripts/:script_id/import-budget", get(api_import_status_get))
        .route("/api/scripts/:script_id/import-pipeline", get(api_import_status_get))
        .route("/api/scripts/:script_id/import-status", get(api_import_status_get))
        // export-pack
        .route("/api/scripts/:script_id/export-pack", get(api_export_pack))
}

// ── GET /api/scripts/{script_id}/import-{budget,pipeline,status} ────────────
//
// 三个路由复用同一个 handler:查 import_jobs 表取最近一条 job。

async fn api_import_status_get(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    let row = sqlx::query(
        r#"
        SELECT job_id, status, stage, stage_progress, stage_total,
               overall_progress, overall_total, budget_estimate, usage_actual,
               stages, error, started_at, finished_at, created_at, kind
        FROM import_jobs
        WHERE script_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        "#,
    )
    .bind(script_id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    match row {
        None => Ok(Json(json!({"ok": true, "status": "none"}))),
        Some(r) => Ok(Json(json!({
            "ok": true,
            "job_id": r.try_get::<String, _>("job_id").unwrap_or_default(),
            "status": r.try_get::<String, _>("status").unwrap_or_default(),
            "stage": r.try_get::<String, _>("stage").unwrap_or_default(),
            "stage_progress": r.try_get::<i32, _>("stage_progress").unwrap_or(0),
            "stage_total": r.try_get::<i32, _>("stage_total").unwrap_or(0),
            "overall_progress": r.try_get::<i32, _>("overall_progress").unwrap_or(0),
            "overall_total": r.try_get::<i32, _>("overall_total").unwrap_or(5),
            "budget_estimate": r.try_get::<Value, _>("budget_estimate").unwrap_or(json!({})),
            "usage_actual": r.try_get::<Value, _>("usage_actual").unwrap_or(json!({})),
            "stages": r.try_get::<Value, _>("stages").unwrap_or(json!([])),
            "error": r.try_get::<String, _>("error").unwrap_or_default(),
            "started_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("started_at").unwrap_or_default(),
            "finished_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("finished_at").unwrap_or_default(),
            "created_at": r.try_get::<chrono::DateTime<chrono::Utc>, _>("created_at").ok(),
            "kind": r.try_get::<String, _>("kind").unwrap_or_default(),
        }))),
    }
}

// ── GET /api/scripts/{script_id}/export-pack ─────────────────────────────────
//
// 返回剧本 ZIP 包,对应 Python 行为:
//   manifest.json + chapters.jsonl + character_cards.jsonl + worldbook_entries.jsonl
// Content-Type: application/zip
// Content-Disposition: attachment; filename=script_pack.zip

async fn api_export_pack(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
) -> Result<Response<Body>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let script_row = sqlx::query(
        "SELECT id, title, description, source_path, chapter_count, word_count, created_at, updated_at \
         FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let Some(sr) = script_row else {
        // Return a JSON error for auth failures (not a ZIP)
        let body = serde_json::to_vec(&json!({"ok": false, "error": "无权访问该剧本"}))
            .unwrap_or_default();
        return Ok(Response::builder()
            .status(StatusCode::FORBIDDEN)
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(body))
            .unwrap());
    };

    let chapters = sqlx::query(
        "SELECT chapter_index, title, volume_title, content, word_count \
         FROM script_chapters WHERE script_id = $1 ORDER BY chapter_index",
    )
    .bind(script_id)
    .fetch_all(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let characters = sqlx::query(
        "SELECT name, identity, appearance, personality, speech_style, \
                current_status, secrets, sample_dialogue, enabled, priority \
         FROM character_cards WHERE script_id = $1 ORDER BY priority DESC",
    )
    .bind(script_id)
    .fetch_all(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let worldbook = sqlx::query(
        "SELECT title, content, keys, priority, enabled, insertion_position \
         FROM worldbook_entries WHERE script_id = $1 ORDER BY priority DESC",
    )
    .bind(script_id)
    .fetch_all(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    // Build in-memory ZIP
    let title = sr.try_get::<String, _>("title").unwrap_or_default();
    let zip_bytes = build_export_zip(&sr, &title, &chapters, &characters, &worldbook)
        .map_err(ResponseError::internal)?;

    let filename = format!(
        "script_{}_pack.zip",
        sr.try_get::<i64, _>("id").unwrap_or(script_id)
    );
    let disposition = format!("attachment; filename=\"{}\"", filename);

    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/zip")
        .header(header::CONTENT_DISPOSITION, disposition)
        .body(Body::from(zip_bytes))
        .unwrap())
}

/// Build the export ZIP in memory.
/// Layout:
///   manifest.json           — pack metadata
///   chapters.jsonl          — one JSON object per line
///   character_cards.jsonl   — one JSON object per line
///   worldbook_entries.jsonl — one JSON object per line
fn build_export_zip(
    sr: &sqlx::postgres::PgRow,
    title: &str,
    chapters: &[sqlx::postgres::PgRow],
    characters: &[sqlx::postgres::PgRow],
    worldbook: &[sqlx::postgres::PgRow],
) -> Result<Vec<u8>, String> {
    use std::io::Write as _;
    use zip::{write::FileOptions, ZipWriter};

    let buf = std::io::Cursor::new(Vec::new());
    let mut zip = ZipWriter::new(buf);
    let options = FileOptions::<()>::default()
        .compression_method(zip::CompressionMethod::Deflated);

    // manifest.json
    let manifest = json!({
        "version": "1",
        "kind": "script_pack",
        "title": title,
        "script_id": sr.try_get::<i64, _>("id").unwrap_or(0),
        "chapter_count": sr.try_get::<i32, _>("chapter_count").unwrap_or(0),
        "word_count": sr.try_get::<i32, _>("word_count").unwrap_or(0),
    });
    zip.start_file("manifest.json", options)
        .map_err(|e| e.to_string())?;
    zip.write_all(
        serde_json::to_string_pretty(&manifest)
            .unwrap_or_default()
            .as_bytes(),
    )
    .map_err(|e| e.to_string())?;

    // chapters.jsonl
    zip.start_file("chapters.jsonl", options)
        .map_err(|e| e.to_string())?;
    for r in chapters {
        let obj = json!({
            "chapter_index": r.try_get::<i32, _>("chapter_index").unwrap_or(0),
            "title": r.try_get::<String, _>("title").unwrap_or_default(),
            "volume_title": r.try_get::<String, _>("volume_title").unwrap_or_default(),
            "content": r.try_get::<String, _>("content").unwrap_or_default(),
            "word_count": r.try_get::<i32, _>("word_count").unwrap_or(0),
        });
        let mut line = serde_json::to_string(&obj).unwrap_or_default();
        line.push('\n');
        zip.write_all(line.as_bytes()).map_err(|e| e.to_string())?;
    }

    // character_cards.jsonl
    zip.start_file("character_cards.jsonl", options)
        .map_err(|e| e.to_string())?;
    for r in characters {
        let obj = json!({
            "name": r.try_get::<String, _>("name").unwrap_or_default(),
            "identity": r.try_get::<String, _>("identity").unwrap_or_default(),
            "appearance": r.try_get::<String, _>("appearance").unwrap_or_default(),
            "personality": r.try_get::<String, _>("personality").unwrap_or_default(),
            "speech_style": r.try_get::<String, _>("speech_style").unwrap_or_default(),
            "current_status": r.try_get::<String, _>("current_status").unwrap_or_default(),
            "secrets": r.try_get::<String, _>("secrets").unwrap_or_default(),
            "sample_dialogue": r.try_get::<Value, _>("sample_dialogue").unwrap_or(json!([])),
            "enabled": r.try_get::<bool, _>("enabled").unwrap_or(true),
            "priority": r.try_get::<i32, _>("priority").unwrap_or(100),
        });
        let mut line = serde_json::to_string(&obj).unwrap_or_default();
        line.push('\n');
        zip.write_all(line.as_bytes()).map_err(|e| e.to_string())?;
    }

    // worldbook_entries.jsonl
    zip.start_file("worldbook_entries.jsonl", options)
        .map_err(|e| e.to_string())?;
    for r in worldbook {
        let obj = json!({
            "title": r.try_get::<String, _>("title").unwrap_or_default(),
            "content": r.try_get::<String, _>("content").unwrap_or_default(),
            "keys": r.try_get::<Value, _>("keys").unwrap_or(json!([])),
            "priority": r.try_get::<i32, _>("priority").unwrap_or(50),
            "enabled": r.try_get::<bool, _>("enabled").unwrap_or(true),
            "insertion_position": r.try_get::<String, _>("insertion_position").unwrap_or_default(),
        });
        let mut line = serde_json::to_string(&obj).unwrap_or_default();
        line.push('\n');
        zip.write_all(line.as_bytes()).map_err(|e| e.to_string())?;
    }

    let cursor = zip.finish().map_err(|e| e.to_string())?;
    Ok(cursor.into_inner())
}

// ── GET /api/scripts ─────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct ScriptsQuery {
    limit: Option<i64>,
    cursor: Option<String>,
}

async fn api_scripts(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<ScriptsQuery>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let scripts = rpg_platform::library::list_scripts(&state.db, user.id.into()).await?;

    // 简单游标分页 (cursor = script id 字符串)
    let cursor_id: Option<i64> = q.cursor.as_deref().and_then(|s| s.parse().ok());
    let limit = q.limit.unwrap_or(50).clamp(1, 200) as usize;

    let filtered: Vec<_> = match cursor_id {
        Some(cid) => scripts.into_iter().filter(|s| s.id < cid).collect(),
        None => scripts,
    };
    let page: Vec<_> = filtered.iter().take(limit).collect();
    let next_cursor = if page.len() == limit {
        page.last().map(|s| s.id.to_string())
    } else {
        None
    };

    Ok(Json(json!({
        "ok": true,
        "scripts": page,
        "next_cursor": next_cursor,
    })))
}

// ── POST /api/scripts/import ─────────────────────────────────────────────────

#[derive(Deserialize)]
struct ImportBody {
    file: Option<Value>,
    upload_id: Option<String>,
    split_rule: Option<String>,
    custom_pattern: Option<String>,
    title: Option<String>,
}

async fn api_import_script(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ImportBody>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let upload_id = body.upload_id.as_deref().unwrap_or("").trim();
    let split_rule = body.split_rule.as_deref().unwrap_or("auto");
    let custom_pattern = body.custom_pattern.as_deref().unwrap_or("");
    let title = body.title.as_deref().unwrap_or("");

    // Determine the import source
    let source = if !upload_id.is_empty() {
        let name = body.file
            .as_ref()
            .and_then(|f| f.get("name"))
            .and_then(|v| v.as_str());
        rpg_platform::script_import::ImportSource::Upload {
            upload_id,
            name,
        }
    } else if let Some(file_val) = &body.file {
        let name = file_val.get("name").and_then(|v| v.as_str()).unwrap_or("script.txt");
        let b64 = file_val
            .get("base64")
            .and_then(|v| v.as_str())
            .ok_or_else(|| ResponseError::bad_request("请提供 file.base64 或 upload_id"))?;
        use base64::Engine as _;
        let raw = base64::engine::general_purpose::STANDARD
            .decode(b64)
            .map_err(|e| ResponseError::bad_request(format!("base64 解码失败: {e}")))?;
        rpg_platform::script_import::ImportSource::Bytes { name, raw }
    } else {
        return Ok(Json(json!({"ok": false, "error": "请提供 file 或 upload_id"})));
    };

    // No embedding client in AppState — pass None (will skip background embed)
    let result = rpg_platform::script_import::import_script(
        &state.db,
        user.id.into(),
        source,
        split_rule,
        custom_pattern,
        title,
        None, // embedding_client
    )
    .await
    .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "script": {
            "id": result.script_id,
            "title": result.title,
            "chapter_count": result.chapter_count,
            "word_count": result.word_count,
        },
        "report": {
            "mode": result.report.mode,
            "mode_label": result.report.mode_label,
            "confidence": result.report.confidence,
            "chapter_count": result.report.chapter_count,
            "total_words": result.report.total_words,
            "average_words": result.report.average_words,
            "min_words": result.report.min_words,
            "max_words": result.report.max_words,
            "split_rule": result.report.split_rule,
            "reasons": result.report.reasons,
            "encoding": result.encoding,
            "source_path": result.source_path,
        },
        "knowledge": {
            "ok": true,
            "job_id": result.knowledge_job_id,
            "status": "pending",
            "async": true,
        },
        "preview": result.preview.iter().map(|p| json!({
            "chapter_index": p.chapter_index,
            "title": p.title,
            "volume_title": p.volume_title,
            "word_count": p.word_count,
            "content_preview": p.content_preview,
        })).collect::<Vec<_>>(),
    })))
}

// ── POST /api/scripts/batch-import ───────────────────────────────────────────

#[derive(Deserialize)]
struct BatchImportBody {
    file: Option<Value>,
    split_rule: Option<String>,
}

async fn api_scripts_batch_import(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<BatchImportBody>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let file_val = body.file.as_ref()
        .ok_or_else(|| ResponseError::bad_request("缺 file"))?;
    let b64 = file_val.get("base64").and_then(|v| v.as_str())
        .ok_or_else(|| ResponseError::bad_request("缺 file.base64"))?;

    use base64::Engine as _;
    let raw = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| ResponseError::bad_request(format!("base64 解码失败: {e}")))?;

    // Phase 1: Extract all file contents synchronously
    let file_entries = {
        use std::io::Read;
        let cursor = std::io::Cursor::new(&raw);
        let mut archive = zip::ZipArchive::new(cursor)
            .map_err(|_| ResponseError::bad_request("不是合法 ZIP 文件"))?;

        let names: Vec<String> = (0..archive.len())
            .filter_map(|i| {
                let f = archive.by_index(i).ok()?;
                let name = f.name().to_string();
                if name.to_lowercase().ends_with(".txt") || name.to_lowercase().ends_with(".md") {
                    Some(name)
                } else {
                    None
                }
            })
            .collect();

        if names.len() > 50 {
            return Ok(Json(json!({"ok": false, "error": "ZIP 最多包含 50 个文件"})));
        }

        let mut entries: Vec<(String, Result<Vec<u8>, String>)> = Vec::new();
        for name in names {
            let result = (|| {
                let mut file = archive.by_name(&name).map_err(|e| e.to_string())?;
                let mut buf = Vec::new();
                file.read_to_end(&mut buf).map_err(|e| e.to_string())?;
                Ok(buf)
            })();
            entries.push((name, result));
        }
        entries
    }; // archive dropped here

    // Phase 2: Import each file (async)
    let split_rule = body.split_rule.as_deref().unwrap_or("auto");
    let mut imported = Vec::new();
    let mut failed = Vec::new();

    for (name, content_result) in &file_entries {
        let content = match content_result {
            Ok(c) => c,
            Err(e) => {
                failed.push(json!({"name": name, "error": e}));
                continue;
            }
        };

        if content.len() > rpg_platform::script_import::max_script_upload_bytes() {
            failed.push(json!({"name": name, "error": "too large"}));
            continue;
        }

        let short_name = name.rsplit('/').next().unwrap_or(name);
        let source = rpg_platform::script_import::ImportSource::Bytes {
            name: short_name,
            raw: content.clone(),
        };

        match rpg_platform::script_import::import_script(
            &state.db,
            user.id.into(),
            source,
            split_rule,
            "",
            "",
            None,
        )
        .await
        {
            Ok(result) => {
                imported.push(json!({"name": name, "script_id": result.script_id}));
            }
            Err(e) => {
                let msg: String = e.to_string().chars().take(200).collect();
                failed.push(json!({"name": name, "error": msg}));
            }
        }
    }

    let total = file_entries.len();
    let succeeded = imported.len();

    Ok(Json(json!({
        "ok": true,
        "imported": imported,
        "failed": failed,
        "total": total,
        "succeeded": succeeded,
    })))
}

// ── POST /api/scripts/import-pack ────────────────────────────────────────────

const MAX_PACK_ZIP_BYTES: usize = 200 * 1024 * 1024; // 200MB

async fn api_import_script_pack(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    if body.is_empty() {
        return Err(ResponseError::bad_request("empty request body"));
    }
    if body.len() > MAX_PACK_ZIP_BYTES {
        return Err(ResponseError::bad_request(format!(
            "file too large (max {}MB)",
            MAX_PACK_ZIP_BYTES / 1024 / 1024
        )));
    }

    // Phase 1: Extract all data synchronously (no borrows across await)
    let pack = extract_pack_from_zip(&body)?;

    // Phase 2: Write to DB
    let script_row = sqlx::query(
        r#"
        INSERT INTO scripts (owner_id, title, description, source_path,
                             chapter_count, word_count)
        VALUES ($1, $2, $3, '', $4, $5)
        RETURNING id
        "#,
    )
    .bind::<i64>(user.id.into())
    .bind(&pack.title)
    .bind(&pack.description)
    .bind(pack.chapters.len() as i32)
    .bind(pack.word_count)
    .fetch_one(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let new_script_id: i64 = script_row.try_get("id")
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    for ch in &pack.chapters {
        let ci = ch.get("chapter_index").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
        let ch_title = ch.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let content = ch.get("content").and_then(|v| v.as_str()).unwrap_or("");
        let wc = ch.get("word_count").and_then(|v| v.as_i64()).unwrap_or(content.chars().count() as i64) as i32;
        let vol = ch.get("volume_title").and_then(|v| v.as_str()).unwrap_or("");
        let marker = ch.get("source_marker").and_then(|v| v.as_str()).unwrap_or("");
        let conf = ch.get("confidence").and_then(|v| v.as_f64()).unwrap_or(0.0);

        sqlx::query(
            r#"
            INSERT INTO script_chapters(
                script_id, chapter_index, title, content, word_count,
                volume_title, source_marker, confidence
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            "#,
        )
        .bind(new_script_id)
        .bind(ci)
        .bind(ch_title)
        .bind(content)
        .bind(wc)
        .bind(vol)
        .bind(marker)
        .bind(conf)
        .execute(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    }

    let mut warnings: Vec<String> = Vec::new();

    for card in &pack.cards {
        if let Err(e) = sqlx::query(
            r#"
            INSERT INTO character_cards(script_id, name, identity, appearance, personality,
                                        speech_style, aliases, sample_dialogue, priority, enabled)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            "#,
        )
        .bind(new_script_id)
        .bind(card.get("name").and_then(|v| v.as_str()).unwrap_or(""))
        .bind(card.get("identity").and_then(|v| v.as_str()).unwrap_or(""))
        .bind(card.get("appearance").and_then(|v| v.as_str()).unwrap_or(""))
        .bind(card.get("personality").and_then(|v| v.as_str()).unwrap_or(""))
        .bind(card.get("speech_style").and_then(|v| v.as_str()).unwrap_or(""))
        .bind(card.get("aliases").unwrap_or(&json!([])))
        .bind(card.get("sample_dialogue").unwrap_or(&json!([])))
        .bind(card.get("priority").and_then(|v| v.as_i64()).unwrap_or(100) as i32)
        .bind(card.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true))
        .execute(&state.db)
        .await
        {
            warnings.push(format!("character_card import error: {}", e));
        }
    }

    for wb in &pack.worldbook {
        if let Err(e) = sqlx::query(
            r#"
            INSERT INTO worldbook_entries(script_id, title, content, keys, priority, enabled)
            VALUES ($1, $2, $3, $4, $5, $6)
            "#,
        )
        .bind(new_script_id)
        .bind(wb.get("title").and_then(|v| v.as_str()).unwrap_or(""))
        .bind(wb.get("content").and_then(|v| v.as_str()).unwrap_or(""))
        .bind(wb.get("keys").unwrap_or(&json!([])))
        .bind(wb.get("priority").and_then(|v| v.as_i64()).unwrap_or(50) as i32)
        .bind(wb.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true))
        .execute(&state.db)
        .await
        {
            warnings.push(format!("worldbook import error: {}", e));
        }
    }

    if let Some(ref overrides_data) = pack.overrides {
        let _ = sqlx::query(
            r#"
            INSERT INTO script_overrides(script_id, data)
            VALUES ($1, $2)
            ON CONFLICT(script_id) DO UPDATE SET data = $2, updated_at = now()
            "#,
        )
        .bind(new_script_id)
        .bind(overrides_data)
        .execute(&state.db)
        .await;
    }

    Ok(Json(json!({
        "ok": true,
        "script_id": new_script_id,
        "warnings": warnings,
    })))
}

/// All data extracted from a script pack ZIP (fully owned, no borrows).
struct PackData {
    title: String,
    description: String,
    word_count: i64,
    chapters: Vec<Value>,
    cards: Vec<Value>,
    worldbook: Vec<Value>,
    overrides: Option<Value>,
}

/// Synchronously extract all needed data from a ZIP pack.
fn extract_pack_from_zip(body: &[u8]) -> Result<PackData, ResponseError> {
    use std::io::Read;

    let cursor = std::io::Cursor::new(body);
    let mut archive = zip::ZipArchive::new(cursor)
        .map_err(|e| ResponseError::bad_request(format!("not a valid zip file: {e}")))?;

    // zip-slip defense
    for i in 0..archive.len() {
        let f = archive.by_index(i)
            .map_err(|e| ResponseError::bad_request(e.to_string()))?;
        let name = f.name().replace('\\', "/");
        if name.starts_with('/') || name.split('/').any(|p| p == "..") {
            return Err(ResponseError::bad_request(format!(
                "zip-slip attempt detected: {name}"
            )));
        }
    }

    // Read manifest.json (validate it exists)
    {
        let mut f = archive.by_name("manifest.json")
            .map_err(|_| ResponseError::bad_request("missing manifest.json in pack"))?;
        let mut buf = Vec::new();
        f.read_to_end(&mut buf)
            .map_err(|e| ResponseError::bad_request(e.to_string()))?;
        let _manifest: Value = serde_json::from_slice(&buf)
            .map_err(|e| ResponseError::bad_request(format!("invalid manifest.json: {e}")))?;
    }

    // Read script.json
    let script_data: Value = {
        let mut f = archive.by_name("script.json")
            .map_err(|_| ResponseError::bad_request("missing script.json in pack"))?;
        let mut buf = Vec::new();
        f.read_to_end(&mut buf)
            .map_err(|e| ResponseError::bad_request(e.to_string()))?;
        serde_json::from_slice(&buf)
            .map_err(|e| ResponseError::bad_request(format!("invalid script.json: {e}")))?
    };

    let chapters = read_jsonl_entry(&mut archive, "chapters.jsonl")?;
    let cards = read_jsonl_entry(&mut archive, "character_cards.jsonl").unwrap_or_default();
    let worldbook = read_jsonl_entry(&mut archive, "worldbook.jsonl").unwrap_or_default();

    let overrides: Option<Value> = archive.by_name("overrides.json").ok().and_then(|mut f| {
        let mut buf = Vec::new();
        f.read_to_end(&mut buf).ok()?;
        serde_json::from_slice(&buf).ok()
    });

    let title = script_data.get("title").and_then(|v| v.as_str()).unwrap_or("Imported script").to_string();
    let description = script_data.get("description").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let word_count: i64 = chapters.iter()
        .map(|c| c.get("word_count").and_then(|v| v.as_i64()).unwrap_or(0))
        .sum();

    Ok(PackData { title, description, word_count, chapters, cards, worldbook, overrides })
}

fn read_jsonl_entry<R: std::io::Read + std::io::Seek>(
    archive: &mut zip::ZipArchive<R>,
    name: &str,
) -> Result<Vec<Value>, ResponseError> {
    use std::io::Read;
    let mut f = match archive.by_name(name) {
        Ok(f) => f,
        Err(_) => return Ok(Vec::new()),
    };
    let mut buf = Vec::new();
    f.read_to_end(&mut buf)
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;
    let text = String::from_utf8_lossy(&buf);
    let mut items = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(v) = serde_json::from_str::<Value>(line) {
            items.push(v);
        }
    }
    Ok(items)
}

// ── POST /api/scripts/preview ────────────────────────────────────────────────

#[derive(Deserialize)]
struct PreviewBody {
    split_rule: Option<String>,
    custom_pattern: Option<String>,
    upload_id: Option<String>,
    sample_limit: Option<usize>,
    // file_item: base64 文件(暂不处理,留 stub 分支)
    file: Option<Value>,
}

async fn api_script_preview(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<PreviewBody>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let split_rule = body.split_rule.as_deref().unwrap_or("auto");
    let custom_pattern = body.custom_pattern.as_deref().unwrap_or("");
    let sample_limit = body.sample_limit.unwrap_or(20).clamp(1, 100);

    // 取 raw bytes
    let raw: Vec<u8> = if let Some(uid) = body.upload_id.as_deref().filter(|s| !s.is_empty()) {
        rpg_platform::script_import::consume_upload_chunks(user.id.into(), uid, true)
            .map_err(|e| ResponseError::bad_request(e.to_string()))?
    } else if let Some(file_val) = &body.file {
        // base64 decode
        let b64 = file_val
            .get("base64")
            .and_then(|v| v.as_str())
            .ok_or_else(|| ResponseError::bad_request("请提供 file.base64 或 upload_id"))?;
        use base64::Engine as _;
        base64::engine::general_purpose::STANDARD
            .decode(b64)
            .map_err(|e| ResponseError::bad_request(format!("base64 解码失败: {e}")))?
    } else {
        return Ok(Json(json!({"ok": false, "error": "请提供 file 或 upload_id"})));
    };

    let (text, encoding) = rpg_platform::script_import::splitter::decode_bytes(&raw);
    let (chapters, report) =
        rpg_platform::script_import::splitter::split_chapters_with_report(&text, split_rule, custom_pattern);

    let preview: Vec<Value> = chapters
        .iter()
        .take(sample_limit)
        .map(|c| {
            let preview_text: String = c.content.replace('\n', " ").chars().take(200).collect();
            json!({
                "chapter_index": c.chapter_number,
                "title": c.title,
                "volume_title": c.volume_title,
                "word_count": c.content.chars().count(),
                "content_preview": preview_text,
            })
        })
        .collect();

    Ok(Json(json!({
        "ok": true,
        "encoding": encoding,
        "chapter_count": chapters.len(),
        "report": {
            "mode": report.mode,
            "mode_label": report.mode_label,
            "confidence": report.confidence,
            "chapter_count": report.chapter_count,
            "total_words": report.total_words,
            "average_words": report.average_words,
            "min_words": report.min_words,
            "max_words": report.max_words,
            "split_rule": report.split_rule,
            "reasons": report.reasons,
        },
        "preview": preview,
    })))
}

// ── GET /api/scripts/{script_id}/chapters ────────────────────────────────────

#[derive(Deserialize)]
struct ChaptersQuery {
    limit: Option<i64>,
    cursor: Option<String>,
    q: Option<String>,
}

async fn api_script_chapters(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    Query(query): Query<ChaptersQuery>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // 先确认归属
    let owned = sqlx::query_scalar::<_, i64>(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    if let Some(q) = query.q.as_deref().filter(|s| !s.is_empty()) {
        let limit = query.limit.unwrap_or(50).clamp(1, 200);
        let like = format!("%{}%", q);
        let rows = sqlx::query(
            r#"
            select id, chapter_index, title, volume_title, word_count, content
            from script_chapters
            where script_id = $1 and (title ilike $2 or content ilike $3)
            order by chapter_index limit $4
            "#,
        )
        .bind(script_id)
        .bind(&like)
        .bind(&like)
        .bind(limit)
        .fetch_all(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

        // Gap 8: 搜索结果截断 200 字符,不返全文
        let items: Vec<Value> = rows
            .iter()
            .map(|r| {
                let ci = r.try_get::<i32,_>("chapter_index").unwrap_or_default();
                let full_content: String = r.try_get::<String,_>("content").unwrap_or_default();
                let content_preview: String = full_content.chars().take(200).collect();
                json!({
                    "id": r.try_get::<i64,_>("id").unwrap_or_default(),
                    "chapter_index": ci,
                    "index": ci,
                    "title": r.try_get::<String,_>("title").unwrap_or_default(),
                    "volume_title": r.try_get::<String,_>("volume_title").unwrap_or_default(),
                    "word_count": r.try_get::<i32,_>("word_count").unwrap_or_default(),
                    "content": content_preview,
                })
            })
            .collect();

        return Ok(Json(json!({"ok": true, "items": items, "query": q})));
    }

    // 游标分页
    let limit = query.limit.unwrap_or(50).clamp(1, 200);
    let cursor_index: Option<i32> = query.cursor.as_deref().and_then(|s| s.parse().ok());

    let rows = if let Some(ci) = cursor_index {
        sqlx::query(
            r#"
            select id, chapter_index, title, volume_title, word_count, content
            from script_chapters
            where script_id = $1 and chapter_index > $2
            order by chapter_index limit $3
            "#,
        )
        .bind(script_id)
        .bind(ci)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    } else {
        sqlx::query(
            r#"
            select id, chapter_index, title, volume_title, word_count, content
            from script_chapters
            where script_id = $1
            order by chapter_index limit $2
            "#,
        )
        .bind(script_id)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    }
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let items: Vec<Value> = rows
        .iter()
        .map(|r| {
            let ci = r.try_get::<i32,_>("chapter_index").unwrap_or_default();
            // SCRIPTS-003: 列表响应中 content 截断到 ~200 字符(与 Python 一致)
            let full_content = r.try_get::<String,_>("content").unwrap_or_default();
            let truncated: String = full_content.chars().take(200).collect();
            json!({
                "id": r.try_get::<i64,_>("id").unwrap_or_default(),
                "chapter_index": ci,
                // SCRIPTS-004: index 是 chapter_index 的兼容别名,保留
                "index": ci,
                "title": r.try_get::<String,_>("title").unwrap_or_default(),
                "volume_title": r.try_get::<String,_>("volume_title").unwrap_or_default(),
                "word_count": r.try_get::<i32,_>("word_count").unwrap_or_default(),
                "content": truncated,
            })
        })
        .collect();

    let next_cursor = if items.len() == limit as usize {
        items
            .last()
            .and_then(|v| v.get("chapter_index"))
            .and_then(|v| v.as_i64())
            .map(|i| i.to_string())
    } else {
        None
    };

    Ok(Json(json!({
        "ok": true,
        "items": items,
        "next_cursor": next_cursor,
    })))
}

// ── GET /api/scripts/{script_id}/chapter-facts ───────────────────────────────

#[derive(Deserialize)]
struct PageQuery {
    limit: Option<i64>,
    cursor: Option<String>,
}

async fn api_script_chapter_facts(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    Query(query): Query<PageQuery>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    let limit = query.limit.unwrap_or(50).clamp(1, 200);
    let cursor_index: Option<i32> = query.cursor.as_deref().and_then(|s| s.parse().ok());

    let rows = if let Some(ci) = cursor_index {
        sqlx::query(
            r#"
            select id, script_id, chapter, story_phase, summary,
                   characters, created_at
            from chapter_facts
            where script_id = $1 and chapter > $2
            order by chapter limit $3
            "#,
        )
        .bind(script_id)
        .bind(ci)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    } else {
        sqlx::query(
            r#"
            select id, script_id, chapter, story_phase, summary,
                   characters, created_at
            from chapter_facts
            where script_id = $1
            order by chapter limit $2
            "#,
        )
        .bind(script_id)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    }
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let items: Vec<Value> = rows
        .iter()
        .map(|r| {
            json!({
                "id": r.try_get::<i64,_>("id").unwrap_or_default(),
                "script_id": r.try_get::<i64,_>("script_id").unwrap_or_default(),
                "chapter": r.try_get::<i32,_>("chapter").unwrap_or_default(),
                "story_phase": r.try_get::<String,_>("story_phase").unwrap_or_default(),
                "summary": r.try_get::<String,_>("summary").unwrap_or_default(),
                "characters": r.try_get::<Value,_>("characters").unwrap_or(json!([])),
                "created_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>,_>("created_at").unwrap_or_default(),
            })
        })
        .collect();

    let next_cursor = if items.len() == limit as usize {
        items
            .last()
            .and_then(|v| v.get("chapter"))
            .and_then(|v| v.as_i64())
            .map(|i| i.to_string())
    } else {
        None
    };

    Ok(Json(json!({
        "ok": true,
        "items": items,
        "next_cursor": next_cursor,
    })))
}

// ── GET /api/scripts/{script_id}/character-cards ─────────────────────────────

async fn api_script_character_cards(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    Query(query): Query<PageQuery>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    let limit = query.limit.unwrap_or(50).clamp(1, 200);
    let cursor_id: Option<i64> = query.cursor.as_deref().and_then(|s| s.parse().ok());

    let rows = if let Some(cid) = cursor_id {
        sqlx::query(
            r#"
            select id, script_id, name, identity, appearance, personality,
                   speech_style, aliases, sample_dialogue, priority, enabled, created_at
            from character_cards
            where script_id = $1 and id > $2 and enabled = true
            order by id limit $3
            "#,
        )
        .bind(script_id)
        .bind(cid)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    } else {
        sqlx::query(
            r#"
            select id, script_id, name, identity, appearance, personality,
                   speech_style, aliases, sample_dialogue, priority, enabled, created_at
            from character_cards
            where script_id = $1 and enabled = true
            order by id limit $2
            "#,
        )
        .bind(script_id)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    }
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let items: Vec<Value> = rows
        .iter()
        .map(|r| {
            json!({
                "id": r.try_get::<i64,_>("id").unwrap_or_default(),
                "script_id": r.try_get::<i64,_>("script_id").unwrap_or_default(),
                "name": r.try_get::<String,_>("name").unwrap_or_default(),
                "identity": r.try_get::<String,_>("identity").unwrap_or_default(),
                "appearance": r.try_get::<String,_>("appearance").unwrap_or_default(),
                "personality": r.try_get::<String,_>("personality").unwrap_or_default(),
                "speech_style": r.try_get::<String,_>("speech_style").unwrap_or_default(),
                "aliases": r.try_get::<Value,_>("aliases").unwrap_or(json!([])),
                "sample_dialogue": r.try_get::<Value,_>("sample_dialogue").unwrap_or(json!([])),
                "priority": r.try_get::<i32,_>("priority").unwrap_or(100),
                "enabled": r.try_get::<bool,_>("enabled").unwrap_or(true),
                "created_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>,_>("created_at").unwrap_or_default(),
            })
        })
        .collect();

    let next_cursor = if items.len() == limit as usize {
        items
            .last()
            .and_then(|v| v.get("id"))
            .and_then(|v| v.as_i64())
            .map(|i| i.to_string())
    } else {
        None
    };

    Ok(Json(json!({
        "ok": true,
        "items": items,
        "next_cursor": next_cursor,
    })))
}

// ── GET /api/scripts/{script_id}/character-cards/{card_id} ───────────────────

async fn api_script_character_card(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((script_id, card_id)): Path<(i64, i64)>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    // SCRIPTS-007: 单卡查询不过滤 enabled,允许查看已禁用的卡(与 Python get_character_card 一致)
    let row = sqlx::query(
        r#"
        select id, script_id, name, identity, appearance, personality,
               speech_style, aliases, sample_dialogue, priority, enabled, created_at
        from character_cards
        where id = $1 and script_id = $2
        "#,
    )
    .bind(card_id)
    .bind(script_id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    match row {
        None => Ok(Json(json!({"ok": false, "error": "character_card 不存在"}))),
        Some(r) => Ok(Json(json!({
            "ok": true,
            "card": {
                "id": r.try_get::<i64,_>("id").unwrap_or_default(),
                "script_id": r.try_get::<i64,_>("script_id").unwrap_or_default(),
                "name": r.try_get::<String,_>("name").unwrap_or_default(),
                "identity": r.try_get::<String,_>("identity").unwrap_or_default(),
                "appearance": r.try_get::<String,_>("appearance").unwrap_or_default(),
                "personality": r.try_get::<String,_>("personality").unwrap_or_default(),
                "speech_style": r.try_get::<String,_>("speech_style").unwrap_or_default(),
                "aliases": r.try_get::<Value,_>("aliases").unwrap_or(json!([])),
                "sample_dialogue": r.try_get::<Value,_>("sample_dialogue").unwrap_or(json!([])),
                "priority": r.try_get::<i32,_>("priority").unwrap_or(100),
                "enabled": r.try_get::<bool,_>("enabled").unwrap_or(true),
                "created_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>,_>("created_at").unwrap_or_default(),
            }
        }))),
    }
}

// ── POST /api/scripts/{script_id}/character-cards (upsert stub) ──────────────

async fn api_script_upsert_character_card(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;
    // 验证归属
    let owned = sqlx::query_scalar::<_, i64>("select 1::bigint from scripts where id = $1 and owner_id = $2")
        .bind(script_id).bind(user.id).fetch_optional(&state.db).await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    if owned.is_none() { return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"}))); }

    let card_id = body.get("id").and_then(|v| v.as_i64());
    let name = body.get("name").and_then(|v| v.as_str()).unwrap_or("").trim();
    if name.is_empty() { return Err(ResponseError::bad_request("name 必填")); }

    let identity = body.get("identity").and_then(|v| v.as_str()).unwrap_or("");
    let appearance = body.get("appearance").and_then(|v| v.as_str()).unwrap_or("");
    let personality = body.get("personality").and_then(|v| v.as_str()).unwrap_or("");
    let speech_style = body.get("speech_style").and_then(|v| v.as_str()).unwrap_or("");
    let secrets = body.get("secrets").and_then(|v| v.as_str()).unwrap_or("");
    let priority = body.get("priority").and_then(|v| v.as_i64()).unwrap_or(100) as i32;
    let enabled = body.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);

    let row = if let Some(cid) = card_id {
        // UPDATE
        sqlx::query(
            "UPDATE character_cards SET name=$1, identity=$2, appearance=$3, personality=$4, \
             speech_style=$5, secrets=$6, priority=$7, enabled=$8 \
             WHERE id=$9 AND script_id=$10 RETURNING id"
        )
        .bind(name).bind(identity).bind(appearance).bind(personality)
        .bind(speech_style).bind(secrets).bind(priority).bind(enabled)
        .bind(cid).bind(script_id)
        .fetch_optional(&state.db).await
        .map_err(|e| ResponseError::internal(e.to_string()))?
    } else {
        // INSERT — book_id 用 0(无 books 表外键依赖时)
        Some(sqlx::query(
            "INSERT INTO character_cards(book_id, script_id, name, identity, appearance, personality, \
             speech_style, secrets, priority, enabled) \
             VALUES (0, $1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id"
        )
        .bind(script_id).bind(name).bind(identity).bind(appearance).bind(personality)
        .bind(speech_style).bind(secrets).bind(priority).bind(enabled)
        .fetch_one(&state.db).await
        .map_err(|e| ResponseError::internal(e.to_string()))?)
    };

    let new_id = row.map(|r| r.try_get::<i64, _>("id").unwrap_or(0)).unwrap_or(0);
    // SCRIPTS-005: 返回完整 card 对象(与 Python 一致),不仅仅是 id
    let card_row = sqlx::query(
        "SELECT id, script_id, name, identity, appearance, personality, \
         speech_style, aliases, sample_dialogue, priority, enabled, created_at \
         FROM character_cards WHERE id = $1",
    )
    .bind(new_id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;
    let card = match card_row {
        Some(r) => json!({
            "id": r.try_get::<i64,_>("id").unwrap_or_default(),
            "script_id": r.try_get::<i64,_>("script_id").unwrap_or_default(),
            "name": r.try_get::<String,_>("name").unwrap_or_default(),
            "identity": r.try_get::<String,_>("identity").unwrap_or_default(),
            "appearance": r.try_get::<String,_>("appearance").unwrap_or_default(),
            "personality": r.try_get::<String,_>("personality").unwrap_or_default(),
            "speech_style": r.try_get::<String,_>("speech_style").unwrap_or_default(),
            "aliases": r.try_get::<Value,_>("aliases").unwrap_or(json!([])),
            "sample_dialogue": r.try_get::<Value,_>("sample_dialogue").unwrap_or(json!([])),
            "priority": r.try_get::<i32,_>("priority").unwrap_or(100),
            "enabled": r.try_get::<bool,_>("enabled").unwrap_or(true),
            "created_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>,_>("created_at").unwrap_or_default(),
        }),
        None => json!({"id": new_id}),
    };
    Ok(Json(json!({"ok": true, "id": new_id, "card": card})))
}

// ── POST /api/scripts/{script_id}/character-cards/{card_id}/delete ────

async fn api_script_delete_character_card(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((script_id, card_id)): Path<(i64, i64)>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let owned = sqlx::query_scalar::<_, i64>("select 1::bigint from scripts where id = $1 and owner_id = $2")
        .bind(script_id).bind(user.id).fetch_optional(&state.db).await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    if owned.is_none() { return Err(ResponseError::forbidden("无权访问该剧本")); }

    let deleted = sqlx::query("DELETE FROM character_cards WHERE id = $1 AND script_id = $2")
        .bind(card_id).bind(script_id)
        .execute(&state.db).await
        .map(|r| r.rows_affected())
        .unwrap_or(0);

    Ok(Json(json!({"ok": true, "deleted": deleted > 0})))
}

// ── POST /api/scripts/{script_id}/character-cards/{card_id}/enabled ───

async fn api_script_card_enabled(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((script_id, card_id)): Path<(i64, i64)>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let owned = sqlx::query_scalar::<_, i64>("select 1::bigint from scripts where id = $1 and owner_id = $2")
        .bind(script_id).bind(user.id).fetch_optional(&state.db).await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    if owned.is_none() { return Err(ResponseError::forbidden("无权访问该剧本")); }

    let enabled = body.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);
    sqlx::query("UPDATE character_cards SET enabled = $1 WHERE id = $2 AND script_id = $3")
        .bind(enabled).bind(card_id).bind(script_id)
        .execute(&state.db).await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(Json(json!({"ok": true, "card_id": card_id, "enabled": enabled})))
}

// ── GET /api/scripts/{script_id}/worldbook ───────────────────────────────────

async fn api_script_worldbook(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    Query(query): Query<PageQuery>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    let limit = query.limit.unwrap_or(50).clamp(1, 200);
    let cursor_id: Option<i64> = query.cursor.as_deref().and_then(|s| s.parse().ok());

    let rows = if let Some(cid) = cursor_id {
        sqlx::query(
            r#"
            select id, script_id, title, content, keys, priority, enabled, created_at
            from worldbook_entries
            where script_id = $1 and id > $2 and enabled = true
            order by id limit $3
            "#,
        )
        .bind(script_id)
        .bind(cid)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    } else {
        sqlx::query(
            r#"
            select id, script_id, title, content, keys, priority, enabled, created_at
            from worldbook_entries
            where script_id = $1 and enabled = true
            order by id limit $2
            "#,
        )
        .bind(script_id)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    }
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let items: Vec<Value> = rows
        .iter()
        .map(|r| {
            json!({
                "id": r.try_get::<i64,_>("id").unwrap_or_default(),
                "script_id": r.try_get::<i64,_>("script_id").unwrap_or_default(),
                "title": r.try_get::<String,_>("title").unwrap_or_default(),
                "content": r.try_get::<String,_>("content").unwrap_or_default(),
                "keys": r.try_get::<Value,_>("keys").unwrap_or(json!([])),
                "priority": r.try_get::<i32,_>("priority").unwrap_or(50),
                "enabled": r.try_get::<bool,_>("enabled").unwrap_or(true),
                "created_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>,_>("created_at").unwrap_or_default(),
            })
        })
        .collect();

    let next_cursor = if items.len() == limit as usize {
        items
            .last()
            .and_then(|v| v.get("id"))
            .and_then(|v| v.as_i64())
            .map(|i| i.to_string())
    } else {
        None
    };

    Ok(Json(json!({
        "ok": true,
        "items": items,
        "next_cursor": next_cursor,
    })))
}

// ── GET /api/scripts/{script_id}/birthpoints ─────────────────────────────────

async fn api_script_birthpoints(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    let phase_rows = sqlx::query(
        r#"
        select phase_label, chapter_min, chapter_max, chapter_count, summary
        from phase_digests
        where script_id = $1
        order by chapter_min asc
        "#,
    )
    .bind(script_id)
    .fetch_all(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let mut phases = Vec::new();

    for pr in &phase_rows {
        let ch_min: i32 = pr.try_get("chapter_min").unwrap_or(0);
        let ch_max: i32 = pr.try_get("chapter_max").unwrap_or(0);

        let anchor_rows = sqlx::query(
            r#"
            select id, story_time_label, chapter_min, chapter_max, chapter_count, sample_summary
            from script_timeline_anchors
            where script_id = $1
              and chapter_min >= $2
              and chapter_max <= $3
            order by chapter_min asc
            "#,
        )
        .bind(script_id)
        .bind(ch_min)
        .bind(ch_max)
        .fetch_all(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

        let n = anchor_rows.len();
        let sampled_indices: Vec<usize> = if n <= 15 {
            (0..n).collect()
        } else {
            let step = ((n as f64 / 12.0).round() as usize).max(1);
            let mut idxs: Vec<usize> = (0..n).step_by(step).collect();
            // 确保末尾 anchor 包含
            if *idxs.last().unwrap_or(&0) != n - 1 {
                idxs.push(n - 1);
            }
            idxs
        };

        let anchors: Vec<Value> = sampled_indices
            .iter()
            .filter_map(|&i| anchor_rows.get(i))
            .map(|ar| {
                json!({
                    "anchor_id": ar.try_get::<i64,_>("id").unwrap_or_default(),
                    "story_time_label": ar.try_get::<String,_>("story_time_label").unwrap_or_default(),
                    "chapter_min": ar.try_get::<i32,_>("chapter_min").unwrap_or_default(),
                    "chapter_max": ar.try_get::<i32,_>("chapter_max").unwrap_or_default(),
                    "chapter_count": ar.try_get::<i32,_>("chapter_count").unwrap_or_default(),
                    "sample_summary": ar.try_get::<String,_>("sample_summary").unwrap_or_default(),
                })
            })
            .collect();

        phases.push(json!({
            "phase_label": pr.try_get::<String,_>("phase_label").unwrap_or_default(),
            "chapter_min": ch_min,
            "chapter_max": ch_max,
            "chapter_count": pr.try_get::<i32,_>("chapter_count").unwrap_or_default(),
            "summary": pr.try_get::<String,_>("summary").unwrap_or_default(),
            "anchors": anchors,
        }));
    }

    Ok(Json(json!({"ok": true, "phases": phases})))
}

// ── GET /api/scripts/{script_id}/overrides ───────────────────────────────────

async fn api_get_script_overrides(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1 FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    let row = sqlx::query(
        "SELECT data FROM script_overrides WHERE script_id = $1",
    )
    .bind(script_id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let data = match row {
        Some(r) => r.try_get::<Value, _>("data").unwrap_or(Value::Object(Default::default())),
        None => Value::Object(Default::default()),
    };

    Ok(Json(json!({"ok": true, "data": data})))
}

// ── POST /api/scripts/{script_id}/overrides ──────────────────────────────────

async fn api_update_script_overrides(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    body: axum::body::Bytes,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1 FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    let parsed: Value = serde_json::from_slice(&body)
        .map_err(|_| ResponseError::bad_request("请求 body 必须是合法 JSON"))?;

    // 支持两种格式: {"data": {...}} 或直接 {...}
    let overrides_data = if let Some(d) = parsed.get("data").and_then(|v| if v.is_object() { Some(v.clone()) } else { None }) {
        d
    } else {
        parsed
    };

    sqlx::query(
        r#"
        INSERT INTO script_overrides(script_id, data)
        VALUES ($1, $2)
        ON CONFLICT(script_id)
        DO UPDATE SET data = $2, updated_at = now()
        "#,
    )
    .bind(script_id)
    .bind(&overrides_data)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(Json(json!({"ok": true})))
}

// ── POST /api/scripts/{script_id}/delete ─────────────────────────────────────

#[derive(Deserialize, Default)]
struct DeleteBody {
    #[serde(default)]
    force: bool,
}

async fn api_script_delete(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    body: axum::body::Bytes,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let delete_body: DeleteBody = if body.is_empty() {
        DeleteBody::default()
    } else {
        serde_json::from_slice(&body).unwrap_or_default()
    };

    // 校验归属
    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1 FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本或剧本不存在"})));
    }

    if delete_body.force {
        // force=true: 删除其下所有存档(saves)
        sqlx::query("DELETE FROM game_saves WHERE script_id = $1")
            .bind(script_id)
            .execute(&state.db)
            .await
            .map_err(|e| ResponseError::internal(e.to_string()))?;
    } else {
        // 检查是否有存档
        let save_count: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM game_saves WHERE script_id = $1",
        )
        .bind(script_id)
        .fetch_one(&state.db)
        .await
        .unwrap_or(0);

        if save_count > 0 {
            return Ok(Json(json!({
                "ok": false,
                "error": format!("该剧本下有 {} 个存档,请先删除存档或使用 force=true", save_count),
            })));
        }
    }

    // 删除剧本(级联删章节等)
    let deleted = sqlx::query(
        "DELETE FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if deleted.rows_affected() == 0 {
        return Ok(Json(json!({"ok": false, "error": "删除失败"})));
    }

    Ok(Json(json!({"ok": true, "deleted": script_id})))
}

// ── POST /api/scripts/{script_id}/knowledge/sync ─────────────────────────────

async fn api_knowledge_sync(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1 FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    // 没有 embedding_client 时直接用 schedule_knowledge_sync 的 DB-only 路径
    // 这里我们直接插入一个 sync job 记录(fire-and-forget DB 侧)
    let token = uuid::Uuid::new_v4().simple().to_string();
    let token = &token[..12];
    let job_id = format!("ks_{}_{}", script_id, token);

    sqlx::query(
        r#"
        INSERT INTO import_jobs(job_id, user_id, script_id, kind, status, stage,
                                stage_progress, stage_total, overall_progress, overall_total)
        VALUES ($1, $2, $3, 'knowledge_sync', 'pending', 'pending', 0, 1, 0, 1)
        ON CONFLICT (user_id, script_id, kind)
            WHERE status IN ('pending', 'running')
            DO NOTHING
        "#,
    )
    .bind(&job_id)
    .bind(user.id)
    .bind(script_id)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    // 查实际 job_id(ON CONFLICT DO NOTHING 时可能用的是旧的)
    let actual_job_id: Option<String> = sqlx::query_scalar(
        r#"
        SELECT job_id FROM import_jobs
        WHERE user_id = $1 AND script_id = $2 AND kind = 'knowledge_sync'
          AND status IN ('pending', 'running')
        ORDER BY created_at DESC LIMIT 1
        "#,
    )
    .bind(user.id)
    .bind(script_id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "job_id": actual_job_id.unwrap_or(job_id),
        "status": "pending",
    })))
}

// ── POST /api/scripts/{script_id}/recommend-identity ─────────────────────────

async fn api_script_recommend_identity(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    // 简化实现:取前 3 张启用的角色卡 name/identity 作为推荐(不调 LLM)
    let cards = sqlx::query(
        "SELECT name, identity FROM character_cards \
         WHERE script_id = $1 AND enabled = true \
         ORDER BY priority DESC \
         LIMIT 3",
    )
    .bind(script_id)
    .fetch_all(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let recommendations: Vec<Value> = cards.iter().map(|r| json!({
        "name": r.try_get::<String, _>("name").unwrap_or_default(),
        "identity": r.try_get::<String, _>("identity").unwrap_or_default(),
    })).collect();

    Ok(Json(json!({"ok": true, "recommendations": recommendations})))
}

// ── POST /api/scripts/{script_id}/chapters/merge ────────────────────────────

#[derive(Deserialize)]
struct MergeBody {
    // SCRIPTS-001: 后端同时兼容前端发的 {first, second} 和原有 {first_index, separator}
    first_index: Option<i32>,
    first: Option<i32>,
    #[allow(dead_code)]
    second: Option<i32>, // 前端发 second 但实际总是 first+1,仅用于兼容反序列化
    separator: Option<String>,
}

async fn api_chapter_merge(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    Json(body): Json<MergeBody>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // Verify ownership
    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1::bigint FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Err(ResponseError::forbidden("无权访问该剧本"));
    }

    // SCRIPTS-001: 兼容 first_index(原协议)和 first(前端协议)
    let first_index = body.first_index.or(body.first)
        .ok_or_else(|| ResponseError::bad_request("需要 first_index 或 first"))?;
    let second_index = first_index + 1;
    let separator = body.separator.as_deref().unwrap_or("\n\n");

    // Fetch both chapters
    let a = sqlx::query(
        "SELECT id, content FROM script_chapters WHERE script_id = $1 AND chapter_index = $2",
    )
    .bind(script_id)
    .bind(first_index)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let b = sqlx::query(
        "SELECT id, content FROM script_chapters WHERE script_id = $1 AND chapter_index = $2",
    )
    .bind(script_id)
    .bind(second_index)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let (a, b) = match (a, b) {
        (Some(a), Some(b)) => (a, b),
        _ => return Err(ResponseError::bad_request(
            format!("需要章节 {} 和 {} 都存在", first_index, second_index)
        )),
    };

    let a_id: i64 = a.try_get("id").unwrap_or_default();
    let b_id: i64 = b.try_get("id").unwrap_or_default();
    let a_content: String = a.try_get("content").unwrap_or_default();
    let b_content: String = b.try_get("content").unwrap_or_default();

    let merged_content = format!("{}{}{}", a_content, separator, b_content);
    let merged_wc = merged_content.chars().count() as i32;

    // Update first chapter with merged content
    sqlx::query(
        "UPDATE script_chapters SET content = $1, word_count = $2, updated_at = now() WHERE id = $3",
    )
    .bind(&merged_content)
    .bind(merged_wc)
    .bind(a_id)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    // Delete second chapter
    sqlx::query("DELETE FROM script_chapters WHERE id = $1")
        .bind(b_id)
        .execute(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    // Shift subsequent chapters down by 1
    sqlx::query(
        "UPDATE script_chapters SET chapter_index = chapter_index - 1, updated_at = now() \
         WHERE script_id = $1 AND chapter_index > $2",
    )
    .bind(script_id)
    .bind(second_index)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    // Update scripts.chapter_count and word_count
    let stats = sqlx::query(
        "SELECT count(*)::bigint AS n, coalesce(sum(word_count),0)::bigint AS w FROM script_chapters WHERE script_id = $1",
    )
    .bind(script_id)
    .fetch_one(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let new_count: i64 = stats.try_get("n").unwrap_or(0);
    let new_words: i64 = stats.try_get("w").unwrap_or(0);

    sqlx::query(
        "UPDATE scripts SET chapter_count = $1, word_count = $2, updated_at = now() WHERE id = $3",
    )
    .bind(new_count as i32)
    .bind(new_words)
    .bind(script_id)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    // SCRIPTS-008: 返回包含合并后章节的完整响应(与 Python 一致)
    Ok(Json(json!({
        "ok": true,
        "merged_into": first_index,
        "new_chapter_count": new_count,
        "chapter": {
            "id": a_id,
            "chapter_index": first_index,
            "index": first_index,
            "word_count": merged_wc,
        },
    })))
}

// ── POST /api/scripts/{script_id}/chapters/{chapter_index} ──────────────────

#[derive(Deserialize)]
struct ChapterUpdateBody {
    title: Option<String>,
    content: Option<String>,
    volume_title: Option<String>,
}

async fn api_chapter_update(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((script_id, chapter_index)): Path<(i64, i32)>,
    Json(body): Json<ChapterUpdateBody>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // Verify ownership
    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1::bigint FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Err(ResponseError::forbidden("无权访问该剧本"));
    }

    if body.title.is_none() && body.content.is_none() && body.volume_title.is_none() {
        return Err(ResponseError::bad_request("没有要更新的字段"));
    }

    // Build dynamic SET clause with positional parameters
    let title_val = body.title.as_ref().map(|t| {
        let truncated: String = t.chars().take(200).collect();
        truncated
    });
    let content_val = body.content.clone();
    let volume_title_val = body.volume_title.as_ref().map(|v| {
        let truncated: String = v.chars().take(200).collect();
        truncated
    });

    let mut param_idx: usize = 3; // $1 = script_id, $2 = chapter_index
    let mut query_parts = Vec::new();
    if title_val.is_some() {
        query_parts.push(format!("title = ${}", param_idx));
        param_idx += 1;
    }
    if content_val.is_some() {
        query_parts.push(format!("content = ${}", param_idx));
        param_idx += 1;
        query_parts.push(format!("word_count = ${}", param_idx));
        param_idx += 1;
    }
    if volume_title_val.is_some() {
        query_parts.push(format!("volume_title = ${}", param_idx));
        let _ = param_idx; // suppress unused warning for last increment
    }
    query_parts.push("updated_at = now()".to_string());

    let sql = format!(
        "UPDATE script_chapters SET {} \
         WHERE script_id = $1 AND chapter_index = $2 \
         RETURNING id, chapter_index, title, volume_title, word_count, content",
        query_parts.join(", ")
    );

    let mut q = sqlx::query(&sql)
        .bind(script_id)
        .bind(chapter_index);

    if let Some(ref t) = title_val {
        q = q.bind(t);
    }
    if let Some(ref c) = content_val {
        q = q.bind(c);
        q = q.bind(c.chars().count() as i32);
    }
    if let Some(ref v) = volume_title_val {
        q = q.bind(v);
    }

    let row = q
        .fetch_optional(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    let row = match row {
        Some(r) => r,
        None => return Err(ResponseError::bad_request(format!("章节 {} 不存在", chapter_index))),
    };

    // Sync scripts.word_count
    let total: i64 = sqlx::query_scalar(
        "SELECT coalesce(sum(word_count),0)::bigint FROM script_chapters WHERE script_id = $1",
    )
    .bind(script_id)
    .fetch_one(&state.db)
    .await
    .unwrap_or(0);

    sqlx::query("UPDATE scripts SET word_count = $1, updated_at = now() WHERE id = $2")
        .bind(total)
        .bind(script_id)
        .execute(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    let ci = row.try_get::<i32,_>("chapter_index").unwrap_or_default();
    Ok(Json(json!({
        "ok": true,
        "chapter": {
            "id": row.try_get::<i64,_>("id").unwrap_or_default(),
            "chapter_index": ci,
            "index": ci,
            "title": row.try_get::<String,_>("title").unwrap_or_default(),
            "volume_title": row.try_get::<String,_>("volume_title").unwrap_or_default(),
            "word_count": row.try_get::<i32,_>("word_count").unwrap_or_default(),
            "content": row.try_get::<String,_>("content").unwrap_or_default(),
        },
    })))
}

// ── POST /api/scripts/{script_id}/chapters/{chapter_index}/split ─────────────

#[derive(Deserialize)]
struct SplitBody {
    // SCRIPTS-002: 兼容前端发 offset 和原有 split_at
    split_at: Option<i64>,
    offset: Option<i64>,
    new_title: Option<String>,
}

async fn api_chapter_split(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((script_id, chapter_index)): Path<(i64, i32)>,
    Json(body): Json<SplitBody>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // SCRIPTS-002: 兼容 split_at 和 offset
    let split_at_val = body.split_at.or(body.offset)
        .ok_or_else(|| ResponseError::bad_request("需要 split_at 或 offset"))?;
    if split_at_val <= 0 {
        return Err(ResponseError::bad_request("split_at 必须 > 0"));
    }

    // Verify ownership
    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1::bigint FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Err(ResponseError::forbidden("无权访问该剧本"));
    }

    // Fetch the chapter to split
    let ch = sqlx::query(
        "SELECT id, title, content, volume_title, confidence FROM script_chapters \
         WHERE script_id = $1 AND chapter_index = $2",
    )
    .bind(script_id)
    .bind(chapter_index)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let ch = match ch {
        Some(r) => r,
        None => return Err(ResponseError::bad_request(format!("章节 {} 不存在", chapter_index))),
    };

    let ch_id: i64 = ch.try_get("id").unwrap_or_default();
    let ch_title: String = ch.try_get("title").unwrap_or_default();
    let content: String = ch.try_get("content").unwrap_or_default();
    let vol_title: String = ch.try_get("volume_title").unwrap_or_default();
    let confidence: f64 = ch.try_get("confidence").unwrap_or(0.0);
    let split_at = split_at_val as usize;

    // split_at is a character position
    let char_len = content.chars().count();
    if split_at >= char_len {
        return Err(ResponseError::bad_request(format!(
            "split_at ({}) 超过章节长度 ({})",
            split_at, char_len
        )));
    }

    // Split the content at the character boundary
    let left_text: String = content.chars().take(split_at).collect();
    let right_text: String = content.chars().skip(split_at).collect();

    let new_title = body.new_title.as_deref()
        .filter(|s| !s.is_empty())
        .map(|s| {
            let t: String = s.chars().take(200).collect();
            t
        })
        .unwrap_or_else(|| format!("{}（下）", ch_title));

    // Shift subsequent chapters up by 1 (make room)
    sqlx::query(
        "UPDATE script_chapters SET chapter_index = chapter_index + 1, updated_at = now() \
         WHERE script_id = $1 AND chapter_index > $2",
    )
    .bind(script_id)
    .bind(chapter_index)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    // Update original chapter with left half
    sqlx::query(
        "UPDATE script_chapters SET content = $1, word_count = $2, updated_at = now() WHERE id = $3",
    )
    .bind(&left_text)
    .bind(left_text.chars().count() as i32)
    .bind(ch_id)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    // Insert right half as new chapter
    sqlx::query(
        r#"
        INSERT INTO script_chapters(
            script_id, chapter_index, title, content, word_count,
            volume_title, source_marker, confidence
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        "#,
    )
    .bind(script_id)
    .bind(chapter_index + 1)
    .bind(&new_title)
    .bind(&right_text)
    .bind(right_text.chars().count() as i32)
    .bind(&vol_title)
    .bind("manual_split")
    .bind(confidence)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    // Update scripts.chapter_count
    let cnt: i64 = sqlx::query_scalar(
        "SELECT count(*)::bigint FROM script_chapters WHERE script_id = $1",
    )
    .bind(script_id)
    .fetch_one(&state.db)
    .await
    .unwrap_or(0);

    sqlx::query("UPDATE scripts SET chapter_count = $1, updated_at = now() WHERE id = $2")
        .bind(cnt as i32)
        .bind(script_id)
        .execute(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "split_at": body.split_at,
        "new_chapter_count": cnt,
    })))
}

// ── POST /api/scripts/{script_id}/resplit ────────────────────────────────────

#[derive(Deserialize)]
struct ResplitBody {
    split_rule: Option<String>,
    custom_pattern: Option<String>,
}

async fn api_script_resplit(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
    Json(body): Json<ResplitBody>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let split_rule = body.split_rule.as_deref().unwrap_or("auto");
    let custom_pattern = body.custom_pattern.as_deref().unwrap_or("");

    // Verify ownership and get source_path
    let script_row = sqlx::query(
        "SELECT id, title, source_path FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    let script_row = match script_row {
        Some(r) => r,
        None => return Err(ResponseError::forbidden("无权访问该剧本")),
    };

    let source_path: String = script_row.try_get::<String,_>("source_path").unwrap_or_default();
    if source_path.trim().is_empty() {
        return Err(ResponseError::bad_request("剧本源文件路径丢失"));
    }

    // Resolve the file path
    let script_root = rpg_platform::script_import::script_root();
    let base = script_root.parent().unwrap_or(std::path::Path::new("."));
    let p = if std::path::Path::new(&source_path).is_absolute() {
        std::path::PathBuf::from(&source_path)
    } else {
        base.join(&source_path)
    };

    if !p.exists() {
        return Err(ResponseError::bad_request("剧本源文件不存在，无法重切"));
    }

    let raw = std::fs::read(&p)
        .map_err(|e| ResponseError::internal(format!("读取源文件失败: {e}")))?;

    // Validate custom pattern
    if split_rule.trim() == "custom" {
        if custom_pattern.trim().is_empty() {
            return Err(ResponseError::bad_request("split_rule=custom 时必须提供 custom_pattern"));
        }
        if rpg_platform::script_import::splitter::build_custom_pattern(custom_pattern).is_none() {
            return Err(ResponseError::bad_request("custom_pattern 不是合法/安全正则"));
        }
    }

    // Decode + split
    let (text, encoding) = rpg_platform::script_import::splitter::decode_bytes(&raw);
    let (chapters, report) =
        rpg_platform::script_import::splitter::split_chapters_with_report(&text, split_rule, custom_pattern);

    if chapters.is_empty() {
        return Err(ResponseError::bad_request("重切结果为空"));
    }

    let total_words: i64 = chapters.iter().map(|c| c.content.chars().count() as i64).sum();

    // Delete existing chapters and re-insert
    sqlx::query("DELETE FROM script_chapters WHERE script_id = $1")
        .bind(script_id)
        .execute(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    for (idx, ch) in chapters.iter().enumerate() {
        let chapter_index = (idx + 1) as i32;
        let title_trunc: String = ch.title.chars().take(200).collect();
        let vol_trunc: String = ch.volume_title.chars().take(200).collect();
        let content_len = ch.content.chars().count() as i32;

        sqlx::query(
            r#"
            INSERT INTO script_chapters(
                script_id, chapter_index, title, content, word_count,
                volume_title, source_marker, confidence
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            "#,
        )
        .bind(script_id)
        .bind(chapter_index)
        .bind(if title_trunc.is_empty() {
            format!("第{}章", chapter_index)
        } else {
            title_trunc
        })
        .bind(&ch.content)
        .bind(content_len)
        .bind(vol_trunc)
        .bind(&ch.source_marker)
        .bind(report.confidence)
        .execute(&state.db)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    }

    // Update scripts metadata
    let report_json = json!({
        "mode": report.mode,
        "mode_label": report.mode_label,
        "confidence": report.confidence,
        "chapter_count": report.chapter_count,
        "total_words": report.total_words,
        "average_words": report.average_words,
        "min_words": report.min_words,
        "max_words": report.max_words,
        "split_rule": report.split_rule,
        "reasons": report.reasons,
        "encoding": encoding,
        "resplit": true,
    });

    sqlx::query(
        "UPDATE scripts SET chapter_count = $1, word_count = $2, import_report = $3, updated_at = now() WHERE id = $4",
    )
    .bind(chapters.len() as i32)
    .bind(total_words)
    .bind(&report_json)
    .bind(script_id)
    .execute(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "script_id": script_id,
        "chapter_count": chapters.len(),
        "word_count": total_words,
        "report": {
            "mode": report.mode,
            "mode_label": report.mode_label,
            "confidence": report.confidence,
            "chapter_count": report.chapter_count,
            "total_words": report.total_words,
            "average_words": report.average_words,
            "min_words": report.min_words,
            "max_words": report.max_words,
            "split_rule": report.split_rule,
            "reasons": report.reasons,
        },
        "knowledge_stale": true,
    })))
}

// ── GET /api/scripts/{script_id}/embed ───────────────────────────────────────

async fn api_script_embed_get(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1::bigint FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    // Query embed status from DB (same as embed/status)
    let status = rpg_platform::knowledge::embedding::embed_status(&state.db, script_id)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "status": {
            "running": status.running,
            "chunks": {"done": status.embedded_chunks, "total": status.total_chunks},
            "cards": {"done": status.embedded_cards, "total": status.total_cards},
            "worldbook": {"done": status.embedded_worldbook, "total": status.total_worldbook},
        },
    })))
}

// ── POST /api/scripts/{script_id}/embed ─────────────────────────────────────

async fn api_script_embed_post(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1::bigint FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    // No embedding_client in AppState — return a graceful "not configured" response
    // Per Python: if _get_vertex_client() is None, return this message
    Ok(Json(json!({
        "ok": true,
        "status": "pending",
        "message": "embedding pipeline not configured",
    })))
}

// ── GET /api/scripts/{script_id}/embed/status ───────────────────────────────

async fn api_script_embed_status(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(script_id): Path<i64>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT 1::bigint FROM scripts WHERE id = $1 AND owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(|e| ResponseError::internal(e.to_string()))?;

    if owned.is_none() {
        return Ok(Json(json!({"ok": false, "error": "无权访问该剧本"})));
    }

    let status = rpg_platform::knowledge::embedding::embed_status(&state.db, script_id)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "status": {
            "running": status.running,
            "chunks": {"done": status.embedded_chunks, "total": status.total_chunks},
            "cards": {"done": status.embedded_cards, "total": status.total_cards},
            "worldbook": {"done": status.embedded_worldbook, "total": status.total_worldbook},
        },
    })))
}
