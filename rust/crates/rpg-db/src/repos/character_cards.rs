//! repos/character_cards.rs — user_character_cards 表 CRUD

use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct CharacterCard {
    pub id: i64,
    pub user_id: i64,
    pub slug: String,
    pub name: String,
    pub aliases: serde_json::Value,
    pub identity: String,
    pub appearance: String,
    pub personality: String,
    pub speech_style: String,
    pub current_status: String,
    pub secrets: String,
    pub sample_dialogue: serde_json::Value,
    pub tags: serde_json::Value,
    pub metadata: serde_json::Value,
    pub token_budget: i32,
    pub priority: i32,
    pub enabled: bool,
    pub scope: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub public_id: Uuid,
    pub row_version: i64,
}

pub async fn get(pool: &PgPool, id: i64) -> Result<Option<CharacterCard>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, user_id, slug, name, aliases, identity, appearance, personality,
                speech_style, current_status, secrets, sample_dialogue, tags, metadata,
                token_budget, priority, enabled, scope, created_at, updated_at,
                public_id, row_version
         FROM user_character_cards WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(pool)
    .await
}

pub async fn list(
    pool: &PgPool,
    user_id: i64,
    enabled_only: bool,
) -> Result<Vec<CharacterCard>, sqlx::Error> {
    if enabled_only {
        sqlx::query_as(
            "SELECT id, user_id, slug, name, aliases, identity, appearance, personality,
                    speech_style, current_status, secrets, sample_dialogue, tags, metadata,
                    token_budget, priority, enabled, scope, created_at, updated_at,
                    public_id, row_version
             FROM user_character_cards
             WHERE user_id = $1 AND enabled = true
             ORDER BY priority DESC, id DESC",
        )
        .bind(user_id)
        .fetch_all(pool)
        .await
    } else {
        sqlx::query_as(
            "SELECT id, user_id, slug, name, aliases, identity, appearance, personality,
                    speech_style, current_status, secrets, sample_dialogue, tags, metadata,
                    token_budget, priority, enabled, scope, created_at, updated_at,
                    public_id, row_version
             FROM user_character_cards
             WHERE user_id = $1
             ORDER BY priority DESC, id DESC",
        )
        .bind(user_id)
        .fetch_all(pool)
        .await
    }
}

pub async fn upsert(pool: &PgPool, card: &CharacterCard) -> Result<CharacterCard, sqlx::Error> {
    sqlx::query_as(
        "INSERT INTO user_character_cards
            (user_id, slug, name, aliases, identity, appearance, personality,
             speech_style, current_status, secrets, sample_dialogue, tags, metadata,
             token_budget, priority, enabled, scope, updated_at, row_version)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, now(), 1)
         ON CONFLICT (user_id, slug) DO UPDATE SET
            name = EXCLUDED.name,
            aliases = EXCLUDED.aliases,
            identity = EXCLUDED.identity,
            appearance = EXCLUDED.appearance,
            personality = EXCLUDED.personality,
            speech_style = EXCLUDED.speech_style,
            current_status = EXCLUDED.current_status,
            secrets = EXCLUDED.secrets,
            sample_dialogue = EXCLUDED.sample_dialogue,
            tags = EXCLUDED.tags,
            metadata = EXCLUDED.metadata,
            token_budget = EXCLUDED.token_budget,
            priority = EXCLUDED.priority,
            enabled = EXCLUDED.enabled,
            scope = EXCLUDED.scope,
            updated_at = now(),
            row_version = user_character_cards.row_version + 1
         RETURNING id, user_id, slug, name, aliases, identity, appearance, personality,
                   speech_style, current_status, secrets, sample_dialogue, tags, metadata,
                   token_budget, priority, enabled, scope, created_at, updated_at,
                   public_id, row_version",
    )
    .bind(card.user_id)
    .bind(&card.slug)
    .bind(&card.name)
    .bind(&card.aliases)
    .bind(&card.identity)
    .bind(&card.appearance)
    .bind(&card.personality)
    .bind(&card.speech_style)
    .bind(&card.current_status)
    .bind(&card.secrets)
    .bind(&card.sample_dialogue)
    .bind(&card.tags)
    .bind(&card.metadata)
    .bind(card.token_budget)
    .bind(card.priority)
    .bind(card.enabled)
    .bind(&card.scope)
    .fetch_one(pool)
    .await
}

pub async fn delete(pool: &PgPool, id: i64) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM user_character_cards WHERE id = $1")
        .bind(id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected() > 0)
}
