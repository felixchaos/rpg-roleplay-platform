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

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::PgPool;
use std::sync::Arc;

use crate::common::AgentResult;
use rpg_llm::pipeline::LlmBackend;
use rpg_llm::vertex::VertexBackend;

/// 默认 Vertex embedding 模型。
const DEFAULT_EMBED_MODEL: &str = "text-embedding-005";

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
            // chapter_min / chapter_max 在 Python 端是 int(允许 None),Rust 端对齐 i32:
            // 渲染时优先取整数,缺失时回落空字符串("?")。不再吃字符串。
            let cmin_render = a
                .get("chapter_min")
                .and_then(|v| v.as_i64())
                .map(|n| n.to_string())
                .unwrap_or_else(|| "?".to_string());
            let cmax_render = a
                .get("chapter_max")
                .and_then(|v| v.as_i64())
                .map(|n| n.to_string())
                .unwrap_or_else(|| "?".to_string());
            let tl = a.get("time_label").and_then(|v| v.as_str()).unwrap_or("");
            parts.push(format!(
                "=== 当前时间线锚点 ===\n故事 phase: {phase}\n参考章节: 第{cmin_render}-{cmax_render}章\n时间标签: {tl}"
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
    /// 可选 Vertex backend(注入后启用 embedding 检索)。
    embedder: Option<Arc<VertexBackend>>,
    embed_model: String,
}

impl WorldbookAgent {
    pub fn new() -> Self {
        Self {
            db: None,
            embedder: None,
            embed_model: DEFAULT_EMBED_MODEL.to_string(),
        }
    }

    pub fn with_db(mut self, pool: Arc<PgPool>) -> Self {
        self.db = Some(pool);
        self
    }

    /// 注入 Vertex embedder — 注入后 consult_worldbook_entries 优先走 pgvector
    /// `embedding <=> $1::vector` 排序,失败回退 ILIKE。
    pub fn with_embedder(mut self, embedder: Arc<VertexBackend>) -> Self {
        self.embedder = Some(embedder);
        self
    }

    pub fn with_embed_model(mut self, model: impl Into<String>) -> Self {
        self.embed_model = model.into();
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

        // Layer 4: worldbook_entries — embedding 优先,失败回退 ILIKE。
        let mut hits = consult_worldbook_entries(
            pool.as_ref(),
            input.script_id,
            input.save_id,
            &input.query,
            self.embedder.as_deref(),
            &self.embed_model,
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
/// 优先级:
///   1. 如果传了 embedder + 表里有 `embedding` 列 → pgvector cosine 排序。
///   2. 失败 / 没传 embedder → ILIKE 模糊匹配。
///
/// 返回的 Value 形如 `{"id":..,"title":..,"content":..,"priority":..,"key":..}`,
/// 已按 priority DESC 排序。
async fn consult_worldbook_entries(
    pool: &PgPool,
    script_id: i64,
    save_id: Option<i64>,
    query: &str,
    embedder: Option<&VertexBackend>,
    embed_model: &str,
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

    // ── 1) 尝试 pgvector 路径(注入 embedder 时)──
    if let Some(emb) = embedder {
        match emb.embed(embed_model, &[query.to_string()]).await {
            Ok(vecs) if !vecs.is_empty() && !vecs[0].is_empty() => {
                let qvec = &vecs[0];
                // pgvector 字面量:`[0.1,0.2,...]`。
                let vec_lit = format!(
                    "[{}]",
                    qvec.iter()
                        .map(|x| format!("{x}"))
                        .collect::<Vec<_>>()
                        .join(",")
                );
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
                         AND embedding IS NOT NULL
                       ORDER BY embedding <=> $3::vector ASC, priority DESC
                       LIMIT 16"#,
                )
                .bind(script_id)
                .bind(save_id)
                .bind(&vec_lit)
                .fetch_all(pool)
                .await;
                match q {
                    Ok(rows) if !rows.is_empty() => {
                        return rows
                            .into_iter()
                            .map(
                                |(id, _sid, _save, _uid, key, aliases, content, comment, _en, priority, _tb, tags, _meta)| {
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
                                },
                            )
                            .collect();
                    }
                    Ok(_) => {
                        tracing::debug!(
                            "[worldbook] pgvector 查询无结果,回退 ILIKE"
                        );
                    }
                    Err(e) => {
                        tracing::warn!(
                            "[worldbook] pgvector 查询失败({e}),回退 ILIKE"
                        );
                    }
                }
            }
            Ok(_) => {
                tracing::warn!("[worldbook] embedder 返回空向量,回退 ILIKE");
            }
            Err(e) => {
                tracing::warn!("[worldbook] embed 失败({e}),回退 ILIKE");
            }
        }
    }

    // ── 2) ILIKE 兜底 ──
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
        "title": entry.title,
        "content": entry.content,
        "keys": entry.keys,
        "priority": entry.priority,
        "enabled": entry.enabled,
    })
}

/// 从 script_timeline_anchors 表按 phase 标签加载 anchor。
/// 表不存在 / 行不存在时返回 None。
async fn load_timeline_anchor(pool: &PgPool, script_id: i64, phase_key: &str) -> Option<Value> {
    if phase_key.is_empty() {
        return None;
    }
    let row = sqlx::query_as::<_, (String, Option<i32>, Option<i32>, Option<String>, Option<Value>)>(
        r#"SELECT story_phase, chapter_min, chapter_max, story_time_label, metadata
           FROM script_timeline_anchors
           WHERE script_id = $1 AND story_phase = $2
           LIMIT 1"#,
    )
    .bind(script_id)
    .bind(phase_key)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();
    row.map(|(phase, cmin, cmax, time_label, metadata)| {
        // 对齐 Python `worldbook_agent.py` —— chapter_min / chapter_max 直接以
        // i32 写进 timeline_anchor,允许为 null(对应 Python 的 None)。
        // 老逻辑用 `.to_string()` 把 i32 编成 JSON 字符串,导致前端 /消费方拿到
        // `"10"` 而非 `10`,与 Python 类型签名漂移。
        let cmin_v = cmin.map(Value::from).unwrap_or(Value::Null);
        let cmax_v = cmax.map(Value::from).unwrap_or(Value::Null);
        json!({
            "phase": phase,
            "chapter_min": cmin_v,
            "chapter_max": cmax_v,
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

/// 返回 save 级"有效世界书"候选列表 (最多 30 条,用于 consult 的 picks 筛选)。
///
/// 对齐 Python `agents/worldbook_agent.py::load_effective_worldbook_for_save`:
///
/// Merge 逻辑:
///   1. 拉 worldbook_entries (script 级, enabled=true), 最多 50 条
///   2. 拉 save_worldbook_overlays (本 save 的 retirement 和 addition)
///   3. 从 script entries 中排除掉被 retirement 覆盖的条目 (按 retired_entry_id)
///   4. 把 addition overlay 追加到候选 (id=None, overlay_id=ov.id)
///   5. 按 priority DESC 排序, 返回前 30 条
///
/// `save_id = None` 时只返回 script 级 entries (无 overlay 合并)。
#[doc(hidden)]
pub async fn load_effective_worldbook_for_save(
    pool: &PgPool,
    script_id: i64,
    save_id: Option<i64>,
) -> AgentResult<Vec<Value>> {
    // 1) script 级基础 entries (Python: LIMIT 50)
    let script_rows = sqlx::query_as::<_, (i64, String, String, Value, i32)>(
        r#"SELECT id, title, content, keys, priority
           FROM worldbook_entries
           WHERE script_id = $1 AND enabled = true
           ORDER BY priority DESC, id ASC
           LIMIT 50"#,
    )
    .bind(script_id)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    // save_id 缺失:只返回 script 级,不走 overlay merge。
    let Some(sid) = save_id else {
        let mut out: Vec<Value> = script_rows
            .into_iter()
            .map(script_row_to_value)
            .collect();
        out.truncate(30);
        return Ok(out);
    };

    // 2) overlay rows
    let overlay_rows = rpg_db::repos::save_worldbook_overlays::list_for_save(pool, sid)
        .await
        .unwrap_or_default();

    Ok(merge_worldbook_with_overlays(script_rows, overlay_rows))
}

/// 把 (i64,String,String,Value,i32) script entry 翻成 Python 同款 dict 形 Value。
fn script_row_to_value(row: (i64, String, String, Value, i32)) -> Value {
    let (id, title, content, keys, priority) = row;
    json!({
        "id": id,
        "title": title,
        "content": content,
        "keys": keys,
        "priority": priority,
        "_source": "script",
    })
}

/// 纯函数版 merge —— 把 script 级 entries 与 save 级 overlay 行合并为有效世界书候选。
///
/// 抽出来便于单测(无需 DB)。
/// 对齐 Python `load_effective_worldbook_for_save` 步骤 3-5:
///   3. 收集 retired_entry_id 集合
///   4. 过滤被 retirement 覆盖的 script entries
///   5. 追加 addition overlay,按 priority DESC 排序,LIMIT 30
fn merge_worldbook_with_overlays(
    script_rows: Vec<(i64, String, String, Value, i32)>,
    overlay_rows: Vec<rpg_db::repos::save_worldbook_overlays::SaveWorldbookOverlay>,
) -> Vec<Value> {
    use std::collections::HashSet;
    let mut retired_ids: HashSet<i64> = HashSet::new();
    let mut additions: Vec<Value> = Vec::new();
    for ov in overlay_rows {
        match ov.kind.as_str() {
            "retirement" => {
                if let Some(rid) = ov.retired_entry_id {
                    retired_ids.insert(rid);
                }
            }
            "addition" => {
                additions.push(json!({
                    "id": Value::Null,
                    "overlay_id": ov.id,
                    "title": ov.title,
                    "content": ov.content,
                    "keys": ov.keys,
                    "priority": ov.priority,
                    "_source": "addition",
                }));
            }
            _ => {
                // CHECK 约束在 SQL 端,不会到这里;防御性 skip。
                tracing::warn!(
                    overlay_id = ov.id,
                    kind = %ov.kind,
                    "save_worldbook_overlays: 未知 kind, 跳过"
                );
            }
        }
    }

    let mut filtered: Vec<Value> = script_rows
        .into_iter()
        .filter(|(id, _, _, _, _)| !retired_ids.contains(id))
        .map(script_row_to_value)
        .collect();
    filtered.extend(additions);
    filtered.sort_by(|a, b| {
        let pa = a.get("priority").and_then(|v| v.as_i64()).unwrap_or(50);
        let pb = b.get("priority").and_then(|v| v.as_i64()).unwrap_or(50);
        pb.cmp(&pa)
    });
    filtered.truncate(30);
    filtered
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── WorldbookResult::to_context_text ────────────────────────────

    #[test]
    fn test_to_context_text_empty_result() {
        let r = WorldbookResult::default();
        let txt = r.to_context_text();
        // 空结果 → 空字符串
        assert!(txt.is_empty());
    }

    #[test]
    fn test_to_context_text_with_anchor() {
        // chapter_min / chapter_max 类型与 Python 对齐:i32(数字),不是 "10"/"20" 字符串。
        let r = WorldbookResult {
            confidence: 0.4,
            timeline_anchor: json!({
                "phase": "东征战役",
                "chapter_min": 10,
                "chapter_max": 20,
                "time_label": "战时"
            }),
            ..Default::default()
        };
        let txt = r.to_context_text();
        assert!(txt.contains("东征战役"));
        assert!(txt.contains("战时"));
        // 渲染时应展开成 "第10-20章"
        assert!(txt.contains("第10-20章"), "actual={txt}");
    }

    #[test]
    fn test_to_context_text_anchor_missing_chapters_fallback() {
        // chapter_min / chapter_max 缺失时回落到 "?"(对齐 Python `.get(...,'?')`)。
        let r = WorldbookResult {
            confidence: 0.2,
            timeline_anchor: json!({
                "phase": "序幕",
                "time_label": "黎明前",
            }),
            ..Default::default()
        };
        let txt = r.to_context_text();
        assert!(txt.contains("第?-?章"), "actual={txt}");
    }

    #[test]
    fn test_to_context_text_with_worldbook_entries() {
        let r = WorldbookResult {
            confidence: 0.1,
            worldbook_entries: vec![
                json!({"title": "王都", "content": "帝国的政治中心"}),
            ],
            ..Default::default()
        };
        let txt = r.to_context_text();
        assert!(txt.contains("王都"));
        assert!(txt.contains("帝国的政治中心"));
    }

    // ── WorldbookAgent::consult (db=None 路径) ─────────────────────

    #[tokio::test]
    async fn test_consult_no_db_returns_zero_confidence() {
        let agent = WorldbookAgent::new(); // db 未注入
        let out = agent
            .consult(ConsultInput {
                script_id: 1,
                query: "寻找信息".to_string(),
                ..Default::default()
            })
            .await
            .unwrap();
        assert_eq!(out.confidence, 0.0);
        assert!(out.sources.contains(&"db_not_injected".to_string()));
    }

    // ── load_effective_worldbook_for_save merge 逻辑 ────────────────
    //
    // 对齐 Python `tests/unit/test_worldbook_overlay.py::TestLoadEffectiveWorldbook`。
    // 这里测纯函数 `merge_worldbook_with_overlays`,无需 DB。

    use rpg_db::repos::save_worldbook_overlays::SaveWorldbookOverlay;

    fn make_overlay(
        id: i64,
        kind: &str,
        title: &str,
        content: &str,
        priority: i32,
        retired_entry_id: Option<i64>,
    ) -> SaveWorldbookOverlay {
        SaveWorldbookOverlay {
            id,
            save_id: 10,
            kind: kind.to_string(),
            title: title.to_string(),
            content: content.to_string(),
            keys: json!([]),
            priority,
            retired_entry_id,
            retired_reason: String::new(),
            introduced_turn: None,
            metadata: json!({}),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        }
    }

    #[test]
    fn test_merge_happy_path_no_overlays() {
        // 无 overlay 时,script 行原样输出,按 priority DESC,_source=script。
        let script_rows = vec![
            (1, "王都".to_string(), "帝国中心".to_string(), json!([]), 80),
            (2, "海港".to_string(), "贸易枢纽".to_string(), json!([]), 60),
        ];
        let out = merge_worldbook_with_overlays(script_rows, vec![]);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].get("title").unwrap().as_str(), Some("王都"));
        assert_eq!(out[0].get("priority").unwrap().as_i64(), Some(80));
        assert_eq!(out[0].get("_source").unwrap().as_str(), Some("script"));
        assert_eq!(out[1].get("title").unwrap().as_str(), Some("海港"));
    }

    #[test]
    fn test_merge_empty_inputs_returns_empty() {
        let out = merge_worldbook_with_overlays(vec![], vec![]);
        assert!(out.is_empty());
    }

    #[test]
    fn test_merge_retirement_excludes_script_entry() {
        // script 有 id=1,2,3;overlay retirement 屏蔽 id=2;结果只剩 1,3。
        let script_rows = vec![
            (1, "A".to_string(), "".to_string(), json!([]), 90),
            (2, "B".to_string(), "".to_string(), json!([]), 70),
            (3, "C".to_string(), "".to_string(), json!([]), 50),
        ];
        let overlays = vec![make_overlay(100, "retirement", "", "", 0, Some(2))];
        let out = merge_worldbook_with_overlays(script_rows, overlays);
        assert_eq!(out.len(), 2);
        // 排序后:A(90) > C(50)
        assert_eq!(out[0].get("title").unwrap().as_str(), Some("A"));
        assert_eq!(out[1].get("title").unwrap().as_str(), Some("C"));
        // B 不在
        for entry in &out {
            assert_ne!(entry.get("title").unwrap().as_str(), Some("B"));
        }
    }

    #[test]
    fn test_merge_addition_appended_and_sorted() {
        // addition overlay priority=95 应该排到 script 80 前面。
        let script_rows = vec![
            (1, "王都".to_string(), "".to_string(), json!([]), 80),
        ];
        let overlays = vec![make_overlay(
            200,
            "addition",
            "新地标",
            "玩家发现",
            95,
            None,
        )];
        let out = merge_worldbook_with_overlays(script_rows, overlays);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].get("title").unwrap().as_str(), Some("新地标"));
        assert_eq!(out[0].get("_source").unwrap().as_str(), Some("addition"));
        assert_eq!(out[0].get("overlay_id").unwrap().as_i64(), Some(200));
        // addition 的 id 字段为 null (无 worldbook_entries.id)
        assert!(out[0].get("id").unwrap().is_null());
        assert_eq!(out[1].get("title").unwrap().as_str(), Some("王都"));
        assert_eq!(out[1].get("_source").unwrap().as_str(), Some("script"));
    }

    #[test]
    fn test_merge_retirement_and_addition_combined() {
        // 三条 script,retire 其中一条,再加一条 addition,验证总数与排序。
        let script_rows = vec![
            (1, "A".to_string(), "".to_string(), json!([]), 80),
            (2, "B".to_string(), "".to_string(), json!([]), 60),
            (3, "C".to_string(), "".to_string(), json!([]), 40),
        ];
        let overlays = vec![
            make_overlay(50, "retirement", "", "", 0, Some(2)),
            make_overlay(51, "addition", "D", "新条目", 70, None),
        ];
        let out = merge_worldbook_with_overlays(script_rows, overlays);
        assert_eq!(out.len(), 3);
        // 排序: A(80) > D(70) > C(40),B 被 retire 掉
        let titles: Vec<&str> = out
            .iter()
            .map(|v| v.get("title").unwrap().as_str().unwrap())
            .collect();
        assert_eq!(titles, vec!["A", "D", "C"]);
    }

    #[test]
    fn test_merge_truncates_to_30() {
        // 35 条 script + 0 overlay → 截断到 30。
        let script_rows: Vec<_> = (0..35)
            .map(|i| (i as i64, format!("E{i}"), "".to_string(), json!([]), 50))
            .collect();
        let out = merge_worldbook_with_overlays(script_rows, vec![]);
        assert_eq!(out.len(), 30);
    }

    // ── ConsultInput default ────────────────────────────────────────

    #[test]
    fn test_consult_input_default() {
        let input = ConsultInput {
            script_id: 99,
            ..Default::default()
        };
        assert_eq!(input.script_id, 99);
        assert!(input.query.is_empty());
        assert!(input.current_phase.is_empty());
    }
}
