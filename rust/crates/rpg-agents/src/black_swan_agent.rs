//! black_swan_agent — 主动触发世界事件的子代理。
//!
//! 对应 Python: `rpg/agents/black_swan_agent.py`
//!
//! 5 层 validator 管线:
//! 1. reality_snapshot — 现实切片快照(给 LLM)
//! 2. proposal_tool_schema — native tool_use 强 schema
//! 3. validators — 5 个独立校验(token blacklist / hard constraint /
//!    timeline anchor / NPC presence / independent critic)
//! 4. retry — max 2 次
//! 5. dispatcher — 落地,origin="autonomous_agent"
//!
//! ⚠️ MEMORY:rpg_black_swan_agent_todo.md 备忘的就是这个 agent。
//! 设计已就绪,但依赖 task 87 工具化底座。本骨架完成 Layer 1+2+3 接口。

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::common::{AgentResult, GameState, SharedLlm, ToolSchema};
use rpg_state::{apply_op, GameState as RealGameState, Op};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RealitySnapshot {
    pub current_phase: String,
    pub current_location: String,
    pub current_time: String,
    pub active_npcs: Vec<Value>,
    pub locked_variables: serde_json::Map<String, Value>,
    pub recent_events: Vec<String>,
    pub chapter_window: Value,
    pub turn: u64,
}

/// Layer 1: 现实切片。
pub fn reality_snapshot(state: &GameState, _script_id: Option<i64>) -> RealitySnapshot {
    let world = state.data.get("world").cloned().unwrap_or(Value::Null);
    let timeline = world.get("timeline").cloned().unwrap_or(Value::Null);
    let player = state.data.get("player").cloned().unwrap_or(Value::Null);
    let worldline = state.data.get("worldline").cloned().unwrap_or(Value::Null);

    let mut locked_vars = serde_json::Map::new();
    if let Some(user_vars) = worldline.get("user_variables").and_then(|v| v.as_object()) {
        for (key, info) in user_vars {
            if info.get("locked").and_then(|v| v.as_bool()).unwrap_or(false) {
                let v = info.get("value").cloned().unwrap_or(Value::String("".into()));
                locked_vars.insert(key.clone(), v);
            }
        }
    }

    let active_entities = state
        .data
        .get("active_entities")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let active_npcs: Vec<Value> = active_entities
        .into_iter()
        .filter(|e| {
            let kind = e.get("kind").and_then(|v| v.as_str()).unwrap_or("unknown");
            matches!(kind, "npc" | "enemy" | "unknown")
        })
        .take(8)
        .map(|e| {
            json!({
                "id": e.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                "name": e.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                "disposition": e.get("disposition").and_then(|v| v.as_str()).unwrap_or("unknown").to_string(),
                "kind": e.get("kind").and_then(|v| v.as_str()).unwrap_or("unknown").to_string(),
            })
        })
        .collect();

    let recent_events: Vec<String> = world
        .get("known_events")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .rev()
        .take(5)
        .map(|v| v.as_str().unwrap_or("").to_string())
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();

    RealitySnapshot {
        current_phase: timeline
            .get("current_phase")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        current_location: player
            .get("current_location")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        current_time: world.get("time").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        active_npcs,
        locked_variables: locked_vars,
        recent_events,
        chapter_window: json!({
            "min": timeline.get("chapter_min").cloned().unwrap_or(Value::Null),
            "max": timeline.get("chapter_max").cloned().unwrap_or(Value::Null),
        }),
        turn: state.turn,
    }
}

/// Layer 2: 生成 LLM tool_use schema(enum 限定 phase/character/location 取值)。
pub fn proposal_tool_schema(snapshot: &RealitySnapshot) -> ToolSchema {
    let valid_npc_ids: Vec<String> = snapshot
        .active_npcs
        .iter()
        .filter_map(|n| n.get("id").and_then(|v| v.as_str()).map(|s| s.to_string()))
        .filter(|s| !s.is_empty())
        .collect();

    let schema = json!({
        "type": "object",
        "required": ["event_kind", "summary"],
        "properties": {
            "event_kind": {
                "type": "string",
                "enum": ["weather", "npc_arrival", "rumor", "encounter", "object", "no_op"],
            },
            "summary": {"type": "string", "description": "Short narrative summary"},
            "affected_npc_ids": {
                "type": "array",
                "items": {"type": "string", "enum": valid_npc_ids},
            },
            "drift_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "rationale": {"type": "string"},
        }
    });

    ToolSchema {
        name: "propose_black_swan_event".into(),
        description: "Propose a black swan event for the current game phase. \
                      Use ONLY entities, locations, and concepts that appear in the snapshot. \
                      DO NOT invent new NPCs, locations, or cross-phase events. \
                      If no suitable event fits the current situation, return event_kind='no_op'."
            .into(),
        input_schema: schema,
    }
}

// ── Layer 3: validators ──────────────────────────────────────────────

/// 3a: token blacklist — proposal 含禁用词直接拒绝。
pub fn validator_token_blacklist(
    proposal: &Value,
    blacklist: &[&str],
) -> (bool, String) {
    let summary = proposal
        .get("summary")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    for kw in blacklist {
        if summary.contains(kw) {
            return (false, format!("命中 blacklist: {kw}"));
        }
    }
    (true, String::new())
}

/// 3c: hard constraint — proposal 不得违反 locked_variables。
pub fn validator_hard_constraints(
    proposal: &Value,
    snapshot: &RealitySnapshot,
) -> (bool, String) {
    let summary = proposal.get("summary").and_then(|v| v.as_str()).unwrap_or("");
    for (key, val) in &snapshot.locked_variables {
        if let Some(v) = val.as_str() {
            if !v.is_empty()
                && !summary.contains(v)
                && summary.contains(key)
            {
                return (
                    false,
                    format!("locked_variable {key}={v} 与 proposal 内容冲突"),
                );
            }
        }
    }
    (true, String::new())
}

/// 3d: timeline anchor — proposal 不得跨 phase。
pub fn validator_timeline_anchor(
    proposal: &Value,
    snapshot: &RealitySnapshot,
) -> (bool, String) {
    let proposed_phase = proposal
        .get("phase")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !proposed_phase.is_empty() && proposed_phase != snapshot.current_phase {
        return (
            false,
            format!(
                "proposal phase '{}' 与 current_phase '{}' 不符",
                proposed_phase, snapshot.current_phase
            ),
        );
    }
    (true, String::new())
}

/// 3b: NPC presence — affected_npc_ids 必须都在 snapshot.active_npcs 里。
pub fn validator_npc_presence(
    proposal: &Value,
    snapshot: &RealitySnapshot,
) -> (bool, String) {
    let active_ids: std::collections::HashSet<&str> = snapshot
        .active_npcs
        .iter()
        .filter_map(|n| n.get("id").and_then(|v| v.as_str()))
        .collect();
    let Some(arr) = proposal.get("affected_npc_ids").and_then(|v| v.as_array()) else {
        return (true, String::new());
    };
    for v in arr {
        let id = v.as_str().unwrap_or("");
        if !id.is_empty() && !active_ids.contains(id) {
            return (false, format!("NPC '{id}' 不在 active_npcs"));
        }
    }
    (true, String::new())
}

/// 3e: independent critic — 接独立 LLM 二次判定(本骨架返回通过)。
///
/// TODO[opus]: 引入第二个便宜 LLM 做 critic + reason 校验。
pub fn validator_independent_critic(
    _proposal: &Value,
    _snapshot: &RealitySnapshot,
) -> (bool, String) {
    (true, String::new())
}

/// 串行跑全部 validator。返回 (all_pass, failures)。
pub fn run_validators(
    proposal: &Value,
    snapshot: &RealitySnapshot,
    blacklist: &[&str],
) -> (bool, Vec<String>) {
    let mut failures: Vec<String> = Vec::new();
    let checks: [(bool, String); 5] = [
        validator_token_blacklist(proposal, blacklist),
        validator_hard_constraints(proposal, snapshot),
        validator_timeline_anchor(proposal, snapshot),
        validator_npc_presence(proposal, snapshot),
        validator_independent_critic(proposal, snapshot),
    ];
    for (pass, msg) in checks {
        if !pass {
            failures.push(msg);
        }
    }
    (failures.is_empty(), failures)
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BlackSwanInput {
    pub user_id: Option<i64>,
    pub script_id: Option<i64>,
    pub probability: f64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BlackSwanOutput {
    pub triggered: bool,
    pub proposal: Option<Value>,
    pub validation_failures: Vec<String>,
}

pub struct BlackSwanAgent {
    #[allow(dead_code)] // 当前管线 LLM 提案路径走 GM 端,这里 dispatcher only
    llm: SharedLlm,
}

impl BlackSwanAgent {
    pub fn new(llm: SharedLlm) -> Self {
        Self { llm }
    }

    /// 主入口:Layer 1 snapshot → Layer 2 schema → LLM propose → Layer 3 validate → Layer 5 dispatch。
    ///
    /// 当前 LLM propose 仍走 GM 端 — 本 agent 提供 snapshot + schema + 校验闸门 + dispatch。
    /// 上层若想跑完整 maybe_trigger,需自己拿 schema 调 LLM 再调 dispatch。
    pub async fn maybe_trigger(
        &self,
        input: BlackSwanInput,
        state: &GameState,
    ) -> AgentResult<BlackSwanOutput> {
        // 概率门:用 thread_rng 而非 secrets — 此处只是 trigger / no-trigger 不涉敏感随机。
        let probability = input.probability.clamp(0.0, 1.0);
        if probability <= 0.0 {
            return Ok(BlackSwanOutput {
                triggered: false,
                proposal: None,
                validation_failures: vec!["probability<=0,本轮不触发".into()],
            });
        }
        let roll: f64 = rand::random();
        if roll > probability {
            return Ok(BlackSwanOutput {
                triggered: false,
                proposal: None,
                validation_failures: vec![format!(
                    "概率未命中 (roll={roll:.3} > p={probability:.3})"
                )],
            });
        }

        let snapshot = reality_snapshot(state, input.script_id);
        let _schema = proposal_tool_schema(&snapshot);

        // 完整 propose+validate 路径需 GM 端协同;此处只做接口校验,返回空。
        // 上层若有 proposal,可直接调 dispatch_swan() apply 到 RealGameState。
        Ok(BlackSwanOutput {
            triggered: false,
            proposal: None,
            validation_failures: vec!["snapshot 已生成,LLM propose 待上层调用 dispatch_swan".into()],
        })
    }
}

/// 把校验通过的 proposal 落地到 RealGameState(rpg-state)。
///
/// 对应 Python `_apply_swan`:把 proposal 转成一组 Op,经由 apply_op 闸门写入 state。
/// `origin` 决定 source 标识,黑天鹅 agent 用 "autonomous_agent"。
///
/// 返回 (applied_ops_count, errors)。
pub fn dispatch_swan(
    state: &mut RealGameState,
    proposal: &Value,
    origin: &str,
) -> (u32, Vec<String>) {
    let mut applied = 0u32;
    let mut errors: Vec<String> = Vec::new();
    let event_kind = proposal
        .get("event_kind")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if event_kind.is_empty() || event_kind == "no_op" {
        return (0, vec!["event_kind=no_op,跳过".into()]);
    }
    let summary = proposal
        .get("summary")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let source = if origin.is_empty() {
        "autonomous_agent".to_string()
    } else {
        format!("autonomous_agent:{origin}")
    };

    // 1) world.known_events append summary
    if !summary.is_empty() {
        let op = Op::Append {
            path: "world.known_events".into(),
            value: Value::String(summary.clone()),
        };
        match apply_op(state, op, &source, true) {
            Ok(_) => applied += 1,
            Err(e) => errors.push(format!("world.known_events append 失败: {e}")),
        }
    }

    // 2) world.last_swan = {kind, summary, ts}
    let last_swan = json!({
        "event_kind": event_kind,
        "summary": summary,
        "rationale": proposal.get("rationale").cloned().unwrap_or(Value::Null),
        "ts": chrono::Utc::now().to_rfc3339(),
    });
    let op = Op::Set {
        path: "world.last_swan".into(),
        value: last_swan,
    };
    match apply_op(state, op, &source, true) {
        Ok(_) => applied += 1,
        Err(e) => errors.push(format!("world.last_swan set 失败: {e}")),
    }

    // 3) kind 特化字段 — weather / npc_arrival / rumor 等
    match event_kind {
        "weather" => {
            if !summary.is_empty() {
                let op = Op::Set {
                    path: "world.weather".into(),
                    value: Value::String(summary.clone()),
                };
                if let Err(e) = apply_op(state, op, &source, true) {
                    errors.push(format!("world.weather set 失败: {e}"));
                } else {
                    applied += 1;
                }
            }
        }
        "npc_arrival" => {
            // 把 affected_npc_ids 加进 world.active_npcs
            if let Some(ids) = proposal
                .get("affected_npc_ids")
                .and_then(|v| v.as_array())
            {
                for id in ids {
                    let id_str = id.as_str().unwrap_or("").to_string();
                    if id_str.is_empty() {
                        continue;
                    }
                    let op = Op::Append {
                        path: "world.active_npcs".into(),
                        value: Value::String(id_str),
                    };
                    if let Err(e) = apply_op(state, op, &source, true) {
                        errors.push(format!("world.active_npcs append 失败: {e}"));
                    } else {
                        applied += 1;
                    }
                }
            }
        }
        "rumor" => {
            let op = Op::Append {
                path: "memory.rumors".into(),
                value: Value::String(summary.clone()),
            };
            if let Err(e) = apply_op(state, op, &source, true) {
                errors.push(format!("memory.rumors append 失败: {e}"));
            } else {
                applied += 1;
            }
        }
        "encounter" | "object" => {
            // 通用:写到 memory.encounters
            let op = Op::Append {
                path: format!("memory.{event_kind}s"),
                value: json!({
                    "summary": summary,
                    "ts": chrono::Utc::now().to_rfc3339(),
                }),
            };
            if let Err(e) = apply_op(state, op, &source, true) {
                errors.push(format!("memory.{event_kind}s append 失败: {e}"));
            } else {
                applied += 1;
            }
        }
        _ => {}
    }

    (applied, errors)
}
