//! ScriptPhaseAnticipationProvider — task 107E (part 2/2)
//! 注入剧本下一阶段(s) 预期 — GM 思考未来的参考。
//! 对应 Python: rpg/context_providers/script_phase_anticipation.py

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use rpg_schemas::GameStateData;
use serde_json::{json, Value};
use sqlx::Row;

pub const MAX_LOOKAHEAD_PHASES: usize = 2;
pub const PER_PHASE_BUDGET: usize = 400;

pub struct ScriptPhaseAnticipationProvider;

#[async_trait]
impl ContextProvider for ScriptPhaseAnticipationProvider {
    fn id(&self) -> &'static str {
        "script_phase_anticipation"
    }

    fn applies(&self, _state_data: &GameStateData, _manifest: &Manifest, _demand: &Demand) -> bool {
        true
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        _demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let script_id = match services.script_id {
            Some(id) => id,
            None => return Ok(ContextContribution::skipped(self.id(), "no script_id")),
        };

        let current_phase = state_data.world.timeline.current_phase.trim().to_string();

        let phases = match load_script_lookahead(
            services,
            script_id,
            &current_phase,
            MAX_LOOKAHEAD_PHASES,
        )
        .await
        {
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
                "no upcoming script phases",
            ));
        }

        let text = render_lookahead(&phases, &current_phase);
        let layer = Layer::new(
            "script_phase_anticipation",
            "剧本预期接下来 (仅参考)",
            text.clone(),
        )
        .with_priority(42);
        let facts: Vec<String> = phases
            .iter()
            .map(|p| {
                let label = p.get("phase_label").and_then(|v| v.as_str()).unwrap_or("");
                format!("剧本下一段: {}", label)
            })
            .collect();
        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "script_future".to_string(),
            priority: 42,
            facts,
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({ "current_phase": current_phase, "lookahead": phases.len() }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

/// 拉 script 在 current_phase 之后的 limit 个 phase digest。
///
/// Python 侧两步 SQL:
///   1. `SELECT chapter_max FROM phase_digests WHERE script_id=? AND phase_label=?`
///      拿到 chapter_threshold(找不到则 0)
///   2. `SELECT phase_label, chapter_min, chapter_max, summary,
///              key_events, key_locations, key_characters,
///              story_time_label_start, story_time_label_end
///       FROM phase_digests WHERE script_id=? AND chapter_min > ?
///       ORDER BY chapter_min ASC LIMIT ?`
///
/// 注:rpg-db 尚未提供 PhaseDigestRepo,直接用 sqlx::query。
/// db_pool 未注入或表缺失 → 返回空 Vec。
async fn load_script_lookahead(
    services: &ProviderServices,
    script_id: i64,
    current_phase: &str,
    limit: usize,
) -> anyhow::Result<Vec<Value>> {
    let pool = match services.db_pool.as_ref() {
        Some(p) => p,
        None => return Ok(Vec::new()),
    };

    // chapter_min/chapter_max 在 phase_digests 表中是 integer(i32)。
    let mut chapter_threshold: i32 = 0;
    if !current_phase.is_empty() {
        let row = sqlx::query(
            "select chapter_max from phase_digests \
             where script_id = $1 and phase_label = $2",
        )
        .bind(script_id)
        .bind(current_phase)
        .fetch_optional(pool)
        .await?;
        if let Some(row) = row {
            chapter_threshold = row.try_get::<i32, _>("chapter_max").unwrap_or(0);
        }
    }

    let rows = sqlx::query(
        "select phase_label, chapter_min, chapter_max, summary, \
                key_events, key_locations, key_characters, \
                story_time_label_start, story_time_label_end \
         from phase_digests \
         where script_id = $1 and chapter_min > $2 \
         order by chapter_min asc \
         limit $3",
    )
    .bind(script_id)
    .bind(chapter_threshold)
    .bind(limit as i64)
    .fetch_all(pool)
    .await?;

    let out: Vec<Value> = rows
        .into_iter()
        .map(|row| {
            let phase_label: Option<String> = row.try_get("phase_label").ok();
            let chapter_min: i32 = row.try_get("chapter_min").unwrap_or(0);
            let chapter_max: i32 = row.try_get("chapter_max").unwrap_or(0);
            let summary: Option<String> = row.try_get("summary").ok();
            let key_events: Value = row.try_get("key_events").unwrap_or(Value::Null);
            let key_locations: Value = row.try_get("key_locations").unwrap_or(Value::Null);
            let key_characters: Value = row.try_get("key_characters").unwrap_or(Value::Null);
            let story_time_label_start: Option<String> =
                row.try_get("story_time_label_start").ok();
            let story_time_label_end: Option<String> = row.try_get("story_time_label_end").ok();
            json!({
                "phase_label": phase_label.unwrap_or_default(),
                "chapter_min": chapter_min,
                "chapter_max": chapter_max,
                "summary": summary.unwrap_or_default(),
                "key_events": key_events,
                "key_locations": key_locations,
                "key_characters": key_characters,
                "story_time_label_start": story_time_label_start.unwrap_or_default(),
                "story_time_label_end": story_time_label_end.unwrap_or_default(),
            })
        })
        .collect();
    Ok(out)
}

/// 渲染 lookahead 段。对应 Python `_render_lookahead`。
pub fn render_lookahead(phases: &[Value], current_phase: &str) -> String {
    let mut parts: Vec<String> = vec!["(以下是剧本作者预期接下来的走向 — 仅供参考方向, 玩家可能完全偏离, 不要强制对齐)".to_string()];
    if !current_phase.is_empty() {
        parts.push(format!("当前所在: {}", current_phase));
    }
    for p in phases {
        let label_raw = p
            .get("phase_label")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let label = if label_raw.is_empty() {
            "无标题段".to_string()
        } else {
            label_raw
        };
        let cmin = p.get("chapter_min").cloned().unwrap_or(Value::Null);
        let cmax = p.get("chapter_max").cloned().unwrap_or(Value::Null);
        let ch_range = format!("第 {}-{} 章", cmin, cmax);
        let s = p
            .get("story_time_label_start")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let e = p
            .get("story_time_label_end")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let story_time = if !s.is_empty() || !e.is_empty() {
            let tail = if !e.is_empty() && e != s {
                format!(" → {}", e)
            } else {
                String::new()
            };
            format!(" · {}{}", s, tail)
        } else {
            String::new()
        };
        let head = format!("# 剧本下一段: {} ({}{})", label, ch_range, story_time);
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
        let locs = p
            .get("key_locations")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        let mut block: Vec<String> = vec![head, summary];
        if !events.is_empty() {
            let mut evt_lines: Vec<String> = Vec::new();
            for ev in events.iter().take(3) {
                let s_ev = if ev.is_object() {
                    ev.get("summary")
                        .or_else(|| ev.get("desc"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .trim()
                        .to_string()
                } else if let Some(s) = ev.as_str() {
                    s.trim().to_string()
                } else {
                    String::new()
                };
                if !s_ev.is_empty() {
                    let short: String = s_ev.chars().take(100).collect();
                    evt_lines.push(format!("  · {}", short));
                }
            }
            if !evt_lines.is_empty() {
                block.push("预期事件:".to_string());
                block.extend(evt_lines);
            }
        }
        if !locs.is_empty() {
            let names: Vec<String> = locs
                .iter()
                .take(5)
                .filter_map(|v| {
                    let s = match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    };
                    if s.is_empty() {
                        None
                    } else {
                        Some(s)
                    }
                })
                .collect();
            if !names.is_empty() {
                block.push(format!("预期场景: {}", names.join("、")));
            }
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── Wave 9-A: render_lookahead 单测 ──────────────────────────────

    #[test]
    fn render_lookahead_empty_phases_returns_header_only() {
        let text = render_lookahead(&[], "当前阶段");
        assert!(text.contains("仅供参考"), "应含参考免责声明: {text}");
        assert!(text.contains("当前所在"), "应含当前所在: {text}");
        assert!(!text.contains("剧本下一段"), "无 phase 时不应有段落标题: {text}");
    }

    #[test]
    fn render_lookahead_single_phase_shows_label_and_chapter_range() {
        let phases = vec![json!({
            "phase_label": "序章:黑暗降临",
            "chapter_min": 1,
            "chapter_max": 10,
            "summary": "世界开始崩裂",
            "key_events": [],
            "key_locations": [],
            "story_time_label_start": "初春",
            "story_time_label_end": "晚秋"
        })];
        let text = render_lookahead(&phases, "");
        assert!(text.contains("序章:黑暗降临"), "应含 phase 标签: {text}");
        assert!(text.contains("1"), "应含 chapter_min: {text}");
        assert!(text.contains("10"), "应含 chapter_max: {text}");
        assert!(text.contains("世界开始崩裂"), "应含 summary: {text}");
        assert!(text.contains("初春"), "应含时间标签: {text}");
    }

    #[test]
    fn render_lookahead_per_phase_budget_truncates_long_summary() {
        // PER_PHASE_BUDGET = 400;生成超过 400 字的 summary 应截断
        let long_summary: String = "深".repeat(500);
        let phases = vec![json!({
            "phase_label": "长章",
            "chapter_min": 1,
            "chapter_max": 5,
            "summary": long_summary,
            "key_events": [],
            "key_locations": [],
            "story_time_label_start": "",
            "story_time_label_end": ""
        })];
        let text = render_lookahead(&phases, "");
        // 单个 phase 块不超过 PER_PHASE_BUDGET 字符
        // (整体 text 包含 header,只看 phase 块是否含截断符)
        // 块内 '深' 字数 <= 400 - 各种前缀字符
        let deep_count = text.chars().filter(|c| *c == '深').count();
        assert!(
            deep_count <= PER_PHASE_BUDGET,
            "超长 summary 应被截断, '深' 出现 {} 次 > budget {}", deep_count, PER_PHASE_BUDGET
        );
        assert!(text.contains("..."), "截断时应有省略号: {text}");
    }
}
