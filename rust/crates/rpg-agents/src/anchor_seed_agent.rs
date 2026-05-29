//! anchor_seed_agent — 世界线收束机制 · 锚点 seed。
//!
//! 对应 Python: `rpg/agents/anchor_seed_agent.py`
//!
//! 把剧本 chapter_facts 里的关键事件拍平到 save_anchor_states,供 GM 在
//! 每一轮查询并主动触发。**纯确定性算法,不调 LLM**。

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::PgPool;
use std::sync::Arc;

use crate::common::AgentResult;

/// "死神来了"模式触发词:包含这些词的事件被标 is_fatal=true,玩家任何
/// 阻止尝试都会以替代方式发生。
const FATAL_KEYWORDS: &[&str] = &[
    "死亡", "战死", "牺牲", "阵亡", "身亡", "暴毙", "灭门", "灭族",
    "失守", "陷落", "覆灭", "毁灭", "炸毁",
    "失踪", "下落不明",
    "暴露", "败露",
    "投降", "归降",
    "判决", "处决", "枪决", "处刑",
];

/// 高重要性额外加权词。
const CRITICAL_KEYWORDS: &[&str] = &[
    "宣战", "停战", "和约", "登基", "继位", "退位",
    "确认", "公开", "公布",
    "出嫁", "成婚", "联姻",
    "诞生", "出生",
    "驾崩", "薨", "崩",
];

/// importance 映射:chapter_facts.events[].importance → 0-100
fn importance_score(level: &str) -> i32 {
    match level {
        "high" => 70,
        "medium" => 50,
        "low" => 30,
        _ => 30,
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SeedAnchorsInput {
    pub save_id: i64,
    pub force: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SeedAnchorsOutput {
    pub ok: bool,
    pub seeded: u32,
    pub fatal_count: u32,
    pub script_id: Option<i64>,
    pub elapsed_ms: u64,
    pub reason: Option<String>,
}

pub struct AnchorSeedAgent {
    db: Option<Arc<PgPool>>,
}

impl AnchorSeedAgent {
    pub fn new() -> Self {
        Self { db: None }
    }

    pub fn with_db(mut self, pool: Arc<PgPool>) -> Self {
        self.db = Some(pool);
        self
    }

    /// 从该 save 关联剧本的 chapter_facts 抽锚点,写入 save_anchor_states。
    /// 幂等:已存在的 anchor_key 默认不覆盖。
    pub async fn seed_anchors_for_save(
        &self,
        input: SeedAnchorsInput,
    ) -> AgentResult<SeedAnchorsOutput> {
        let t0 = std::time::Instant::now();
        let Some(pool) = self.db.as_ref() else {
            return Ok(SeedAnchorsOutput {
                ok: false,
                reason: Some("db_not_injected".into()),
                elapsed_ms: t0.elapsed().as_millis() as u64,
                ..Default::default()
            });
        };

        // (1) 拿 save → script_id。
        let script_id = match load_script_id_for_save(pool.as_ref(), input.save_id).await {
            Ok(Some(s)) => s,
            Ok(None) => {
                return Ok(SeedAnchorsOutput {
                    ok: false,
                    reason: Some("save row not found".into()),
                    elapsed_ms: t0.elapsed().as_millis() as u64,
                    ..Default::default()
                });
            }
            Err(e) => {
                return Ok(SeedAnchorsOutput {
                    ok: false,
                    reason: Some(format!("load_script_id failed: {e}")),
                    elapsed_ms: t0.elapsed().as_millis() as u64,
                    ..Default::default()
                });
            }
        };

        // (2) 遍 chapter_facts.events,生成 anchors。
        let anchors = match build_anchors_from_chapter_facts(pool.as_ref(), script_id).await {
            Ok(a) => a,
            Err(e) => {
                return Ok(SeedAnchorsOutput {
                    ok: false,
                    script_id: Some(script_id),
                    reason: Some(format!("build_anchors failed: {e}")),
                    elapsed_ms: t0.elapsed().as_millis() as u64,
                    ..Default::default()
                });
            }
        };

        // (3) upsert save_anchor_states。
        let mut seeded = 0u32;
        let mut fatal = 0u32;
        for a in &anchors {
            if a.is_fatal {
                fatal += 1;
            }
            // force=false → ON CONFLICT DO NOTHING;force=true → DO UPDATE 大多字段。
            let res = upsert_anchor(pool.as_ref(), input.save_id, a, input.force).await;
            match res {
                Ok(true) => seeded += 1,
                Ok(false) => {}
                Err(e) => {
                    tracing::warn!("[anchor_seed] upsert {} 失败: {e}", a.anchor_key);
                }
            }
        }

        Ok(SeedAnchorsOutput {
            ok: true,
            seeded,
            fatal_count: fatal,
            script_id: Some(script_id),
            elapsed_ms: t0.elapsed().as_millis() as u64,
            reason: None,
        })
    }

    /// 强制重 seed。keep_satisfied=true 时,已经 occurred/variant 的锚点保留。
    pub async fn reseed_anchors_for_save(
        &self,
        save_id: i64,
        keep_satisfied: bool,
    ) -> AgentResult<SeedAnchorsOutput> {
        let t0 = std::time::Instant::now();
        let Some(pool) = self.db.as_ref() else {
            return Ok(SeedAnchorsOutput {
                ok: false,
                reason: Some("db_not_injected".into()),
                elapsed_ms: t0.elapsed().as_millis() as u64,
                ..Default::default()
            });
        };
        // 删未满足锚点,然后重新 seed。
        let del = if keep_satisfied {
            sqlx::query(
                r#"DELETE FROM save_anchor_states
                   WHERE save_id = $1
                     AND COALESCE(status, 'pending') NOT IN ('occurred', 'variant')"#,
            )
            .bind(save_id)
            .execute(pool.as_ref())
            .await
        } else {
            sqlx::query("DELETE FROM save_anchor_states WHERE save_id = $1")
                .bind(save_id)
                .execute(pool.as_ref())
                .await
        };
        if let Err(e) = del {
            return Ok(SeedAnchorsOutput {
                ok: false,
                reason: Some(format!("delete 旧 anchors 失败: {e}")),
                elapsed_ms: t0.elapsed().as_millis() as u64,
                ..Default::default()
            });
        }
        self.seed_anchors_for_save(SeedAnchorsInput {
            save_id,
            force: true,
        })
        .await
    }

    /// 直接 upsert 一个 anchor(用于上层手动 update_anchor)。
    pub async fn update_anchor(
        &self,
        save_id: i64,
        anchor: AnchorRow,
    ) -> AgentResult<bool> {
        let Some(pool) = self.db.as_ref() else {
            return Ok(false);
        };
        Ok(upsert_anchor(pool.as_ref(), save_id, &anchor, true)
            .await
            .unwrap_or(false))
    }
}

impl Default for AnchorSeedAgent {
    fn default() -> Self {
        Self::new()
    }
}

/// 一条 anchor 行(seed 阶段构造)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnchorRow {
    pub anchor_key: String,
    pub source_chapter: Option<i32>,
    pub source_event_index: i32,
    /// Wave 5-B / P0-2: phase_digests.phase_index (script 级)。
    /// Python schema 在 migration v15 里就声明了这列,但 Python 侧 seed 时漏写,
    /// 导致 chapter-sourced 锚点全部 NULL → drift_by_phase 无法按 phase 聚合。
    /// Rust 这里在 chapter_facts.story_phase 出现顺序基础上,
    /// 按"首次出现的 chapter asc"计算 phase_index,与 Python phase_digests 编号策略一致。
    pub source_phase_index: Option<i32>,
    pub script_id: i64,
    pub summary: String,
    pub participants: Vec<String>,
    pub importance: i32,
    pub is_fatal: bool,
    pub must_preserve: Vec<String>,
    /// status 默认 "pending";已存在的 occurred/variant 行不覆盖。
    pub status: String,
}

async fn load_script_id_for_save(
    pool: &PgPool,
    save_id: i64,
) -> Result<Option<i64>, sqlx::Error> {
    let row: Option<(Option<i64>,)> =
        sqlx::query_as("SELECT script_id FROM game_saves WHERE id = $1")
            .bind(save_id)
            .fetch_optional(pool)
            .await?;
    Ok(row.and_then(|(sid,)| sid))
}

async fn build_anchors_from_chapter_facts(
    pool: &PgPool,
    script_id: i64,
) -> Result<Vec<AnchorRow>, sqlx::Error> {
    // chapter_facts.events 是 jsonb 数组;每条 event 形如:
    //   {"event":"...", "importance":"high", "participants":["A","B"]}
    // Python: anchor_key = f"chapter:{chapter}:event:{idx}"
    //
    // Wave 5-B / P0-2: 同时把 story_phase 拉出来,在 ORDER BY chapter ASC 顺序里
    // 按 phase 首次出现给一个 0-based phase_index。空 story_phase 不参与编号(NULL)。
    let rows = sqlx::query_as::<_, (i32, Option<Value>, Option<String>)>(
        r#"SELECT chapter, events, story_phase
           FROM chapter_facts
           WHERE script_id = $1
           ORDER BY chapter ASC"#,
    )
    .bind(script_id)
    .fetch_all(pool)
    .await?;
    // phase_label -> phase_index(首次出现顺序)
    let mut phase_index_map: std::collections::HashMap<String, i32> =
        std::collections::HashMap::new();
    let mut next_phase_idx: i32 = 0;
    for (_chapter, _events, story_phase) in &rows {
        if let Some(label) = story_phase.as_ref().and_then(|s| {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        }) {
            phase_index_map.entry(label).or_insert_with(|| {
                let v = next_phase_idx;
                next_phase_idx += 1;
                v
            });
        }
    }
    let mut out: Vec<AnchorRow> = Vec::new();
    for (chapter, events_opt, story_phase) in rows {
        let source_phase_index: Option<i32> = story_phase
            .as_ref()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .and_then(|label| phase_index_map.get(&label).copied());
        let arr = events_opt
            .and_then(|v| v.as_array().cloned())
            .unwrap_or_default();
        for (idx, ev) in arr.iter().enumerate() {
            let summary = ev
                .get("event")
                .and_then(|v| v.as_str())
                .or_else(|| ev.get("summary").and_then(|v| v.as_str()))
                .unwrap_or("")
                .to_string();
            if summary.trim().is_empty() {
                continue;
            }
            // 过滤太短
            if summary.chars().count() < 6 {
                continue;
            }
            let importance_level = ev
                .get("importance")
                .and_then(|v| v.as_str())
                .unwrap_or("low");
            let participants: Vec<String> = ev
                .get("participants")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .filter_map(|x| x.as_str().map(String::from))
                .collect();
            let is_fatal = classify_event_fatal(&summary);
            let importance = compute_importance(importance_level, &summary);
            // 过滤太低
            if importance < 40 {
                continue;
            }
            let must_preserve = derive_must_preserve(&summary, &participants);
            // Python: anchor_key = f"chapter:{chapter}:event:{idx}"
            let anchor_key = format!("chapter:{chapter}:event:{idx}");
            out.push(AnchorRow {
                anchor_key,
                source_chapter: Some(chapter),
                source_event_index: idx as i32,
                source_phase_index,
                script_id,
                summary,
                participants,
                importance,
                is_fatal,
                must_preserve,
                status: "pending".into(),
            });
        }
    }
    Ok(out)
}

async fn upsert_anchor(
    pool: &PgPool,
    save_id: i64,
    anchor: &AnchorRow,
    force: bool,
) -> Result<bool, sqlx::Error> {
    // Python: metadata = Jsonb({"participants":..., "locations":..., "concepts":..., "seed_source":"deterministic"})
    let metadata = json!({
        "participants": anchor.participants,
        "must_preserve": anchor.must_preserve,
        "seed_source": "deterministic",
    });
    if force {
        // force: 覆盖大多字段,但 status (occurred/variant) 保留。
        // Python: on conflict (save_id, anchor_key) do nothing (force 时先 delete 再 insert;
        //         Rust 用 DO UPDATE WHERE NOT IN ('occurred','variant') 等价)
        // Wave 5-B / P0-2: 新增 source_phase_index 列,修 chapter-sourced 锚点 NULL bug。
        let res = sqlx::query(
            r#"INSERT INTO save_anchor_states
                 (save_id, anchor_key, source_kind, source_chapter, source_event_index,
                  source_phase_index, script_id, summary, importance, is_fatal,
                  status, metadata, created_at, updated_at)
               VALUES ($1, $2, 'chapter', $3, $4, $5, $6, $7, $8, $9, $10, $11, now(), now())
               ON CONFLICT (save_id, anchor_key) DO UPDATE SET
                 source_chapter = EXCLUDED.source_chapter,
                 source_event_index = EXCLUDED.source_event_index,
                 source_phase_index = EXCLUDED.source_phase_index,
                 summary = EXCLUDED.summary,
                 importance = EXCLUDED.importance,
                 is_fatal = EXCLUDED.is_fatal,
                 metadata = EXCLUDED.metadata,
                 updated_at = now()
               WHERE save_anchor_states.status NOT IN ('occurred', 'variant')"#,
        )
        .bind(save_id)
        .bind(&anchor.anchor_key)
        .bind(anchor.source_chapter)
        .bind(anchor.source_event_index)
        .bind(anchor.source_phase_index)
        .bind(anchor.script_id)
        .bind(&anchor.summary)
        .bind(anchor.importance)
        .bind(anchor.is_fatal)
        .bind(&anchor.status)
        .bind(&metadata)
        .execute(pool)
        .await?;
        Ok(res.rows_affected() > 0)
    } else {
        // Python: on conflict (save_id, anchor_key) do nothing
        let res = sqlx::query(
            r#"INSERT INTO save_anchor_states
                 (save_id, anchor_key, source_kind, source_chapter, source_event_index,
                  source_phase_index, script_id, summary, importance, is_fatal,
                  status, metadata, created_at, updated_at)
               VALUES ($1, $2, 'chapter', $3, $4, $5, $6, $7, $8, $9, $10, $11, now(), now())
               ON CONFLICT (save_id, anchor_key) DO NOTHING"#,
        )
        .bind(save_id)
        .bind(&anchor.anchor_key)
        .bind(anchor.source_chapter)
        .bind(anchor.source_event_index)
        .bind(anchor.source_phase_index)
        .bind(anchor.script_id)
        .bind(&anchor.summary)
        .bind(anchor.importance)
        .bind(anchor.is_fatal)
        .bind(&anchor.status)
        .bind(&metadata)
        .execute(pool)
        .await?;
        Ok(res.rows_affected() > 0)
    }
}

/// 启发式判断:这个事件是否"死神来了"模式(重大死亡 / 失踪 / 战败)。
pub fn classify_event_fatal(event_text: &str) -> bool {
    FATAL_KEYWORDS.iter().any(|kw| event_text.contains(kw))
}

/// 计算重要性分数。
pub fn compute_importance(importance_level: &str, summary: &str) -> i32 {
    let mut score = importance_score(importance_level);
    if CRITICAL_KEYWORDS.iter().any(|kw| summary.contains(kw)) {
        score = (score + 20).min(100);
    }
    if FATAL_KEYWORDS.iter().any(|kw| summary.contains(kw)) {
        score = (score + 15).min(100);
    }
    score
}

/// 从 summary + participants 抽出 must_preserve 维度。
/// 例如:summary 含"死亡"则 must_preserve 包括 "outcome";
///       participants 非空则 must_preserve 包括 "characters"。
pub fn derive_must_preserve(summary: &str, participants: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    if FATAL_KEYWORDS.iter().any(|kw| summary.contains(kw)) {
        out.push("outcome".into());
    }
    if CRITICAL_KEYWORDS.iter().any(|kw| summary.contains(kw)) {
        out.push("event_type".into());
    }
    if !participants.is_empty() {
        out.push("characters".into());
    }
    out
}

// ── 查询辅助 (给 GM 工具用) ────────────────────────────────────────────

/// 查待发生的锚点。phase_label 给定时按 phase 过滤;chapter window 给定时按章节范围过滤。
/// 按 importance desc + source_chapter asc 排序。
/// 对应 Python: list_pending_for_phase(save_id, phase_label, *, limit, chapter_min, chapter_max)
pub async fn list_pending_for_phase(
    pool: &PgPool,
    save_id: i64,
    phase_label: Option<&str>,
    limit: i64,
    chapter_min: Option<i32>,
    chapter_max: Option<i32>,
) -> Result<Vec<Value>, sqlx::Error> {
    // Python 用动态 WHERE 拼接;Rust 用 NULL-safe 参数等价。
    // 若参数为 None,条件 `$n::int IS NULL OR col >= $n` 等价于无过滤。
    // Wave 5-B / P0-2: 同时回 source_phase_index,与 Python 行为对齐。
    let rows = sqlx::query_as::<
        _,
        (
            i64,
            String,
            Option<i32>,
            Option<i32>,
            Option<String>,
            String,
            Option<i32>,
            i32,
            bool,
            Value,
        ),
    >(
        r#"SELECT id, anchor_key, source_chapter, source_phase_index, phase_label,
                  summary, source_event_index, importance, is_fatal, metadata
           FROM save_anchor_states
           WHERE save_id = $1
             AND status = 'pending'
             AND ($2::text IS NULL OR phase_label = $2)
             AND ($3::int  IS NULL OR source_chapter >= $3)
             AND ($4::int  IS NULL OR source_chapter <= $4)
           ORDER BY importance DESC, source_chapter ASC
           LIMIT $5"#,
    )
    .bind(save_id)
    .bind(phase_label)
    .bind(chapter_min)
    .bind(chapter_max)
    .bind(limit.max(1))
    .fetch_all(pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(id, anchor_key, source_chapter, source_phase_index, phase_lbl, summary, event_idx, importance, is_fatal, metadata)| {
            json!({
                "id": id,
                "anchor_key": anchor_key,
                "chapter": source_chapter,
                "source_phase_index": source_phase_index,
                "phase_label": phase_lbl.unwrap_or_default(),
                "summary": summary,
                "source_event_index": event_idx,
                "importance": importance,
                "is_fatal": is_fatal,
                "metadata": metadata,
            })
        })
        .collect())
}

/// 按 phase_label 聚合 drift score,供 UI 时间线展示。
/// 返回 [{phase_label, chapter_min, total, pending, occurred, variant, superseded,
///         fatal_pending, avg_drift, convergence_pressure}, ...]
/// 按 chapter_min asc 排序。
/// 对应 Python: drift_by_phase(save_id)
pub async fn drift_by_phase(pool: &PgPool, save_id: i64) -> Result<Vec<Value>, sqlx::Error> {
    // Wave 5-B / P0-2: GROUP BY 同时按 phase_label 和 source_phase_index,
    // 这样既能按 script-级 phase_index 对齐到 phase_digests,也兼容老 NULL 行(单独成一组)。
    let rows = sqlx::query_as::<
        _,
        (
            Option<String>,
            Option<i32>,
            Option<i32>,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            f64,
        ),
    >(
        r#"SELECT
             phase_label,
             source_phase_index,
             min(source_chapter) as ch_min,
             count(*) as total,
             sum(case when status = 'pending'    then 1 else 0 end) as pending,
             sum(case when status = 'occurred'   then 1 else 0 end) as occurred,
             sum(case when status = 'variant'    then 1 else 0 end) as variant,
             sum(case when status = 'superseded' then 1 else 0 end) as superseded,
             sum(case when status = 'pending' and is_fatal then 1 else 0 end) as fatal_pending,
             coalesce(avg(drift_score), 0)::float8 as avg_drift
           FROM save_anchor_states
           WHERE save_id = $1
           GROUP BY phase_label, source_phase_index
           ORDER BY min(source_chapter) asc"#,
    )
    .bind(save_id)
    .fetch_all(pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(phase_lbl, phase_idx, ch_min, total, pending, occurred, variant, superseded, fatal_pending, avg_drift)| {
            // convergence_pressure 启发式: Python 算法逐行翻译
            let pressure = if total > 0 {
                let p = (pending as f64 / total as f64) * 0.4
                    + avg_drift * 0.3
                    + (fatal_pending as f64 / 3.0).min(1.0) * 0.3;
                (p * 1000.0).round() / 1000.0_f64.min(1.0)
            } else {
                0.0
            };
            json!({
                "phase_label": phase_lbl.unwrap_or_default(),
                "source_phase_index": phase_idx,
                "chapter_min": ch_min,
                "total": total,
                "pending": pending,
                "occurred": occurred,
                "variant": variant,
                "superseded": superseded,
                "fatal_pending": fatal_pending,
                "avg_drift": avg_drift,
                "convergence_pressure": pressure,
            })
        })
        .collect())
}

/// 整体状态: pending/occurred/variant/superseded 各多少 + drift 平均。
/// 对应 Python: summarize_save_anchor_state(save_id)
pub async fn summarize_save_anchor_state(
    pool: &PgPool,
    save_id: i64,
) -> Result<Value, sqlx::Error> {
    let rows = sqlx::query_as::<_, (Option<String>, i64, f64, i64)>(
        r#"SELECT status, count(*) as n,
                  coalesce(avg(drift_score), 0)::float8 as avg_drift,
                  sum(case when is_fatal then 1 else 0 end) as fatal_n
           FROM save_anchor_states
           WHERE save_id = $1
           GROUP BY status"#,
    )
    .bind(save_id)
    .fetch_all(pool)
    .await?;

    let mut pending: i64 = 0;
    let mut occurred: i64 = 0;
    let mut variant: i64 = 0;
    let mut superseded: i64 = 0;
    let mut fatal_pending: i64 = 0;
    let mut total: i64 = 0;
    let mut weighted_drift: f64 = 0.0;

    for (status_opt, n, avg_drift, fatal_n) in rows {
        let status = status_opt.unwrap_or_default();
        total += n;
        weighted_drift += avg_drift * n as f64;
        match status.as_str() {
            "pending" => {
                pending = n;
                fatal_pending = fatal_n;
            }
            "occurred" => occurred = n,
            "variant" => variant = n,
            "superseded" => superseded = n,
            _ => {}
        }
    }
    let avg_drift = if total > 0 {
        (weighted_drift / total as f64 * 1000.0).round() / 1000.0
    } else {
        0.0
    };

    Ok(json!({
        "save_id": save_id,
        "pending": pending,
        "occurred": occurred,
        "variant": variant,
        "superseded": superseded,
        "fatal_pending": fatal_pending,
        "avg_drift": avg_drift,
        "total": total,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── classify_event_fatal ─────────────────────────────────────

    #[test]
    fn test_classify_fatal_empty() {
        assert!(!classify_event_fatal(""));
    }

    #[test]
    fn test_classify_fatal_match() {
        assert!(classify_event_fatal("王爷战死沙场"));
        assert!(classify_event_fatal("灭门惨案发生"));
        assert!(classify_event_fatal("全军覆灭消息传来"));
    }

    #[test]
    fn test_classify_fatal_no_match() {
        assert!(!classify_event_fatal("公主出嫁庆典"));
        assert!(!classify_event_fatal("秋日宴席"));
    }

    // ── compute_importance ────────────────────────────────────────

    #[test]
    fn test_importance_base_levels() {
        assert_eq!(compute_importance("high", "普通事件"), 70);
        assert_eq!(compute_importance("medium", "普通事件"), 50);
        assert_eq!(compute_importance("low", "普通事件"), 30);
        assert_eq!(compute_importance("unknown", "普通事件"), 30);
    }

    #[test]
    fn test_importance_fatal_bonus() {
        // fatal keyword 命中 → +15,不超过 100
        let score = compute_importance("high", "皇帝驾崩战死");
        assert!(score > 70);
        assert!(score <= 100);
    }

    #[test]
    fn test_importance_critical_bonus() {
        // critical keyword 命中 → +20,不超过 100
        let score = compute_importance("medium", "太子登基继位");
        assert!(score > 50);
        assert!(score <= 100);
    }

    // ── derive_must_preserve ──────────────────────────────────────

    #[test]
    fn test_must_preserve_empty() {
        let items = derive_must_preserve("普通叙事", &[]);
        assert!(items.is_empty());
    }

    #[test]
    fn test_must_preserve_with_fatal() {
        let items = derive_must_preserve("角色死亡", &[]);
        assert!(items.contains(&"outcome".to_string()));
    }

    #[test]
    fn test_must_preserve_with_participants() {
        let p = vec!["李将军".to_string()];
        let items = derive_must_preserve("普通事件", &p);
        assert!(items.contains(&"characters".to_string()));
    }

    // ── AnchorSeedAgent::seed_anchors_for_save (db=None 路径) ────

    #[tokio::test]
    async fn test_seed_no_db_returns_ok_false() {
        let agent = AnchorSeedAgent::new(); // db 未注入
        let out = agent
            .seed_anchors_for_save(SeedAnchorsInput {
                save_id: 1,
                force: false,
            })
            .await
            .unwrap();
        assert!(!out.ok);
        assert_eq!(out.reason.as_deref(), Some("db_not_injected"));
    }

    #[tokio::test]
    async fn test_reseed_no_db_returns_ok_false() {
        let agent = AnchorSeedAgent::new();
        let out = agent.reseed_anchors_for_save(1, true).await.unwrap();
        assert!(!out.ok);
    }
}
