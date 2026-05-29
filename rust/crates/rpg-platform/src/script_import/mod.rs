//! script_import —— 拆书导入流水线。
//!
//! 对应 Python `rpg/platform_app/script_import.py` (1006 行)。
//! 完成度: **Job 状态机骨架** —— 类型定义 + 状态转换函数,
//! 实际章节切分由 `chapter_splitter` crate(已存在,但未在 workspace)负责。
//!
//! 流水线:
//! ```text
//!   Pending → Splitting → Persisting → SyncingKnowledge → Done
//!                                  ↘ Failed
//! ```

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// Job 状态枚举。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Pending,
    Splitting,
    Persisting,
    SyncingKnowledge,
    Done,
    Failed,
}

impl JobStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            JobStatus::Pending => "pending",
            JobStatus::Splitting => "splitting",
            JobStatus::Persisting => "persisting",
            JobStatus::SyncingKnowledge => "syncing_knowledge",
            JobStatus::Done => "done",
            JobStatus::Failed => "failed",
        }
    }
}

/// 拆书 Job 行(对应 Python `_import_jobs` 表)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ImportJob {
    pub id: i64,
    pub user_id: i64,
    pub script_id: Option<i64>,
    pub source_name: String,
    pub status: String,
    #[serde(default)]
    pub progress: f64,
    #[serde(default)]
    pub error: String,
    #[serde(default)]
    pub report: serde_json::Value,
    pub created_at: Option<DateTime<Utc>>,
    pub updated_at: Option<DateTime<Utc>>,
}

/// 启动一个新 Job(占位)。
///
/// Python 流程:
/// 1. 上传/拉块 → bytes
/// 2. `chapter_splitter.split_chapters_with_report`
/// 3. 写 `scripts` + `script_chapters`
/// 4. `_schedule_knowledge_sync` → 异步触发 knowledge embedding
///
/// TODO[Sonnet]: 把 chapter_splitter 翻成 rpg-chapter-splitter crate 后接进来。
pub async fn start_job(
    pool: &PgPool,
    user_id: i64,
    source_name: &str,
) -> PlatformResult<ImportJob> {
    let row = sqlx::query(
        r#"
        insert into script_import_jobs(user_id, source_name, status, progress, report)
        values ($1, $2, $3, 0.0, '{}'::jsonb)
        returning *
        "#,
    )
    .bind(user_id)
    .bind(source_name)
    .bind(JobStatus::Pending.as_str())
    .fetch_one(pool)
    .await?;
    row_to_job(&row)
}

/// 状态机推进(把当前 job 切到新状态)。
pub async fn transition(pool: &PgPool, job_id: i64, status: JobStatus) -> PlatformResult<ImportJob> {
    let row = sqlx::query(
        r#"
        update script_import_jobs
           set status = $1, updated_at = now()
         where id = $2
        returning *
        "#,
    )
    .bind(status.as_str())
    .bind(job_id)
    .fetch_one(pool)
    .await?;
    row_to_job(&row)
}

/// 标记 Job 失败。
pub async fn fail(pool: &PgPool, job_id: i64, error: &str) -> PlatformResult<ImportJob> {
    let row = sqlx::query(
        r#"
        update script_import_jobs
           set status = $1, error = $2, updated_at = now()
         where id = $3
        returning *
        "#,
    )
    .bind(JobStatus::Failed.as_str())
    .bind(error)
    .bind(job_id)
    .fetch_one(pool)
    .await?;
    row_to_job(&row)
}

/// 取当前 Job 状态。
pub async fn get(pool: &PgPool, job_id: i64) -> PlatformResult<ImportJob> {
    let row = sqlx::query("select * from script_import_jobs where id = $1")
        .bind(job_id)
        .fetch_optional(pool)
        .await?;
    match row {
        Some(r) => row_to_job(&r),
        None => Err(PlatformError::not_found("import job not found")),
    }
}

fn row_to_job(row: &sqlx::postgres::PgRow) -> PlatformResult<ImportJob> {
    Ok(ImportJob {
        id: row.try_get("id")?,
        user_id: row.try_get("user_id")?,
        script_id: row.try_get::<Option<i64>, _>("script_id").ok().flatten(),
        source_name: row.try_get::<String, _>("source_name").unwrap_or_default(),
        status: row.try_get::<String, _>("status")?,
        progress: row.try_get::<f64, _>("progress").unwrap_or(0.0),
        error: row.try_get::<String, _>("error").unwrap_or_default(),
        report: row
            .try_get::<serde_json::Value, _>("report")
            .unwrap_or(serde_json::json!({})),
        created_at: row.try_get("created_at").ok(),
        updated_at: row.try_get("updated_at").ok(),
    })
}

// TODO[Sonnet]: 完整 import_script(user_id, file_item, *, split_rule, custom_pattern, title, upload_id)
//               需要先有 chapter_splitter / library::decode_upload 的 Rust 等价物
// TODO[Sonnet]: _consume_upload_chunks(user_id, upload_id, peek) —— 拼接 chunk 上传
// TODO[Sonnet]: _schedule_knowledge_sync —— spawn task,调 knowledge::embedding::embed_script
// TODO[Sonnet]: init_upload / put_chunk / finish_upload —— 大文件分块上传 API 三件套
