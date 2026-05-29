//! context_runs —— context_runs 表 CRUD,记录一次上下文召回。
//!
//! 对应 Python `rpg/platform_app/knowledge/context_runs.py` +
//! `_context_runs_repo.py`。
//!
//! 表 `context_runs` 由 rpg-db migration 接管,本模块只做 CRUD。
//! 列:`id, session_id, save_id, user_id, turn, user_input, agent_steps,
//! curator_plan, layers, active_character_cards, active_worldbook,
//! retrieved_chunks, estimated_tokens, cache_plan, status, error,
//! duration_ms, started_at, created_at`。

use chrono::{DateTime, Utc};
use rpg_core::UserId;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// `context_runs` 一行。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextRunRow {
    pub id: i64,
    pub session_id: Option<i64>,
    pub save_id: i64,
    pub user_id: UserId,
    pub turn: i32,
    pub user_input: String,
    pub agent_steps: Value,
    pub curator_plan: Value,
    pub layers: Value,
    pub active_character_cards: Value,
    pub active_worldbook: Value,
    pub retrieved_chunks: Value,
    pub estimated_tokens: i32,
    pub cache_plan: Value,
    pub status: String,
    pub error: String,
    pub duration_ms: i32,
    pub started_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
}

fn row_to_run(row: &sqlx::postgres::PgRow) -> sqlx::Result<ContextRunRow> {
    Ok(ContextRunRow {
        id: row.try_get("id")?,
        session_id: row.try_get::<Option<i64>, _>("session_id").unwrap_or(None),
        save_id: row.try_get::<i64, _>("save_id").unwrap_or(0),
        user_id: row.try_get::<UserId, _>("user_id").unwrap_or(UserId(0)),
        turn: row.try_get::<i32, _>("turn").unwrap_or(0),
        user_input: row.try_get::<String, _>("user_input").unwrap_or_default(),
        agent_steps: row
            .try_get::<Value, _>("agent_steps")
            .unwrap_or(json!([])),
        curator_plan: row
            .try_get::<Value, _>("curator_plan")
            .unwrap_or(json!({})),
        layers: row.try_get::<Value, _>("layers").unwrap_or(json!([])),
        active_character_cards: row
            .try_get::<Value, _>("active_character_cards")
            .unwrap_or(json!([])),
        active_worldbook: row
            .try_get::<Value, _>("active_worldbook")
            .unwrap_or(json!([])),
        retrieved_chunks: row
            .try_get::<Value, _>("retrieved_chunks")
            .unwrap_or(json!([])),
        estimated_tokens: row.try_get::<i32, _>("estimated_tokens").unwrap_or(0),
        cache_plan: row.try_get::<Value, _>("cache_plan").unwrap_or(json!({})),
        status: row.try_get::<String, _>("status").unwrap_or_else(|_| "done".into()),
        error: row.try_get::<String, _>("error").unwrap_or_default(),
        duration_ms: row.try_get::<i32, _>("duration_ms").unwrap_or(0),
        started_at: row.try_get::<Option<DateTime<Utc>>, _>("started_at").ok().flatten(),
        created_at: row
            .try_get::<DateTime<Utc>, _>("created_at")
            .unwrap_or_else(|_| Utc::now()),
    })
}

/// 写入一条 context_run。对应 Python `record_context_run` 的 DB 层。
#[allow(clippy::too_many_arguments)]
pub async fn record_context_run(
    pool: &PgPool,
    session_id: Option<i64>,
    save_id: i64,
    user_id: UserId,
    turn: i32,
    user_input: &str,
    agent_steps: Value,
    curator_plan: Value,
    layers: Value,
    active_character_cards: Value,
    active_worldbook: Value,
    retrieved_chunks: Value,
    estimated_tokens: i32,
    cache_plan: Value,
    status: &str,
    error: &str,
    duration_ms: i32,
) -> PlatformResult<ContextRunRow> {
    let status = if status.is_empty() { "done" } else { status };
    let row = sqlx::query(
        "insert into context_runs( \
            session_id, save_id, user_id, turn, user_input, agent_steps, \
            curator_plan, layers, active_character_cards, active_worldbook, \
            retrieved_chunks, estimated_tokens, cache_plan, \
            status, error, duration_ms \
         ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16) \
         returning *",
    )
    .bind(session_id)
    .bind(save_id)
    .bind(user_id)
    .bind(turn)
    .bind(user_input)
    .bind(&agent_steps)
    .bind(&curator_plan)
    .bind(&layers)
    .bind(&active_character_cards)
    .bind(&active_worldbook)
    .bind(&retrieved_chunks)
    .bind(estimated_tokens)
    .bind(&cache_plan)
    .bind(status)
    .bind(error)
    .bind(duration_ms)
    .fetch_one(pool)
    .await?;
    Ok(row_to_run(&row)?)
}

/// 翻某存档的运行历史(游标分页 by id desc)。
pub async fn list_context_runs(
    pool: &PgPool,
    user_id: UserId,
    save_id: i64,
    before_id: Option<i64>,
    limit: i64,
) -> PlatformResult<(Vec<ContextRunRow>, bool)> {
    let limit = limit.clamp(1, 200);
    let rows = sqlx::query(
        "select * from context_runs \
          where save_id = $1 and user_id = $2 \
            and ($3::bigint is null or id < $3) \
          order by id desc \
          limit $4",
    )
    .bind(save_id)
    .bind(user_id)
    .bind(before_id)
    .bind(limit + 1)
    .fetch_all(pool)
    .await?;
    let has_more = rows.len() as i64 > limit;
    let take = rows.len().min(limit as usize);
    let items: Result<Vec<_>, sqlx::Error> = rows.iter().take(take).map(row_to_run).collect();
    Ok((items?, has_more))
}

/// 单条详情。
pub async fn get_context_run(
    pool: &PgPool,
    user_id: UserId,
    run_id: i64,
) -> PlatformResult<Option<ContextRunRow>> {
    let row = sqlx::query("select * from context_runs where id = $1 and user_id = $2")
        .bind(run_id)
        .bind(user_id)
        .fetch_optional(pool)
        .await?;
    Ok(row.map(|r| row_to_run(&r)).transpose()?)
}

/// 更新状态(running -> done / stopped / failed)。返回是否实际更新。
pub async fn update_context_run_status(
    pool: &PgPool,
    run_id: i64,
    status: &str,
    error: &str,
    duration_ms: Option<i32>,
) -> PlatformResult<bool> {
    if status.is_empty() {
        return Err(PlatformError::validation("status 不能为空"));
    }
    let res = if let Some(d) = duration_ms {
        sqlx::query(
            "update context_runs set status = $1, error = $2, duration_ms = $3 where id = $4",
        )
        .bind(status)
        .bind(error)
        .bind(d)
        .bind(run_id)
        .execute(pool)
        .await?
    } else {
        sqlx::query("update context_runs set status = $1, error = $2 where id = $3")
            .bind(status)
            .bind(error)
            .bind(run_id)
            .execute(pool)
            .await?
    };
    Ok(res.rows_affected() > 0)
}

/// 删除某存档的所有 context_runs(删存档时连带清)。
pub async fn delete_context_runs_for_save(pool: &PgPool, save_id: i64) -> PlatformResult<u64> {
    let res = sqlx::query("delete from context_runs where save_id = $1")
        .bind(save_id)
        .execute(pool)
        .await?;
    Ok(res.rows_affected())
}
