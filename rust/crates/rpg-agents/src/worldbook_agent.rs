//! worldbook_agent — 世界书子代理(分层信息架构)。
//!
//! 对应 Python: `rpg/agents/worldbook_agent.py`
//!
//! Layer 0: 原文 (script_chapters / document_chunks FTS)
//! Layer 1: ChapterFact (chapter_facts) — 每章一行
//! Layer 2: PhaseDigest (phase_digests) — 同 phase 多章聚合
//! Layer 3: WorldTimeline — phase_digests 按时间顺序遍列
//!
//! **确定性算法,不调 LLM**(快、低延迟、可解释)。
//!
//! ⚠️ DB 查询全部留 TODO,接 rpg-db 后填。

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::PgPool;
use std::sync::Arc;

use crate::common::AgentResult;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct WorldbookResult {
    /// [0, 1] 0=完全没匹配, 1.0=精确命中
    pub confidence: f64,
    pub timeline_anchor: Value,
    pub phase_digest: Option<Value>,
    pub chapter_facts: Vec<Value>,
    pub worldbook_entries: Vec<Value>,
    /// 大幅跳跃时的 progress 说明
    pub progress_note: String,
    /// 拉了哪些层
    pub sources: Vec<String>,
    pub elapsed_ms: u64,
}

impl WorldbookResult {
    /// 打包成 GM context bundle 的一段文本。
    pub fn to_context_text(&self) -> String {
        let mut parts: Vec<String> = Vec::new();

        if let Some(a) = self.timeline_anchor.as_object() {
            let phase = a.get("phase").and_then(|v| v.as_str()).unwrap_or("(未匹配)");
            let cmin = a.get("chapter_min").and_then(|v| v.as_str()).unwrap_or("?");
            let cmax = a.get("chapter_max").and_then(|v| v.as_str()).unwrap_or("?");
            let tl = a.get("time_label").and_then(|v| v.as_str()).unwrap_or("");
            parts.push(format!(
                "=== 当前时间线锚点 ===\n故事 phase: {phase}\n参考章节: 第{cmin}-{cmax}章\n时间标签: {tl}"
            ));
        }
        if let Some(pd) = &self.phase_digest {
            let label = pd.get("phase_label").and_then(|v| v.as_str()).unwrap_or("");
            let summary = pd.get("summary").and_then(|v| v.as_str()).unwrap_or("");
            let summary_truncated: String = summary.chars().take(1500).collect();
            parts.push(format!(
                "=== 阶段摘要 ({label}) ===\n{summary_truncated}"
            ));
        }
        if !self.chapter_facts.is_empty() {
            let mut lines: Vec<String> = Vec::new();
            for cf in self.chapter_facts.iter().take(5) {
                let chapter = cf.get("chapter").map(|v| v.to_string()).unwrap_or_default();
                let title = cf.get("title").and_then(|v| v.as_str()).unwrap_or("");
                let time_label = cf
                    .get("story_time_label")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let summary: String = cf
                    .get("summary")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .chars()
                    .take(200)
                    .collect();
                let events: Vec<String> = cf
                    .get("events")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .take(2)
                    .map(|e| {
                        e.get("event")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string()
                    })
                    .collect();
                let ev_str: String = events.join("; ").chars().take(160).collect();
                lines.push(format!(
                    "第{chapter}章《{title}》｜{time_label}\n  摘要: {summary}\n  事件: {ev_str}"
                ));
            }
            parts.push(format!("=== 相关章节事实 ===\n{}", lines.join("\n\n")));
        }
        if !self.worldbook_entries.is_empty() {
            let mut lines: Vec<String> = Vec::new();
            for wb in self.worldbook_entries.iter().take(5) {
                let title = wb.get("title").and_then(|v| v.as_str()).unwrap_or("");
                let content: String = wb
                    .get("content")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .chars()
                    .take(400)
                    .collect();
                lines.push(format!("【{title}】\n{content}"));
            }
            parts.push(format!("=== 世界设定 ===\n{}", lines.join("\n\n")));
        }
        if !self.progress_note.is_empty() {
            parts.push(format!("=== 跳跃进度说明 ===\n{}", self.progress_note));
        }
        parts.join("\n\n")
    }
}

#[derive(Debug, Clone, Default)]
pub struct ConsultInput {
    pub script_id: i64,
    pub query: String,
    pub save_id: Option<i64>,
    pub current_phase: String,
    pub current_time: String,
    pub jump_to_phase: String,
    pub jump_to_chapter: Option<i32>,
}

pub struct WorldbookAgent {
    db: Option<Arc<PgPool>>,
}

impl WorldbookAgent {
    pub fn new() -> Self {
        Self { db: None }
    }

    pub fn with_db(mut self, pool: Arc<PgPool>) -> Self {
        self.db = Some(pool);
        self
    }

    /// 主入口。返回 WorldbookResult。
    ///
    /// 实装路径:
    ///  - Layer 1: 由 anchor 表(script_timeline_anchors)解析 timeline_anchor(若有 db)
    ///  - Layer 2: phase_digests(script-level)按 jump_to_phase 命中
    ///  - Layer 3: chapter_facts 按 jump_to_chapter ±2 取窗口
    ///  - Layer 4: worldbook_entries 按 keyword / aliases 模糊匹配
    ///
    /// db 未注入时降级:仅做 confidence=0,但保留 elapsed。
    pub async fn consult(&self, input: ConsultInput) -> AgentResult<WorldbookResult> {
        let t0 = std::time::Instant::now();
        let mut sources: Vec<String> = Vec::new();
        let mut result = WorldbookResult::default();

        let Some(pool) = self.db.as_ref() else {
            result.confidence = 0.0;
            result.sources.push("db_not_injected".into());
            result.elapsed_ms = t0.elapsed().as_millis() as u64;
            return Ok(result);
        };

        // Layer 1: timeline anchor —— 优先取 jump_to_phase,否则 current_phase。
        let phase_key = if !input.jump_to_phase.is_empty() {
            input.jump_to_phase.clone()
        } else {
            input.current_phase.clone()
        };
        if let Some(anchor) =
            load_timeline_anchor(pool.as_ref(), input.script_id, &phase_key).await
        {
            result.timeline_anchor = anchor;
            sources.push("timeline_anchor".into());
        }

        // Layer 2: phase_digests (剧本级)
        if !phase_key.is_empty() {
            if let Some(pd) = load_phase_digest_by_label(
                pool.as_ref(),
                input.script_id,
                &phase_key,
            )
            .await
            {
                result.phase_digest = Some(pd);
                sources.push("phase_digest".into());
            }
        }

        // Layer 3: chapter_facts 窗口
        if let Some(chapter_min) = input.jump_to_chapter {
            let facts = load_chapter_facts_window(
                pool.as_ref(),
                input.script_id,
                chapter_min - 2,
                chapter_min + 2,
            )
            .await;
            if !facts.is_empty() {
                result.chapter_facts = facts;
                sources.push("chapter_facts".into());
            }
        }

        // Layer 4: worldbook_entries — keyword / aliases 模糊匹配。
        let mut hits = consult_worldbook_entries(
            pool.as_ref(),
            input.script_id,
            input.save_id,
            &input.query,
        )
        .await;
        if !hits.is_empty() {
            // 限 8 个 + 按 priority 倒序(consult_worldbook_entries 内已排)
            hits.truncate(8);
            result.worldbook_entries = hits;
            sources.push("worldbook_entries".into());
        }

        // confidence:有 timeline / digest / chapter_facts / worldbook 任一即基本可用。
        let mut conf = 0.0_f64;
        if result.timeline_anchor.is_object() && !result.timeline_anchor.as_object().unwrap().is_empty() {
            conf += 0.35;
        }
        if result.phase_digest.is_some() {
            conf += 0.25;
        }
        if !result.chapter_facts.is_empty() {
            conf += 0.20;
        }
        if !result.worldbook_entries.is_empty() {
            conf += 0.20;
        }
        result.confidence = conf.min(1.0);
        result.sources = sources;
        result.elapsed_ms = t0.elapsed().as_millis() as u64;
        Ok(result)
    }
}

impl Default for WorldbookAgent {
    fn default() -> Self {
        Self::new()
    }
}

// ── DB 加载辅助 ────────────────────────────────────────────────────────

/// 从 worldbook_entries 表按 keyword / aliases / content 做模糊匹配。
///
/// 返回的 Value 形如 `{"id":..,"title":..,"content":..,"priority":..,"key":..}`,
/// 已按 priority DESC 排序。
async fn consult_worldbook_entries(
    pool: &PgPool,
    script_id: i64,
    save_id: Option<i64>,
    query: &str,
) -> Vec<Value> {
    if query.trim().is_empty() {
        // 仍返回 script 全部,按 priority 排序前 8。
        let entries = rpg_db::repos::worldbook_entries::list_for_script(pool, script_id)
            .await
            .unwrap_or_default();
        return entries
            .into_iter()
            .take(8)
            .map(worldbook_entry_to_value)
            .collect();
    }
    // pattern: 任何子串都触发 ILIKE。
    let pat = format!("%{}%", query.trim().to_lowercase());

    // 自定义 raw query —— key/content/comment/aliases::text ILIKE
    let q = sqlx::query_as::<
        _,
        (
            i64,
            Option<i64>,
            Option<i64>,
            Option<i64>,
            String,
            Value,
            String,
            String,
            bool,
            i32,
            i32,
            Value,
            Value,
        ),
    >(
        r#"SELECT id, script_id, save_id, user_id, key, aliases, content, comment,
                  enabled, priority, token_budget, tags, metadata
           FROM worldbook_entries
           WHERE enabled = true
             AND (script_id = $1 OR save_id = $2)
             AND (
                  LOWER(key) LIKE $3
               OR LOWER(content) LIKE $3
               OR LOWER(comment) LIKE $3
               OR LOWER(COALESCE(aliases::text, '')) LIKE $3
             )
           ORDER BY priority DESC, id ASC
           LIMIT 16"#,
    )
    .bind(script_id)
    .bind(save_id)
    .bind(&pat)
    .fetch_all(pool)
    .await;

    let rows = match q {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("[worldbook] consult ILIKE 查询失败: {e}");
            return Vec::new();
        }
    };

    rows.into_iter()
        .map(|(id, _sid, _save, _uid, key, aliases, content, comment, _en, priority, _tb, tags, _meta)| {
            json!({
                "id": id,
                "title": key,
                "key": "",
                "content": content,
                "comment": comment,
                "aliases": aliases,
                "priority": priority,
                "tags": tags,
            })
        })
        .collect()
}

fn worldbook_entry_to_value(entry: rpg_db::repos::worldbook_entries::WorldbookEntry) -> Value {
    json!({
        "id": entry.id,
        "title": entry.key,
        "content": entry.content,
        "comment": entry.comment,
        "aliases": entry.aliases,
        "priority": entry.priority,
        "tags": entry.tags,
    })
}

/// 从 script_timeline_anchors 表按 phase 标签加载 anchor。
/// 表不存在 / 行不存在时返回 None。
async fn load_timeline_anchor(pool: &PgPool, script_id: i64, phase_key: &str) -> Option<Value> {
    if phase_key.is_empty() {
        return None;
    }
    let row = sqlx::query_as::<_, (String, Option<i32>, Option<i32>, Option<String>, Option<Value>)>(
        r#"SELECT phase, chapter_min, chapter_max, time_label, metadata
           FROM script_timeline_anchors
           WHERE script_id = $1 AND phase = $2
           LIMIT 1"#,
    )
    .bind(script_id)
    .bind(phase_key)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();
    row.map(|(phase, cmin, cmax, time_label, metadata)| {
        let cmin_s = cmin.map(|x| x.to_string()).unwrap_or_default();
        let cmax_s = cmax.map(|x| x.to_string()).unwrap_or_default();
        json!({
            "phase": phase,
            "chapter_min": cmin_s,
            "chapter_max": cmax_s,
            "time_label": time_label.unwrap_or_default(),
            "metadata": metadata.unwrap_or(Value::Null),
        })
    })
}

/// 按 phase_label 在 phase_digests(script-level)里命中。
async fn load_phase_digest_by_label(
    pool: &PgPool,
    script_id: i64,
    phase_label: &str,
) -> Option<Value> {
    let row = sqlx::query_as::<_, (i32, String, String, Value)>(
        r#"SELECT phase_index, phase_label, summary, key_events
           FROM phase_digests
           WHERE script_id = $1 AND phase_label = $2
           ORDER BY phase_index DESC
           LIMIT 1"#,
    )
    .bind(script_id)
    .bind(phase_label)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();
    row.map(|(idx, label, summary, key_events)| {
        json!({
            "phase_index": idx,
            "phase_label": label,
            "summary": summary,
            "key_events": key_events,
        })
    })
}

/// 取 [chapter_min, chapter_max] 窗口内的 chapter_facts。
async fn load_chapter_facts_window(
    pool: &PgPool,
    script_id: i64,
    chapter_min: i32,
    chapter_max: i32,
) -> Vec<Value> {
    let rows = sqlx::query_as::<
        _,
        (
            i32,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<Value>,
        ),
    >(
        r#"SELECT chapter, title, story_time_label, summary, events
           FROM chapter_facts
           WHERE script_id = $1 AND chapter BETWEEN $2 AND $3
           ORDER BY chapter ASC
           LIMIT 20"#,
    )
    .bind(script_id)
    .bind(chapter_min)
    .bind(chapter_max)
    .fetch_all(pool)
    .await
    .unwrap_or_default();
    rows.into_iter()
        .map(|(chapter, title, time_label, summary, events)| {
            json!({
                "chapter": chapter,
                "title": title.unwrap_or_default(),
                "story_time_label": time_label.unwrap_or_default(),
                "summary": summary.unwrap_or_default(),
                "events": events.unwrap_or(Value::Array(vec![])),
            })
        })
        .collect()
}

#[doc(hidden)]
pub async fn load_effective_worldbook_for_save(
    pool: &PgPool,
    script_id: i64,
    save_id: Option<i64>,
) -> AgentResult<Vec<Value>> {
    let mut out: Vec<Value> = Vec::new();
    if let Ok(rows) = rpg_db::repos::worldbook_entries::list_for_script(pool, script_id).await {
        out.extend(rows.into_iter().map(worldbook_entry_to_value));
    }
    if let Some(sid) = save_id {
        if let Ok(rows) = rpg_db::repos::worldbook_entries::list_for_save(pool, sid).await {
            out.extend(rows.into_iter().map(worldbook_entry_to_value));
        }
    }
    Ok(out)
}
