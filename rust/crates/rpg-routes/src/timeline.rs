//! timeline.py → timeline.rs — 存档时间线路由
//! GET /api/saves/{save_id}/timeline — 双时间线数据

use axum::{
    extract::{Path, State},
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use http::HeaderMap;
use serde_json::json;
use sqlx::Row;

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new().route(
        "/api/saves/{save_id}/timeline",
        get(api_saves_timeline),
    )
}

/// GET /api/saves/{save_id}/timeline
/// 返回:剧本期望线 (script_anchors) + 实际足迹线 (save_phases) + current_phase_index
///
/// Python 端会校验 ownership(save belongs to user)+ 拉 script_timeline_anchors
/// + save_phase_digests。Rust 翻译期只做 1 次合理 DB 查询(ownership +
/// 两个表 join,失败 → 空数组),不写完整数据迁移。
#[tracing::instrument(skip_all)]
async fn api_saves_timeline(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    // ownership 校验:save 必须 belong to current user,同时取 script_id。
    let save_row: Option<(i64, i64)> = sqlx::query_as(
        "SELECT user_id, script_id FROM game_saves WHERE id = $1 AND user_id = $2",
    )
    .bind(save_id)
    .bind(user.id)
    .fetch_optional(&s.db)
    .await
    .ok()
    .flatten();
    let script_id = match save_row {
        Some((_, sid)) => sid,
        None => return Err(ResponseError::forbidden("save 不属于当前用户")),
    };
    // 简单读两个表(若不存在/未迁移,直接当空)。
    // script_timeline_anchors 按 script_id 索引,列名 story_phase / story_time_label。
    let script_anchors = sqlx::query(
        "SELECT story_phase, story_time_label, chapter_min, chapter_max \
         FROM script_timeline_anchors \
         WHERE script_id = $1 ORDER BY chapter_min",
    )
    .bind(script_id)
    .fetch_all(&s.db)
    .await
    .ok()
    .unwrap_or_default()
    .into_iter()
    .map(|r| {
        json!({
            "phase": r.try_get::<String, _>("story_phase").unwrap_or_default(),
            "label": r.try_get::<String, _>("story_time_label").unwrap_or_default(),
            "chapter_min": r.try_get::<i32, _>("chapter_min").unwrap_or(0),
            "chapter_max": r.try_get::<i32, _>("chapter_max").unwrap_or(0),
        })
    })
    .collect::<Vec<_>>();
    // save_phase_digests 列名 phase_label / turn_start。
    let save_phases = sqlx::query(
        "SELECT phase_index, phase_label, turn_start FROM save_phase_digests \
         WHERE save_id = $1 ORDER BY phase_index",
    )
    .bind(save_id)
    .fetch_all(&s.db)
    .await
    .ok()
    .unwrap_or_default()
    .into_iter()
    .map(|r| {
        json!({
            "phase_index": r.try_get::<i64, _>("phase_index").unwrap_or(0),
            "label": r.try_get::<String, _>("phase_label").unwrap_or_default(),
            "turn": r.try_get::<i32, _>("turn_start").unwrap_or(0),
        })
    })
    .collect::<Vec<_>>();
    let current_phase_index = save_phases.len().saturating_sub(1) as i64;
    Ok(Json(json!({
        "ok": true,
        "script_anchors": script_anchors,
        "save_phases": save_phases,
        "current_phase_index": current_phase_index,
    }))
    .into_response())
}
