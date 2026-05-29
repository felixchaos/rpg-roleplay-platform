//! repos/worldbook_entries.rs — worldbook_entries 表 CRUD
//!
//! 字段从 001_init.sql 中的 worldbook_entries 表定义推断。

use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct WorldbookEntry {
    pub id: i64,
    pub script_id: Option<i64>,
    pub save_id: Option<i64>,
    pub user_id: Option<i64>,
    pub key: String,
    pub aliases: serde_json::Value,
    pub content: String,
    pub comment: String,
    pub enabled: bool,
    pub priority: i32,
    pub token_budget: i32,
    pub tags: serde_json::Value,
    pub metadata: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[tracing::instrument(skip(pool), fields(id = %id))]
pub async fn get(pool: &PgPool, id: i64) -> Result<Option<WorldbookEntry>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, script_id, save_id, user_id, key, aliases, content, comment,
                enabled, priority, token_budget, tags, metadata, created_at, updated_at
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
    sqlx::query_as(
        "SELECT id, script_id, save_id, user_id, key, aliases, content, comment,
                enabled, priority, token_budget, tags, metadata, created_at, updated_at
         FROM worldbook_entries
         WHERE script_id = $1 AND enabled = true
         ORDER BY priority DESC, id ASC",
    )
    .bind(script_id)
    .fetch_all(pool)
    .await
}

#[tracing::instrument(skip(pool), fields(save_id = %save_id))]
pub async fn list_for_save(
    pool: &PgPool,
    save_id: i64,
) -> Result<Vec<WorldbookEntry>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, script_id, save_id, user_id, key, aliases, content, comment,
                enabled, priority, token_budget, tags, metadata, created_at, updated_at
         FROM worldbook_entries
         WHERE save_id = $1 AND enabled = true
         ORDER BY priority DESC, id ASC",
    )
    .bind(save_id)
    .fetch_all(pool)
    .await
}

#[tracing::instrument(skip(pool, entry), fields(id = %entry.id, script_id = ?entry.script_id, save_id = ?entry.save_id))]
pub async fn upsert(
    pool: &PgPool,
    entry: &WorldbookEntry,
) -> Result<WorldbookEntry, sqlx::Error> {
    sqlx::query_as(
        "INSERT INTO worldbook_entries
            (script_id, save_id, user_id, key, aliases, content, comment,
             enabled, priority, token_budget, tags, metadata)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
         ON CONFLICT (id) DO UPDATE SET
            key = EXCLUDED.key,
            aliases = EXCLUDED.aliases,
            content = EXCLUDED.content,
            comment = EXCLUDED.comment,
            enabled = EXCLUDED.enabled,
            priority = EXCLUDED.priority,
            token_budget = EXCLUDED.token_budget,
            tags = EXCLUDED.tags,
            metadata = EXCLUDED.metadata,
            updated_at = now()
         RETURNING id, script_id, save_id, user_id, key, aliases, content, comment,
                   enabled, priority, token_budget, tags, metadata, created_at, updated_at",
    )
    .bind(entry.script_id)
    .bind(entry.save_id)
    .bind(entry.user_id)
    .bind(&entry.key)
    .bind(&entry.aliases)
    .bind(&entry.content)
    .bind(&entry.comment)
    .bind(entry.enabled)
    .bind(entry.priority)
    .bind(entry.token_budget)
    .bind(&entry.tags)
    .bind(&entry.metadata)
    .fetch_one(pool)
    .await
}

#[tracing::instrument(skip(pool), fields(id = %id))]
pub async fn delete(pool: &PgPool, id: i64) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM worldbook_entries WHERE id = $1")
        .bind(id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected() > 0)
}
