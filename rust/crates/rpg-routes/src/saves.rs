//! `/api/saves/*` — 存档 CRUD/激活/导出/导入/上下文/锚点。
//!
//! 对应 Python: `rpg/platform_app/api/saves.py` + `rpg/platform_app/frontend_routes.py`。
//! Service: `rpg_platform::save_io`、`rpg_platform::branches::activation`、
//!          `rpg_platform::context_runs`。

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
        // list / create — 顺序敏感:具名段 /import 必须在 /{save_id} 之前注册
        .route("/api/saves", get(api_saves_list).post(api_saves_create))
        .route("/api/saves/import", post(api_saves_import))
        // 单档操作
        .route("/api/saves/:save_id", get(api_save_detail))
        .route("/api/saves/:save_id/export", get(api_save_export))
        .route("/api/saves/:save_id/activate", post(api_save_activate))
        .route("/api/saves/:save_id/delete", post(api_save_delete))
        .route("/api/saves/:save_id/rename", post(api_save_rename))
        // 附属资源
        .route("/api/saves/:save_id/context-runs", get(api_save_context_runs))
        .route("/api/saves/:save_id/anchors", get(api_save_anchors))
        .route("/api/saves/:save_id/anchors/reseed", post(api_save_anchors_reseed))
}

// ── Query / Body 参数 ─────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct PaginationQuery {
    limit: Option<i64>,
    cursor: Option<String>,
}

#[derive(Debug, Deserialize)]
struct CreateSaveBody {
    script_id: Option<Value>,
    #[serde(default)]
    title: String,
    new_card: Option<Value>,
    character_id: Option<Value>,
    character_kind: Option<String>,
    birthpoint: Option<Value>,
    identity: Option<Value>,
    story_intent: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RenameSaveBody {
    title: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ContextRunsQuery {
    before_id: Option<i64>,
    limit: Option<i64>,
}

#[derive(Debug, Deserialize)]
struct AnchorReseedBody {
    #[serde(default = "default_keep_satisfied")]
    keep_satisfied: bool,
}

fn default_keep_satisfied() -> bool {
    true
}

// ── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/saves — 存档列表（轻量摘要）
/// 对应 Python: 附带 branch_count per save（来自 branch_commits 表）
async fn api_saves_list(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<PaginationQuery>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // P0-7: 确保用户至少有默认存档(对齐 Python workspace.saves() 的 ensure_default)
    crate::auth::ensure_default(&state.db, user.id.into()).await;

    let saves = rpg_platform::save_io::list_saves_for_user(&state.db, user.id)
        .await
        .map_err(ResponseError::from)?;

    // Apply cursor / limit pagination (mirror Python saves_page logic)
    let limit = q.limit.unwrap_or(50).clamp(1, 200) as usize;
    let before_id: Option<i64> = q
        .cursor
        .as_deref()
        .and_then(|c| c.parse::<i64>().ok());

    let mut filtered: Vec<_> = saves
        .into_iter()
        .filter(|s| before_id.map(|id| s.id < id).unwrap_or(true))
        .collect();

    let has_more = filtered.len() > limit;
    if has_more {
        filtered.truncate(limit);
    }

    let next_cursor = if has_more {
        filtered.last().map(|s| s.id.to_string())
    } else {
        None
    };

    // 查询每个 save 的 branch_count（对应 Python overview 中 branch_commits 计数）
    let save_ids: Vec<i64> = filtered.iter().map(|s| s.id).collect();
    let mut branch_counts: std::collections::HashMap<i64, i32> = std::collections::HashMap::new();
    if !save_ids.is_empty() {
        let rows = sqlx::query(
            "SELECT save_id, count(*)::int as count FROM branch_commits WHERE save_id = ANY($1) GROUP BY save_id",
        )
        .bind(&save_ids)
        .fetch_all(&state.db)
        .await
        .unwrap_or_default();
        for row in rows {
            use sqlx::Row;
            if let (Ok(sid), Ok(cnt)) = (row.try_get::<i64, _>("save_id"), row.try_get::<i32, _>("count")) {
                branch_counts.insert(sid, cnt);
            }
        }
    }

    // 将 branch_count 合并进 save JSON
    let saves_with_branch: Vec<serde_json::Value> = filtered
        .iter()
        .map(|s| {
            let mut v = serde_json::to_value(s).unwrap_or(json!({}));
            if let Some(obj) = v.as_object_mut() {
                obj.insert(
                    "branch_count".to_string(),
                    json!(branch_counts.get(&s.id).copied().unwrap_or(0)),
                );
            }
            v
        })
        .collect();

    Ok(Json(json!({
        "ok": true,
        "saves": saves_with_branch,
        "has_more": has_more,
        "next_cursor": next_cursor,
    })))
}

/// GET /api/saves/{save_id} — 单档详情（含完整 state_snapshot）
async fn api_save_detail(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let save = rpg_platform::save_io::read_save(&state.db, user.id, save_id)
        .await
        .map_err(ResponseError::from)?;

    match save {
        Some(s) => Ok(Json(json!({"ok": true, "save": s}))),
        None => Err(ResponseError::forbidden(format!(
            "无权访问该存档: {save_id}"
        ))),
    }
}

/// POST /api/saves — 创建新存档
async fn api_saves_create(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateSaveBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // 校验 script_id
    let script_id_val = body
        .script_id
        .as_ref()
        .ok_or_else(|| ResponseError::bad_request("script_id 必填"))?;
    let script_id: i64 = match script_id_val {
        Value::Number(n) => n
            .as_i64()
            .ok_or_else(|| ResponseError::bad_request("script_id 必须为整数"))?,
        Value::String(s) => s
            .parse::<i64>()
            .map_err(|_| ResponseError::bad_request("script_id 必须为整数"))?,
        _ => return Err(ResponseError::bad_request("script_id 必须为整数")),
    };

    // 校验 script 归属
    let owned: Option<(i64,)> = sqlx::query_as(
        "select 1::bigint from scripts where id = $1 and owner_id = $2",
    )
    .bind(script_id)
    .bind(user.id)
    .fetch_optional(&state.db)
    .await
    .map_err(ResponseError::from)?;

    if owned.is_none() {
        return Err(ResponseError::forbidden("无权访问该剧本"));
    }

    // 构建 character 引用(合并 character_id + character_kind)
    let character: Option<Value> = match (&body.character_id, &body.character_kind) {
        (Some(cid), Some(ckind)) if !ckind.is_empty() => {
            Some(json!({"id": cid, "kind": ckind}))
        }
        _ => None,
    };

    // 用 build_initial_snapshot 构造合法 GameState(对齐 Python _build_initial_snapshot)
    let snapshot = rpg_platform::save_io::build_initial_snapshot(
        &state.db,
        user.id.into(),
        script_id,
        body.new_card.as_ref(),
        character.as_ref(),
        body.birthpoint.as_ref(),
        body.identity.as_ref(),
        body.story_intent.as_deref(),
    )
    .await;

    let title = body.title.trim().to_string();

    let save = rpg_platform::save_io::create_save(
        &state.db,
        user.id,
        script_id,
        &title,
        &snapshot,
    )
    .await
    .map_err(ResponseError::from)?;

    // seed_tree: 建立 branch tree root commit(对齐 Python workspace.create_save 的 anchor_seed)
    let save_id = save.id;
    let db = state.db.clone();
    tokio::spawn(async move {
        if let Err(e) =
            rpg_platform::branches::seed::seed_tree(&db, save_id, "").await
        {
            tracing::warn!(
                target: "rpg_routes::saves",
                save_id,
                error = %e,
                "background seed_tree failed for new save"
            );
        }
    });

    // iss-6: 添加 branch_count(新建存档初始 branch_count = 1)
    let mut save_val = serde_json::to_value(&save).unwrap_or(json!({}));
    if let Some(obj) = save_val.as_object_mut() {
        obj.entry("branch_count").or_insert(json!(1));
    }
    Ok(Json(json!({"ok": true, "save": save_val})))
}

/// POST /api/saves/import — 导入存档
async fn api_saves_import(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let payload = body
        .get("payload")
        .cloned()
        .unwrap_or_else(|| body.clone());

    let result = rpg_platform::save_io::import_save(&state.db, user.id, &payload)
        .await
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": result.ok,
        "save_id": result.save_id,
        "commits_imported": result.commits_imported,
        "script_id": result.script_id,
    })))
}

/// GET /api/saves/{save_id}/export — 导出存档 JSON
async fn api_save_export(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let export = rpg_platform::save_io::export_save(&state.db, user.id, save_id)
        .await
        .map_err(|e| ResponseError::forbidden(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "export_version": export.export_version,
        "exported_at": export.exported_at,
        "save": export.save,
        "commits": export.commits,
        "refs": export.refs,
        "messages": export.messages,
        "memories": export.memories,
    })))
}

/// POST /api/saves/{save_id}/activate — 激活该存档为当前 runtime
async fn api_save_activate(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let result = rpg_platform::branches::activation::activate_save(&state.db, user.id.into(), save_id)
        .await
        .map_err(|e| ResponseError::forbidden(e.to_string()))?;

    // 关键:激活存档后必须重新加载 state_snapshot 到 state_store,
    // 否则 state_store 缓存的可能是旧存档(或空白 state)的数据。
    // 对齐 Python `_invalidate_user_cache(user)` 的行为。
    {
        let user_id_str = user.id.to_string();
        // 从 DB 读新激活存档的 state_snapshot
        let snapshot = sqlx::query_scalar::<_, serde_json::Value>(
            "SELECT coalesce(state_snapshot, '{}'::jsonb) FROM game_saves WHERE id = $1 AND user_id = $2",
        )
        .bind(save_id)
        .bind(user.id)
        .fetch_optional(&state.db)
        .await
        .ok()
        .flatten()
        .unwrap_or(serde_json::json!({}));

        // 用 from_value 重建 GameState(含 migration),写入 state_store 覆盖缓存
        let new_state = rpg_state::GameState::from_value(user_id_str.clone(), snapshot);
        let shared = state.state_store.get_or_create(&user_id_str).await;
        *shared.write() = new_state;
    }

    // iss-1/2/3/4/5: 返回完整 TreeResult(与 api_branches_activate 一致,包含 runtime/nodes/refs/save)
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

/// POST /api/saves/{save_id}/delete — 删存档
async fn api_save_delete(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // 验证归属
    let owned: Option<(i64,)> =
        sqlx::query_as("select 1::bigint from game_saves where id = $1 and user_id = $2")
            .bind(save_id)
            .bind(user.id)
            .fetch_optional(&state.db)
            .await
            .map_err(ResponseError::from)?;

    if owned.is_none() {
        return Err(ResponseError::forbidden("无权操作该存档"));
    }

    rpg_platform::save_io::delete_save(&state.db, user.id, save_id)
        .await
        .map_err(ResponseError::from)?;

    Ok(Json(json!({"ok": true})))
}

/// POST /api/saves/{save_id}/rename — 改名
async fn api_save_rename(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
    Json(body): Json<RenameSaveBody>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    let title = body.title.unwrap_or_default();
    let title = title.trim().to_string();
    if title.is_empty() {
        return Err(ResponseError::bad_request("标题不能为空"));
    }

    // 验证归属
    let owned: Option<(i64,)> =
        sqlx::query_as("select 1::bigint from game_saves where id = $1 and user_id = $2")
            .bind(save_id)
            .bind(user.id)
            .fetch_optional(&state.db)
            .await
            .map_err(ResponseError::from)?;

    if owned.is_none() {
        return Err(ResponseError::forbidden("无权操作该存档"));
    }

    sqlx::query("update game_saves set title = $1, updated_at = now() where id = $2")
        .bind(&title)
        .bind(save_id)
        .execute(&state.db)
        .await
        .map_err(ResponseError::from)?;

    Ok(Json(json!({"ok": true, "title": title})))
}

/// GET /api/saves/{save_id}/context-runs — 上下文运行记录
async fn api_save_context_runs(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
    Query(q): Query<ContextRunsQuery>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;
    let limit = q.limit.unwrap_or(50);

    let (rows, has_more) = rpg_platform::context_runs::list_context_runs(
        &state.db,
        user.id,
        save_id,
        q.before_id,
        limit,
    )
    .await
    .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "runs": rows,
        "has_more": has_more,
    })))
}

/// GET /api/saves/{save_id}/anchors — 世界线锚点状态
async fn api_save_anchors(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
) -> Result<impl IntoResponse, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // 验证归属
    let owned: Option<(i64,)> =
        sqlx::query_as("select 1::bigint from game_saves where id = $1 and user_id = $2")
            .bind(save_id)
            .bind(user.id)
            .fetch_optional(&state.db)
            .await
            .map_err(ResponseError::from)?;

    if owned.is_none() {
        return Err(ResponseError::forbidden("无权访问该存档"));
    }

    // summary
    let summary: Value = sqlx::query_scalar(
        r#"
        select jsonb_build_object(
            'pending',       count(*) filter (where status = 'pending'),
            'occurred',      count(*) filter (where status = 'occurred'),
            'variant',       count(*) filter (where status = 'variant'),
            'superseded',    count(*) filter (where status = 'superseded'),
            'fatal_pending', count(*) filter (where status = 'pending' and is_fatal),
            'avg_drift',     coalesce(avg(drift_score), 0),
            'total',         count(*)
        )
        from save_anchor_states
        where save_id = $1
        "#,
    )
    .bind(save_id)
    .fetch_one(&state.db)
    .await
    .map_err(ResponseError::from)?;

    // by_phase
    let by_phase: Vec<Value> = sqlx::query_scalar(
        r#"
        select jsonb_build_object(
            'phase_label',           coalesce(phase_label, ''),
            'pending',               count(*) filter (where status = 'pending'),
            'occurred',              count(*) filter (where status = 'occurred'),
            'variant',               count(*) filter (where status = 'variant'),
            'superseded',            count(*) filter (where status = 'superseded'),
            'avg_drift',             coalesce(avg(drift_score), 0),
            'convergence_pressure',  coalesce(sum(drift_score) filter (where status = 'pending'), 0)
        )
        from save_anchor_states
        where save_id = $1
        group by phase_label
        order by phase_label
        "#,
    )
    .bind(save_id)
    .fetch_all(&state.db)
    .await
    .map_err(ResponseError::from)?;

    // recent pending (up to 12)
    let recent_pending: Vec<Value> = sqlx::query_scalar(
        r#"
        select to_jsonb(t) from (
            select anchor_key, source_chapter, summary, phase_label,
                   status, drift_score, is_fatal, updated_at
            from save_anchor_states
            where save_id = $1 and status = 'pending'
            order by is_fatal desc, drift_score desc nulls last, updated_at desc
            limit 12
        ) t
        "#,
    )
    .bind(save_id)
    .fetch_all(&state.db)
    .await
    .map_err(ResponseError::from)?;

    // recent occurred (up to 8)
    let recent_occurred: Vec<Value> = sqlx::query_scalar(
        r#"
        select to_jsonb(t) from (
            select anchor_key, source_chapter, summary, phase_label,
                   status, variant_description as how_it_happened,
                   occurred_at_turn, drift_score, is_fatal
            from save_anchor_states
            where save_id = $1 and status in ('occurred', 'variant')
            order by occurred_at_turn desc nulls last, updated_at desc
            limit 8
        ) t
        "#,
    )
    .bind(save_id)
    .fetch_all(&state.db)
    .await
    .map_err(ResponseError::from)?;

    Ok(Json(json!({
        "ok": true,
        "save_id": save_id,
        "summary": summary,
        "by_phase": by_phase,
        "recent_pending": recent_pending,
        "recent_occurred": recent_occurred,
    })))
}

/// POST /api/saves/{save_id}/anchors/reseed — 重 seed 锚点
async fn api_save_anchors_reseed(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
    body: Option<Json<AnchorReseedBody>>,
) -> Result<Json<serde_json::Value>, ResponseError> {
    let user = require_user(&state, &headers).await?;

    // 验证归属
    let owned: Option<(i64,)> =
        sqlx::query_as("select 1::bigint from game_saves where id = $1 and user_id = $2")
            .bind(save_id)
            .bind(user.id)
            .fetch_optional(&state.db)
            .await
            .map_err(ResponseError::from)?;

    if owned.is_none() {
        return Err(ResponseError::forbidden("无权访问该存档"));
    }

    let keep = body.map(|b| b.keep_satisfied).unwrap_or(true);

    // anchor_seed_agent 在 Python 端是一个大模块(读 script_timeline_anchors +
    // 对比 save_anchor_states + LLM 生成描述)。Rust 端实现简化版:
    // 删除当前 save 的所有 anchor states(keep_satisfied 时保留 occurred/variant),
    // 然后从 script_timeline_anchors 重建 pending 锚点。
    let delete_sql = if keep {
        "DELETE FROM save_anchor_states WHERE save_id = $1 AND status NOT IN ('occurred', 'variant')"
    } else {
        "DELETE FROM save_anchor_states WHERE save_id = $1"
    };
    let deleted = sqlx::query(delete_sql)
        .bind(save_id)
        .execute(&state.db)
        .await
        .map(|r| r.rows_affected())
        .unwrap_or(0);

    // 从 script_timeline_anchors 拉锚点重建 pending
    let script_id: Option<i64> = sqlx::query_scalar("SELECT script_id FROM game_saves WHERE id = $1")
        .bind(save_id)
        .fetch_optional(&state.db)
        .await
        .ok()
        .flatten();

    let mut seeded = 0u64;
    if let Some(sid) = script_id {
        let anchors = sqlx::query(
            "SELECT anchor_key, chapter_index, phase_label, summary \
             FROM script_timeline_anchors WHERE script_id = $1 ORDER BY chapter_index"
        )
        .bind(sid)
        .fetch_all(&state.db)
        .await
        .unwrap_or_default();

        for a in &anchors {
            use sqlx::Row as _;
            let key: String = a.try_get("anchor_key").unwrap_or_default();
            let chapter: i32 = a.try_get("chapter_index").unwrap_or(0);
            let phase: String = a.try_get("phase_label").unwrap_or_default();
            let summary: String = a.try_get("summary").unwrap_or_default();
            let _ = sqlx::query(
                "INSERT INTO save_anchor_states(save_id, anchor_key, source_chapter, phase_label, summary, status) \
                 VALUES ($1, $2, $3, $4, $5, 'pending') \
                 ON CONFLICT (save_id, anchor_key) DO NOTHING"
            )
            .bind(save_id)
            .bind(&key)
            .bind(chapter)
            .bind(&phase)
            .bind(&summary)
            .execute(&state.db)
            .await;
            seeded += 1;
        }
    }

    Ok(Json(json!({
        "ok": true,
        "deleted": deleted,
        "seeded": seeded,
        "keep_satisfied": keep,
    })))
}
