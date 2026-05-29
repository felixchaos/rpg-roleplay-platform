//! repos/user_credentials.rs — user_api_credentials 表操作（v4 migration）

use crate::query_timed;
use rpg_core::UserId;
use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct UserApiCredential {
    pub id: i64,
    pub user_id: UserId,
    pub api_id: String,
    pub encrypted_key: Vec<u8>,
    pub base_url_override: String,
    pub enabled: bool,
    pub metadata: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[tracing::instrument(skip(pool), fields(user_id = %user_id))]
pub async fn list(
    pool: &PgPool,
    user_id: UserId,
) -> Result<Vec<UserApiCredential>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, user_id, api_id, encrypted_key, base_url_override,
                enabled, metadata, created_at, updated_at
         FROM user_api_credentials
         WHERE user_id = $1
         ORDER BY api_id",
    )
    .bind(user_id)
    .fetch_all(pool)
    .await
}

#[tracing::instrument(skip(pool, cred), fields(user_id = %cred.user_id, api_id = %cred.api_id))]
pub async fn upsert(
    pool: &PgPool,
    cred: &UserApiCredential,
) -> Result<UserApiCredential, sqlx::Error> {
    query_timed!("upsert", "rpg-db", {
        sqlx::query_as(
            "INSERT INTO user_api_credentials
                (user_id, api_id, encrypted_key, base_url_override, enabled, metadata)
             VALUES ($1, $2, $3, $4, $5, $6)
             ON CONFLICT (user_id, api_id) DO UPDATE SET
                encrypted_key = EXCLUDED.encrypted_key,
                base_url_override = EXCLUDED.base_url_override,
                enabled = EXCLUDED.enabled,
                metadata = EXCLUDED.metadata,
                updated_at = now()
             RETURNING id, user_id, api_id, encrypted_key, base_url_override,
                       enabled, metadata, created_at, updated_at",
        )
        .bind(cred.user_id)
        .bind(&cred.api_id)
        .bind(&cred.encrypted_key)
        .bind(&cred.base_url_override)
        .bind(cred.enabled)
        .bind(&cred.metadata)
        .fetch_one(pool)
        .await
    })
}

#[tracing::instrument(skip(pool), fields(user_id = %user_id, api_id = %api_id))]
pub async fn delete(pool: &PgPool, user_id: UserId, api_id: &str) -> Result<bool, sqlx::Error> {
    let result = sqlx::query(
        "DELETE FROM user_api_credentials WHERE user_id = $1 AND api_id = $2",
    )
    .bind(user_id)
    .bind(api_id)
    .execute(pool)
    .await?;
    Ok(result.rows_affected() > 0)
}

/// 解析：按 user_id + api_id 取单条凭据（用于运行时鉴权）。
#[tracing::instrument(skip(pool), fields(user_id = %user_id, api_id = %api_id))]
pub async fn resolve(
    pool: &PgPool,
    user_id: UserId,
    api_id: &str,
) -> Result<Option<UserApiCredential>, sqlx::Error> {
    query_timed!("select", "rpg-db", {
        sqlx::query_as(
            "SELECT id, user_id, api_id, encrypted_key, base_url_override,
                    enabled, metadata, created_at, updated_at
             FROM user_api_credentials
             WHERE user_id = $1 AND api_id = $2 AND enabled = true",
        )
        .bind(user_id)
        .bind(api_id)
        .fetch_optional(pool)
        .await
    })
}
