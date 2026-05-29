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
async fn api_saves_timeline(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(save_id): Path<i64>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    // ownership 校验:save 必须 belong to current user。
    let owns: Option<(i64,)> = sqlx::query_as(
        "SELECT user_id FROM platform_saves WHERE id = $1 AND user_id = $2",
    )
    .bind(save_id)
    .bind(user.id)
    .fetch_optional(&s.db)
    .await
    .ok()
    .flatten();
    if owns.is_none() {
        return Err(ResponseError::forbidden("save 不属于当前用户"));
    }
    // 简单读两个表(若不存在/未迁移,直接当空)。
    let script_anchors = sqlx::query(
        "SELECT phase, label, anchor_turn FROM script_timeline_anchors \
         WHERE save_id = $1 ORDER BY phase",
    )
    .bind(save_id)
    .fetch_all(&s.db)
    .await
    .ok()
    .unwrap_or_default()
    .into_iter()
    .map(|r| {
        json!({
            "phase": r.try_get::<String, _>("phase").unwrap_or_default(),
            "label": r.try_get::<String, _>("label").unwrap_or_default(),
            "anchor_turn": r.try_get::<i64, _>("anchor_turn").unwrap_or(0),
        })
    })
    .collect::<Vec<_>>();
    let save_phases = sqlx::query(
        "SELECT phase_index, label, turn FROM save_phase_digests \
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
            "label": r.try_get::<String, _>("label").unwrap_or_default(),
            "turn": r.try_get::<i64, _>("turn").unwrap_or(0),
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
