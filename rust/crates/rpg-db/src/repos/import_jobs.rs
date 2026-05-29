//! repos/import_jobs.rs — import_jobs 拆书流水线状态表操作

use crate::query_timed;
use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct ImportJob {
    pub id: i64,
    pub job_id: String,
    pub user_id: i64,
    pub script_id: Option<i64>,
    pub status: String,
    pub stage: String,
    pub stage_progress: i32,
    pub stage_total: i32,
    pub overall_progress: i32,
    pub overall_total: i32,
    pub cancel_requested: bool,
    pub budget_estimate: serde_json::Value,
    pub usage_actual: serde_json::Value,
    pub stages: serde_json::Value,
    pub error: String,
    pub started_at: Option<chrono::DateTime<chrono::Utc>>,
    pub finished_at: Option<chrono::DateTime<chrono::Utc>>,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub kind: String,
    pub heartbeat_at: Option<chrono::DateTime<chrono::Utc>>,
}

/// 创建一条新的 import_jobs 记录（status=pending）。
#[tracing::instrument(skip(pool), fields(job_id = %job_id, user_id = %user_id, kind = %kind))]
pub async fn start(
    pool: &PgPool,
    job_id: &str,
    user_id: i64,
    script_id: Option<i64>,
    kind: &str,
) -> Result<ImportJob, sqlx::Error> {
    query_timed!("insert", "rpg-db", {
        sqlx::query_as(
            "INSERT INTO import_jobs
                (job_id, user_id, script_id, status, stage, kind)
             VALUES ($1, $2, $3, 'pending', 'pending', $4)
             RETURNING id, job_id, user_id, script_id, status, stage,
                       stage_progress, stage_total, overall_progress, overall_total,
                       cancel_requested, budget_estimate, usage_actual, stages, error,
                       started_at, finished_at, created_at, updated_at, kind, heartbeat_at",
        )
        .bind(job_id)
        .bind(user_id)
        .bind(script_id)
        .bind(kind)
        .fetch_one(pool)
        .await
    })
}

/// 查询单条 import_jobs 记录。
#[tracing::instrument(skip(pool), fields(job_id = %job_id))]
pub async fn get(pool: &PgPool, job_id: &str) -> Result<Option<ImportJob>, sqlx::Error> {
    query_timed!("select", "rpg-db", {
        sqlx::query_as(
            "SELECT id, job_id, user_id, script_id, status, stage,
                    stage_progress, stage_total, overall_progress, overall_total,
                    cancel_requested, budget_estimate, usage_actual, stages, error,
                    started_at, finished_at, created_at, updated_at, kind, heartbeat_at
             FROM import_jobs WHERE job_id = $1",
        )
        .bind(job_id)
        .fetch_optional(pool)
        .await
    })
}

/// 状态流转：更新 status/stage/进度等字段。
#[tracing::instrument(skip(pool), fields(job_id = %job_id, status = %status, stage = %stage))]
pub async fn transition(
    pool: &PgPool,
    job_id: &str,
    status: &str,
    stage: &str,
    stage_progress: i32,
    stage_total: i32,
    overall_progress: i32,
) -> Result<Option<ImportJob>, sqlx::Error> {
    query_timed!("update", "rpg-db", {
        sqlx::query_as(
            "UPDATE import_jobs SET
                status = $2,
                stage = $3,
                stage_progress = $4,
                stage_total = $5,
                overall_progress = $6,
                updated_at = now(),
                heartbeat_at = now()
             WHERE job_id = $1
             RETURNING id, job_id, user_id, script_id, status, stage,
                       stage_progress, stage_total, overall_progress, overall_total,
                       cancel_requested, budget_estimate, usage_actual, stages, error,
                       started_at, finished_at, created_at, updated_at, kind, heartbeat_at",
        )
        .bind(job_id)
        .bind(status)
        .bind(stage)
        .bind(stage_progress)
        .bind(stage_total)
        .bind(overall_progress)
        .fetch_optional(pool)
        .await
    })
}

/// 标记任务失败。
#[tracing::instrument(skip(pool), fields(job_id = %job_id))]
pub async fn fail(
    pool: &PgPool,
    job_id: &str,
    error: &str,
) -> Result<Option<ImportJob>, sqlx::Error> {
    query_timed!("update", "rpg-db", {
        sqlx::query_as(
            "UPDATE import_jobs SET
                status = 'failed',
                error = $2,
                finished_at = now(),
                updated_at = now()
             WHERE job_id = $1
             RETURNING id, job_id, user_id, script_id, status, stage,
                       stage_progress, stage_total, overall_progress, overall_total,
                       cancel_requested, budget_estimate, usage_actual, stages, error,
                       started_at, finished_at, created_at, updated_at, kind, heartbeat_at",
        )
        .bind(job_id)
        .bind(error)
        .fetch_optional(pool)
        .await
    })
}
