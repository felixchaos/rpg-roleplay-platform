//! repos/phase_digests.rs — phase_digests（剧本级）表操作

use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct PhaseDigest {
    pub id: i64,
    pub script_id: i64,
    pub phase_index: i32,
    pub phase_label: String,
    pub summary: String,
    pub key_events: serde_json::Value,
    pub characters: serde_json::Value,
    pub world_state: serde_json::Value,
    pub chapter_range_start: Option<i32>,
    pub chapter_range_end: Option<i32>,
    pub metadata: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// 拉取剧本的 phase_digests，用于 anticipation 场景（含 key_events）。
#[tracing::instrument(skip(pool), fields(script_id = %script_id))]
pub async fn list_for_script_anticipation(
    pool: &PgPool,
    script_id: i64,
) -> Result<Vec<PhaseDigest>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, script_id, phase_index, phase_label, summary, key_events,
                characters, world_state, chapter_range_start, chapter_range_end,
                metadata, created_at, updated_at
         FROM phase_digests
         WHERE script_id = $1
         ORDER BY phase_index ASC",
    )
    .bind(script_id)
    .fetch_all(pool)
    .await
}

#[tracing::instrument(skip(pool), fields(script_id = %script_id, phase_index = %phase_index))]
pub async fn get(
    pool: &PgPool,
    script_id: i64,
    phase_index: i32,
) -> Result<Option<PhaseDigest>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, script_id, phase_index, phase_label, summary, key_events,
                characters, world_state, chapter_range_start, chapter_range_end,
                metadata, created_at, updated_at
         FROM phase_digests
         WHERE script_id = $1 AND phase_index = $2",
    )
    .bind(script_id)
    .bind(phase_index)
    .fetch_optional(pool)
    .await
}

#[tracing::instrument(skip(pool, digest), fields(script_id = %digest.script_id, phase_index = %digest.phase_index))]
pub async fn upsert(
    pool: &PgPool,
    digest: &PhaseDigest,
) -> Result<PhaseDigest, sqlx::Error> {
    sqlx::query_as(
        "INSERT INTO phase_digests
            (script_id, phase_index, phase_label, summary, key_events,
             characters, world_state, chapter_range_start, chapter_range_end, metadata)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
         ON CONFLICT (script_id, phase_index) DO UPDATE SET
            phase_label = EXCLUDED.phase_label,
            summary = EXCLUDED.summary,
            key_events = EXCLUDED.key_events,
            characters = EXCLUDED.characters,
            world_state = EXCLUDED.world_state,
            chapter_range_start = EXCLUDED.chapter_range_start,
            chapter_range_end = EXCLUDED.chapter_range_end,
            metadata = EXCLUDED.metadata,
            updated_at = now()
         RETURNING id, script_id, phase_index, phase_label, summary, key_events,
                   characters, world_state, chapter_range_start, chapter_range_end,
                   metadata, created_at, updated_at",
    )
    .bind(digest.script_id)
    .bind(digest.phase_index)
    .bind(&digest.phase_label)
    .bind(&digest.summary)
    .bind(&digest.key_events)
    .bind(&digest.characters)
    .bind(&digest.world_state)
    .bind(digest.chapter_range_start)
    .bind(digest.chapter_range_end)
    .bind(&digest.metadata)
    .fetch_one(pool)
    .await
}
