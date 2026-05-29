//! RuntimePhaseDigestProvider — task 107E (part 1/2)
//! Save 级 runtime phase digest provider — 把当前 save 已经摘要好的历史阶段塞进 GM context。
//! 对应 Python: rpg/context_providers/runtime_phase_digests.py
//!
//! 注:实际 DB 拉取需要 services.db_pool 接上 sqlx::PgPool。这里框架就绪,
//! 真正读 save_phase_digests 表的实现等 rpg-db Phase repo 完成。

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use serde_json::{json, Value};
use sqlx::Row;

/// 最大返回的 phase 数。
pub const MAX_PHASES: usize = 4;
/// 单 phase 渲染上限(字符)。
pub const PER_PHASE_BUDGET: usize = 450;

pub struct RuntimePhaseDigestProvider;

#[async_trait]
impl ContextProvider for RuntimePhaseDigestProvider {
    fn id(&self) -> &'static str {
        "runtime_phase_digests"
    }

    fn applies(&self, _state_data: &Value, _manifest: &Manifest, _demand: &Demand) -> bool {
        true
    }

    async fn collect(
        &self,
        _state_data: &Value,
        _manifest: &Manifest,
        _demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let save_id = match services.save_id {
            Some(id) => id,
            None => {
                return Ok(ContextContribution::skipped(
                    self.id(),
                    "no save_id in services",
                ));
            }
        };

        let phases = match load_recent_phases(services, save_id, MAX_PHASES).await {
            Ok(p) => p,
            Err(exc) => {
                return Ok(ContextContribution::skipped(
                    self.id(),
                    format!("db error: {}", exc),
                ));
            }
        };

        if phases.is_empty() {
            return Ok(ContextContribution::skipped(
                self.id(),
                "no phase digests yet",
            ));
        }
        // 过滤掉空摘要
        let phases: Vec<Value> = phases
            .into_iter()
            .filter(|p| {
                p.get("summary")
                    .and_then(|v| v.as_str())
                    .map(|s| !s.trim().is_empty())
                    .unwrap_or(false)
            })
            .collect();
        if phases.is_empty() {
            return Ok(ContextContribution::skipped(
                self.id(),
                "phases exist but all summaries empty",
            ));
        }

        let text = render_phases(&phases);
        let layer = Layer::new(
            "runtime_phase_digests",
            "已发生历史摘要(本存档)",
            text.clone(),
        )
        .with_priority(48);

        let facts: Vec<String> = phases
            .iter()
            .map(|p| {
                let idx = p.get("phase_index").cloned().unwrap_or(Value::Null);
                let ts = p.get("turn_start").cloned().unwrap_or(Value::Null);
                let te = p.get("turn_end").cloned().unwrap_or(Value::Null);
                let label = p
                    .get("phase_label")
                    .and_then(|v| v.as_str())
                    .unwrap_or("无标题");
                format!("phase {}: turn {}-{} ({})", idx, ts, te, label)
            })
            .collect();
        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "runtime_history".to_string(),
            priority: 48,
            facts,
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({ "phase_count": phases.len(), "save_id": save_id }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

/// 拉 save 最近 limit 个 phase。
///
/// 对应 Python `_load_recent_phases(save_id, limit)`:
///   SELECT phase_index, turn_start, turn_end, story_time_label, phase_label,
///          summary, key_events, key_npcs, key_locations, key_decisions,
///          emotion_arc, status
///   FROM save_phase_digests
///   WHERE save_id = $1
///   ORDER BY phase_index DESC
///   LIMIT $2
/// 然后 reverse() 时间正序。
///
/// 注:rpg-db 尚未提供 SavePhaseDigestRepo,直接用 sqlx::query 拉。
/// 如 db_pool 未注入或表不存在,返回空 Vec。
async fn load_recent_phases(
    services: &ProviderServices,
    save_id: i64,
    limit: usize,
) -> anyhow::Result<Vec<Value>> {
    let pool = match services.db_pool.as_ref() {
        Some(p) => p,
        None => return Ok(Vec::new()),
    };

    let rows = sqlx::query(
        "select phase_index, turn_start, turn_end, story_time_label, phase_label, \
                summary, key_events, key_npcs, key_locations, key_decisions, \
                emotion_arc, status \
         from save_phase_digests \
         where save_id = $1 \
         order by phase_index desc \
         limit $2",
    )
    .bind(save_id)
    .bind(limit as i64)
    .fetch_all(pool)
    .await?;

    let mut out: Vec<Value> = rows
        .into_iter()
        .map(|row| {
            let phase_index: i64 = row.try_get("phase_index").unwrap_or(0);
            let turn_start: i64 = row.try_get("turn_start").unwrap_or(0);
            let turn_end: i64 = row.try_get("turn_end").unwrap_or(0);
            let story_time_label: Option<String> = row.try_get("story_time_label").ok();
            let phase_label: Option<String> = row.try_get("phase_label").ok();
            let summary: Option<String> = row.try_get("summary").ok();
            let key_events: Value = row.try_get("key_events").unwrap_or(Value::Null);
            let key_npcs: Value = row.try_get("key_npcs").unwrap_or(Value::Null);
            let key_locations: Value = row.try_get("key_locations").unwrap_or(Value::Null);
            let key_decisions: Value = row.try_get("key_decisions").unwrap_or(Value::Null);
            let emotion_arc: Option<String> = row.try_get("emotion_arc").ok();
            let status: Option<String> = row.try_get("status").ok();
            json!({
                "phase_index": phase_index,
                "turn_start": turn_start,
                "turn_end": turn_end,
                "story_time_label": story_time_label.unwrap_or_default(),
                "phase_label": phase_label.unwrap_or_default(),
                "summary": summary.unwrap_or_default(),
                "key_events": key_events,
                "key_npcs": key_npcs,
                "key_locations": key_locations,
                "key_decisions": key_decisions,
                "emotion_arc": emotion_arc.unwrap_or_default(),
                "status": status.unwrap_or_default(),
            })
        })
        .collect();
    out.reverse(); // 时间正序: 早 phase 在前, 当前 open phase 在后
    Ok(out)
}

/// 把 phase digest 列表渲染成 GM 看的简短文本。对应 Python `_render_phases`。
pub fn render_phases(phases: &[Value]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let n = phases.len();
    for (i, p) in phases.iter().enumerate() {
        let last = i == n - 1;
        let status = p.get("status").and_then(|v| v.as_str()).unwrap_or("");
        let status_tag = if status == "open" { "进行中" } else { "已结束" };

        let phase_index = p.get("phase_index").cloned().unwrap_or(Value::Null);
        let turn_start = p.get("turn_start").cloned().unwrap_or(Value::Null);
        let turn_end = p.get("turn_end").cloned().unwrap_or(Value::Null);
        let mut head = format!(
            "# Phase {} (turn {}-{} · {})",
            phase_index, turn_start, turn_end, status_tag
        );
        let label = p
            .get("phase_label")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let story_time = p
            .get("story_time_label")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if !label.is_empty() || !story_time.is_empty() {
            head.push_str(&format!(" — {}", label));
            if !story_time.is_empty() {
                head.push_str(&format!(" · {}", story_time));
            }
        }

        let summary = p
            .get("summary")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let events = p
            .get("key_events")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let npcs = p
            .get("key_npcs")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let decisions = p
            .get("key_decisions")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let emotion = p
            .get("emotion_arc")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();

        let mut block: Vec<String> = vec![head, summary];
        if !events.is_empty() {
            let mut evt_lines: Vec<String> = Vec::new();
            for ev in events.iter().take(3) {
                if !ev.is_object() {
                    continue;
                }
                let t = ev.get("turn").cloned().unwrap_or(Value::from("?"));
                let s = ev
                    .get("summary")
                    .or_else(|| ev.get("desc"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if !s.is_empty() {
                    let s_short: String = s.chars().take(100).collect();
                    evt_lines.push(format!("  · t{}: {}", t, s_short));
                }
            }
            if !evt_lines.is_empty() {
                block.push("关键事件:".to_string());
                block.extend(evt_lines);
            }
        }
        if !npcs.is_empty() && last {
            let mut npc_lines: Vec<String> = Vec::new();
            for n in npcs.iter().take(4) {
                if !n.is_object() {
                    continue;
                }
                let nm = n
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                let role = n
                    .get("role")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if !nm.is_empty() {
                    let extra = if role.is_empty() {
                        String::new()
                    } else {
                        format!(" ({})", role)
                    };
                    npc_lines.push(format!("  · {}{}", nm, extra));
                }
            }
            if !npc_lines.is_empty() {
                block.push("活跃 NPC:".to_string());
                block.extend(npc_lines);
            }
        }
        if !decisions.is_empty() && last {
            let mut dec_lines: Vec<String> = Vec::new();
            for d in decisions.iter().take(2) {
                if let Some(ch) = d.get("choice").and_then(|v| v.as_str()) {
                    let ch = ch.trim();
                    if !ch.is_empty() {
                        let ch_short: String = ch.chars().take(60).collect();
                        dec_lines.push(format!("  · {}", ch_short));
                    }
                }
            }
            if !dec_lines.is_empty() {
                block.push("关键决定:".to_string());
                block.extend(dec_lines);
            }
        }
        if !emotion.is_empty() && last {
            let e_short: String = emotion.chars().take(80).collect();
            block.push(format!("情感弧线: {}", e_short));
        }

        let mut chunk = block.join("\n");
        let cnt = chunk.chars().count();
        if cnt > PER_PHASE_BUDGET {
            let cut: String = chunk.chars().take(PER_PHASE_BUDGET - 3).collect();
            chunk = format!("{}...", cut);
        }
        parts.push(chunk);
    }
    parts.join("\n\n")
}
