//! retrieval —— 知识库高层封装(chapter_facts + BM25 chunks + entity 向量召回)。
//!
//! 完成度: **主路径完整 + entity 向量层占位**
//! - `list_chapter_facts(state, query, top_k)` —— chapter_facts 按章节范围拉取
//! - `retrieve_script_context(...)` —— chunks(BM25-lite via rpg-retrieval)+ entity 召回拼接
//! - `retrieve_runtime_context(...)` —— 从 runtime 拿 save_id → script_id 后转发
//! - `entity_search(...)` —— pgvector 语义召回 character_cards / worldbook_entries
//!
//! 对应 Python:
//!   - `rpg/platform_app/knowledge/retrieval.py`
//!   - `rpg/platform_app/knowledge/_search.py`
//!   - `rpg/platform_app/knowledge/_utils.py::_query_tokens`
//!
//! TODO:
//!   - vector embed_query 实际接入 rpg-llm Vertex(目前 entity_search 在 vec=None 时跳过)
//!   - runtime.read_runtime 多人 user_id 校验已部分翻译,
//!     完整 runtime 二进制兼容(file backend)等 `runtime::read_runtime` 稳定

use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// `chapter_facts` 行(精简,前端 list 用)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChapterFactRow {
    pub id: i64,
    #[serde(default)]
    pub public_id: Option<String>,
    pub chapter: i32,
    pub title: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub story_phase: String,
    #[serde(default)]
    pub story_time_label: String,
    pub scene_count: i32,
    pub token_estimate: i32,
    #[serde(default)]
    pub confidence: f64,
}

/// `entity_search` 命中(character_cards / worldbook_entries 通用)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EntityHit {
    pub id: i64,
    pub kind: String, // "card" | "worldbook"
    pub title: String,
    pub identity: String,
    pub personality: String,
    pub appearance: String,
    pub content: String,
    pub score: f64,
    pub first_chapter: Option<i32>,
}

#[derive(Debug, Default, Clone)]
pub struct RetrievalOptions {
    pub chapter_min: Option<i32>,
    pub chapter_max: Option<i32>,
    pub top_k: usize,
}

impl RetrievalOptions {
    pub fn with_top_k(mut self, k: usize) -> Self {
        self.top_k = k;
        self
    }
}

async fn require_script(pool: &PgPool, user_id: i64, script_id: i64) -> PlatformResult<()> {
    let row = sqlx::query("select 1 from scripts where id = $1 and owner_id = $2")
        .bind(script_id)
        .bind(user_id)
        .fetch_optional(pool)
        .await?;
    if row.is_none() {
        return Err(PlatformError::forbidden("无权访问该剧本"));
    }
    Ok(())
}

// ─── chapter facts list ───────────────────────────────────────────────────

/// Python: `list_chapter_facts(user_id, script_id, limit, cursor)`。
pub async fn list_chapter_facts(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    limit: i64,
    before_chapter: Option<i32>,
) -> PlatformResult<(Vec<ChapterFactRow>, bool)> {
    require_script(pool, user_id, script_id).await?;
    let page_limit = limit.max(1).min(200);
    let rows = sqlx::query(
        r#"
        select id, public_id, chapter, title, summary, story_phase, story_time_label,
               scene_count, token_estimate, confidence
          from chapter_facts
         where script_id = $1
           and ($2::integer is null or chapter > $2)
         order by chapter asc
         limit $3
        "#,
    )
    .bind(script_id)
    .bind(before_chapter)
    .bind(page_limit + 1)
    .fetch_all(pool)
    .await?;
    let has_more = rows.len() as i64 > page_limit;
    let take = (rows.len()).min(page_limit as usize);
    let items: Vec<ChapterFactRow> = rows
        .iter()
        .take(take)
        .map(|r| ChapterFactRow {
            id: r.try_get("id").unwrap_or(0),
            public_id: r
                .try_get::<Option<uuid::Uuid>, _>("public_id")
                .ok()
                .flatten()
                .map(|u| u.to_string()),
            chapter: r.try_get("chapter").unwrap_or(0),
            title: r.try_get::<Option<String>, _>("title").ok().flatten().unwrap_or_default(),
            summary: r
                .try_get::<Option<String>, _>("summary")
                .ok()
                .flatten()
                .unwrap_or_default(),
            story_phase: r
                .try_get::<Option<String>, _>("story_phase")
                .ok()
                .flatten()
                .unwrap_or_default(),
            story_time_label: r
                .try_get::<Option<String>, _>("story_time_label")
                .ok()
                .flatten()
                .unwrap_or_default(),
            scene_count: r.try_get::<i32, _>("scene_count").unwrap_or(0),
            token_estimate: r.try_get::<i32, _>("token_estimate").unwrap_or(0),
            confidence: r.try_get::<f64, _>("confidence").unwrap_or(0.0),
        })
        .collect();
    Ok((items, has_more))
}

// ─── 文本检索:chapter_facts + chunks ──────────────────────────────────────

async fn load_chapter_fact_text(
    pool: &PgPool,
    script_id: i64,
    chapter_min: Option<i32>,
    chapter_max: Option<i32>,
    top_k: usize,
) -> PlatformResult<String> {
    let rows = sqlx::query(
        r#"
        select chapter, title, story_time_label, summary, events
          from chapter_facts
         where script_id = $1
           and ($2::integer is null or chapter >= $2)
           and ($3::integer is null or chapter <= $3)
         order by chapter
         limit $4
        "#,
    )
    .bind(script_id)
    .bind(chapter_min)
    .bind(chapter_max)
    .bind((top_k.max(1) + 2) as i64)
    .fetch_all(pool)
    .await?;
    if rows.is_empty() {
        return Ok(String::new());
    }
    let mut lines: Vec<String> = Vec::with_capacity(rows.len());
    for r in &rows {
        let chapter: i32 = r.try_get("chapter").unwrap_or(0);
        let title: String = r.try_get::<Option<String>, _>("title").ok().flatten().unwrap_or_default();
        let stl: String = r
            .try_get::<Option<String>, _>("story_time_label")
            .ok()
            .flatten()
            .unwrap_or_default();
        let summary: String = r
            .try_get::<Option<String>, _>("summary")
            .ok()
            .flatten()
            .unwrap_or_default();
        let events: serde_json::Value = r
            .try_get::<Option<serde_json::Value>, _>("events")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([]));
        let event_text: String = events
            .as_array()
            .map(|a| {
                a.iter()
                    .take(2)
                    .filter_map(|e| e.get("event").and_then(|v| v.as_str()))
                    .collect::<Vec<_>>()
                    .join("；")
            })
            .unwrap_or_default();
        // 长度截断
        let summary_t: String = summary.chars().take(180).collect();
        let event_t: String = event_text.chars().take(220).collect();
        lines.push(format!(
            "第{}章《{}》｜{}\n摘要：{}\n事件：{}",
            chapter, title, stl, summary_t, event_t
        ));
    }
    Ok(format!(
        "=== Postgres ChapterFact ===\n{}",
        lines.join("\n\n")
    ))
}

/// Python: `retrieve_script_context(script_id, query, chapter_min, chapter_max, top_k)`。
///
/// 输出拼接好的文本块,供 GM prompt 注入。组合策略与 Python 一致:
/// 1. chapter_facts 摘要
/// 2. document_chunks BM25 命中(via rpg-retrieval)
/// 3. entity 向量召回(失败自动跳过,目前 embed_query 未接入 → 直接跳)
pub async fn retrieve_script_context(
    pool: &PgPool,
    script_id: i64,
    query: &str,
    opts: RetrievalOptions,
) -> PlatformResult<String> {
    let top_k = if opts.top_k == 0 { 3 } else { opts.top_k };
    let mut parts: Vec<String> = Vec::new();

    // 1) chapter_facts
    let facts = load_chapter_fact_text(pool, script_id, opts.chapter_min, opts.chapter_max, top_k).await?;
    if !facts.is_empty() {
        parts.push(facts);
    }

    // 2) document_chunks via rpg-retrieval bm25_search
    if !query.trim().is_empty() {
        let cmin = opts.chapter_min;
        let cmax = opts.chapter_max;
        // rpg-retrieval API 用 i32 表示 script_id
        let script_id_i32 = i32::try_from(script_id).unwrap_or(i32::MAX);
        let hits = rpg_retrieval::bm25_search(pool, script_id_i32, query, top_k, cmin, cmax)
            .await
            .unwrap_or_default();
        if !hits.is_empty() {
            let body: Vec<String> = hits
                .iter()
                .map(|h| {
                    let snippet: String = h.text.chars().take(360).collect();
                    format!("[第{}章片段]\n{}", h.chapter_index, snippet.trim())
                })
                .collect();
            parts.push(format!(
                "=== Postgres 原文片段 ===\n{}",
                body.join("\n\n")
            ));
        }
    }

    // 3) entity 向量召回(目前 embed_query 未接入 → 内部静默返回空,不报错)
    if let Ok(ents) = entity_search(pool, script_id, query, opts.chapter_max, top_k).await {
        let mut card_lines: Vec<String> = Vec::new();
        let mut wb_lines: Vec<String> = Vec::new();
        for h in &ents {
            if h.kind == "card" {
                let bio = if h.identity.is_empty() { "—" } else { h.identity.as_str() };
                let persona_full: String = h.personality.chars().take(240).collect();
                let persona = if persona_full.is_empty() { "—".to_string() } else { persona_full };
                let look_full: String = h.appearance.chars().take(160).collect();
                let look = if look_full.is_empty() { "—".to_string() } else { look_full };
                card_lines.push(format!(
                    "《{}》(相关度 {:.2})\n  身份:{}\n  性格:{}\n  外貌:{}",
                    h.title, h.score, bio, persona, look
                ));
            } else {
                let content: String = h.content.chars().take(240).collect();
                wb_lines.push(format!(
                    "《{}》(相关度 {:.2}): {}",
                    h.title, h.score, content
                ));
            }
        }
        if !card_lines.is_empty() {
            parts.push(format!(
                "=== 角色档案(向量召回) ===\n{}",
                card_lines.join("\n")
            ));
        }
        if !wb_lines.is_empty() {
            parts.push(format!(
                "=== 世界书条目(向量召回) ===\n{}",
                wb_lines.join("\n")
            ));
        }
    }

    Ok(parts.join("\n\n"))
}

/// Python: `retrieve_runtime_context(query, ..., user_id)`。
///
/// 从 runtime 拿当前 save_id → script_id 后转发 `retrieve_script_context`。
pub async fn retrieve_runtime_context(
    pool: &PgPool,
    user_id: i64,
    query: &str,
    opts: RetrievalOptions,
) -> PlatformResult<String> {
    // 取 runtime —— file or db backend,read_runtime 内部自动分发
    let rt = crate::runtime::read_runtime(pool, Some(user_id)).await?;
    if rt.save_id == 0 {
        return Ok(String::new());
    }
    // user_id 校验:rt 必须属于当前 user
    if rt.user_id != 0 && rt.user_id != user_id {
        return Ok(String::new());
    }
    // 校验 save 归属
    let row = sqlx::query(
        "select script_id from game_saves where id = $1 and user_id = $2",
    )
    .bind(rt.save_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    let Some(r) = row else {
        return Ok(String::new());
    };
    let script_id: i64 = r.try_get("script_id").unwrap_or(0);
    if script_id == 0 {
        return Ok(String::new());
    }
    retrieve_script_context(pool, script_id, query, opts).await
}

// ─── entity 向量召回 ──────────────────────────────────────────────────────

/// Python: `_search_entities(db, script_id, query, chapter_min, chapter_max, ...)`。
///
/// pgvector 余弦距离召回 character_cards + worldbook_entries。
/// 当前 embedding 模块的 `embed_query` 还没接 rpg-llm,会返 `None`,
/// 此时直接返回空 vec(GM 端继续走 chunks fallback,无副作用)。
pub async fn entity_search(
    pool: &PgPool,
    script_id: i64,
    query: &str,
    chapter_max: Option<i32>,
    top_k: usize,
) -> PlatformResult<Vec<EntityHit>> {
    if query.trim().is_empty() {
        return Ok(Vec::new());
    }
    // 没真正 client → embedding 跑不动,跳过
    // 这一层的 client 注入由调用方负责,Python 也是 lazy import + try/except 静默
    let _ = (pool, script_id, chapter_max, top_k);
    Ok(Vec::new())
}

/// 给定 embedding 向量(由 caller 用 `embedding::embed_query` 拿到)做 entity 召回。
///
/// 调用方知道自己有 client、知道当前 chapter_max,自然该传向量进来。
pub async fn entity_search_with_vec(
    pool: &PgPool,
    script_id: i64,
    embedding: &[f32],
    chapter_max: Option<i32>,
    top_k_cards: usize,
    top_k_wb: usize,
) -> PlatformResult<Vec<EntityHit>> {
    let mut out: Vec<EntityHit> = Vec::new();
    let lit = vec_to_pgvector_literal(embedding);

    // character_cards
    let card_rows = sqlx::query(
        r#"
        select id, name, identity, personality, appearance,
               first_chapter,
               (1 - (embedding_vec <=> $1::vector)) as score
          from character_cards
         where script_id = $2
           and embedding_vec is not null
           and enabled = true
           and ($3::integer is null
                or first_chapter is null
                or first_chapter <= $3)
         order by embedding_vec <=> $1::vector
         limit $4
        "#,
    )
    .bind(&lit)
    .bind(script_id)
    .bind(chapter_max)
    .bind(top_k_cards.max(1).min(8) as i64)
    .fetch_all(pool)
    .await
    .unwrap_or_default();
    for r in &card_rows {
        out.push(EntityHit {
            id: r.try_get::<i64, _>("id").unwrap_or(0),
            kind: "card".to_string(),
            title: r.try_get::<String, _>("name").unwrap_or_default(),
            identity: r.try_get::<Option<String>, _>("identity").ok().flatten().unwrap_or_default(),
            personality: r
                .try_get::<Option<String>, _>("personality")
                .ok()
                .flatten()
                .unwrap_or_default(),
            appearance: r
                .try_get::<Option<String>, _>("appearance")
                .ok()
                .flatten()
                .unwrap_or_default(),
            content: String::new(),
            score: r.try_get::<f64, _>("score").unwrap_or(0.0),
            first_chapter: r.try_get::<Option<i32>, _>("first_chapter").ok().flatten(),
        });
    }

    // worldbook_entries
    let wb_rows = sqlx::query(
        r#"
        select id, title, content, first_chapter,
               (1 - (embedding_vec <=> $1::vector)) as score
          from worldbook_entries
         where script_id = $2
           and embedding_vec is not null
           and ($3::integer is null
                or first_chapter is null
                or first_chapter <= $3)
         order by embedding_vec <=> $1::vector
         limit $4
        "#,
    )
    .bind(&lit)
    .bind(script_id)
    .bind(chapter_max)
    .bind(top_k_wb.max(1).min(8) as i64)
    .fetch_all(pool)
    .await
    .unwrap_or_default();
    for r in &wb_rows {
        out.push(EntityHit {
            id: r.try_get::<i64, _>("id").unwrap_or(0),
            kind: "worldbook".to_string(),
            title: r.try_get::<String, _>("title").unwrap_or_default(),
            identity: String::new(),
            personality: String::new(),
            appearance: String::new(),
            content: r.try_get::<Option<String>, _>("content").ok().flatten().unwrap_or_default(),
            score: r.try_get::<f64, _>("score").unwrap_or(0.0),
            first_chapter: r.try_get::<Option<i32>, _>("first_chapter").ok().flatten(),
        });
    }

    Ok(out)
}

/// 把 f32 向量序列化为 pgvector 字面量字符串(`[v1,v2,...]`)。
fn vec_to_pgvector_literal(v: &[f32]) -> String {
    let mut s = String::with_capacity(v.len() * 10 + 2);
    s.push('[');
    for (i, x) in v.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!("{:.6}", x));
    }
    s.push(']');
    s
}
