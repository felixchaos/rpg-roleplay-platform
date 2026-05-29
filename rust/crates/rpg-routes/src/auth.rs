//! `/api/auth/*` — 注册/登录/会话/密码/审计。
//!
//! 对应 Python: `rpg/platform_app/api/auth.py` + `rpg/platform_app/frontend_routes.py`(auth 部分)
//! Service: `rpg_platform::auth`

use axum::{
    extract::{Query, State},
    http::{header, HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::json;
use sqlx::Row;

use crate::{
    build_session_delete_cookie, build_session_set_cookie, request_is_https, require_user,
    token_from_headers, AppState, ResponseError,
};
use rpg_platform::auth::{
    login, logout, public_user, register, user_from_token, SESSION_DAYS,
};
use rpg_platform::auth::password::{hash_password, verify_password};

// ── Router ────────────────────────────────────────────────────────────────────

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/auth/schema", get(api_auth_schema))
        .route("/api/auth/register", post(api_register))
        .route("/api/auth/login", post(api_login))
        .route("/api/auth/logout", post(api_logout))
        .route("/api/auth/me", get(api_me))
        .route("/api/auth/password", post(api_change_password))
        .route("/api/auth/login-history", get(api_login_history))
        .route("/api/auth/sessions", get(api_list_sessions))
        .route("/api/auth/sessions/revoke", post(api_revoke_session))
        .route("/api/auth/sessions/revoke-all", post(api_revoke_all_sessions))
        .route("/api/auth/sms-code", post(api_sms_code))
        .route("/api/auth/sms-verify", post(api_sms_verify))
}

/// GET /api/auth/schema —— 返回登录/注册表单的字段定义,供前端 Login 页动态渲染。
///
/// 字段定义只代表 HTTP 契约约定的形态(key/label/type/required/...),
/// **不**包含密码策略 / SMS 验证开关之类的运行时配置(由各 handler 内部决定)。
///
/// 用户实测改变字段需求(比如以后增加 email 必填),只需:
///   1. 修 `register` 数组 + Service 层 register 函数
///   2. 前端无需改动 — 自动按新 schema 渲染
async fn api_auth_schema(State(state): State<AppState>) -> impl IntoResponse {
    let min_password = rpg_core::config::min_password_length();
    let password_hint = format!("至少 {min_password} 位");
    let mode = state.config.deployment_mode.as_str();
    let invite_only = matches!(mode, "server" | "production" | "prod" | "cloud");

    Json(serde_json::json!({
        "ok": true,
        "login": [
            {
                "key": "username",
                "label": "用户名",
                "type": "text",
                "required": true,
                "autocomplete": "username",
                "placeholder": "字母 / 数字 / 下划线",
                "max_length": 64
            },
            {
                "key": "password",
                "label": "密码",
                "type": "password",
                "required": true,
                "autocomplete": "current-password",
                "placeholder": password_hint.clone(),
                "min_length": min_password
            }
        ],
        "register": [
            {
                "key": "username",
                "label": "用户名",
                "type": "text",
                "required": true,
                "autocomplete": "username",
                "placeholder": "字母 / 数字 / 下划线,3-32 位",
                "max_length": 32,
                "min_length": 3
            },
            {
                "key": "password",
                "label": "密码",
                "type": "password",
                "required": true,
                "autocomplete": "new-password",
                "placeholder": password_hint,
                "min_length": min_password
            },
            {
                "key": "display_name",
                "label": "显示名",
                "type": "text",
                "required": false,
                "autocomplete": "nickname",
                "placeholder": "可选 · 留空将用用户名",
                "max_length": 64
            }
        ],
        "notes": {
            // 给前端用作页脚提示。后端是唯一权威。
            "first_user_is_admin": true,
            "invite_only": invite_only,
            "min_password_length": min_password
        }
    }))
}

// ── 请求/响应 query 结构体 ─────────────────────────────────────────────────────

#[derive(Deserialize, Default)]
struct LoginHistoryQuery {
    #[serde(default)]
    format: Option<String>,
    #[serde(default)]
    limit: Option<i64>,
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// 用 Postgres 计算 token 的 SHA-256 hex(与 rpg_platform::auth::sessions 内部用法一致)。
/// 避免在 rpg-routes Cargo.toml 引入 sha2/hex 依赖。
async fn sha256_hex_pg(pool: &sqlx::PgPool, token: &str) -> Result<String, sqlx::Error> {
    let row = sqlx::query("select encode(sha256($1::bytea), 'hex') as h")
        .bind(token)
        .fetch_one(pool)
        .await?;
    Ok(row.try_get::<String, _>("h").unwrap_or_default())
}

/// 从 X-Forwarded-For / X-Real-IP / RemoteAddr 提取客户端 IP(对应 Python `_client_ip`)。
/// axum 在反代后面时,真实 IP 在 X-Forwarded-For 首个条目。
fn extract_ip(headers: &HeaderMap) -> String {
    if let Some(v) = headers.get("x-forwarded-for") {
        if let Ok(s) = v.to_str() {
            if let Some(first) = s.split(',').next() {
                let ip = first.trim().to_string();
                if !ip.is_empty() {
                    return ip;
                }
            }
        }
    }
    if let Some(v) = headers.get("x-real-ip") {
        if let Ok(s) = v.to_str() {
            let ip = s.trim().to_string();
            if !ip.is_empty() {
                return ip;
            }
        }
    }
    String::new()
}

// ── POST /api/auth/register ───────────────────────────────────────────────────

async fn api_register(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    body: Option<Json<serde_json::Value>>,
) -> Response {
    let body = body.map(|b| b.0).unwrap_or_default();
    let username = body.get("username").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let password = body.get("password").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let display_name = body.get("display_name").and_then(|v| v.as_str()).unwrap_or("").to_string();

    let _reg_user = match register(&state.db, &username, &password, &display_name).await {
        Ok(u) => u,
        Err(e) => {
            let msg = match &e {
                rpg_platform::PlatformError::Validation(m) => m.clone(),
                rpg_platform::PlatformError::Conflict(m) => m.clone(),
                _ => e.to_string(),
            };
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": msg})),
            )
                .into_response();
        }
    };

    // TODO: workspace::ensure_default(user.id) — rpg_platform::workspace 模块暂未暴露此函数

    let (user, token) = match login(&state.db, &username, &password, "").await {
        Ok(pair) => pair,
        Err(e) => {
            let msg = match &e {
                rpg_platform::PlatformError::Validation(m) => m.clone(),
                _ => e.to_string(),
            };
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": msg})),
            )
                .into_response();
        }
    };

    let is_https = request_is_https(&headers, &uri);
    let cookie = build_session_set_cookie(&token, SESSION_DAYS * 86400, is_https);

    // platform_for(user) — rpg_platform 暂未暴露等价函数;返回 null + TODO
    // TODO: 补 platform payload(rpg_platform::workspace::platform_for)
    let resp_body = json!({
        "ok": true,
        "user": public_user(&user),
        "platform": null,
    });

    (
        StatusCode::OK,
        [(header::SET_COOKIE, cookie)],
        Json(resp_body),
    )
        .into_response()
}

// ── POST /api/auth/login ──────────────────────────────────────────────────────

async fn api_login(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    body: Option<Json<serde_json::Value>>,
) -> Response {
    let body = body.map(|b| b.0).unwrap_or_default();
    let username = body.get("username").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let password = body.get("password").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let ip = extract_ip(&headers);

    let (user, token) = match login(&state.db, &username, &password, &ip).await {
        Ok(pair) => pair,
        Err(rpg_platform::PlatformError::RateLimited { retry_after_sec, .. }) => {
            let msg = format!("登录失败次数过多，请 {retry_after_sec} 秒后再试");
            let mut resp = (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({"ok": false, "error": msg})),
            )
                .into_response();
            if let Ok(v) = retry_after_sec.to_string().parse() {
                resp.headers_mut().insert(header::RETRY_AFTER, v);
            }
            return resp;
        }
        Err(e) => {
            let msg = match &e {
                rpg_platform::PlatformError::Validation(m) => m.clone(),
                _ => e.to_string(),
            };
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": msg})),
            )
                .into_response();
        }
    };

    // TODO: workspace::ensure_default(user.id) — rpg_platform::workspace 模块暂未暴露此函数

    let is_https = request_is_https(&headers, &uri);
    let cookie = build_session_set_cookie(&token, SESSION_DAYS * 86400, is_https);

    // TODO: 补 platform payload(rpg_platform::workspace::platform_for)
    let resp_body = json!({
        "ok": true,
        "user": public_user(&user),
        "platform": null,
    });

    (
        StatusCode::OK,
        [(header::SET_COOKIE, cookie)],
        Json(resp_body),
    )
        .into_response()
}

// ── POST /api/auth/logout ─────────────────────────────────────────────────────

async fn api_logout(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let token = token_from_headers(&headers);
    let _ = logout(&state.db, token.as_deref()).await;

    let is_https = request_is_https(&headers, &uri);
    let cookie = build_session_delete_cookie(is_https);

    (
        StatusCode::OK,
        [(header::SET_COOKIE, cookie)],
        Json(json!({"ok": true})),
    )
        .into_response()
}

// ── GET /api/auth/me ──────────────────────────────────────────────────────────

async fn api_me(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Response {
    let token = token_from_headers(&headers);
    let user_opt = match user_from_token(&state.db, token.as_deref()).await {
        Ok(u) => u,
        Err(_) => None,
    };

    let is_admin = user_opt.as_ref().map(|u| u.role == "admin").unwrap_or(false);

    // database 健康状态(对应 Python `db_status(reveal_details=is_admin)`)。
    // rpg_platform 暂未暴露 db_status 函数;简单 ping 判断 ok/error。
    let db_status = match sqlx::query("select 1").fetch_one(&state.db).await {
        Ok(_) => {
            if is_admin {
                json!({
                    "ok": true,
                    "driver": "postgres",
                    "pool_size": state.db.size(),
                    "idle": state.db.num_idle(),
                })
            } else {
                json!({ "ok": true })
            }
        }
        Err(e) => json!({ "ok": false, "error": e.to_string() }),
    };

    let pub_user = user_opt.as_ref().map(public_user);

    Json(json!({
        "ok": true,
        "user": pub_user,
        "database": db_status,
    }))
    .into_response()
}

// ── POST /api/auth/password ───────────────────────────────────────────────────

async fn api_change_password(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<serde_json::Value>>,
) -> Response {
    let user = match require_user(&state, &headers).await {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };

    let body = body.map(|b| b.0).unwrap_or_default();
    let cur_pw = body.get("current").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let next_pw = body.get("next").and_then(|v| v.as_str()).unwrap_or("").to_string();

    let min_len = rpg_core::config::min_password_length();
    if next_pw.chars().count() < min_len {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": format!("新密码至少 {min_len} 位")})),
        )
            .into_response();
    }

    // 查当前密码 hash
    let row = match sqlx::query("select password_hash from users where id = $1")
        .bind(user.id)
        .fetch_optional(&state.db)
        .await
    {
        Ok(Some(r)) => r,
        Ok(None) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": "当前密码错误"})),
            )
                .into_response();
        }
        Err(e) => return ResponseError::internal(e.to_string()).into_response(),
    };

    let stored_hash: String = row.try_get("password_hash").unwrap_or_default();
    if !verify_password(&cur_pw, &stored_hash) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "当前密码错误"})),
        )
            .into_response();
    }

    let new_hash = hash_password(&next_pw);
    if let Err(e) = sqlx::query(
        "update users set password_hash = $1, row_version = row_version + 1, updated_at = now() where id = $2",
    )
    .bind(&new_hash)
    .bind(user.id)
    .execute(&state.db)
    .await
    {
        return ResponseError::internal(e.to_string()).into_response();
    }

    // 撤销除当前 session 之外的所有 session(对应 Python 改密后踢掉其他 session)。
    let cur_token = token_from_headers(&headers);
    if let Some(token) = &cur_token {
        if let Ok(cur_hash) = sha256_hex_pg(&state.db, token).await {
            // 删除该 user 下 token_hash 不是当前 token 的 session
            let _ = sqlx::query(
                "delete from sessions where user_id = $1 and token_hash <> $2",
            )
            .bind(user.id)
            .bind(&cur_hash)
            .execute(&state.db)
            .await;
        }
    } else {
        // 无 cookie 时踢全部 session
        let _ = sqlx::query("delete from sessions where user_id = $1")
            .bind(user.id)
            .execute(&state.db)
            .await;
    }

    Json(json!({"ok": true, "message": "密码已修改"})).into_response()
}

// ── GET /api/auth/login-history ───────────────────────────────────────────────

async fn api_login_history(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<LoginHistoryQuery>,
) -> Response {
    let user = match require_user(&state, &headers).await {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };

    let fmt = params.format.as_deref().unwrap_or("").to_lowercase();
    let limit: i64 = params.limit.unwrap_or(50).clamp(1, 500);

    // login_audit 表真实 schema: (id, username, ip, event, meta jsonb, created_at)
    // 按 username 匹配当前用户,username 存的就是登录时提交的用户名。
    let rows = match sqlx::query(
        r#"
        select id,
               username,
               ip,
               event,
               meta,
               created_at
        from login_audit
        where username = $1
        order by created_at desc
        limit $2
        "#,
    )
    .bind(&user.username)
    .bind(limit)
    .fetch_all(&state.db)
    .await
    {
        Ok(r) => r,
        Err(_) => vec![],
    };

    let items: Vec<serde_json::Value> = rows
        .iter()
        .map(|r| {
            let id: i64 = r.try_get("id").unwrap_or(0);
            let at: Option<chrono::DateTime<chrono::Utc>> = r.try_get("created_at").ok();
            let ip: Option<String> = r.try_get("ip").ok().flatten();
            let event: Option<String> = r.try_get("event").ok();
            let meta: Option<serde_json::Value> = r.try_get("meta").ok();
            // AUTH-11: 与 Python 一致 — event == 'login_ok' → 'ok', 其余 → 'blocked'
            let result_str = if event.as_deref() == Some("login_ok") { "ok" } else { "blocked" }.to_string();
            // AUTH-08: 从 meta.ua 提取 user_agent
            let user_agent: Option<String> = meta
                .as_ref()
                .and_then(|m| m.get("ua").and_then(|v| v.as_str()).map(String::from));
            json!({
                "id": id,
                "at": at.map(|t| t.to_rfc3339()),
                "ip": ip,
                "user_agent": user_agent,
                "meta": meta,
                "result": result_str,
                "event": event,
            })
        })
        .collect();

    if fmt == "csv" {
        let mut csv_buf = String::new();
        csv_buf.push_str("at,ip,event,result\n");
        for item in &items {
            let at = item.get("at").and_then(|v| v.as_str()).unwrap_or("");
            let ip = item.get("ip").and_then(|v| v.as_str()).unwrap_or("");
            let event = item.get("event").and_then(|v| v.as_str()).unwrap_or("");
            let result = item.get("result").and_then(|v| v.as_str()).unwrap_or("");
            csv_buf.push_str(&format!(
                "{},{},{},{}\n",
                csv_escape(at),
                csv_escape(ip),
                csv_escape(event),
                csv_escape(result),
            ));
        }
        return (
            StatusCode::OK,
            [
                (header::CONTENT_TYPE, "text/csv; charset=utf-8".to_string()),
                (
                    header::CONTENT_DISPOSITION,
                    r#"attachment; filename="login-history.csv""#.to_string(),
                ),
            ],
            csv_buf,
        )
            .into_response();
    }

    Json(json!({"ok": true, "entries": items})).into_response()
}

/// 简单 CSV 字段转义:含逗号/引号/换行时用双引号包裹,内部引号加倍。
fn csv_escape(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

// ── GET /api/auth/sessions ────────────────────────────────────────────────────

async fn api_list_sessions(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Response {
    let user = match require_user(&state, &headers).await {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };

    let cur_token = token_from_headers(&headers);
    // 计算当前 token_hash 用于标记 current session
    let cur_hash: Option<String> = if let Some(ref t) = cur_token {
        sha256_hex_pg(&state.db, t).await.ok()
    } else {
        None
    };

    let rows = match sqlx::query(
        r#"
        select token_hash, user_id, created_at, expires_at, last_seen_at
        from sessions
        where user_id = $1 and expires_at > now()
        order by created_at desc
        "#,
    )
    .bind(user.id)
    .fetch_all(&state.db)
    .await
    {
        Ok(r) => r,
        Err(e) => return ResponseError::internal(e.to_string()).into_response(),
    };

    let sessions: Vec<serde_json::Value> = rows
        .iter()
        .map(|r| {
            let token_hash: String = r.try_get("token_hash").unwrap_or_default();
            let created_at: Option<chrono::DateTime<chrono::Utc>> = r.try_get("created_at").ok();
            let expires_at: Option<chrono::DateTime<chrono::Utc>> = r.try_get("expires_at").ok();
            let last_seen_at: Option<chrono::DateTime<chrono::Utc>> =
                r.try_get("last_seen_at").ok();
            // Python 暴露的是明文 token 后 12 字符;Rust 存 token_hash,无明文。
            // 用 token_hash 后 12 字符作为 session_id(前端仅用于撤销匹配)。
            let session_id = if token_hash.len() >= 12 {
                token_hash[token_hash.len() - 12..].to_string()
            } else {
                token_hash.clone()
            };
            let is_current = cur_hash.as_deref() == Some(token_hash.as_str());
            json!({
                "id": &session_id,
                "session_id": &session_id,
                "created_at": created_at.map(|t| t.to_rfc3339()),
                "expires_at": expires_at.map(|t| t.to_rfc3339()),
                "last_seen_at": last_seen_at.or(created_at).map(|t| t.to_rfc3339()),
                "current": is_current,
            })
        })
        .collect();

    Json(json!({"ok": true, "sessions": sessions})).into_response()
}

// ── POST /api/auth/sessions/revoke ────────────────────────────────────────────

async fn api_revoke_session(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<serde_json::Value>>,
) -> Response {
    let user = match require_user(&state, &headers).await {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };

    let body = body.map(|b| b.0).unwrap_or_default();
    let sid = body
        .get("session_id")
        .or_else(|| body.get("id"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    if sid.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "缺少 session_id"})),
        )
            .into_response();
    }

    let cur_token = token_from_headers(&headers);
    let cur_hash = if let Some(ref t) = cur_token {
        sha256_hex_pg(&state.db, t).await.unwrap_or_default()
    } else {
        String::new()
    };

    // 匹配 token_hash 后缀(对应 Python `token LIKE %sid AND token <> cur_token`)。
    // Rust DB 存 token_hash(hex 64 字符),session_id 是 token_hash 后 12 字符。
    let pattern = format!("%{sid}");
    let result = sqlx::query(
        "delete from sessions where user_id = $1 and token_hash like $2 and token_hash <> $3 returning token_hash",
    )
    .bind(user.id)
    .bind(&pattern)
    .bind(&cur_hash)
    .fetch_optional(&state.db)
    .await;

    let deleted = match result {
        Ok(Some(_)) => true,
        Ok(None) => false,
        Err(e) => return ResponseError::internal(e.to_string()).into_response(),
    };

    Json(json!({"ok": true, "deleted": deleted})).into_response()
}

// ── POST /api/auth/sessions/revoke-all ───────────────────────────────────────

async fn api_revoke_all_sessions(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Response {
    let user = match require_user(&state, &headers).await {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };

    let cur_token = token_from_headers(&headers);
    let cur_hash = if let Some(ref t) = cur_token {
        sha256_hex_pg(&state.db, t).await.unwrap_or_default()
    } else {
        String::new()
    };

    let result = if cur_hash.is_empty() {
        sqlx::query("delete from sessions where user_id = $1")
            .bind(user.id)
            .execute(&state.db)
            .await
    } else {
        sqlx::query("delete from sessions where user_id = $1 and token_hash <> $2")
            .bind(user.id)
            .bind(&cur_hash)
            .execute(&state.db)
            .await
    };

    match result {
        Ok(r) => {
            let n = r.rows_affected();
            Json(json!({"ok": true, "result": format!("DELETE {n}")})).into_response()
        }
        Err(e) => ResponseError::internal(e.to_string()).into_response(),
    }
}

// ── POST /api/auth/sms-code (stub) ───────────────────────────────────────────

async fn api_sms_code(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<serde_json::Value>>,
) -> Response {
    let _ = require_user(&state, &headers).await;
    let body = body.map(|b| b.0).unwrap_or_default();
    let phone = body
        .get("phone")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if phone.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "请提供手机号"})),
        )
            .into_response();
    }
    // Stub:未配置 SMS 服务
    Json(json!({
        "ok": true,
        "message": "验证码已发送（演示）",
        "expires_in_sec": 60,
    }))
    .into_response()
}

// ── POST /api/auth/sms-verify (stub) ─────────────────────────────────────────

async fn api_sms_verify(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<serde_json::Value>>,
) -> Response {
    let _ = require_user(&state, &headers).await;
    let body = body.map(|b| b.0).unwrap_or_default();
    let code = body
        .get("code")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let valid = !code.is_empty()
        && code.chars().all(|c| c.is_ascii_digit())
        && (4..=6).contains(&code.len());
    if !valid {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "验证码无效"})),
        )
            .into_response();
    }
    Json(json!({"ok": true, "verified": true})).into_response()
}
