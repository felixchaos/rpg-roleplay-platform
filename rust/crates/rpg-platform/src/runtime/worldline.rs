//! runtime::worldline —— 用户世界线变量(UserWorldline)CRUD。
//!
//! 对应 Python `rpg/platform_app/knowledge/worldline.py` +
//! `_worldline_repo.py`(变量层)+ `rpg/schemas/worldline.py`(数据形状)。
//!
//! 表 `worldline_variables(session_id, key, value, locked, source, metadata,
//! updated_at)`。session_id 是 `game_sessions.id`,user_id 通过 `game_saves` 边界
//! 隔离 —— 调用方需先校验 save 归属(本模块不重复校验,只做表层 CRUD)。
//!
//! 提供:
//! - `list_user_worldline` 按 save_id 翻当前世界线变量
//! - `get_user_worldline_variable` 单值读
//! - `set_user_worldline_variable` upsert(locked + source 可控)
//! - `remove_user_worldline_variable` 删除
//! - `clear_user_worldline` 清空整条会话的变量
//!
//! TODO: session_id 解析当前要求调用方提供;后续接入 game_sessions 找寻路径
//! 由 routes 层封装(避免本模块依赖 sessions repo)。

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// `worldline_variables` 表一行。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserWorldlineVariable {
    pub session_id: i64,
    pub key: String,
    pub value: String,
    pub locked: bool,
    pub source: String,
    pub metadata: Value,
    pub updated_at: DateTime<Utc>,
}

fn row_to_var(row: &sqlx::postgres::PgRow) -> sqlx::Result<UserWorldlineVariable> {
    Ok(UserWorldlineVariable {
        session_id: row.try_get::<i64, _>("session_id")?,
        key: row.try_get::<String, _>("key")?,
        value: row.try_get::<String, _>("value").unwrap_or_default(),
        locked: row.try_get::<bool, _>("locked").unwrap_or(true),
        source: row.try_get::<String, _>("source").unwrap_or_else(|_| "user".into()),
        metadata: row
            .try_get::<Value, _>("metadata")
            .unwrap_or(Value::Object(Default::default())),
        updated_at: row
            .try_get::<DateTime<Utc>, _>("updated_at")
            .unwrap_or_else(|_| Utc::now()),
    })
}

fn clean_text(s: &str) -> String {
    s.trim().to_string()
}

/// 按 session 列出所有变量。
pub async fn list_user_worldline(
    pool: &PgPool,
    session_id: i64,
) -> PlatformResult<Vec<UserWorldlineVariable>> {
    let rows = sqlx::query(
        "select session_id, key, value, locked, source, metadata, updated_at \
           from worldline_variables \
          where session_id = $1 \
          order by key",
    )
    .bind(session_id)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(row_to_var)
        .collect::<Result<Vec<_>, _>>()
        .map_err(Into::into)
}

/// 取一条。
pub async fn get_user_worldline_variable(
    pool: &PgPool,
    session_id: i64,
    key: &str,
) -> PlatformResult<Option<UserWorldlineVariable>> {
    let key = clean_text(key);
    if key.is_empty() {
        return Ok(None);
    }
    let row = sqlx::query(
        "select session_id, key, value, locked, source, metadata, updated_at \
           from worldline_variables \
          where session_id = $1 and key = $2",
    )
    .bind(session_id)
    .bind(&key)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|r| row_to_var(&r)).transpose()?)
}

/// upsert 一条;value 不能为空。
pub async fn set_user_worldline_variable(
    pool: &PgPool,
    session_id: i64,
    key: &str,
    value: &str,
    source: &str,
    locked: bool,
    metadata: Option<Value>,
) -> PlatformResult<UserWorldlineVariable> {
    let key = clean_text(key);
    let value = clean_text(value);
    if key.is_empty() || value.is_empty() {
        return Err(PlatformError::validation("变量名和变量值不能为空"));
    }
    let source = if source.trim().is_empty() {
        "user".to_string()
    } else {
        source.trim().to_string()
    };
    let metadata = metadata.unwrap_or_else(|| Value::Object(Default::default()));
    let row = sqlx::query(
        "insert into worldline_variables(session_id, key, value, locked, source, metadata) \
         values ($1, $2, $3, $4, $5, $6) \
         on conflict(session_id, key) do update set \
           value = excluded.value, \
           locked = excluded.locked, \
           source = excluded.source, \
           metadata = excluded.metadata, \
           updated_at = now() \
         returning session_id, key, value, locked, source, metadata, updated_at",
    )
    .bind(session_id)
    .bind(&key)
    .bind(&value)
    .bind(locked)
    .bind(&source)
    .bind(&metadata)
    .fetch_one(pool)
    .await?;
    Ok(row_to_var(&row)?)
}

/// 删除一条。
pub async fn remove_user_worldline_variable(
    pool: &PgPool,
    session_id: i64,
    key: &str,
) -> PlatformResult<bool> {
    let key = clean_text(key);
    if key.is_empty() {
        return Ok(false);
    }
    let res = sqlx::query("delete from worldline_variables where session_id = $1 and key = $2")
        .bind(session_id)
        .bind(&key)
        .execute(pool)
        .await?;
    Ok(res.rows_affected() > 0)
}

/// 清空整条 session 的世界线变量。返回删除条数。
pub async fn clear_user_worldline(pool: &PgPool, session_id: i64) -> PlatformResult<u64> {
    let res = sqlx::query("delete from worldline_variables where session_id = $1")
        .bind(session_id)
        .execute(pool)
        .await?;
    Ok(res.rows_affected())
}
