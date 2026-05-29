//! `/api/scripts/*` — 剧本列表 / 章节 / 知识库同步 / overrides 等。
//!
//! 对应 Python: `rpg/platform_app/api/scripts.py` (574 行)。
//! Service: `rpg_platform::script_import`、`rpg_platform::library`。

use axum::{
    extract::{Path, Query, State},
    http::HeaderMap,
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
        // import 状态 (stub)
        .route("/api/scripts/:script_id/import-budget", get(api_stub_get))
        .route("/api/scripts/:script_id/import-pipeline", get(api_stub_get))
        .route("/api/scripts/:script_id/import-status", get(api_stub_get))
        // export-pack (stub)
        .route("/api/scripts/:script_id/export-pack", get(api_stub_get))
}

// ── 通用 stub helper ─────────────────────────────────────────────────────────

async fn api_stub_get() -> Json<Value> {
    Json(json!({"ok": false, "error": "not yet implemented"}))
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

// ── POST /api/scripts/import (stub — 大流水线) ───────────────────────────────

async fn api_import_script(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/batch-import (stub) ────────────────────────────────────

async fn api_scripts_batch_import(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/import-pack (stub) ─────────────────────────────────────

async fn api_import_script_pack(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, ResponseError> {
    let _ = body;
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
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

        let items: Vec<Value> = rows
            .iter()
            .map(|r| {
                let ci = r.try_get::<i32,_>("chapter_index").unwrap_or_default();
                json!({
                    "id": r.try_get::<i64,_>("id").unwrap_or_default(),
                    "chapter_index": ci,
                    "index": ci,
                    "title": r.try_get::<String,_>("title").unwrap_or_default(),
                    "volume_title": r.try_get::<String,_>("volume_title").unwrap_or_default(),
                    "word_count": r.try_get::<i32,_>("word_count").unwrap_or_default(),
                    "content": r.try_get::<String,_>("content").unwrap_or_default(),
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
            json!({
                "id": r.try_get::<i64,_>("id").unwrap_or_default(),
                "chapter_index": ci,
                "index": ci,
                "title": r.try_get::<String,_>("title").unwrap_or_default(),
                "volume_title": r.try_get::<String,_>("volume_title").unwrap_or_default(),
                "word_count": r.try_get::<i32,_>("word_count").unwrap_or_default(),
                "content": r.try_get::<String,_>("content").unwrap_or_default(),
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

    let row = sqlx::query(
        r#"
        select id, script_id, name, identity, appearance, personality,
               speech_style, aliases, sample_dialogue, priority, enabled, created_at
        from character_cards
        where id = $1 and script_id = $2 and enabled = true
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
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let _ = (state, script_id, user);
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/{script_id}/character-cards/{card_id}/delete (stub) ────

async fn api_script_delete_character_card(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((_script_id, _card_id)): Path<(i64, i64)>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/{script_id}/character-cards/{card_id}/enabled (stub) ───

async fn api_script_card_enabled(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((_script_id, _card_id)): Path<(i64, i64)>,
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
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

    // LLM 推荐身份:stub — 依赖 console_assistant dispatch,暂未移植
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/{script_id}/chapters/merge (stub) ──────────────────────

async fn api_chapter_merge(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(_script_id): Path<i64>,
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/{script_id}/chapters/{chapter_index} (stub) ────────────

async fn api_chapter_update(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((_script_id, _chapter_index)): Path<(i64, i32)>,
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/{script_id}/chapters/{chapter_index}/split (stub) ───────

async fn api_chapter_split(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((_script_id, _chapter_index)): Path<(i64, i32)>,
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/{script_id}/resplit (stub) ─────────────────────────────

async fn api_script_resplit(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(_script_id): Path<i64>,
    Json(_body): Json<Value>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── GET /api/scripts/{script_id}/embed (stub) ────────────────────────────────

async fn api_script_embed_get(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(_script_id): Path<i64>,
) -> Result<Json<Value>, ResponseError> {
    require_user(&state, &headers).await?;
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── POST /api/scripts/{script_id}/embed (stub) ───────────────────────────────

async fn api_script_embed_post(
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

    // embed 触发:暂 stub
    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}

// ── GET /api/scripts/{script_id}/embed/status (stub) ─────────────────────────

async fn api_script_embed_status(
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

    Ok(Json(json!({"ok": false, "error": "not yet implemented"})))
}
