//! repos/token_usage.rs — token_usage 用量计费表操作（v5 migration）

use crate::query_timed;
use rpg_core::UserId;
use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct TokenUsageRow {
    pub id: i64,
    pub user_id: UserId,
    pub save_id: Option<i64>,
    pub context_run_id: Option<i64>,
    pub api_id: String,
    pub model_real_name: String,
    pub input_tokens: i32,
    pub output_tokens: i32,
    pub cached_input_tokens: i32,
    pub reasoning_tokens: i32,
    pub total_tokens: i32,
    /// cost_usd 存储为字符串以兼容 numeric(12,6)（sqlx 未启用 bigdecimal feature）
    pub cost_usd: String,
    pub context_used: i32,
    pub context_max: i32,
    pub metadata: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

/// 插入一条 token_usage 记录（fire-and-forget 计费写入）。
#[tracing::instrument(skip(pool, row), fields(user_id = %row.user_id, api_id = %row.api_id, model = %row.model_real_name))]
pub async fn insert(pool: &PgPool, row: &TokenUsageRow) -> Result<TokenUsageRow, sqlx::Error> {
    query_timed!("insert", "rpg-db", {
        sqlx::query_as(
            "INSERT INTO token_usage
                (user_id, save_id, context_run_id, api_id, model_real_name,
                 input_tokens, output_tokens, cached_input_tokens, reasoning_tokens,
                 total_tokens, cost_usd, context_used, context_max, metadata)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
             RETURNING id, user_id, save_id, context_run_id, api_id, model_real_name,
                       input_tokens, output_tokens, cached_input_tokens, reasoning_tokens,
                       total_tokens, cost_usd, context_used, context_max, metadata, created_at",
        )
        .bind(row.user_id)
        .bind(row.save_id)
        .bind(row.context_run_id)
        .bind(&row.api_id)
        .bind(&row.model_real_name)
        .bind(row.input_tokens)
        .bind(row.output_tokens)
        .bind(row.cached_input_tokens)
        .bind(row.reasoning_tokens)
        .bind(row.total_tokens)
        .bind(&row.cost_usd)
        .bind(row.context_used)
        .bind(row.context_max)
        .bind(&row.metadata)
        .fetch_one(pool)
        .await
    })
}
