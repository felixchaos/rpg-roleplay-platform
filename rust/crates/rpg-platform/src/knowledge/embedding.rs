//! Vertex text-embedding-004 + pgvector 接入。
//!
//! 对应 Python `rpg/platform_app/knowledge/embedding.py`。
//! 完成度:
//! - `EmbeddingClient` trait + `VertexEmbeddingClient` 通过 rpg-llm VertexBackend 实现
//! - `embed_query` / `embed_status` / `embed_script` 顶层入口
//! - embed_script 覆盖 document_chunks / character_cards / worldbook_entries 三类
//! - 批量大小 / 维度等常量与 Python 完全对齐
//! - running 状态通过进程内 AtomicBool 追踪

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use once_cell::sync::Lazy;
use parking_lot::Mutex;
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

/// character_cards 查询行:(id, name, identity, personality, appearance)
type CharCardRow = (i64, String, Option<String>, Option<String>, Option<String>);

// 进程内运行标记:script_id → Arc<AtomicBool>
// 对应 Python `_EMBED_QUEUE_RUNNING: dict[int, bool]`
static RUNNING_MAP: Lazy<Mutex<HashMap<i64, Arc<AtomicBool>>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

fn is_running(script_id: i64) -> bool {
    RUNNING_MAP
        .lock()
        .get(&script_id)
        .map(|b| b.load(Ordering::Relaxed))
        .unwrap_or(false)
}

fn set_running(script_id: i64, flag: Arc<AtomicBool>) {
    RUNNING_MAP.lock().insert(script_id, flag);
}

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

impl From<rpg_llm::LlmError> for EmbeddingError {
    fn from(e: rpg_llm::LlmError) -> Self {
        EmbeddingError::Api(e.to_string())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EmbeddingTaskType {
    RetrievalQuery,
    RetrievalDocument,
}

impl EmbeddingTaskType {
    fn as_vertex_str(self) -> &'static str {
        match self {
            EmbeddingTaskType::RetrievalQuery => "RETRIEVAL_QUERY",
            EmbeddingTaskType::RetrievalDocument => "RETRIEVAL_DOCUMENT",
        }
    }
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

/// Vertex 实现 —— 通过 rpg-llm VertexBackend 调 `:predict`。
///
/// 对应 Python `_embed_batch` / `embed_query` 里的 `client.models.embed_content(...)` 调用。
/// 重试逻辑在 VertexBackend::embed_with_task_type 内部处理(429/503 指数退避,5xx 3 次)。
pub struct VertexEmbeddingClient {
    pub project_id: String,
    pub location: String,
    backend: Arc<rpg_llm::VertexBackend>,
}

impl VertexEmbeddingClient {
    /// 从已构造好的 VertexBackend 创建。
    pub fn from_backend(backend: rpg_llm::VertexBackend) -> Self {
        let project_id = backend.project_id().to_string();
        let location = backend.region().to_string();
        Self {
            project_id,
            location,
            backend: Arc::new(backend),
        }
    }

    /// 尝试从 `vertex_sa.json` / 环境 `GOOGLE_APPLICATION_CREDENTIALS` 自动建。
    /// 如果找不到文件或初始化失败则返回 None。
    pub async fn from_env() -> Option<Self> {
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
        let backend = rpg_llm::VertexBackend::from_sa_file(&sa_path).await.ok()?;
        Some(Self::from_backend(backend))
    }
}

#[async_trait]
impl EmbeddingClient for VertexEmbeddingClient {
    async fn embed(
        &self,
        texts: &[String],
        task_type: EmbeddingTaskType,
    ) -> Result<Vec<Vec<f32>>, EmbeddingError> {
        self.backend
            .embed_with_task_type(EMBED_MODEL, texts, task_type.as_vertex_str())
            .await
            .map_err(EmbeddingError::from)
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
        running: is_running(script_id),
    })
}

/// pgvector "[v1,v2,...]" 字面量辅助函数。
/// 对应 Python `_vec_literal`。
fn vec_literal(v: &[f32]) -> String {
    let inner: Vec<String> = v.iter().map(|f| format!("{:.6}", f)).collect();
    format!("[{}]", inner.join(","))
}

/// Python `embed_script(script_id, user_id)` —— 后台批量 embed。
///
/// 当前实现:
///  1. document_chunks:串行 batch 拉未 embed 的 chunks → client.embed → 写 pgvector
///  2. character_cards:同样 batch,文本拼接 name + identity + personality + appearance
///  3. worldbook_entries:同样 batch,文本拼接 title + content
///
/// 对应 Python `_embed_chunks_loop` 的完整逻辑(含 entity 层)。
pub async fn embed_script(
    pool: &PgPool,
    client: &dyn EmbeddingClient,
    script_id: i64,
) -> Result<EmbeddingJobStatus, EmbeddingError> {
    // ---- 1. document_chunks ----
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
            let lit = vec_literal(vec);
            sqlx::query("update document_chunks set embedding_vec = $1::vector where id = $2")
                .bind(lit)
                .bind(id)
                .execute(pool)
                .await?;
        }
    }

    // ---- 2. character_cards ----
    // 对应 Python: entity 层 embed —— name + identity + personality + appearance 拼接
    loop {
        // 每次只拉 BATCH_SIZE 条未 embed 的
        let rows: Vec<CharCardRow> =
            sqlx::query_as(
                r#"
                select id, name, identity, personality, appearance
                  from character_cards
                 where book_id = $1 and embedding_vec is null
                 order by id
                 limit $2
                "#,
            )
            .bind(script_id)
            .bind(BATCH_SIZE as i64)
            .fetch_all(pool)
            .await?;
        if rows.is_empty() {
            break;
        }
        let texts: Vec<String> = rows
            .iter()
            .map(|(_, name, identity, personality, appearance)| {
                let id_part = identity.as_deref().unwrap_or("");
                let per_part = &personality.as_deref().unwrap_or("")[..personality
                    .as_deref()
                    .unwrap_or("")
                    .len()
                    .min(1000)];
                let app_part = &appearance.as_deref().unwrap_or("")[..appearance
                    .as_deref()
                    .unwrap_or("")
                    .len()
                    .min(500)];
                format!("{name}。{id_part}。{per_part}。{app_part}")
            })
            .collect();
        let vecs = client.embed(&texts, EmbeddingTaskType::RetrievalDocument).await?;
        for ((id, _, _, _, _), vec) in rows.iter().zip(vecs.iter()) {
            let lit = vec_literal(vec);
            sqlx::query(
                "update character_cards set embedding_vec = $1::vector where id = $2",
            )
            .bind(lit)
            .bind(id)
            .execute(pool)
            .await?;
        }
    }

    // ---- 3. worldbook_entries ----
    // 对应 Python: entity 层 embed —— title + content 拼接
    loop {
        let rows: Vec<(i64, String, Option<String>)> = sqlx::query_as(
            r#"
            select id, title, content
              from worldbook_entries
             where book_id = $1 and embedding_vec is null
             order by id
             limit $2
            "#,
        )
        .bind(script_id)
        .bind(BATCH_SIZE as i64)
        .fetch_all(pool)
        .await?;
        if rows.is_empty() {
            break;
        }
        let texts: Vec<String> = rows
            .iter()
            .map(|(_, title, content)| {
                let body = &content.as_deref().unwrap_or("")[..content
                    .as_deref()
                    .unwrap_or("")
                    .len()
                    .min(2000)];
                format!("{title}。{body}")
            })
            .collect();
        let vecs = client.embed(&texts, EmbeddingTaskType::RetrievalDocument).await?;
        for ((id, _, _), vec) in rows.iter().zip(vecs.iter()) {
            let lit = vec_literal(vec);
            sqlx::query(
                "update worldbook_entries set embedding_vec = $1::vector where id = $2",
            )
            .bind(lit)
            .bind(id)
            .execute(pool)
            .await?;
        }
    }

    embed_status(pool, script_id).await
}

/// 后台启动 embed_script,返回立即(fire-and-forget)。
/// 调用方需要自己传入已构建好的 client Arc。
/// 对应 Python `embed_script` 里的 `threading.Thread(target=_embed_chunks_loop, ...)` 模式。
pub fn spawn_embed_script(
    pool: PgPool,
    client: Arc<dyn EmbeddingClient>,
    script_id: i64,
) -> Arc<AtomicBool> {
    let flag = Arc::new(AtomicBool::new(true));
    set_running(script_id, flag.clone());
    let flag_clone = flag.clone();
    tokio::spawn(async move {
        let _ = embed_script(&pool, client.as_ref(), script_id).await;
        flag_clone.store(false, Ordering::Relaxed);
    });
    flag
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    // -------------------------------------------------------------------------
    // MockEmbeddingClient — 用于 unit 测试,不发真实网络请求
    // -------------------------------------------------------------------------

    struct MockEmbeddingClient {
        /// 每次 embed 调用返回的固定向量维度
        dim: usize,
        /// 追踪 embed 被调用的次数
        call_count: Arc<AtomicUsize>,
        /// 可选:注入失败模式
        fail_after: Option<usize>,
    }

    impl MockEmbeddingClient {
        fn new(dim: usize) -> Self {
            Self {
                dim,
                call_count: Arc::new(AtomicUsize::new(0)),
                fail_after: None,
            }
        }

        fn with_fail_after(dim: usize, n: usize) -> Self {
            Self {
                dim,
                call_count: Arc::new(AtomicUsize::new(0)),
                fail_after: Some(n),
            }
        }
    }

    #[async_trait]
    impl EmbeddingClient for MockEmbeddingClient {
        async fn embed(
            &self,
            texts: &[String],
            _task_type: EmbeddingTaskType,
        ) -> Result<Vec<Vec<f32>>, EmbeddingError> {
            let count = self.call_count.fetch_add(1, Ordering::SeqCst);
            if let Some(fail_after) = self.fail_after {
                if count >= fail_after {
                    return Err(EmbeddingError::Api("mock: simulated failure".into()));
                }
            }
            // 返回全 0 向量(768 维或指定维度)
            Ok(texts.iter().map(|_| vec![0.0f32; self.dim]).collect())
        }
    }

    // -------------------------------------------------------------------------
    // embed_query tests
    // -------------------------------------------------------------------------

    #[tokio::test]
    async fn test_embed_query_empty_returns_none() {
        let client = MockEmbeddingClient::new(EMBED_DIM);
        let result = embed_query(&client, "   ").await.unwrap();
        assert!(result.is_none());
    }

    #[tokio::test]
    async fn test_embed_query_returns_vector() {
        let client = MockEmbeddingClient::new(EMBED_DIM);
        let result = embed_query(&client, "hello world").await.unwrap();
        assert!(result.is_some());
        let v = result.unwrap();
        assert_eq!(v.len(), EMBED_DIM);
    }

    // -------------------------------------------------------------------------
    // EmbeddingTaskType tests
    // -------------------------------------------------------------------------

    #[test]
    fn test_task_type_vertex_strings() {
        assert_eq!(
            EmbeddingTaskType::RetrievalQuery.as_vertex_str(),
            "RETRIEVAL_QUERY"
        );
        assert_eq!(
            EmbeddingTaskType::RetrievalDocument.as_vertex_str(),
            "RETRIEVAL_DOCUMENT"
        );
    }

    // -------------------------------------------------------------------------
    // vec_literal tests
    // -------------------------------------------------------------------------

    #[test]
    fn test_vec_literal_format() {
        let v = vec![0.123456_f32, -1.0_f32, 0.5_f32];
        let lit = vec_literal(&v);
        assert!(lit.starts_with('['));
        assert!(lit.ends_with(']'));
        // 每个值有 6 位小数
        assert!(lit.contains("0.123456"));
        assert!(lit.contains("-1.000000"));
        assert!(lit.contains("0.500000"));
    }

    #[test]
    fn test_vec_literal_empty() {
        let lit = vec_literal(&[]);
        assert_eq!(lit, "[]");
    }

    // -------------------------------------------------------------------------
    // batch size / constants tests
    // -------------------------------------------------------------------------

    #[test]
    fn test_constants_match_python() {
        assert_eq!(EMBED_MODEL, "text-embedding-004");
        assert_eq!(EMBED_DIM, 768);
        assert_eq!(BATCH_SIZE, 30);
        assert_eq!(PER_CHUNK_CHAR_LIMIT, 1200);
    }

    // -------------------------------------------------------------------------
    // MockEmbeddingClient call_count test
    // -------------------------------------------------------------------------

    #[tokio::test]
    async fn test_mock_client_call_count() {
        let client = MockEmbeddingClient::new(EMBED_DIM);
        let call_count = client.call_count.clone();

        let texts: Vec<String> = (0..5).map(|i| format!("text {i}")).collect();
        let vecs = client
            .embed(&texts, EmbeddingTaskType::RetrievalDocument)
            .await
            .unwrap();

        assert_eq!(vecs.len(), 5);
        assert_eq!(vecs[0].len(), EMBED_DIM);
        assert_eq!(call_count.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn test_mock_client_fail_mode() {
        let client = MockEmbeddingClient::with_fail_after(EMBED_DIM, 0);
        let texts = vec!["some text".to_string()];
        let result = client
            .embed(&texts, EmbeddingTaskType::RetrievalDocument)
            .await;
        assert!(result.is_err());
        match result.unwrap_err() {
            EmbeddingError::Api(msg) => assert!(msg.contains("simulated failure")),
            _ => panic!("unexpected error variant"),
        }
    }

    // -------------------------------------------------------------------------
    // running map tests
    // -------------------------------------------------------------------------

    #[test]
    fn test_running_map_initial_false() {
        assert!(!is_running(99999));
    }

    #[test]
    fn test_running_map_set_and_clear() {
        let flag = Arc::new(AtomicBool::new(true));
        set_running(88888, flag.clone());
        assert!(is_running(88888));
        flag.store(false, Ordering::Relaxed);
        assert!(!is_running(88888));
    }
}
