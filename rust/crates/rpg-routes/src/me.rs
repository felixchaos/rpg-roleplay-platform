//! `/api/me/*` / `/api/profile/*` / `/api/account/*`
//!
//! 对应 Python:
//!   - `rpg/platform_app/api/me.py` (421 行)
//!   - `rpg/platform_app/frontend_routes.py` 中 `/api/profile/*` / `/api/account/*` 部分
//!
//! Service: `rpg_platform::{users, user_cards, tavern_cards, usage}`

use axum::{
    extract::{Multipart, Path, Query, State},
    http::{header, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use base64::Engine as _;
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row as _;

use rpg_platform::{
    usage as usage_svc,
    user_cards as cards_svc,
    users as users_svc,
};
use rpg_platform::tavern_cards::{
    parse_card_str, parse_card_value, parse_png_card, tavern_to_user_card, user_card_to_tavern_v2,
    write_png_card,
};

use crate::{
    build_session_delete_cookie, request_is_https, require_user, AppState, ResponseError,
};

// ─── router ──────────────────────────────────────────────────────────────────

pub fn router() -> Router<AppState> {
    Router::new()
        // /api/me/*
        .route("/api/me/profile", get(get_profile))
        .route("/api/me/stats", get(get_stats))
        .route("/api/me/usage", get(get_usage))
        .route("/api/me/usage/timeline", get(get_usage_timeline))
        .route("/api/me/preference", get(get_preference).post(set_preference))
        // personas
        .route("/api/me/personas", get(list_personas).post(upsert_persona))
        .route(
            "/api/me/personas/:persona_id",
            get(get_persona_by_id),
        )
        .route(
            "/api/me/personas/:persona_id/delete",
            post(delete_persona),
        )
        // character-cards — static routes must come before dynamic
        .route(
            "/api/me/character-cards/import-json",
            post(import_card_json),
        )
        .route(
            "/api/me/character-cards/import-tavern",
            post(import_card_tavern),
        )
        .route(
            "/api/me/character-cards",
            get(list_character_cards).post(upsert_character_card),
        )
        .route(
            "/api/me/character-cards/:card_id",
            get(get_character_card),
        )
        .route(
            "/api/me/character-cards/:card_id/delete",
            post(delete_character_card),
        )
        .route(
            "/api/me/character-cards/:card_id/export-png",
            get(export_card_png),
        )
        .route(
            "/api/me/character-cards/:card_id/export-tavern",
            get(export_card_tavern),
        )
        // credentials
        .route("/api/me/credentials", get(list_credentials).post(set_credential))
        .route("/api/me/credentials/delete", post(delete_credential))
        .route("/api/me/credentials/test", get(test_credential))
        // /api/profile/*
        .route("/api/profile/avatar", post(upload_avatar))
        .route("/api/profile/avatar/reset", post(reset_avatar))
        .route("/api/profile/avatar/file/:name", get(avatar_file))
        .route("/api/profile/visibility", post(profile_visibility))
        // /api/account/*
        .route("/api/account/export", post(account_export))
        .route("/api/account/deactivate", post(account_deactivate))
        .route("/api/account/delete", post(account_delete))
}

// ─── query param types ───────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
struct UsageQuery {
    days: Option<i32>,
}

#[derive(Debug, Deserialize, Default)]
struct UsageTimelineQuery {
    days: Option<i32>,
    group_by: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
struct CardListQuery {
    q: Option<String>,
    enabled: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
struct CredTestQuery {
    api_id: Option<String>,
}

// ─── /api/me/profile ─────────────────────────────────────────────────────────

/// GET /api/me/profile — 个人主页（AUTH-17: 以 frontend_routes.py 为权威）
///
/// Python frontend_routes.py 返回 {ok, profile: {id, username, display_name, bio,
/// role, avatar_url, ...profile_extras_fields}}，与 api/me.py 的扩展格式不同。
/// 前端 Profile 页面依赖此 profile_extras 格式。
async fn get_profile(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;

    // AUTH-17: 以 frontend_routes.py 为权威。
    // 用 row_to_json 将 profile_extras 整行转 JSON，再剔除 user_id，与 Python expose(row) 等价。
    let extras: Value = sqlx::query_scalar(
        "select row_to_json(e) from profile_extras e where user_id = $1",
    )
    .bind(user.id)
    .fetch_optional(&s.db)
    .await
    .ok()
    .flatten()
    .unwrap_or(Value::Object(Default::default()));

    let mut profile = serde_json::Map::new();
    // 先把 extras 的所有字段展开（包含 visibility、real_name、gender、avatar_url 等）
    if let Value::Object(extras_map) = extras {
        for (k, v) in extras_map {
            if k != "user_id" {
                profile.insert(k, v);
            }
        }
    }
    // 再覆写核心字段（user 表权威字段优先）
    profile.insert("id".to_string(), json!(user.id));
    profile.insert("username".to_string(), json!(user.username));
    profile.insert("display_name".to_string(), json!(user.display_name));
    profile.insert("bio".to_string(), json!(user.bio));
    profile.insert("role".to_string(), json!(user.role));

    // ME-PROFILE-01: 返回顶层 preferences 字段(前端依赖)
    let prefs_row = sqlx::query(
        "select preferences from user_preferences where user_id = $1",
    )
    .bind(user.id)
    .fetch_optional(&s.db)
    .await
    .ok()
    .flatten();
    let prefs: Value = prefs_row
        .and_then(|r| r.try_get::<Value, _>("preferences").ok())
        .unwrap_or(Value::Object(Default::default()));

    Ok(Json(json!({
        "ok": true,
        "profile": Value::Object(profile),
        "preferences": prefs,
    }))
    .into_response())
}

// ─── /api/me/stats ───────────────────────────────────────────────────────────

/// GET /api/me/stats — 玩家档案统计（回合/分支/字数/连续登录）
async fn get_stats(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let uid = user.id;

    // 剧本汇总
    let sc_row = sqlx::query(
        "select coalesce(count(*),0)::bigint as n, \
         coalesce(sum(word_count),0)::bigint as words, \
         coalesce(sum(chapter_count),0)::bigint as chapters \
         from scripts where owner_id = $1",
    )
    .bind(uid)
    .fetch_one(&s.db)
    .await?;
    let sc_n: i64 = sc_row.try_get("n").unwrap_or(0);
    let sc_words: i64 = sc_row.try_get("words").unwrap_or(0);
    let sc_chapters: i64 = sc_row.try_get("chapters").unwrap_or(0);

    // 存档数
    let saves_count: i64 = sqlx::query_scalar(
        "select count(*)::bigint from game_saves where user_id = $1",
    )
    .bind(uid)
    .fetch_one(&s.db)
    .await
    .unwrap_or(0);

    // 回合数：每个 save 取最大 turn_index 后求和
    let total_rounds: i64 = sqlx::query_scalar(
        "select coalesce(sum(per_save_max),0)::bigint from (\
           select max(b.turn_index) as per_save_max \
           from branch_commits b join game_saves s on s.id = b.save_id \
           where s.user_id = $1 group by b.save_id\
         ) t",
    )
    .bind(uid)
    .fetch_one(&s.db)
    .await
    .unwrap_or(0);

    // 分支节点总数
    let branch_nodes: i64 = sqlx::query_scalar(
        "select count(*)::bigint from branch_commits b \
         join game_saves s on s.id = b.save_id where s.user_id = $1",
    )
    .bind(uid)
    .fetch_one(&s.db)
    .await
    .unwrap_or(0);

    // 分支数 = 父节点下额外子节点数之和
    let branches: i64 = sqlx::query_scalar(
        "select coalesce(sum(extra),0)::bigint from (\
           select count(*) - 1 as extra \
           from branch_commits b join game_saves s on s.id = b.save_id \
           where s.user_id = $1 and b.parent_id is not null \
           group by b.parent_id having count(*) > 1\
         ) t",
    )
    .bind(uid)
    .fetch_one(&s.db)
    .await
    .unwrap_or(0);

    // 最深分支层数（递归 CTE）
    let max_branch_depth: i64 = sqlx::query_scalar(
        "with recursive bn as (\
           select b.id, b.parent_id, 1 as depth \
           from branch_commits b join game_saves s on s.id = b.save_id \
           where s.user_id = $1 and b.parent_id is null \
           union all \
           select c.id, c.parent_id, bn.depth + 1 \
           from branch_commits c join bn on c.parent_id = bn.id\
         ) select coalesce(max(depth),0)::bigint from bn",
    )
    .bind(uid)
    .fetch_one(&s.db)
    .await
    .unwrap_or(0);

    // 上次登录（当前 session 之外）
    let last_login_at: Option<String> = sqlx::query_scalar(
        "select created_at from login_audit \
         where username = $1 and event = 'login_ok' \
         order by created_at desc offset 1 limit 1",
    )
    .bind(&user.username)
    .fetch_optional(&s.db)
    .await
    .ok()
    .flatten()
    .map(|t: chrono::DateTime<chrono::Utc>| t.to_rfc3339());

    // 最近 365 天登录日集合（desc）
    let login_dates: Vec<chrono::NaiveDate> = sqlx::query_scalar(
        "select distinct date_trunc('day', created_at at time zone 'UTC')::date as d \
         from login_audit \
         where username = $1 and event = 'login_ok' \
           and created_at >= now() - interval '365 days' \
         order by d desc",
    )
    .bind(&user.username)
    .fetch_all(&s.db)
    .await
    .unwrap_or_default();

    // 算连续登录天数
    let today = chrono::Utc::now().date_naive();
    let yesterday = today - chrono::Duration::days(1);
    let (streak, longest) = compute_streaks(&login_dates, today, yesterday);

    Ok(Json(json!({
        "ok": true,
        "imported": {
            "scripts": sc_n,
            "words": sc_words,
            "chapters": sc_chapters,
        },
        "saves_count": saves_count,
        "total_rounds": total_rounds,
        "branch_nodes": branch_nodes,
        "branches": branches,
        "max_branch_depth": max_branch_depth,
        "last_login_at": last_login_at,
        "login_streak": streak,
        "longest_login_streak": longest,
        "play_minutes_total": Value::Null,
        "play_minutes_week": Value::Null,
    }))
    .into_response())
}

fn compute_streaks(
    dates: &[chrono::NaiveDate],
    today: chrono::NaiveDate,
    yesterday: chrono::NaiveDate,
) -> (i64, i64) {
    let streak = if dates.first().map(|&d| d == today || d == yesterday).unwrap_or(false) {
        let mut cur = *dates.first().unwrap();
        let mut count = 0i64;
        for &d in dates {
            if d == cur {
                count += 1;
                cur = cur - chrono::Duration::days(1);
            } else if d < cur {
                break;
            }
        }
        count
    } else {
        0
    };

    let longest = {
        let mut best = 0i64;
        let mut run = 0i64;
        let mut prev: Option<chrono::NaiveDate> = None;
        for &d in dates {
            match prev {
                None => { run = 1; }
                Some(p) if (p - d).num_days() == 1 => { run += 1; }
                _ => {
                    best = best.max(run);
                    run = 1;
                }
            }
            prev = Some(d);
        }
        best.max(run)
    };

    (streak, longest)
}

// ─── /api/me/usage ───────────────────────────────────────────────────────────

/// GET /api/me/usage?days=30
async fn get_usage(
    State(s): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<UsageQuery>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let days = q.days.unwrap_or(30);
    let agg = usage_svc::aggregate_usage(&s.db, user.id, days).await?;
    Ok(Json(json!({
        "ok": true,
        "window_days": agg.window_days,
        "totals": agg.totals,
        "by_model": agg.by_model,
        "recent_turns": agg.recent_turns,
    })).into_response())
}

/// GET /api/me/usage/timeline?days=30&group_by=day
async fn get_usage_timeline(
    State(s): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<UsageTimelineQuery>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let days = q.days.unwrap_or(30);
    let group_by = q.group_by.as_deref().unwrap_or("day");
    match usage_svc::timeline_usage(&s.db, user.id, days, group_by).await {
        // AUTH-06: 与 Python 一致 — 字段名 series, 补 group_by / days
        Ok(rows) => Ok(Json(json!({"ok": true, "group_by": group_by, "days": days, "series": rows})).into_response()),
        Err(e) => {
            let msg = e.to_string();
            Ok((
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": msg})),
            )
                .into_response())
        }
    }
}

// ─── /api/me/preference ──────────────────────────────────────────────────────

/// GET /api/me/preference — 读偏好
async fn get_preference(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let row = sqlx::query(
        "select preferences, updated_at from user_preferences where user_id = $1",
    )
    .bind(user.id)
    .fetch_optional(&s.db)
    .await?;
    let (prefs, updated_at): (Value, Option<String>) = match row {
        Some(r) => {
            let v: Value = r.try_get("preferences").unwrap_or(Value::Object(Default::default()));
            let ts: Option<chrono::DateTime<chrono::Utc>> = r.try_get("updated_at").ok();
            (v, ts.map(|t| t.to_rfc3339()))
        }
        None => (Value::Object(Default::default()), None),
    };
    Ok(Json(json!({
        "ok": true,
        "preferences": prefs,
        "updated_at": updated_at,
    }))
    .into_response())
}

/// POST /api/me/preference — 更新或合并界面偏好
///
/// ME-PREF-01 / ME-PROFILE-02 / AUTH-PROFILE-EXTRAS-01:
/// 已验证:Rust 统一写 user_preferences 表(与前端读取一致)。
/// Python 有 api/me.py 和 frontend_routes.py 双路由冲突,Rust 合并为单一权威实现。
async fn set_preference(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let replace = body.get("replace").and_then(|v| v.as_bool()).unwrap_or(false);
    let payload = body
        .get("preferences")
        .or_else(|| body.get("value"))
        .cloned()
        .unwrap_or(body.clone());
    let payload = match payload {
        Value::Object(_) => payload,
        _ => {
            return Ok((
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": "preferences 必须是对象"})),
            )
                .into_response());
        }
    };

    let row = if replace {
        sqlx::query(
            "insert into user_preferences(user_id, preferences) values ($1, $2) \
             on conflict(user_id) do update set \
               preferences = excluded.preferences, updated_at = now() \
             returning preferences, updated_at",
        )
        .bind(user.id)
        .bind(&payload)
        .fetch_one(&s.db)
        .await?
    } else {
        sqlx::query(
            "insert into user_preferences(user_id, preferences) values ($1, $2) \
             on conflict(user_id) do update set \
               preferences = user_preferences.preferences || excluded.preferences, \
               updated_at = now() \
             returning preferences, updated_at",
        )
        .bind(user.id)
        .bind(&payload)
        .fetch_one(&s.db)
        .await?
    };

    let prefs: Value = row.try_get("preferences").unwrap_or(Value::Object(Default::default()));
    let ts: Option<chrono::DateTime<chrono::Utc>> = row.try_get("updated_at").ok();
    Ok(Json(json!({
        "ok": true,
        "preferences": prefs,
        "updated_at": ts.map(|t| t.to_rfc3339()),
    }))
    .into_response())
}

// ─── /api/me/personas ────────────────────────────────────────────────────────

/// GET /api/me/personas
async fn list_personas(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let personas = cards_svc::list_personas(&s.db, user.id.get()).await?;
    Ok(Json(personas).into_response())
}

/// POST /api/me/personas — 创建或更新 persona
async fn upsert_persona(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    match cards_svc::upsert_persona(&s.db, user.id.get(), &body).await {
        Ok(p) => Ok(Json(json!({"ok": true, "persona": p})).into_response()),
        Err(e) => Ok((
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": e.to_string()})),
        )
            .into_response()),
    }
}

/// GET /api/me/personas/:persona_id
async fn get_persona_by_id(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(persona_id): Path<i64>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    match cards_svc::get_persona(&s.db, user.id.get(), persona_id).await? {
        Some(p) => Ok(Json(json!({"ok": true, "persona": p})).into_response()),
        None => Ok((
            StatusCode::NOT_FOUND,
            Json(json!({"ok": false, "error": "persona 不存在"})),
        )
            .into_response()),
    }
}

/// POST /api/me/personas/:persona_id/delete
async fn delete_persona(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(persona_id): Path<i64>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let deleted = cards_svc::delete_persona(&s.db, user.id.get(), persona_id).await?;
    Ok(Json(json!({"ok": true, "deleted": deleted})).into_response())
}

// ─── /api/me/character-cards ─────────────────────────────────────────────────

/// GET /api/me/character-cards?q=...&enabled=1
async fn list_character_cards(
    State(s): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<CardListQuery>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let enabled_only = q.enabled.as_deref() == Some("1");
    let cards =
        cards_svc::list_user_cards(&s.db, user.id.get(), q.q.as_deref(), enabled_only).await?;
    Ok(Json(cards).into_response())
}

/// POST /api/me/character-cards — 创建或更新角色卡
async fn upsert_character_card(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    match cards_svc::upsert_user_card(&s.db, user.id.get(), &body).await {
        Ok(card) => Ok(Json(json!({"ok": true, "card": card})).into_response()),
        Err(e) => Ok((
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": e.to_string()})),
        )
            .into_response()),
    }
}

/// GET /api/me/character-cards/:card_id
async fn get_character_card(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(card_id): Path<i64>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    match cards_svc::get_user_card(&s.db, user.id.get(), card_id).await? {
        Some(c) => Ok(Json(json!({"ok": true, "card": c})).into_response()),
        None => Ok((
            StatusCode::NOT_FOUND,
            Json(json!({"ok": false, "error": "card 不存在"})),
        )
            .into_response()),
    }
}

/// POST /api/me/character-cards/:card_id/delete
async fn delete_character_card(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(card_id): Path<i64>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let deleted = cards_svc::delete_user_card(&s.db, user.id.get(), card_id).await?;
    Ok(Json(json!({"ok": true, "deleted": deleted})).into_response())
}

/// POST /api/me/character-cards/import-json
async fn import_card_json(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let raw = body.get("json").cloned().unwrap_or(Value::Null);
    let data: Value = match &raw {
        Value::String(str_val) => match serde_json::from_str(str_val) {
            Ok(v) => v,
            Err(_) => {
                return Ok((
                    StatusCode::BAD_REQUEST,
                    Json(json!({"ok": false, "error": "JSON 解析失败"})),
                )
                    .into_response());
            }
        },
        Value::Object(_) => raw.clone(),
        _ => {
            return Ok((
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": "JSON 解析失败"})),
            )
                .into_response());
        }
    };
    let name = data
        .get("name")
        .or_else(|| data.get("char_name"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if name.is_empty() {
        return Ok((
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "缺少 name 字段"})),
        )
            .into_response());
    }
    let description = data
        .get("description")
        .or_else(|| data.get("personality"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let first_message = data
        .get("first_mes")
        .or_else(|| data.get("first_message"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let tags = data
        .get("tags")
        .cloned()
        .unwrap_or(Value::Array(vec![]));

    let payload = json!({
        "name": name,
        "description": description,
        "first_message": first_message,
        "tags": tags,
        "source": "import-json",
    });
    match cards_svc::upsert_user_card(&s.db, user.id.get(), &payload).await {
        Ok(card) => Ok(Json(json!({"ok": true, "card": card})).into_response()),
        Err(e) => Ok((
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": e.to_string()})),
        )
            .into_response()),
    }
}

/// POST /api/me/character-cards/import-tavern
///
/// 支持 json / json_string / base64 / png_base64 四种形态。
async fn import_card_tavern(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;

    let tavern_result = if let Some(png_b64) = body.get("png_base64").and_then(|v| v.as_str()) {
        let blob = base64::engine::general_purpose::STANDARD
            .decode(png_b64)
            .map_err(|e| {
                ResponseError::bad_request(format!("png_base64 不合法：{e}"))
            })?;
        parse_png_card(&blob)
    } else if let Some(json_val) = body.get("json") {
        parse_card_value(json_val)
    } else if let Some(json_str) = body.get("json_string").and_then(|v| v.as_str()) {
        parse_card_str(json_str)
    } else if let Some(b64) = body.get("base64").and_then(|v| v.as_str()) {
        parse_card_str(b64)
    } else {
        return Ok((
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "需要 json / json_string / base64 / png_base64 之一"})),
        )
            .into_response());
    };

    match tavern_result {
        Ok(card) => {
            let payload = tavern_to_user_card(&card);
            match cards_svc::upsert_user_card(&s.db, user.id.get(), &payload).await {
                Ok(saved) => Ok(Json(json!({
                    "ok": true,
                    "card": saved,
                    "imported_from": "tavern_v2",
                }))
                .into_response()),
                Err(e) => Ok((
                    StatusCode::BAD_REQUEST,
                    Json(json!({"ok": false, "error": e.to_string()})),
                )
                    .into_response()),
            }
        }
        Err(e) => Ok((
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": e.to_string()})),
        )
            .into_response()),
    }
}

/// GET /api/me/character-cards/:card_id/export-tavern
async fn export_card_tavern(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(card_id): Path<i64>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    match cards_svc::get_user_card(&s.db, user.id.get(), card_id).await? {
        None => Ok((
            StatusCode::NOT_FOUND,
            Json(json!({"ok": false, "error": "card 不存在"})),
        )
            .into_response()),
        Some(card) => {
            let card_val = serde_json::to_value(&card).unwrap_or(Value::Null);
            let v2 = user_card_to_tavern_v2(&card_val);
            Ok(Json(json!({"ok": true, "card": v2, "spec": "chara_card_v2"})).into_response())
        }
    }
}

/// GET /api/me/character-cards/:card_id/export-png
async fn export_card_png(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(card_id): Path<i64>,
) -> Result<Response, ResponseError> {
    use axum::response::Response as AxumResponse;
    use http::header::{CONTENT_DISPOSITION, CONTENT_TYPE};

    let user = require_user(&s, &headers).await?;
    let card = match cards_svc::get_user_card(&s.db, user.id.get(), card_id).await? {
        None => {
            return Ok((
                StatusCode::NOT_FOUND,
                Json(json!({"ok": false, "error": "card 不存在"})),
            )
                .into_response());
        }
        Some(c) => c,
    };

    let card_name = card.name.replace(' ', "_");
    let card_val = serde_json::to_value(&card).unwrap_or(Value::Null);
    let v2 = user_card_to_tavern_v2(&card_val);
    let png_bytes = write_png_card(&v2, None).map_err(|e| ResponseError::internal(e.to_string()))?;

    let name = if card_name.is_empty() {
        format!("card_{card_id}")
    } else {
        card_name
    };
    let filename = format!("{name}.png");

    let response = AxumResponse::builder()
        .status(StatusCode::OK)
        .header(CONTENT_TYPE, "image/png")
        .header(
            CONTENT_DISPOSITION,
            format!("attachment; filename=\"{filename}\""),
        )
        .body(axum::body::Body::from(png_bytes))
        .map_err(|e| ResponseError::internal(e.to_string()))?;

    Ok(response)
}

// ─── /api/me/credentials ─────────────────────────────────────────────────────

/// GET /api/me/credentials — 列出已配置的 API 凭证（不含 raw key）
async fn list_credentials(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let creds = users_svc::list_credentials(&s.db, user.id).await?;
    // AUTH-07: 补 total 字段
    let total = creds.len();
    Ok(Json(json!({"ok": true, "items": creds, "total": total})).into_response())
}

/// POST /api/me/credentials — 设置/更新 API key
async fn set_credential(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let is_admin = user.role == "admin";
    let api_id = body.get("api_id").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let api_key = body.get("api_key").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let base_url_override = if is_admin {
        body.get("base_url_override").and_then(|v| v.as_str()).unwrap_or("").to_string()
    } else {
        String::new()
    };
    let enabled = body.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);

    match users_svc::set_credential(
        &s.db,
        user.id,
        &api_id,
        &api_key,
        &base_url_override,
        enabled,
        is_admin,
    )
    .await
    {
        Ok(()) => Ok(Json(json!({"ok": true})).into_response()),
        Err(e) => Ok((
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": e.to_string()})),
        )
            .into_response()),
    }
}

/// POST /api/me/credentials/delete
async fn delete_credential(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let api_id = body.get("api_id").and_then(|v| v.as_str()).unwrap_or("");
    users_svc::delete_credential(&s.db, user.id, api_id).await?;
    Ok(Json(json!({"ok": true})).into_response())
}

/// GET /api/me/credentials/test?api_id=...
async fn test_credential(
    State(s): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<CredTestQuery>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let api_id = q.api_id.as_deref().unwrap_or("");
    // list_credentials は全部取るので api_id でフィルタ
    let creds = users_svc::list_credentials(&s.db, user.id).await?;
    let found = creds.iter().find(|c| c.api_id == api_id);
    let has_credential = found.map(|c| c.has_credential).unwrap_or(false);
    let base_url_override = found
        .map(|c| c.base_url_override.clone())
        .unwrap_or_default();
    Ok(Json(json!({
        "ok": true,
        "api_id": api_id,
        "has_credential": has_credential,
        "base_url_override": base_url_override,
    }))
    .into_response())
}

// ─── /api/profile/* ──────────────────────────────────────────────────────────

/// 头像存储根目录 — 对应 Python `_UPLOAD_ROOT = platform_data/avatars`。
fn avatar_root() -> std::path::PathBuf {
    let base = std::env::var("RPG_PLATFORM_DATA_DIR")
        .ok()
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from("platform_data"));
    base.join("avatars")
}

/// POST /api/profile/avatar — 上传头像文件
async fn upload_avatar(
    State(s): State<AppState>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;

    // 解析 multipart，找 file 字段。
    let mut file_name: Option<String> = None;
    let mut file_data: Option<Vec<u8>> = None;
    while let Some(field) = multipart.next_field().await.map_err(|e| {
        ResponseError::bad_request(&format!("multipart error: {e}"))
    })? {
        let name = field.name().unwrap_or("").to_string();
        let orig_filename = field
            .file_name()
            .map(|s| s.to_string())
            .unwrap_or_default();
        let data = field.bytes().await.map_err(|e| {
            ResponseError::bad_request(&format!("read field error: {e}"))
        })?;
        if name == "file" && !data.is_empty() {
            file_name = Some(orig_filename);
            file_data = Some(data.to_vec());
        }
    }

    let orig_filename = file_name.unwrap_or_default();
    let data = file_data.ok_or_else(|| ResponseError::bad_request("请选择文件"))?;
    if orig_filename.is_empty() {
        return Err(ResponseError::bad_request("请选择文件"));
    }

    // 校验扩展名。
    let ext = std::path::Path::new(&orig_filename)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();
    if !matches!(ext.as_str(), "png" | "jpg" | "jpeg" | "webp") {
        return Err(ResponseError::bad_request("仅支持 PNG / JPG / WEBP"));
    }

    // 大小限制 2 MB。
    if data.len() > 2 * 1024 * 1024 {
        return Err(ResponseError::bad_request("文件超过 2 MB"));
    }

    let root = avatar_root();
    std::fs::create_dir_all(&root).map_err(|e| {
        ResponseError::internal(&format!("create dir: {e}"))
    })?;

    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let safe_name = format!("u{}_{}.{}", user.id, ts, ext);
    let dest = root.join(&safe_name);
    std::fs::write(&dest, &data).map_err(|e| {
        ResponseError::internal(&format!("write file: {e}"))
    })?;

    let avatar_url = format!("/api/profile/avatar/file/{safe_name}");
    sqlx::query(
        "update users set avatar_url = $1, updated_at = now() where id = $2",
    )
    .bind(&avatar_url)
    .bind(user.id)
    .execute(&s.db)
    .await?;

    Ok(Json(json!({"ok": true, "avatar_url": avatar_url})).into_response())
}

/// POST /api/profile/avatar/reset — 清除自定义头像
async fn reset_avatar(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    sqlx::query(
        "update users set avatar_url = null, updated_at = now() where id = $1",
    )
    .bind(user.id)
    .execute(&s.db)
    .await?;
    Ok(Json(json!({"ok": true})).into_response())
}

/// GET /api/profile/avatar/file/:name — 返回头像文件
async fn avatar_file(
    Path(name): Path<String>,
) -> impl IntoResponse {
    // 安全校验:禁止路径穿越。
    if name.contains('/') || name.contains('\\') || name.starts_with('.') {
        return (StatusCode::NOT_FOUND, "not found").into_response();
    }
    let path = avatar_root().join(&name);
    match std::fs::read(&path) {
        Ok(data) => {
            let ext = std::path::Path::new(&name)
                .extension()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_lowercase();
            let mime = match ext.as_str() {
                "png" => "image/png",
                "jpg" | "jpeg" => "image/jpeg",
                "webp" => "image/webp",
                _ => "application/octet-stream",
            };
            (
                StatusCode::OK,
                [(header::CONTENT_TYPE, mime)],
                data,
            )
                .into_response()
        }
        Err(_) => (StatusCode::NOT_FOUND, "not found").into_response(),
    }
}

/// POST /api/profile/visibility
async fn profile_visibility(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    sqlx::query(
        "insert into profile_extras(user_id, visibility) values ($1, $2) \
         on conflict(user_id) do update set visibility = excluded.visibility, updated_at = now()",
    )
    .bind(user.id)
    .bind(&body)
    .execute(&s.db)
    .await?;
    Ok(Json(json!({"ok": true, "visibility": body})).into_response())
}

// ─── /api/account/* ──────────────────────────────────────────────────────────

/// POST /api/account/export — 记录导出申请(对应 Python:插行到 account_exports 表)
async fn account_export(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<Value>>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let body = body.map(|b| b.0).unwrap_or_default();
    let scope = body.get("scope").and_then(|v| v.as_str()).unwrap_or("all");
    let format = body.get("format").and_then(|v| v.as_str()).unwrap_or("zip");
    let email = body.get("email").and_then(|v| v.as_str()).unwrap_or("");

    // 确保表存在(idempotent DDL)。
    sqlx::query(
        r#"create table if not exists account_exports (
            id bigint generated by default as identity primary key,
            user_id bigint not null references users(id) on delete cascade,
            scope text default 'all',
            format text default 'zip',
            email text,
            status text default 'pending',
            created_at timestamptz not null default now()
        )"#,
    )
    .execute(&s.db)
    .await?;

    sqlx::query(
        "insert into account_exports(user_id, scope, format, email) values ($1, $2, $3, $4)",
    )
    .bind(user.id)
    .bind(scope)
    .bind(format)
    .bind(email)
    .execute(&s.db)
    .await?;

    Ok(Json(json!({"ok": true, "message": "导出申请已记录，完成后会邮件通知"})).into_response())
}

/// POST /api/account/deactivate
async fn account_deactivate(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    sqlx::query(
        "update users set deactivated_at = now(), updated_at = now() where id = $1",
    )
    .bind(user.id)
    .execute(&s.db)
    .await?;
    sqlx::query("delete from sessions where user_id = $1")
        .bind(user.id)
        .execute(&s.db)
        .await?;
    Ok(Json(json!({"ok": true})).into_response())
}

/// POST /api/account/delete — 删除账号（级联清理）
async fn account_delete(
    State(s): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    sqlx::query("delete from sessions where user_id = $1")
        .bind(user.id)
        .execute(&s.db)
        .await?;
    sqlx::query("delete from users where id = $1")
        .bind(user.id)
        .execute(&s.db)
        .await?;
    // AUTH-09: 与 Python _delete_session_cookie 一致 — 清除浏览器 cookie
    let is_https = request_is_https(&headers, &uri);
    let cookie = build_session_delete_cookie(is_https);
    Ok((
        StatusCode::OK,
        [(header::SET_COOKIE, cookie)],
        Json(json!({"ok": true})),
    )
        .into_response())
}
