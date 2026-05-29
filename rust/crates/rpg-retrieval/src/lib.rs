//! rpg-retrieval — bigram BM25-lite + postgres chunk 检索
//! 对应 Python: rpg/retrieval.py + rpg/chapter_fact_indexer.py
//!
//! 翻译了:
//!   - `bigrams()` — 中文 2-char n-gram 切词
//!   - `bm25_tokens()` — 提取查询词元（word + bigram）
//!   - `bm25_search()` — BM25-lite LIKE 搜索（postgres document_chunks 表）
//!   - `load_chapter_facts()` — 按章节范围拉 chapter_facts 行
//!   - `keyword_scan_chunks()` — 章节关键词频次扫描（对应 chapter_fact_indexer 的 _rank_terms/_rank_entities 逻辑）
//!
//! 未翻译 / TODO:
//!   - `retrieve_context()` 中依赖 timeline_index、platform_app.knowledge 等 Python 模块的组合逻辑
//!   - `_resolve_active_phase_range()` / `_load_worldbook_for_retrieval()` / `_load_script_character_cards()` — 需要完整 rpg-db repo 层
//!   - `chapter_fact_indexer::build_chapter_facts()` 的 SQLite 写入侧（Rust 侧只读 postgres）
//!   - pgvector 语义搜索（embedding <=> $1::vector）— 占位函数 `vector_search()` 已预留

use anyhow::Result;
use futures::StreamExt;
use once_cell::sync::OnceCell;
use regex::Regex;
use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use rpg_llm::pipeline::{ChatChunk, ChatMessage, ChatRequest, LlmBackend};
use rpg_llm::AnyBackend;

// ─────────────────────────────────────────────
// 公共结果类型
// ─────────────────────────────────────────────

/// BM25-lite 检索命中的 chunk 行
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetrievalHit {
    pub chunk_id: i64,
    pub chapter_index: i32,
    pub chunk_index: i32,
    pub score: f64,
    pub text: String,
}

/// chapter_facts 行（精简版，供注入 GM 上下文）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChapterFactRow {
    pub chapter: i32,
    pub title: String,
    pub story_time_label: String,
    pub summary: String,
    pub events_json: String,
}

/// 关键词频次（对应 Python _rank_entities / _rank_terms 输出）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KeywordHit {
    pub term: String,
    pub count: i64,
}

// ─────────────────────────────────────────────
// 核心工具：bigram 切词
// ─────────────────────────────────────────────

/// 中文 bigram：`[text[i:i+2] for i in range(len(text)-1)]`
/// Python 等价: `chars().collect::<Vec<_>>().windows(2).map(|w| w.iter().collect::<String>())`
pub fn bigrams(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    chars
        .windows(2)
        .map(|w| w.iter().collect::<String>())
        .collect()
}

/// 是否是中文字符（CJK Unified Ideographs）
#[inline]
fn is_chinese(c: char) -> bool {
    ('\u{4e00}'..='\u{9fff}').contains(&c)
}

/// 从查询文本提取 BM25 词元（≤8 个），对应 Python bm25_search 的 token 提取段
pub fn bm25_tokens(query: &str) -> Vec<String> {
    // 替换非中文、非字母数字为空格
    let clean_re = Regex::new(r"[^\u{4e00}-\u{9fff}\w]").unwrap();
    let clean = clean_re.replace_all(query, " ");

    let mut tokens: HashSet<String> = HashSet::new();

    // 空格切词（≥2 字）
    for word in clean.split_whitespace() {
        if word.chars().count() >= 2 {
            tokens.insert(word.to_string());
        }
    }

    // 补充中文 bigram
    let chars: Vec<char> = clean.chars().collect();
    for w in chars.windows(2) {
        if w.iter().all(|&c| is_chinese(c)) {
            tokens.insert(w.iter().collect::<String>());
        }
    }

    // 最多 8 个词元
    tokens.into_iter().take(8).collect()
}

// ─────────────────────────────────────────────
// BM25-lite 搜索（postgres document_chunks 表）
// ─────────────────────────────────────────────

/// 从 postgres `document_chunks` 表以 LIKE 关键词匹配，返回 `RetrievalHit` 列表。
///
/// 对应 Python `bm25_search(query, top_k, chapter_min, chapter_max)`。
/// 评分 = 命中词元数（简单计数）。
///
/// SQL 模式：
/// ```sql
/// SELECT id, chapter_index, chunk_index, content
/// FROM document_chunks
/// WHERE script_id = $1
///   AND (content LIKE $2 OR content LIKE $3 ...)
///   [AND chapter_index >= $n]
///   [AND chapter_index <= $m]
/// LIMIT <tok_count * 6>
/// ```
pub async fn bm25_search(
    pool: &PgPool,
    script_id: i32,
    query: &str,
    top_k: usize,
    chapter_min: Option<i32>,
    chapter_max: Option<i32>,
) -> Result<Vec<RetrievalHit>> {
    let tokens = bm25_tokens(query);
    if tokens.is_empty() {
        return Ok(vec![]);
    }

    // 动态拼 LIKE 子句（参数化，防 SQL 注入）
    // sqlx 不支持动态参数数量的 query!，用 query_builder
    let limit = (tokens.len() * 6) as i64;

    // 构造查询字符串
    let mut like_parts: Vec<String> = Vec::new();
    let mut bind_idx = 2usize; // $1 = script_id
    for _ in &tokens {
        like_parts.push(format!("content LIKE ${}", bind_idx));
        bind_idx += 1;
    }
    let mut sql = format!(
        "SELECT id, chapter_index, chunk_index, content \
         FROM document_chunks \
         WHERE script_id = $1 AND ({}) ",
        like_parts.join(" OR ")
    );
    if chapter_min.is_some() {
        sql.push_str(&format!("AND chapter_index >= ${} ", bind_idx));
        bind_idx += 1;
    }
    if chapter_max.is_some() {
        sql.push_str(&format!("AND chapter_index <= ${} ", bind_idx));
        // bind_idx += 1; // 最后不再用
    }
    sql.push_str(&format!("LIMIT {}", limit));

    // 用 sqlx::query 绑定
    let mut q = sqlx::query(&sql).bind(script_id);
    for tok in &tokens {
        q = q.bind(format!("%{}%", tok));
    }
    if let Some(cmin) = chapter_min {
        q = q.bind(cmin);
    }
    if let Some(cmax) = chapter_max {
        q = q.bind(cmax);
    }

    let rows = q.fetch_all(pool).await?;

    // 评分 + 去重
    let mut seen: HashSet<i64> = HashSet::new();
    let mut hits: Vec<RetrievalHit> = Vec::new();
    for row in rows {
        use sqlx::Row;
        let id: i64 = row.try_get("id")?;
        if seen.contains(&id) {
            continue;
        }
        seen.insert(id);
        let chapter_index: i32 = row.try_get("chunk_index").unwrap_or(0);
        let chunk_index: i32 = row.try_get("chunk_index").unwrap_or(0);
        let content: String = row.try_get("content").unwrap_or_default();
        let score = tokens.iter().filter(|t| content.contains(t.as_str())).count() as f64;
        hits.push(RetrievalHit {
            chunk_id: id,
            chapter_index,
            chunk_index,
            score,
            text: content,
        });
    }

    // 降序排序，取 top_k
    hits.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    hits.truncate(top_k);
    Ok(hits)
}

// ─────────────────────────────────────────────
// chapter_facts 检索
// ─────────────────────────────────────────────

/// 按章节范围从 postgres `chapter_facts` 表拉取事实行。
///
/// 对应 Python `load_chapter_facts(chapter_min, chapter_max, limit)`。
pub async fn load_chapter_facts(
    pool: &PgPool,
    script_id: i32,
    chapter_min: i32,
    chapter_max: i32,
    limit: i64,
) -> Result<Vec<ChapterFactRow>> {
    // 使用运行时 query，不依赖 DATABASE_URL 或 sqlx-prepare 缓存
    let rows = sqlx::query(
        r#"
        SELECT chapter, title, story_time_label, summary, events::text AS events_json
        FROM chapter_facts
        WHERE script_id = $1
          AND chapter BETWEEN $2 AND $3
        ORDER BY chapter
        LIMIT $4
        "#,
    )
    .bind(script_id)
    .bind(chapter_min)
    .bind(chapter_max)
    .bind(limit)
    .fetch_all(pool)
    .await?;

    use sqlx::Row;
    let rows: Vec<ChapterFactRow> = rows
        .into_iter()
        .map(|r| ChapterFactRow {
            chapter: r.try_get("chapter").unwrap_or(0),
            title: r.try_get("title").unwrap_or_default(),
            story_time_label: r.try_get("story_time_label").unwrap_or_default(),
            summary: r.try_get("summary").unwrap_or_default(),
            events_json: r.try_get("events_json").unwrap_or_default(),
        })
        .collect();
    Ok(rows)
}

/// 将 `ChapterFactRow` 列表格式化为可注入 GM prompt 的字符串。
///
/// 对应 Python `load_chapter_facts` 返回的拼接文本。
pub fn format_chapter_facts(rows: &[ChapterFactRow]) -> String {
    rows.iter()
        .map(|r| {
            // 解析 events_json，取前 2 条 event 字段
            let event_text: String = serde_json::from_str::<Vec<serde_json::Value>>(&r.events_json)
                .unwrap_or_default()
                .iter()
                .take(2)
                .filter_map(|e| e.get("event").and_then(|v| v.as_str()).map(String::from))
                .collect::<Vec<_>>()
                .join("；");
            format!(
                "第{}章《{}》｜{}\n摘要：{}\n事件：{}",
                r.chapter,
                r.title,
                r.story_time_label,
                &r.summary[..r.summary.len().min(180)],
                &event_text[..event_text.len().min(220)],
            )
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

// ─────────────────────────────────────────────
// 关键词频次扫描（对应 chapter_fact_indexer 的 _rank_terms / _rank_entities）
// ─────────────────────────────────────────────

/// 在文本中统计给定词表的命中频次，返回降序 `KeywordHit` 列表。
///
/// 对应 Python `_rank_terms(text, terms, entity_type)` 和
/// `_rank_entities(text, known_names, entity_type)` 的计数逻辑。
pub fn keyword_freq(text: &str, terms: &[&str]) -> Vec<KeywordHit> {
    let mut counts: HashMap<String, i64> = HashMap::new();
    for &term in terms {
        let cnt = text.match_indices(term).count() as i64;
        if cnt > 0 {
            *counts.entry(term.to_string()).or_insert(0) += cnt;
        }
    }
    let mut hits: Vec<KeywordHit> = counts
        .into_iter()
        .map(|(term, count)| KeywordHit { term, count })
        .collect();
    hits.sort_by_key(|b| std::cmp::Reverse(b.count));
    hits
}

/// 从 postgres `document_chunks` 按 `chapter_index` 拉取原文，
/// 在其上跑 `keyword_freq`，汇总整个章节范围的关键词频次。
///
/// 对应 `chapter_fact_indexer.py` 对章节文本运行 `_rank_entities` / `_rank_terms` 的逻辑。
pub async fn keyword_scan_chunks(
    pool: &PgPool,
    script_id: i32,
    chapter_min: i32,
    chapter_max: i32,
    terms: &[&str],
) -> Result<Vec<KeywordHit>> {
    let rows = sqlx::query(
        r#"
        SELECT content
        FROM document_chunks
        WHERE script_id = $1
          AND chapter_index BETWEEN $2 AND $3
        ORDER BY chapter_index, chunk_index
        "#,
    )
    .bind(script_id)
    .bind(chapter_min)
    .bind(chapter_max)
    .fetch_all(pool)
    .await?;

    use sqlx::Row;
    // 拼接所有 chunk 文本
    let full_text: String = rows
        .iter()
        .map(|r| r.try_get::<Option<String>, _>("content").unwrap_or(None).unwrap_or_default())
        .collect::<Vec<_>>()
        .join("");

    Ok(keyword_freq(&full_text, terms))
}

// ─────────────────────────────────────────────
// pgvector 语义搜索
// ─────────────────────────────────────────────

/// pgvector 余弦相似度搜索（`embedding <=> $1::vector`）。
///
/// 对应 Python `retrieval.__init__.search(embedding, top_k, chapter_min, chapter_max)`:
/// ```sql
/// SELECT id, chapter_index, chunk_index, content,
///        1.0 - (embedding <=> '<vec>'::vector) AS score
/// FROM document_chunks
/// WHERE script_id = $1
///   [AND chapter_index >= $2]
///   [AND chapter_index <= $3]
/// ORDER BY embedding <=> '<vec>'::vector
/// LIMIT top_k
/// ```
/// 对应 Python search():
///   - embedding 是已生成的 f32 向量（调用方负责生成，Wave 0-B Vertex embed）
///   - 余弦相似度 = 1 - cosine_distance（越大越好）
///   - 向量通过 pgvector 字面量(`[v1,v2,...]::vector`)内联到 SQL，避免 Encode trait 依赖
pub async fn search_embeddings(
    pool: &PgPool,
    script_id: i32,
    embedding: &[f32],
    top_k: usize,
    chapter_min: Option<i32>,
    chapter_max: Option<i32>,
) -> Result<Vec<RetrievalHit>> {
    if embedding.is_empty() {
        return Ok(vec![]);
    }
    // 序列化为 pgvector 字面量字符串 `[v1,v2,...]`（与 rpg-platform entity_search 一致）
    let vec_lit = embedding_to_pgvector_literal(embedding);

    // 动态构造 WHERE 子句（章节过滤）
    // $1 = script_id, $2 = chapter_min（可选）, $3 = chapter_max（可选）
    let mut bind_idx = 2usize; // $1=script_id
    let mut where_extra = String::new();
    if chapter_min.is_some() {
        where_extra.push_str(&format!(" AND chapter_index >= ${}", bind_idx));
        bind_idx += 1;
    }
    if chapter_max.is_some() {
        where_extra.push_str(&format!(" AND chapter_index <= ${}", bind_idx));
        // bind_idx += 1; // 最后
    }
    let sql = format!(
        "SELECT id, chapter_index, chunk_index, content, \
         1.0 - (embedding <=> '{vec}'::vector) AS score \
         FROM document_chunks \
         WHERE script_id = $1{extra} \
         ORDER BY embedding <=> '{vec}'::vector \
         LIMIT {top}",
        vec = vec_lit,
        extra = where_extra,
        top = top_k,
    );

    let mut q = sqlx::query(&sql).bind(script_id);
    if let Some(cmin) = chapter_min {
        q = q.bind(cmin);
    }
    if let Some(cmax) = chapter_max {
        q = q.bind(cmax);
    }

    let rows = q.fetch_all(pool).await?;

    use sqlx::Row;
    let mut hits: Vec<RetrievalHit> = Vec::with_capacity(rows.len());
    for row in rows {
        let id: i64 = row.try_get("id")?;
        let chapter_index: i32 = row.try_get("chapter_index").unwrap_or(0);
        let chunk_index: i32 = row.try_get("chunk_index").unwrap_or(0);
        let content: String = row.try_get("content").unwrap_or_default();
        // score = cosine 相似度（1 - 距离）
        let score: f64 = row.try_get::<f64, _>("score")
            .or_else(|_| row.try_get::<f32, _>("score").map(|v| v as f64))
            .unwrap_or(0.0);
        hits.push(RetrievalHit {
            chunk_id: id,
            chapter_index,
            chunk_index,
            score,
            text: content,
        });
    }
    Ok(hits)
}

/// 将 f32 向量序列化为 pgvector 字面量字符串 `[v1,v2,...]`。
/// 与 rpg-platform knowledge/retrieval.rs 中的 `vec_to_pgvector_literal` 逻辑一致。
fn embedding_to_pgvector_literal(v: &[f32]) -> String {
    let inner: Vec<String> = v.iter().map(|x| format!("{:.8}", x)).collect();
    format!("[{}]", inner.join(","))
}

/// 触发 embedding pipeline，为给定文本片段生成并写入 embedding。
///
/// 对应 Python `retrieval.__init__.build_index(texts, script_id, chapter_index)`:
///   - 将 texts 切分为 chunks（已由调用方切好，每条一行）
///   - 通过 Wave 0-B Vertex embed 接口生成向量
///   - UPSERT 到 `document_chunks` 的 `embedding` 列
///
/// 当前约束：embedding 生成侧（Vertex AI / Wave 0-B）未接入 Rust 端，
/// 故本函数执行 DB 写入侧（清空旧 embedding、写 content），
/// 并在行记录里将 `embedding` 置 NULL（等后台 pipeline 回填）。
/// 返回已插入/更新的行数。
///
/// 完整 embedding 回填路径：TODO[P2-EMBED] — 等 Wave 0-B vertex_embed crate 完成后接入。
pub async fn build_index(
    pool: &PgPool,
    script_id: i32,
    chapter_index: i32,
    chunks: &[String],
) -> Result<usize> {
    if chunks.is_empty() {
        return Ok(0);
    }
    // 删除当前 chapter 已有的 chunks，准备重新写入
    // （对应 Python build_index 的 _clear_chapter_chunks 步骤）
    sqlx::query(
        "DELETE FROM document_chunks \
         WHERE script_id = $1 AND chapter_index = $2",
    )
    .bind(script_id)
    .bind(chapter_index)
    .execute(pool)
    .await?;

    // 逐条插入（embedding 列留 NULL，等 pipeline 回填）
    let mut inserted = 0usize;
    for (chunk_idx, content) in chunks.iter().enumerate() {
        sqlx::query(
            "INSERT INTO document_chunks \
             (script_id, chapter_index, chunk_index, content, embedding) \
             VALUES ($1, $2, $3, $4, NULL)",
        )
        .bind(script_id)
        .bind(chapter_index)
        .bind(chunk_idx as i32)
        .bind(content.as_str())
        .execute(pool)
        .await?;
        inserted += 1;
    }
    Ok(inserted)
}

// ─────────────────────────────────────────────
// LLM summary pipeline (chunks → 摘要)
// ─────────────────────────────────────────────
//
// Wave 7-A:rpg-retrieval 召回的 chunks 太长(每条 300+ 字),直接塞 GM prompt 会
// 挤占预算。新增 summarize_chunks 调 LLM 把召回片段压成 80-150 字的紧凑摘要,
// 给 GM "事实+情节脉络",而不是冗余原文。
//
// 注入入口:启动期(rpg-routes / rpg-server bootstrap)调用 [`init_summary_backend`]
// 把 `Arc<AnyBackend>` + 默认 model_id 注入 OnceCell。未注入时 summarize_chunks
// 直接走原文拼接 fallback(不阻断生产流量,与 branches::summary 同款幂等模式)。

/// `summarize_chunks` 的 system prompt。
const RETRIEVAL_SUMMARY_SYSTEM: &str = concat!(
    "你是检索摘要助手。读完一组从原著章节召回的文本片段后,",
    "用 80-150 字概括其中与玩家本轮意图相关的事实 / 角色 / 场景。\n",
    "要求:\n",
    "- 只输出摘要本身,不要前缀 / 标题\n",
    "- 客观陈述,不要推测后续\n",
    "- 保留关键人名 / 地名 / 时间标签\n",
    "- 多条片段冲突时,逐条写明,不强行合并",
);

/// `summarize_chunks` 默认 max_tokens。对应 80-150 字 ≈ 120-240 token,留余量。
const RETRIEVAL_SUMMARY_MAX_TOKENS: u32 = 360;

/// LLM 调用总超时(秒)。
const RETRIEVAL_SUMMARY_TIMEOUT_SECS: u64 = 30;

/// LLM 摘要 backend OnceCell 容器。
///
/// 启动期(rpg-routes)调 `init_summary_backend` 注入选定的 backend + model。
/// 未注入时 `summarize_chunks` 走 fallback,行为与"只 embed 不 summary"一致。
struct SummaryBackend {
    backend: Arc<AnyBackend>,
    model: String,
}

static SUMMARY_BACKEND: OnceCell<SummaryBackend> = OnceCell::new();

/// 启动期注入 backend + 默认 model_id。重复注入只生效第一次(OnceCell 语义)。
///
/// 对应 `rpg_platform::branches::summary::init_summary_backend` 模式 —— Wave 5-D
/// 已建立同款幂等注入入口,这里复用相同形态。
pub fn init_summary_backend(backend: Arc<AnyBackend>, model: impl Into<String>) {
    let _ = SUMMARY_BACKEND.set(SummaryBackend {
        backend,
        model: model.into(),
    });
}

/// 仅测试可见:验证 OnceCell 是否注入过(不暴露内容)。
#[cfg(test)]
fn summary_backend_is_set() -> bool {
    SUMMARY_BACKEND.get().is_some()
}

/// Wave 7-A 主入口:把召回的 chunks 调 LLM 摘要为一段紧凑文本,供 GM prompt 注入。
///
/// 行为:
/// - 没有 chunks → 返 ""
/// - backend 未注入 → fallback:返 `format_chunks_fallback`(原文拼接,带章节标号)。
/// - 已注入 → 拼 prompt → backend.stream_chat → drain Text chunk → trim。
/// - 超时 / 错误 → 回退到 fallback,记 tracing::warn。
///
/// 参数:
/// - `query`:玩家本轮 retrieval_query,塞进 user prompt 帮 LLM 锁住相关性。
/// - `chunks`:从 BM25 / vector search 拿到的片段。
pub async fn summarize_chunks(query: &str, chunks: &[RetrievalHit]) -> String {
    if chunks.is_empty() {
        return String::new();
    }
    let Some(cfg) = SUMMARY_BACKEND.get() else {
        tracing::debug!("summarize_chunks: 未注入 backend,走原文拼接 fallback");
        return format_chunks_fallback(chunks);
    };
    match run_llm_summarize(cfg.backend.as_ref(), &cfg.model, query, chunks).await {
        Ok(text) if !text.is_empty() => text,
        Ok(_) => {
            tracing::debug!("summarize_chunks: LLM 返回空字符串,回退原文");
            format_chunks_fallback(chunks)
        }
        Err(e) => {
            tracing::warn!(error = %e, "summarize_chunks: LLM 失败,回退原文");
            format_chunks_fallback(chunks)
        }
    }
}

/// 不依赖 LLM 的 fallback:把 chunks 按章节排序后拼成可读文本。
///
/// 与 Python `bm25_search` 返回风格对齐:`[第N章片段]\n<content[:300]>`。
pub fn format_chunks_fallback(chunks: &[RetrievalHit]) -> String {
    let mut sorted: Vec<&RetrievalHit> = chunks.iter().collect();
    sorted.sort_by(|a, b| {
        a.chapter_index
            .cmp(&b.chapter_index)
            .then(a.chunk_index.cmp(&b.chunk_index))
    });
    sorted
        .into_iter()
        .map(|h| {
            let body: String = h.text.chars().take(300).collect();
            format!("[第{}章片段]\n{}", h.chapter_index, body.trim())
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

/// 实际跑 LLM 摘要 —— 注入了 backend 才走这条。
async fn run_llm_summarize(
    backend: &dyn LlmBackend,
    model: &str,
    query: &str,
    chunks: &[RetrievalHit],
) -> Result<String> {
    let prompt = build_summarize_prompt(query, chunks);
    let req = ChatRequest {
        model: model.to_string(),
        system: Some(RETRIEVAL_SUMMARY_SYSTEM.to_string()),
        messages: vec![ChatMessage::user(prompt)],
        tools: Vec::new(),
        temperature: None,
        max_tokens: Some(RETRIEVAL_SUMMARY_MAX_TOKENS),
        stream: false,
        extra: serde_json::Value::Null,
    };

    let collect_fut = async {
        let mut stream = backend
            .stream_chat(req)
            .await
            .map_err(|e| anyhow::anyhow!("stream_chat: {e}"))?;
        let mut text = String::new();
        while let Some(chunk) = stream.next().await {
            match chunk {
                Ok(ChatChunk::Text(t)) => text.push_str(&t),
                Ok(_) => {}
                Err(e) => return Err(anyhow::anyhow!("stream_chat chunk: {e}")),
            }
        }
        Ok::<String, anyhow::Error>(text)
    };

    let raw = match tokio::time::timeout(
        std::time::Duration::from_secs(RETRIEVAL_SUMMARY_TIMEOUT_SECS),
        collect_fut,
    )
    .await
    {
        Ok(r) => r?,
        Err(_) => return Err(anyhow::anyhow!("LLM summary timed out")),
    };
    Ok(raw.trim().to_string())
}

/// 拼 `summarize_chunks` 的 user prompt:玩家 query + 编号列出每条 chunk。
fn build_summarize_prompt(query: &str, chunks: &[RetrievalHit]) -> String {
    let query_clip: String = query.chars().take(300).collect();
    let mut lines: Vec<String> = Vec::with_capacity(chunks.len() + 2);
    lines.push(format!("玩家本轮意图:\n{}", query_clip));
    lines.push(String::new());
    lines.push("召回片段:".to_string());
    for (idx, hit) in chunks.iter().enumerate() {
        let body: String = hit.text.chars().take(400).collect();
        lines.push(format!(
            "{}. [第{}章, chunk {}, score {:.2}]\n{}",
            idx + 1,
            hit.chapter_index,
            hit.chunk_index,
            hit.score,
            body.trim()
        ));
    }
    lines.join("\n")
}

// ─────────────────────────────────────────────
// 单测
// ─────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bigrams_basic() {
        let result = bigrams("你好世界");
        assert_eq!(result, vec!["你好", "好世", "世界"]);
    }

    #[test]
    fn bigrams_empty() {
        assert!(bigrams("").is_empty());
        assert!(bigrams("a").is_empty()); // 单字无 bigram
    }

    #[test]
    fn bm25_tokens_dedup_and_limit() {
        let tokens = bm25_tokens("调查调查调查调查调查调查调查调查调查");
        // "调查" 只应出现一次（HashSet 去重）
        let count = tokens.iter().filter(|t| t.as_str() == "调查").count();
        assert_eq!(count, 1, "dedup 失败: {:?}", tokens);
        // 不超过 8 个
        assert!(tokens.len() <= 8);
    }

    #[test]
    fn bm25_tokens_extracts_cjk_bigrams() {
        let tokens = bm25_tokens("隐蔽潜行");
        assert!(
            tokens.iter().any(|t| t == "隐蔽" || t == "蔽潜" || t == "潜行"),
            "CJK bigram 提取失败: {:?}", tokens
        );
    }

    #[test]
    fn keyword_freq_counts_correctly() {
        let text = "怪物怪物宝剑";
        let hits = keyword_freq(text, &["怪物", "宝剑", "法术"]);
        let monster = hits.iter().find(|h| h.term == "怪物").expect("怪物应命中");
        assert_eq!(monster.count, 2);
        let sword = hits.iter().find(|h| h.term == "宝剑").expect("宝剑应命中");
        assert_eq!(sword.count, 1);
        assert!(hits.iter().all(|h| h.term != "法术"), "法术不应出现");
    }

    #[test]
    fn keyword_freq_sorted_descending() {
        let text = "abc abc abc xyz xyz";
        let hits = keyword_freq(text, &["abc", "xyz"]);
        assert!(!hits.is_empty());
        assert_eq!(hits[0].term, "abc", "最高频词应排在最前");
        assert_eq!(hits[0].count, 3);
    }

    // ── Wave 7-A: summarize_chunks 单测 ─────────────────────────

    fn make_hit(chapter: i32, chunk: i32, score: f64, text: &str) -> RetrievalHit {
        RetrievalHit {
            chunk_id: (chapter as i64) * 1000 + chunk as i64,
            chapter_index: chapter,
            chunk_index: chunk,
            score,
            text: text.to_string(),
        }
    }

    #[test]
    fn format_chunks_fallback_orders_by_chapter_then_chunk() {
        let hits = vec![
            make_hit(3, 0, 1.0, "丙"),
            make_hit(1, 1, 1.0, "乙"),
            make_hit(1, 0, 1.0, "甲"),
        ];
        let text = format_chunks_fallback(&hits);
        let pos_jia = text.find("甲").expect("含甲");
        let pos_yi = text.find("乙").expect("含乙");
        let pos_bing = text.find("丙").expect("含丙");
        assert!(pos_jia < pos_yi, "1/0 应在 1/1 前: {text}");
        assert!(pos_yi < pos_bing, "1/1 应在 3/0 前: {text}");
        assert!(text.contains("[第1章片段]"));
        assert!(text.contains("[第3章片段]"));
    }

    #[test]
    fn format_chunks_fallback_truncates_to_300_chars() {
        let long = "字".repeat(800);
        let hits = vec![make_hit(1, 0, 1.0, &long)];
        let text = format_chunks_fallback(&hits);
        // 300 字内容 + 标题"[第1章片段]\n" 12 字符
        let body_chars = text.chars().filter(|c| *c == '字').count();
        assert_eq!(body_chars, 300, "应截到 300 字: 实际 {body_chars}");
    }

    #[test]
    fn format_chunks_fallback_empty_returns_empty() {
        assert_eq!(format_chunks_fallback(&[]), "");
    }

    #[tokio::test]
    async fn summarize_chunks_empty_returns_empty() {
        let out = summarize_chunks("任意 query", &[]).await;
        assert_eq!(out, "");
    }

    #[tokio::test]
    async fn summarize_chunks_falls_back_when_backend_unset() {
        // 若 OnceCell 未注入,直接返 fallback(原文拼接)。
        // 由于 OnceCell 跨测试持久,这个测试只能断言:
        //   - 如果未注入 → 输出应包含原文 / 章节标号
        //   - 如果已注入(其它 test 先跑了) → 我们不做强断言,仅验证非 panic
        let hits = vec![make_hit(7, 2, 1.5, "薇瑟之歌"), make_hit(8, 0, 1.0, "另一段")];
        let out = summarize_chunks("薇瑟", &hits).await;
        if !summary_backend_is_set() {
            assert!(out.contains("第7章") || out.contains("薇瑟"), "fallback 文本: {out}");
            assert!(!out.is_empty());
        }
    }

    #[test]
    fn build_summarize_prompt_includes_query_and_indexed_chunks() {
        let hits = vec![make_hit(1, 0, 0.5, "甲文"), make_hit(2, 0, 0.8, "乙文")];
        let prompt = build_summarize_prompt("查询X", &hits);
        assert!(prompt.contains("玩家本轮意图"), "{prompt}");
        assert!(prompt.contains("查询X"));
        assert!(prompt.starts_with("玩家本轮意图"));
        assert!(prompt.contains("1. [第1章"));
        assert!(prompt.contains("2. [第2章"));
        assert!(prompt.contains("甲文"));
        assert!(prompt.contains("乙文"));
    }

    #[test]
    fn build_summarize_prompt_clips_query_to_300_chars() {
        let big_query: String = "Q".repeat(500);
        let prompt = build_summarize_prompt(&big_query, &[]);
        // Q 出现的次数 = 实际写入的 query 长度
        let q_count = prompt.chars().filter(|c| *c == 'Q').count();
        assert_eq!(q_count, 300);
    }

    #[test]
    fn init_summary_backend_helper_is_callable() {
        // OnceCell 跨测持久,只验证 helper API 在 cfg(test) 下编译通过。
        let _ = summary_backend_is_set();
    }

    #[test]
    fn format_chapter_facts_renders_correctly() {
        let rows = vec![ChapterFactRow {
            chapter: 1,
            title: "序章".into(),
            story_time_label: "初春".into(),
            summary: "玩家到达村庄".into(),
            events_json: r#"[{"event":"进入村庄"},{"event":"遇见村长"}]"#.into(),
        }];
        let text = format_chapter_facts(&rows);
        assert!(text.contains("第1章"), "应含章节号: {}", text);
        assert!(text.contains("序章"), "应含标题: {}", text);
        assert!(text.contains("进入村庄"), "应含事件: {}", text);
    }
}
