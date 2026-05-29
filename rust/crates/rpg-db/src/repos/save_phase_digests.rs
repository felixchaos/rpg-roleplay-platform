//! repos/save_phase_digests.rs — save_phase_digests 表操作

use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct SavePhaseDigest {
    pub id: i64,
    pub save_id: i64,
    pub phase_index: i32,
    pub phase_label: String,
    pub summary: String,
    pub key_events: serde_json::Value,
    pub characters_state: serde_json::Value,
    pub world_state: serde_json::Value,
    pub metadata: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

pub async fn list_recent_for_save(
    pool: &PgPool,
    save_id: i64,
    limit: i64,
) -> Result<Vec<SavePhaseDigest>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, save_id, phase_index, phase_label, summary, key_events,
                characters_state, world_state, metadata, created_at, updated_at
         FROM save_phase_digests
         WHERE save_id = $1
         ORDER BY phase_index DESC
         LIMIT $2",
    )
    .bind(save_id)
    .bind(limit)
    .fetch_all(pool)
    .await
}

pub async fn get(
    pool: &PgPool,
    save_id: i64,
    phase_index: i32,
) -> Result<Option<SavePhaseDigest>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, save_id, phase_index, phase_label, summary, key_events,
                characters_state, world_state, metadata, created_at, updated_at
         FROM save_phase_digests
         WHERE save_id = $1 AND phase_index = $2",
    )
    .bind(save_id)
    .bind(phase_index)
    .fetch_optional(pool)
    .await
}

pub async fn upsert(
    pool: &PgPool,
    digest: &SavePhaseDigest,
) -> Result<SavePhaseDigest, sqlx::Error> {
    sqlx::query_as(
        "INSERT INTO save_phase_digests
            (save_id, phase_index, phase_label, summary, key_events,
             characters_state, world_state, metadata)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
         ON CONFLICT (save_id, phase_index) DO UPDATE SET
            phase_label = EXCLUDED.phase_label,
            summary = EXCLUDED.summary,
            key_events = EXCLUDED.key_events,
            characters_state = EXCLUDED.characters_state,
            world_state = EXCLUDED.world_state,
            metadata = EXCLUDED.metadata,
            updated_at = now()
         RETURNING id, save_id, phase_index, phase_label, summary, key_events,
                   characters_state, world_state, metadata, created_at, updated_at",
    )
    .bind(digest.save_id)
    .bind(digest.phase_index)
    .bind(&digest.phase_label)
    .bind(&digest.summary)
    .bind(&digest.key_events)
    .bind(&digest.characters_state)
    .bind(&digest.world_state)
    .bind(&digest.metadata)
    .fetch_one(pool)
    .await
}
