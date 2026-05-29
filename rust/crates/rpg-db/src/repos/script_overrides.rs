//! repos/script_overrides.rs — script_overrides 表操作（v16 migration）

use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct ScriptOverride {
    pub script_id: i64,
    pub data: serde_json::Value,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// 加载指定 save 对应剧本的 script_overrides。
/// save_id → script_id 由调用方解析，这里直接按 script_id 查。
#[tracing::instrument(skip(pool), fields(script_id = %script_id))]
pub async fn load_for_save(
    pool: &PgPool,
    script_id: i64,
) -> Result<Option<ScriptOverride>, sqlx::Error> {
    sqlx::query_as(
        "SELECT script_id, data, updated_at
         FROM script_overrides
         WHERE script_id = $1",
    )
    .bind(script_id)
    .fetch_optional(pool)
    .await
}

#[tracing::instrument(skip(pool), fields(script_id = %script_id))]
pub async fn get(pool: &PgPool, script_id: i64) -> Result<Option<ScriptOverride>, sqlx::Error> {
    sqlx::query_as(
        "SELECT script_id, data, updated_at
         FROM script_overrides
         WHERE script_id = $1",
    )
    .bind(script_id)
    .fetch_optional(pool)
    .await
}

#[tracing::instrument(skip(pool, data), fields(script_id = %script_id))]
pub async fn upsert(
    pool: &PgPool,
    script_id: i64,
    data: &serde_json::Value,
) -> Result<ScriptOverride, sqlx::Error> {
    sqlx::query_as(
        "INSERT INTO script_overrides (script_id, data)
         VALUES ($1, $2)
         ON CONFLICT (script_id) DO UPDATE SET
            data = EXCLUDED.data,
            updated_at = now()
         RETURNING script_id, data, updated_at",
    )
    .bind(script_id)
    .bind(data)
    .fetch_one(pool)
    .await
}

#[tracing::instrument(skip(pool), fields(script_id = %script_id))]
pub async fn delete(pool: &PgPool, script_id: i64) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM script_overrides WHERE script_id = $1")
        .bind(script_id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected() > 0)
}
