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
use regex::Regex;
use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use std::collections::{HashMap, HashSet};
use tracing::warn;

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
        SELECT chapter, title, story_time_label, summary, events_json
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
// pgvector 语义搜索（占位）
// ─────────────────────────────────────────────

/// pgvector 余弦相似度搜索（`embedding <=> $1::vector`）。
///
/// TODO: 等 rpg-db PgPool wrapper + embedding 生成侧完成后实现。
/// 当前只返回空 Vec，不影响编译。
#[allow(unused_variables)]
pub async fn vector_search(
    pool: &PgPool,
    script_id: i32,
    embedding: pgvector::Vector,
    top_k: usize,
    chapter_min: Option<i32>,
    chapter_max: Option<i32>,
) -> Result<Vec<RetrievalHit>> {
    // TODO: 等 rpg-db PgPool wrapper
    // SELECT id, chapter_index, chunk_index, content,
    //        1 - (embedding <=> $1::vector) AS score
    // FROM document_chunks
    // WHERE script_id = $2 ...
    warn!("vector_search: not yet implemented, returning empty");
    Ok(vec![])
}
