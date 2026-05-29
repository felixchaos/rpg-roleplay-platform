//! Wave 9-B — e2e 集成测试 harness。
//!
//! ## 跑法
//!
//! ```bash
//! # 1) 启 docker compose(rust/docker-compose.e2e.yml 自带 postgres:16 + pgvector)
//! cd rust && docker compose -f docker-compose.e2e.yml up -d
//!
//! # 2) 跑 e2e
//! export RPG_TEST_DB_URL=postgres://rpg:changeme@localhost:55432/rpg_e2e
//! cargo test -p rpg-server --features e2e -- --ignored
//!
//! # 3) 收
//! docker compose -f docker-compose.e2e.yml down -v
//! ```
//!
//! ## 设计
//!
//! - 每个测试 `#[ignore]` + `#[cfg(feature = "e2e")]`,默认 `cargo test --workspace`
//!   既不编译也不跑(避免 CI 无 docker 时炸)。
//! - `RPG_TEST_DB_URL` env 不存在 → 早返回(允许本地手贱跑测试时不报错)。
//! - 每个测试创建自己的 schema(`e2e_<nano_uuid>`),互不污染,DROP CASCADE 收尾;
//!   sqlx migrate 跑在 schema 下,断言落库走相同 schema。
//! - HTTP 层走 `tower::ServiceExt::oneshot` 打 `build_regular_routes()` 返回的 Router,
//!   不起真实 server,无端口冲突。
//! - LLM 不 mock —— 改测 `/api/chat` 在无 token 时正确 401(后续若想真测 SSE,可
//!   起 wiremock + LlmRouter::with_anthropic_base_url 覆盖)。
//! - session token 不写 fixture,真走 `AuthService::register` + `login` 拿。
//!
//! ## 覆盖
//!
//! 1. register → login → /api/state cookie 验
//! 2. /api/state 匿名 200(`user_id_or_anon` 兜底)
//! 3. /api/state_events 401(无 token)
//! 4. /api/chat 401(无 token)
//! 5. save_io::create_save + branches::seed_tree + record_runtime_turn → branch_commits 真落库
//! 6. /api/uploads/begin → /api/uploads/chunk 真落 chunk 状态
//! 7. /api/permissions/pending-write 缺 action → 400
//! 8. script 表 + chapters 直 SQL 落 + 读回

#![cfg(feature = "e2e")]

use std::time::Duration;

use axum::{
    body::Body,
    http::{header, Method, Request, StatusCode},
    Router,
};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use sqlx::{postgres::PgPoolOptions, Connection, Executor, PgPool};
use tower::ServiceExt;

use rpg_platform::auth::{AuthService, User};
use rpg_routes::{
    build_regular_routes, build_sse_routes, build_upload_routes, AppState,
};

// ─── 测试 harness ────────────────────────────────────────────────────────────

/// 从 env 读 `RPG_TEST_DB_URL`,缺失 → 返回 None(测试早退,但不 fail)。
fn test_db_url() -> Option<String> {
    std::env::var("RPG_TEST_DB_URL").ok().filter(|s| !s.is_empty())
}

/// 为单个测试创建一个独立 schema,跑 migrations,返回 (pool, schema_name)。
///
/// 返回的 `pool` 默认 search_path 已切到该 schema(`SET search_path TO ...`),
/// 后续所有 `create table` / `select` 都在隔离区里。
///
/// 失败 → 直接 panic,让测试 red(此时已确认 RPG_TEST_DB_URL 有效)。
async fn setup_schema(url: &str) -> (PgPool, String) {
    // schema 名:e2e_<8 hex>。pg 标识符长度 63,足够。
    let schema = format!("e2e_{}", &uuid::Uuid::new_v4().simple().to_string()[..8]);

    // 先用 root pool 建 schema。
    let admin = PgPoolOptions::new()
        .max_connections(2)
        .acquire_timeout(Duration::from_secs(10))
        .connect(url)
        .await
        .expect("connect admin pool");
    admin
        .execute(format!(r#"create schema "{schema}""#).as_str())
        .await
        .expect("create schema");
    admin.close().await;

    // 业务 pool:固定 search_path 到该 schema。after_connect 每个连接都设。
    let schema_clone = schema.clone();
    let pool = PgPoolOptions::new()
        .max_connections(4)
        .acquire_timeout(Duration::from_secs(10))
        .after_connect(move |conn, _meta| {
            let schema_clone = schema_clone.clone();
            Box::pin(async move {
                conn.execute(format!(r#"set search_path to "{schema_clone}", public"#).as_str())
                    .await?;
                Ok(())
            })
        })
        .connect(url)
        .await
        .expect("connect test pool");

    // 跑 migrations(rpg-db 自带版本化 advisory-lock,跑一次即可)。
    rpg_db::migrations::run_migrations(&pool)
        .await
        .expect("run_migrations");

    (pool, schema)
}

/// 收尾:DROP SCHEMA CASCADE。失败仅 log,不影响测试结果。
async fn drop_schema(url: &str, schema: &str) {
    let Ok(mut conn) = sqlx::PgConnection::connect(url).await else {
        eprintln!("[e2e] drop_schema: connect failed (schema {schema} leaked)");
        return;
    };
    if let Err(e) = conn
        .execute(format!(r#"drop schema if exists "{schema}" cascade"#).as_str())
        .await
    {
        eprintln!("[e2e] drop_schema {schema} failed: {e}");
    }
}

/// 装配最小 AppState 跑 router。
fn make_app(pool: PgPool) -> (AppState, Router) {
    let state = AppState::new(pool);
    // build_regular_routes 已 .merge 进 core/game/permissions/uploads 等大部分非 SSE 路由;
    // SSE/upload 单独 merge 进同一 Router,以便测全流。
    let router = Router::new()
        .merge(build_regular_routes())
        .merge(build_sse_routes())
        .merge(build_upload_routes())
        .with_state(state.clone());
    (state, router)
}

/// 真注册一个用户 + 登录,返回 (user, session_token)。
async fn register_and_login(state: &AppState, username: &str) -> (User, String) {
    let svc = AuthService::new(state.db.clone());
    let user = svc
        .register(username, "p@ssw0rd-test-12345", "Tester")
        .await
        .expect("register");
    let (u2, token) = svc
        .login(username, "p@ssw0rd-test-12345", "127.0.0.1")
        .await
        .expect("login");
    assert_eq!(u2.id, user.id);
    (user, token)
}

/// 读完 axum Response body 成 bytes。
async fn body_bytes(resp: axum::response::Response) -> Vec<u8> {
    resp.into_body()
        .collect()
        .await
        .expect("collect body")
        .to_bytes()
        .to_vec()
}

/// 读完 body 然后解析 JSON。
async fn body_json(resp: axum::response::Response) -> Value {
    let bytes = body_bytes(resp).await;
    serde_json::from_slice(&bytes).expect("body is json")
}

// ─── 用 macro 把 "缺 env → skip" 标准化 ──────────────────────────────────────
macro_rules! skip_if_no_db {
    () => {
        match test_db_url() {
            Some(u) => u,
            None => {
                eprintln!("[e2e] RPG_TEST_DB_URL not set, skipping");
                return;
            }
        }
    };
}

// ═══ 测试 ════════════════════════════════════════════════════════════════════

/// 1. 注册 → 登录 → 用 cookie 打 /api/state → 看 user_id 不再是 anonymous。
#[tokio::test]
#[ignore = "需 RPG_TEST_DB_URL + docker postgres"]
async fn e2e_register_login_state_cookie() {
    let url = skip_if_no_db!();
    let (pool, schema) = setup_schema(&url).await;
    let (state, app) = make_app(pool.clone());

    let (_user, token) = register_and_login(&state, "alice").await;

    // 无 cookie → anonymous user_id
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::GET)
                .uri("/api/state")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot");
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    assert_eq!(body["user_id"], Value::String("anonymous".into()));

    // 带 cookie → 落到登录 user_id
    let cookie = format!("session_token={token}");
    let resp = app
        .oneshot(
            Request::builder()
                .method(Method::GET)
                .uri("/api/state")
                .header(header::COOKIE, &cookie)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot");
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    assert_ne!(body["user_id"], Value::String("anonymous".into()));
    assert_eq!(body["ok"], Value::Bool(true));

    drop_schema(&url, &schema).await;
}

/// 2. /api/state_events 无 token → 401。
#[tokio::test]
#[ignore = "需 RPG_TEST_DB_URL"]
async fn e2e_state_events_requires_auth() {
    let url = skip_if_no_db!();
    let (pool, schema) = setup_schema(&url).await;
    let (_state, app) = make_app(pool);

    let resp = app
        .oneshot(
            Request::builder()
                .method(Method::GET)
                .uri("/api/state_events")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot");
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);

    drop_schema(&url, &schema).await;
}

/// 3. /api/chat 无 token → 401。
///
/// LLM 本身不 mock,因为强鉴权先于 LLM 调用。匿名 POST → 直接 401。
/// 后续要测带 token 的 chat,需 wiremock + LlmRouter base_url override —— 留 wave 9-C。
#[tokio::test]
#[ignore = "需 RPG_TEST_DB_URL"]
async fn e2e_chat_requires_auth() {
    let url = skip_if_no_db!();
    let (pool, schema) = setup_schema(&url).await;
    let (_state, app) = make_app(pool);

    let body = json!({"message": "hi"}).to_string();
    let resp = app
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/chat")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .expect("oneshot");
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);

    drop_schema(&url, &schema).await;
}

/// 4. save + seed_tree + record_runtime_turn → branch_commits 真落库。
///
/// 直接走 platform 而不 HTTP,因为 save / turn 在 Rust 里目前没暴露 REST(走 SDK)。
#[tokio::test]
#[ignore = "需 RPG_TEST_DB_URL"]
async fn e2e_save_seed_and_runtime_turn() {
    let url = skip_if_no_db!();
    let (pool, schema) = setup_schema(&url).await;

    let svc = AuthService::new(pool.clone());
    let user = svc
        .register("bob", "p@ssw0rd-test-12345", "Bob")
        .await
        .expect("register");

    // 建一个 script(save 外键依赖)
    let script_id: i64 = sqlx::query_scalar(
        "insert into scripts(owner_id, title) values ($1, $2) returning id",
    )
    .bind(user.id)
    .bind("Test Script")
    .fetch_one(&pool)
    .await
    .expect("insert script");

    // 建 save。state_snapshot 含最小骨架。
    let snap = json!({
        "turn": 0,
        "history": [],
        "permissions": {"mode": "ask", "pending_writes": [], "audit_log": []},
    });
    let save = rpg_platform::save_io::create_save(
        &pool,
        user.id,
        script_id,
        "我的存档",
        &snap,
    )
    .await
    .expect("create_save");
    assert!(save.id > 0);

    // seed_tree → 应建 root commit(branch_commits 应 ≥ 1 条)
    rpg_platform::branches::seed::seed_tree(&pool, save.id, "")
        .await
        .expect("seed_tree");
    let commit_count: i64 = sqlx::query_scalar(
        "select count(*)::bigint from branch_commits where save_id = $1",
    )
    .bind(save.id)
    .fetch_one(&pool)
    .await
    .expect("count after seed");
    assert!(commit_count >= 1, "seed_tree 未落 root commit");

    // 拿 root commit id 当 parent,record_runtime_turn 应再增 1 条 commit
    let root_id: i64 =
        sqlx::query_scalar("select id from branch_commits where save_id = $1 order by id asc limit 1")
            .bind(save.id)
            .fetch_one(&pool)
            .await
            .expect("get root commit");

    let next_state = json!({
        "turn": 1,
        "history": [{"role":"user","content":"你好"}],
        "permissions": {"mode":"ask","pending_writes":[],"audit_log":[]},
    });
    let recorded = rpg_platform::branches::runtime::record_runtime_turn(
        &pool,
        user.id.get(),
        save.id,
        root_id,
        None,
        "你好",
        "你好,旅人",
        &next_state,
        "",
    )
    .await
    .expect("record_runtime_turn");
    assert!(recorded.commit.id > root_id);

    let final_count: i64 = sqlx::query_scalar(
        "select count(*)::bigint from branch_commits where save_id = $1",
    )
    .bind(save.id)
    .fetch_one(&pool)
    .await
    .expect("count after turn");
    assert!(
        final_count >= commit_count + 1,
        "record_runtime_turn 未新增 commit (before {commit_count}, after {final_count})"
    );

    drop_schema(&url, &schema).await;
}

/// 5. /api/uploads/begin + /chunk —— chunk 真累积到 AppState.chunk_uploads。
#[tokio::test]
#[ignore = "需 RPG_TEST_DB_URL"]
async fn e2e_uploads_begin_and_chunk() {
    let url = skip_if_no_db!();
    let (pool, schema) = setup_schema(&url).await;
    let (state, app) = make_app(pool.clone());

    let (_user, token) = register_and_login(&state, "carol").await;
    let cookie = format!("session_token={token}");

    // begin
    let body = json!({"total_chunks": 1, "kind": "skill", "file_name": "x.zip"}).to_string();
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/uploads/begin")
                .header(header::CONTENT_TYPE, "application/json")
                .header(header::COOKIE, &cookie)
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .expect("begin");
    assert_eq!(resp.status(), StatusCode::OK);
    let begin_json = body_json(resp).await;
    let upload_id = begin_json["upload_id"].as_str().expect("upload_id").to_string();
    assert!(!upload_id.is_empty());

    // chunk 1/1 — base64 of "hello"
    let body = json!({
        "upload_id": upload_id,
        "chunk_index": 0,
        "total_chunks": 1,
        "base64": "aGVsbG8=",
    })
    .to_string();
    let resp = app
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/uploads/chunk")
                .header(header::CONTENT_TYPE, "application/json")
                .header(header::COOKIE, &cookie)
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .expect("chunk");
    assert_eq!(resp.status(), StatusCode::OK);
    let chunk_json = body_json(resp).await;
    assert_eq!(chunk_json["received"], json!(1));

    // 验内存里 chunk_uploads 真有这条记录
    assert!(state.chunk_uploads.contains_key(&upload_id));

    drop_schema(&url, &schema).await;
}

/// 6. /api/permissions/pending-write 参数校验。
///
/// 缺 action / decision → 400 + code=bad_request。
/// 这条不需要 token —— api_pending_write 用 user_id_or_anon。
#[tokio::test]
#[ignore = "需 RPG_TEST_DB_URL"]
async fn e2e_pending_write_bad_request() {
    let url = skip_if_no_db!();
    let (pool, schema) = setup_schema(&url).await;
    let (_state, app) = make_app(pool);

    let body = json!({"id": "missing-id"}).to_string(); // 故意不带 action
    let resp = app
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/permissions/pending-write")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .expect("oneshot");
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let v = body_json(resp).await;
    assert_eq!(v["code"], Value::String("bad_request".into()));

    drop_schema(&url, &schema).await;
}

/// 7. scripts + chapters 表存在,直 SQL 写读一遍(覆盖 migration 021)。
#[tokio::test]
#[ignore = "需 RPG_TEST_DB_URL"]
async fn e2e_scripts_and_chapters_schema() {
    let url = skip_if_no_db!();
    let (pool, schema) = setup_schema(&url).await;

    // 建 user(scripts.owner_id 外键)
    let svc = AuthService::new(pool.clone());
    let user = svc
        .register("dave", "p@ssw0rd-test-12345", "Dave")
        .await
        .expect("register");

    let script_id: i64 = sqlx::query_scalar(
        "insert into scripts(owner_id, title) values ($1, $2) returning id",
    )
    .bind(user.id)
    .bind("e2e script")
    .fetch_one(&pool)
    .await
    .expect("insert script");

    // script_chapters 表(migration 001 / 021 兜底)
    sqlx::query(
        "insert into script_chapters(script_id, chapter_index, title, content) \
         values ($1, $2, $3, $4)",
    )
    .bind(script_id)
    .bind(0_i32)
    .bind("第一章")
    .bind("正文 …")
    .execute(&pool)
    .await
    .expect("insert chapter");

    let n: i64 = sqlx::query_scalar(
        "select count(*)::bigint from script_chapters where script_id = $1",
    )
    .bind(script_id)
    .fetch_one(&pool)
    .await
    .expect("count chapters");
    assert_eq!(n, 1);

    drop_schema(&url, &schema).await;
}

/// 8. /api/state 不带 cookie → user_id = "anonymous"(`user_id_or_anon` 兜底)
#[tokio::test]
#[ignore = "需 RPG_TEST_DB_URL"]
async fn e2e_state_anonymous_ok() {
    let url = skip_if_no_db!();
    let (pool, schema) = setup_schema(&url).await;
    let (_state, app) = make_app(pool);

    let resp = app
        .oneshot(
            Request::builder()
                .method(Method::GET)
                .uri("/api/state")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("oneshot");
    assert_eq!(resp.status(), StatusCode::OK);
    let v = body_json(resp).await;
    assert_eq!(v["ok"], Value::Bool(true));
    assert_eq!(v["user_id"], Value::String("anonymous".into()));

    drop_schema(&url, &schema).await;
}
