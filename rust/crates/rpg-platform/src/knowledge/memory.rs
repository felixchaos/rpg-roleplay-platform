//! memory —— 玩家档案 memory CRUD。
//!
//! 完成度: **主路径完整**
//! - `MemoryItem` 行结构(对应 `memories` 表)
//! - `list_memories` —— 按 save_id + 可选 bucket 翻页
//! - `get_memory` / `upsert_memory` / `delete_memory`
//!
//! 对应 Python:
//!   - `rpg/platform_app/knowledge/memory.py`
//!   - `rpg/platform_app/knowledge/_memory_repo.py`

use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// `memories` 表行(玩家存档作用域)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryItem {
    pub id: i64,
    pub session_id: i64,
    #[serde(default)]
    pub bucket: String,
    pub content: String,
    pub importance: i32,
    #[serde(default)]
    pub tags: serde_json::Value,
    #[serde(default)]
    pub metadata: serde_json::Value,
    #[serde(default)]
    pub turn_added: Option<i32>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MemoryPayload {
    #[serde(default)]
    pub id: Option<i64>,
    pub session_id: i64,
    #[serde(default = "default_bucket")]
    pub bucket: String,
    pub content: String,
    #[serde(default = "default_importance")]
    pub importance: i32,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub metadata: serde_json::Value,
    #[serde(default)]
    pub turn_added: Option<i32>,
}

fn default_bucket() -> String {
    "general".to_string()
}
fn default_importance() -> i32 {
    50
}

fn row_to_memory(r: &sqlx::postgres::PgRow) -> Result<MemoryItem, sqlx::Error> {
    Ok(MemoryItem {
        id: r.try_get("id")?,
        session_id: r.try_get("session_id")?,
        bucket: r.try_get::<Option<String>, _>("bucket").ok().flatten().unwrap_or_default(),
        content: r.try_get::<Option<String>, _>("content").ok().flatten().unwrap_or_default(),
        importance: r.try_get::<i32, _>("importance").unwrap_or(50),
        tags: r
            .try_get::<Option<serde_json::Value>, _>("tags")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([])),
        metadata: r
            .try_get::<Option<serde_json::Value>, _>("metadata")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!({})),
        turn_added: r.try_get::<Option<i32>, _>("turn_added").ok().flatten(),
    })
}

/// 校验 save 属于 user(对应 Python `select * from game_saves where id and user_id`)。
async fn require_save(pool: &PgPool, user_id: i64, save_id: i64) -> PlatformResult<()> {
    let row = sqlx::query("select 1 from game_saves where id = $1 and user_id = $2")
        .bind(save_id)
        .bind(user_id)
        .fetch_optional(pool)
        .await?;
    if row.is_none() {
        return Err(PlatformError::forbidden("无权访问该存档"));
    }
    Ok(())
}

// ─── CRUD ──────────────────────────────────────────────────────────────────

/// Python: `list_memories(user_id, save_id, bucket, limit, cursor)`。
///
/// cursor 为 `before_id`(按 importance desc, id desc 翻页)。
pub async fn list_memories(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    bucket: Option<&str>,
    limit: i64,
    before_id: Option<i64>,
) -> PlatformResult<(Vec<MemoryItem>, bool)> {
    require_save(pool, user_id, save_id).await?;
    let page_limit = limit.max(1).min(200);
    let rows = match bucket {
        Some(b) => sqlx::query(
            r#"
            select m.* from memories m
              join game_sessions s on s.id = m.session_id
             where s.save_id = $1 and m.bucket = $2
               and ($3::bigint is null or m.id < $3)
             order by m.importance desc, m.id desc
             limit $4
            "#,
        )
        .bind(save_id)
        .bind(b)
        .bind(before_id)
        .bind(page_limit + 1)
        .fetch_all(pool)
        .await?,
        None => sqlx::query(
            r#"
            select m.* from memories m
              join game_sessions s on s.id = m.session_id
             where s.save_id = $1
               and ($2::bigint is null or m.id < $2)
             order by m.importance desc, m.id desc
             limit $3
            "#,
        )
        .bind(save_id)
        .bind(before_id)
        .bind(page_limit + 1)
        .fetch_all(pool)
        .await?,
    };
    let has_more = rows.len() as i64 > page_limit;
    let take = (rows.len()).min(page_limit as usize);
    let items: Result<Vec<_>, sqlx::Error> = rows.iter().take(take).map(row_to_memory).collect();
    Ok((items?, has_more))
}

/// 单条详情。校验 session 属于 user 的 save。
pub async fn get_memory(
    pool: &PgPool,
    user_id: i64,
    memory_id: i64,
) -> PlatformResult<Option<MemoryItem>> {
    let row = sqlx::query(
        r#"
        select m.* from memories m
          join game_sessions s on s.id = m.session_id
          join game_saves g on g.id = s.save_id
         where m.id = $1 and g.user_id = $2
        "#,
    )
    .bind(memory_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(match row {
        Some(r) => Some(row_to_memory(&r)?),
        None => None,
    })
}

/// 新增/更新 memory。
///
/// session 归属校验:必须属于 user 的某个 save。
pub async fn upsert_memory(
    pool: &PgPool,
    user_id: i64,
    payload: MemoryPayload,
) -> PlatformResult<MemoryItem> {
    let content = payload.content.trim().to_string();
    if content.is_empty() {
        return Err(PlatformError::validation("memory.content 不能为空"));
    }
    // session ownership check
    let row = sqlx::query(
        r#"
        select 1 from game_sessions s
          join game_saves g on g.id = s.save_id
         where s.id = $1 and g.user_id = $2
        "#,
    )
    .bind(payload.session_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    if row.is_none() {
        return Err(PlatformError::forbidden("无权访问该 session"));
    }
    let tags_json = serde_json::to_value(&payload.tags)?;
    let metadata_json = if payload.metadata.is_null() {
        serde_json::json!({})
    } else {
        payload.metadata
    };
    let bucket = if payload.bucket.is_empty() {
        "general".to_string()
    } else {
        payload.bucket
    };
    let importance = payload.importance.clamp(0, 100);

    let row = if let Some(mem_id) = payload.id {
        sqlx::query(
            r#"
            update memories set
              session_id = $1, bucket = $2, content = $3, importance = $4,
              tags = $5, metadata = $6, turn_added = $7, updated_at = now()
             where id = $8
            returning *
            "#,
        )
        .bind(payload.session_id)
        .bind(&bucket)
        .bind(&content)
        .bind(importance)
        .bind(&tags_json)
        .bind(&metadata_json)
        .bind(payload.turn_added)
        .bind(mem_id)
        .fetch_optional(pool)
        .await?
        .ok_or_else(|| PlatformError::not_found("memory 不存在"))?
    } else {
        sqlx::query(
            r#"
            insert into memories (
              session_id, bucket, content, importance, tags, metadata, turn_added
            ) values (
              $1, $2, $3, $4, $5, $6, $7
            )
            returning *
            "#,
        )
        .bind(payload.session_id)
        .bind(&bucket)
        .bind(&content)
        .bind(importance)
        .bind(&tags_json)
        .bind(&metadata_json)
        .bind(payload.turn_added)
        .fetch_one(pool)
        .await?
    };
    Ok(row_to_memory(&row)?)
}

/// 删除。同样要 join 校验。
pub async fn delete_memory(
    pool: &PgPool,
    user_id: i64,
    memory_id: i64,
) -> PlatformResult<bool> {
    let res = sqlx::query(
        r#"
        delete from memories where id in (
          select m.id from memories m
            join game_sessions s on s.id = m.session_id
            join game_saves g on g.id = s.save_id
           where m.id = $1 and g.user_id = $2
        )
        "#,
    )
    .bind(memory_id)
    .bind(user_id)
    .execute(pool)
    .await?;
    Ok(res.rows_affected() > 0)
}
