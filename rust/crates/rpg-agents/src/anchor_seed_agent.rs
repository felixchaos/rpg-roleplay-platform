//! anchor_seed_agent — 世界线收束机制 · 锚点 seed。
//!
//! 对应 Python: `rpg/agents/anchor_seed_agent.py`
//!
//! 把剧本 chapter_facts 里的关键事件拍平到 save_anchor_states,供 GM 在
//! 每一轮查询并主动触发。**纯确定性算法,不调 LLM**。
//!
//! ⚠️ DB 全部留 TODO,接 rpg-db 后填。

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
    pub chapter: Option<i32>,
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
    let rows = sqlx::query_as::<_, (i32, Option<Value>)>(
        r#"SELECT chapter, events
           FROM chapter_facts
           WHERE script_id = $1
           ORDER BY chapter ASC"#,
    )
    .bind(script_id)
    .fetch_all(pool)
    .await?;
    let mut out: Vec<AnchorRow> = Vec::new();
    for (chapter, events_opt) in rows {
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
            let must_preserve = derive_must_preserve(&summary, &participants);
            let anchor_key = format!("ch{chapter}_e{idx}");
            out.push(AnchorRow {
                anchor_key,
                chapter: Some(chapter),
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
    let metadata = json!({
        "participants": anchor.participants,
        "must_preserve": anchor.must_preserve,
    });
    if force {
        // force: 覆盖大多字段,但 status (occurred/variant) 保留。
        let res = sqlx::query(
            r#"INSERT INTO save_anchor_states
                 (save_id, anchor_key, chapter, summary, importance,
                  is_fatal, status, metadata, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), now())
               ON CONFLICT (save_id, anchor_key) DO UPDATE SET
                 chapter = EXCLUDED.chapter,
                 summary = EXCLUDED.summary,
                 importance = EXCLUDED.importance,
                 is_fatal = EXCLUDED.is_fatal,
                 metadata = EXCLUDED.metadata,
                 updated_at = now()
               WHERE save_anchor_states.status NOT IN ('occurred', 'variant')"#,
        )
        .bind(save_id)
        .bind(&anchor.anchor_key)
        .bind(anchor.chapter)
        .bind(&anchor.summary)
        .bind(anchor.importance)
        .bind(anchor.is_fatal)
        .bind(&anchor.status)
        .bind(&metadata)
        .execute(pool)
        .await?;
        Ok(res.rows_affected() > 0)
    } else {
        let res = sqlx::query(
            r#"INSERT INTO save_anchor_states
                 (save_id, anchor_key, chapter, summary, importance,
                  is_fatal, status, metadata, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), now())
               ON CONFLICT (save_id, anchor_key) DO NOTHING"#,
        )
        .bind(save_id)
        .bind(&anchor.anchor_key)
        .bind(anchor.chapter)
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
