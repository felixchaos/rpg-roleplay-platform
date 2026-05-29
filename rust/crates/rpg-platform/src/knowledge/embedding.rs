//! Vertex text-embedding-004 + pgvector 接入骨架。
//!
//! 对应 Python `knowledge/embedding.py`。
//! 完成度:
//! - `EmbeddingClient` trait + `VertexEmbeddingClient` 占位实现(待 rpg-llm vertex client)
//! - `embed_query` / `embed_status` / `embed_script` 顶层入口
//! - 批量大小 / 维度等常量与 Python 完全对齐
//!
//! 实际 Vertex 调用细节(yup-oauth2 / generateContent)由 rpg-llm crate 提供。

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use thiserror::Error;

/// Python: `EMBED_MODEL`。
pub const EMBED_MODEL: &str = "text-embedding-004";
/// Python: `EMBED_DIM`。
pub const EMBED_DIM: usize = 768;
/// Python: `BATCH_SIZE = 30`。
pub const BATCH_SIZE: usize = 30;
/// Python: `PER_CHUNK_CHAR_LIMIT = 1200`。
pub const PER_CHUNK_CHAR_LIMIT: usize = 1200;

#[derive(Debug, Error)]
pub enum EmbeddingError {
    #[error("vertex auth failed: {0}")]
    Auth(String),

    #[error("vertex API error: {0}")]
    Api(String),

    #[error("db error: {0}")]
    Db(#[from] sqlx::Error),

    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EmbeddingTaskType {
    RetrievalQuery,
    RetrievalDocument,
}

/// embedding client trait —— 让 platform 单测可注入 mock。
#[async_trait]
pub trait EmbeddingClient: Send + Sync {
    async fn embed(
        &self,
        texts: &[String],
        task_type: EmbeddingTaskType,
    ) -> Result<Vec<Vec<f32>>, EmbeddingError>;
}

/// 默认 Vertex 实现(stub)。
///
/// TODO[Sonnet]: 通过 rpg-llm vertex client 调 `:embedContent`。
pub struct VertexEmbeddingClient {
    pub project_id: String,
    pub location: String,
}

impl VertexEmbeddingClient {
    pub fn new(project_id: impl Into<String>) -> Self {
        Self {
            project_id: project_id.into(),
            // Python: `location='us-central1'`
            location: "us-central1".to_string(),
        }
    }

    /// 尝试从 `vertex_sa.json` / 环境 `GOOGLE_APPLICATION_CREDENTIALS` 自动建。
    pub fn from_env() -> Option<Self> {
        let sa_path = std::env::var("GOOGLE_APPLICATION_CREDENTIALS")
            .ok()
            .filter(|p| std::path::Path::new(p).exists())
            .or_else(|| {
                for candidate in ["./vertex_sa.json", "./rpg/vertex_sa.json"] {
                    if std::path::Path::new(candidate).exists() {
                        return Some(candidate.to_string());
                    }
                }
                None
            })?;
        let text = std::fs::read_to_string(&sa_path).ok()?;
        let sa: serde_json::Value = serde_json::from_str(&text).ok()?;
        let project_id = sa.get("project_id")?.as_str()?.to_string();
        Some(Self::new(project_id))
    }
}

#[async_trait]
impl EmbeddingClient for VertexEmbeddingClient {
    async fn embed(
        &self,
        _texts: &[String],
        _task_type: EmbeddingTaskType,
    ) -> Result<Vec<Vec<f32>>, EmbeddingError> {
        // TODO[Sonnet]: 调用 rpg-llm 的 vertex client:
        //   POST https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{EMBED_MODEL}:predict
        //   body = { "instances": [{"content": t, "task_type": ...}], "parameters": {"outputDimensionality": 768} }
        Err(EmbeddingError::Api(
            "VertexEmbeddingClient::embed not yet wired to rpg-llm".into(),
        ))
    }
}

/// Python: `embed_query(text)` —— 单条 query 向量化。
pub async fn embed_query(
    client: &dyn EmbeddingClient,
    text: &str,
) -> Result<Option<Vec<f32>>, EmbeddingError> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Ok(None);
    }
    let vecs = client
        .embed(&[trimmed.to_string()], EmbeddingTaskType::RetrievalQuery)
        .await?;
    Ok(vecs.into_iter().next())
}

/// Python `embed_status(script_id)` —— 进度查询。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbeddingJobStatus {
    pub script_id: i64,
    pub total_chunks: i64,
    pub embedded_chunks: i64,
    pub total_cards: i64,
    pub embedded_cards: i64,
    pub total_worldbook: i64,
    pub embedded_worldbook: i64,
    pub running: bool,
}

pub async fn embed_status(pool: &PgPool, script_id: i64) -> Result<EmbeddingJobStatus, EmbeddingError> {
    let (total_chunks, embedded_chunks): (i64, i64) = sqlx::query_as(
        r#"
        select coalesce(count(*),0)::bigint as total,
               coalesce(count(*) filter (where embedding_vec is not null),0)::bigint as embedded
          from document_chunks where book_id = $1
        "#,
    )
    .bind(script_id)
    .fetch_one(pool)
    .await
    .unwrap_or((0, 0));

    // 角色卡 / worldbook 表名可能未对齐 → 失败默认 0
    let (total_cards, embedded_cards): (i64, i64) = sqlx::query_as(
        "select count(*)::bigint, count(*) filter (where embedding_vec is not null)::bigint \
         from character_cards where book_id = $1",
    )
    .bind(script_id)
    .fetch_one(pool)
    .await
    .unwrap_or((0, 0));

    let (total_world, embedded_world): (i64, i64) = sqlx::query_as(
        "select count(*)::bigint, count(*) filter (where embedding_vec is not null)::bigint \
         from worldbook_entries where book_id = $1",
    )
    .bind(script_id)
    .fetch_one(pool)
    .await
    .unwrap_or((0, 0));

    Ok(EmbeddingJobStatus {
        script_id,
        total_chunks,
        embedded_chunks,
        total_cards,
        embedded_cards,
        total_worldbook: total_world,
        embedded_worldbook: embedded_world,
        running: false, // TODO[Sonnet]: 跟踪进程内运行标记(对应 _EMBED_QUEUE_RUNNING)
    })
}

/// Python `embed_script(script_id, user_id)` —— 后台批量 embed。
///
/// 当前实现: 串行 batch 拉 chunks → client.embed → 写 pgvector。
/// TODO[Sonnet]: 后台 task 化、cards/worldbook 并跑。
pub async fn embed_script(
    pool: &PgPool,
    client: &dyn EmbeddingClient,
    script_id: i64,
) -> Result<EmbeddingJobStatus, EmbeddingError> {
    // 1. 取出未 embed 的 chunks(限 BATCH_SIZE * N)
    loop {
        let rows: Vec<(i64, String)> = sqlx::query_as(
            r#"
            select id, substring(content from 1 for $2)
              from document_chunks
             where book_id = $1 and embedding_vec is null
             order by id
             limit $3
            "#,
        )
        .bind(script_id)
        .bind(PER_CHUNK_CHAR_LIMIT as i32)
        .bind(BATCH_SIZE as i64)
        .fetch_all(pool)
        .await?;
        if rows.is_empty() {
            break;
        }
        let texts: Vec<String> = rows.iter().map(|(_, t)| t.clone()).collect();
        let vecs = client.embed(&texts, EmbeddingTaskType::RetrievalDocument).await?;
        for ((id, _), vec) in rows.iter().zip(vecs.iter()) {
            // pgvector::Vector 在 sqlx 0.9 / 0.8 间有 API 漂移;先用字面量写入。
            // 文本形态:`[v1,v2,...]` ::vector
            let lit = format!(
                "[{}]",
                vec.iter()
                    .map(|f| format!("{:.6}", f))
                    .collect::<Vec<_>>()
                    .join(",")
            );
            sqlx::query("update document_chunks set embedding_vec = $1::vector where id = $2")
                .bind(lit)
                .bind(id)
                .execute(pool)
                .await?;
        }
    }
    // TODO[Sonnet]: 同样的循环跑 character_cards / worldbook_entries
    embed_status(pool, script_id).await
}
