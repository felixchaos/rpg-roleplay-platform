//! repos/worldbook_entries.rs — worldbook_entries 表 CRUD
//!
//! 字段对齐 001_init.sql worldbook_entries 表定义:
//!   id, book_id, script_id, title, content, keys, regex_keys,
//!   priority, token_budget, insertion_position, sticky_turns, cooldown_turns,
//!   probability, character_filter, scene_filter, enabled, metadata,
//!   created_at, updated_at, public_id, row_version
//!
//! 注:表无 save_id / user_id / key / aliases / comment / tags 列;
//!     save 级 overlay 在 save_worldbook_overlays 表。

use crate::query_timed;
use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct WorldbookEntry {
    pub id: i64,
    pub book_id: i64,
    pub script_id: i64,
    pub title: String,
    pub content: String,
    pub keys: serde_json::Value,
    pub regex_keys: serde_json::Value,
    pub priority: i32,
    pub token_budget: i32,
    pub insertion_position: String,
    pub sticky_turns: i32,
    pub cooldown_turns: i32,
    pub probability: f64,
    pub character_filter: serde_json::Value,
    pub scene_filter: serde_json::Value,
    pub enabled: bool,
    pub metadata: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[tracing::instrument(skip(pool), fields(id = %id))]
pub async fn get(pool: &PgPool, id: i64) -> Result<Option<WorldbookEntry>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, book_id, script_id, title, content, keys, regex_keys,
                priority, token_budget, insertion_position, sticky_turns, cooldown_turns,
                probability, character_filter, scene_filter, enabled, metadata,
                created_at, updated_at
         FROM worldbook_entries WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(pool)
    .await
}

#[tracing::instrument(skip(pool), fields(script_id = %script_id))]
pub async fn list_for_script(
    pool: &PgPool,
    script_id: i64,
) -> Result<Vec<WorldbookEntry>, sqlx::Error> {
    query_timed!("select", "rpg-db", {
        sqlx::query_as(
            "SELECT id, book_id, script_id, title, content, keys, regex_keys,
                    priority, token_budget, insertion_position, sticky_turns, cooldown_turns,
                    probability, character_filter, scene_filter, enabled, metadata,
                    created_at, updated_at
             FROM worldbook_entries
             WHERE script_id = $1 AND enabled = true
             ORDER BY priority DESC, id ASC",
        )
        .bind(script_id)
        .fetch_all(pool)
        .await
    })
}

/// worldbook_entries 无 save_id 列 — save 级 overlay 在 save_worldbook_overlays。
/// 保留签名兼容现有调用方,始终返回空列表。
#[tracing::instrument(skip(pool), fields(save_id = %save_id))]
pub async fn list_for_save(
    pool: &PgPool,
    save_id: i64,
) -> Result<Vec<WorldbookEntry>, sqlx::Error> {
    let _ = pool;
    tracing::debug!(save_id, "worldbook_entries.list_for_save: 此表无 save_id 列,返回空");
    Ok(vec![])
}

#[tracing::instrument(skip(pool, entry), fields(id = %entry.id, script_id = %entry.script_id))]
pub async fn upsert(
    pool: &PgPool,
    entry: &WorldbookEntry,
) -> Result<WorldbookEntry, sqlx::Error> {
    query_timed!("upsert", "rpg-db", {
        sqlx::query_as(
            "INSERT INTO worldbook_entries
                (book_id, script_id, title, content, keys, regex_keys,
                 priority, token_budget, insertion_position, sticky_turns, cooldown_turns,
                 probability, character_filter, scene_filter, enabled, metadata)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
             ON CONFLICT (script_id, title) DO UPDATE SET
                content = EXCLUDED.content,
                keys = EXCLUDED.keys,
                regex_keys = EXCLUDED.regex_keys,
                priority = EXCLUDED.priority,
                token_budget = EXCLUDED.token_budget,
                insertion_position = EXCLUDED.insertion_position,
                sticky_turns = EXCLUDED.sticky_turns,
                cooldown_turns = EXCLUDED.cooldown_turns,
                probability = EXCLUDED.probability,
                character_filter = EXCLUDED.character_filter,
                scene_filter = EXCLUDED.scene_filter,
                enabled = EXCLUDED.enabled,
                metadata = EXCLUDED.metadata,
                updated_at = now()
             RETURNING id, book_id, script_id, title, content, keys, regex_keys,
                       priority, token_budget, insertion_position, sticky_turns, cooldown_turns,
                       probability, character_filter, scene_filter, enabled, metadata,
                       created_at, updated_at",
        )
        .bind(entry.book_id)
        .bind(entry.script_id)
        .bind(&entry.title)
        .bind(&entry.content)
        .bind(&entry.keys)
        .bind(&entry.regex_keys)
        .bind(entry.priority)
        .bind(entry.token_budget)
        .bind(&entry.insertion_position)
        .bind(entry.sticky_turns)
        .bind(entry.cooldown_turns)
        .bind(entry.probability)
        .bind(&entry.character_filter)
        .bind(&entry.scene_filter)
        .bind(entry.enabled)
        .bind(&entry.metadata)
        .fetch_one(pool)
        .await
    })
}

#[tracing::instrument(skip(pool), fields(id = %id))]
pub async fn delete(pool: &PgPool, id: i64) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM worldbook_entries WHERE id = $1")
        .bind(id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected() > 0)
}
