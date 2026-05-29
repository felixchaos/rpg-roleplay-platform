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

use crate::common::{
    call_with_tools, extract_json_block, state_turn, AgentResult, ChatChunk, ChatMessage,
    ChatRequest, GameState, LlmBackend, SharedLlm, ToolSchema,
};
use futures_util::StreamExt;
use rpg_state::{apply_op, Op};

/// 与 RealGameState 别名 — 现在 common::GameState 已经就是 rpg_state::GameState。
pub type RealGameState = GameState;

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
    let mut locked_vars = serde_json::Map::new();
    for (key, info) in &state.data.worldline.user_variables {
        if info.get("locked").and_then(|v| v.as_bool()).unwrap_or(false) {
            let v = info.get("value").cloned().unwrap_or(Value::String("".into()));
            locked_vars.insert(key.clone(), v);
        }
    }

    let active_npcs: Vec<Value> = state
        .data
        .active_entities
        .iter()
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

    let recent_events: Vec<String> = state
        .data
        .world
        .known_events
        .iter()
        .rev()
        .take(5)
        .map(|v| v.as_str().unwrap_or("").to_string())
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();

    // chapter_min/max are non-schema extra fields
    let chapter_min = state.data.world.timeline.extra.get("chapter_min").cloned().unwrap_or(Value::Null);
    let chapter_max = state.data.world.timeline.extra.get("chapter_max").cloned().unwrap_or(Value::Null);

    RealitySnapshot {
        current_phase: state.data.world.timeline.current_phase.clone(),
        current_location: state.data.player.current_location.clone(),
        current_time: state.data.world.time.clone(),
        active_npcs,
        locked_variables: locked_vars,
        recent_events,
        chapter_window: json!({
            "min": chapter_min,
            "max": chapter_max,
        }),
        turn: state_turn(state),
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
        server_id: None,
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

/// 3e: independent critic — 同步骨架(无 LLM,直接通过)。
///
/// 与 Python `validator_independent_critic` 同语义:always pass。
/// 真实 LLM 版本走 [`validator_independent_critic_llm`],异步,失败 fail-soft。
/// 这里保留同步版本是给 `run_validators` 串行管线兜底(LLM 不可用 / 离线)用。
pub fn validator_independent_critic(
    _proposal: &Value,
    _snapshot: &RealitySnapshot,
) -> (bool, String) {
    (true, String::new())
}

/// critic 用的默认便宜模型(env `RPG_BLACK_SWAN_CRITIC_MODEL` 覆盖)。
const DEFAULT_CRITIC_MODEL: &str = "claude-haiku-4-5";

/// critic LLM 最大 token — 只要 JSON {accept, reason},开很小。
const CRITIC_MAX_TOKENS: u32 = 200;

const CRITIC_SYSTEM_PROMPT: &str = "你是 black swan 事件提案的独立审稿人(independent critic)。\n\
你的任务:阅读 reality_snapshot 与 proposal,判断 proposal 是否符合 reality_snapshot。\n\
不符合的典型情况:与 locked_variables 冲突;引入了 snapshot 不存在的 NPC / 地点;\n\
事件与 current_phase / current_location / current_time 明显矛盾;summary 内含禁词。\n\
严格只输出 JSON,形如 {\"accept\": true, \"reason\": \"...\"} 或 {\"accept\": false, \"reason\": \"...\"},\n\
不要附带任何前后缀文字。";

/// 取 critic 用的 model_id —— env 覆盖 → 默认 haiku。
fn critic_model_id() -> String {
    std::env::var("RPG_BLACK_SWAN_CRITIC_MODEL")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_CRITIC_MODEL.to_string())
}

/// 组装 critic 用的 user prompt —— 把 snapshot + proposal 打成两段 JSON。
fn build_critic_prompt(proposal: &Value, snapshot: &RealitySnapshot) -> String {
    let snap_json = serde_json::to_string_pretty(snapshot)
        .unwrap_or_else(|_| "{}".to_string());
    let prop_json = serde_json::to_string_pretty(proposal)
        .unwrap_or_else(|_| "{}".to_string());
    format!(
        "## reality_snapshot\n```json\n{snap}\n```\n\n## proposal\n```json\n{prop}\n```\n\n\
         判定此 black swan 提案是否符合 reality_snapshot。返回 JSON {{accept: bool, reason: str}}。",
        snap = snap_json,
        prop = prop_json,
    )
}

/// 解析 critic LLM 输出 → (accept, reason)。
///
/// 失败(空 / 非 JSON / 无 accept 字段)返回 None,由 caller 走 fail-soft 路径。
fn parse_critic_response(raw: &str) -> Option<(bool, String)> {
    let blk = extract_json_block(raw).ok()?;
    let val: Value = serde_json::from_str(blk).ok()?;
    let accept = val.get("accept").and_then(|v| v.as_bool())?;
    let reason = val
        .get("reason")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    Some((accept, reason))
}

/// 3e (真实 LLM 版):独立 critic,二次 LLM 评分一致性。
///
/// 行为:
/// * 用便宜模型(`RPG_BLACK_SWAN_CRITIC_MODEL` 或 `claude-haiku-4-5`)做二次判定。
/// * stream_chat 一次性收集 Text 后 JSON parse。
/// * fail-soft:网络 / parse 失败 → 返回 `(true, "...")`(accept),并 warn log,
///   不阻塞主流程。
///
/// 返回 `(pass, reason)` 与同步版本同 shape,方便和 [`run_validators`] 组合。
pub async fn validator_independent_critic_llm(
    llm: &dyn LlmBackend,
    proposal: &Value,
    snapshot: &RealitySnapshot,
) -> (bool, String) {
    let user_prompt = build_critic_prompt(proposal, snapshot);
    let req = ChatRequest {
        model: critic_model_id(),
        system: Some(CRITIC_SYSTEM_PROMPT.to_string()),
        messages: vec![ChatMessage::user(user_prompt)],
        tools: Vec::new(),
        temperature: Some(0.0),
        max_tokens: Some(CRITIC_MAX_TOKENS),
        stream: false,
        extra: Value::Null,
    };

    let mut stream = match llm.stream_chat(req).await {
        Ok(s) => s,
        Err(e) => {
            tracing::warn!(
                "[black_swan][critic] LLM stream_chat 失败,fail-soft accept: {e}"
            );
            return (true, format!("critic fail-soft (llm error: {e})"));
        }
    };

    let mut out = String::new();
    while let Some(chunk) = stream.next().await {
        match chunk {
            Ok(ChatChunk::Text(t)) => out.push_str(&t),
            Ok(_) => {}
            Err(e) => {
                tracing::warn!(
                    "[black_swan][critic] LLM chunk 失败,fail-soft accept: {e}"
                );
                return (true, format!("critic fail-soft (chunk error: {e})"));
            }
        }
    }

    match parse_critic_response(&out) {
        Some((true, reason)) => (true, reason),
        Some((false, reason)) => (false, format!("independent critic reject: {reason}")),
        None => {
            tracing::warn!(
                "[black_swan][critic] LLM 输出 parse 失败,fail-soft accept: raw={:?}",
                out.chars().take(200).collect::<String>()
            );
            (true, "critic fail-soft (parse error)".to_string())
        }
    }
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

/// 默认 token blacklist — 阻止 propose 用穿越 / 重启之类的禁词。
const DEFAULT_BLACKLIST: &[&str] = &[
    "穿越", "重启世界", "重置世界", "时间倒流", "时空错乱", "回到过去",
    "死亡", "灭门", "覆灭",
];

const BLACK_SWAN_SYSTEM_PROMPT: &str = r#"你是世界事件提案器(black swan agent)。
任务:根据 reality snapshot,主动提议一个【小规模】的世界事件,丰富当前 phase 的环境。
约束:
  * 只能使用 snapshot 里出现过的 NPC / 地点 / phase。不要发明新角色或新地点。
  * 不得违反 locked_variables(玩家锁定的世界设定)。
  * 不得跨 phase。
  * 不得描写穿越 / 时间倒流 / 重置世界 / 死亡 / 灭门类内容。
  * 若当前情境不适合任何事件,event_kind 返回 "no_op"。
输出:严格调用 propose_black_swan_event 工具。"#;

const BLACK_SWAN_MAX_TOKENS: usize = 600;

pub struct BlackSwanAgent {
    llm: SharedLlm,
}

impl BlackSwanAgent {
    pub fn new(llm: SharedLlm) -> Self {
        Self { llm }
    }

    /// 主入口:Layer 1 snapshot → Layer 2 schema → LLM propose → Layer 3 validate → Layer 5 dispatch。
    ///
    /// LLM propose 走 rpg_llm::stream_chat — native tool_use 优先,JSON fallback 兜底。
    /// validate 通过后由 caller 拿 proposal 自己调 [`dispatch_swan`] apply 到 state。
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
        let schema = proposal_tool_schema(&snapshot);

        // 1) propose via native tool_use(走 call_with_tools)。
        let user_prompt = build_propose_prompt(&snapshot);
        let messages = vec![ChatMessage::user(user_prompt)];
        let proposal = match call_with_tools(
            self.llm.as_ref(),
            BLACK_SWAN_SYSTEM_PROMPT,
            &messages,
            std::slice::from_ref(&schema),
            BLACK_SWAN_MAX_TOKENS,
        )
        .await
        {
            Ok(resp) => {
                // 优先 tool_calls[0].input;否则尝试 resp.text 抠 JSON。
                resp.tool_calls
                    .into_iter()
                    .find(|tc| tc.name == "propose_black_swan_event")
                    .map(|tc| tc.input)
                    .or_else(|| {
                        let blk = extract_json_block(&resp.text).ok()?;
                        serde_json::from_str::<Value>(blk).ok()
                    })
            }
            Err(e) => {
                tracing::warn!("[black_swan] propose 调用失败: {e}");
                None
            }
        };

        let Some(proposal) = proposal else {
            return Ok(BlackSwanOutput {
                triggered: false,
                proposal: None,
                validation_failures: vec!["LLM 未返回有效 proposal".into()],
            });
        };

        // event_kind="no_op" 视为 "agent 主动放弃本轮",不算 fail。
        if proposal
            .get("event_kind")
            .and_then(|v| v.as_str())
            .map(|k| k == "no_op")
            .unwrap_or(false)
        {
            return Ok(BlackSwanOutput {
                triggered: false,
                proposal: Some(proposal),
                validation_failures: vec!["event_kind=no_op".into()],
            });
        }

        // 2) Layer 3 同步 validators(token / hard / timeline / npc / 同步 critic stub)。
        let (ok, failures) = run_validators(&proposal, &snapshot, DEFAULT_BLACKLIST);
        if !ok {
            return Ok(BlackSwanOutput {
                triggered: false,
                proposal: Some(proposal),
                validation_failures: failures,
            });
        }

        // 3) Layer 3e 真实 LLM critic —— 便宜模型二次判定,fail-soft accept。
        let (critic_pass, critic_reason) =
            validator_independent_critic_llm(self.llm.as_ref(), &proposal, &snapshot).await;
        if !critic_pass {
            return Ok(BlackSwanOutput {
                triggered: false,
                proposal: Some(proposal),
                validation_failures: vec![critic_reason],
            });
        }

        Ok(BlackSwanOutput {
            triggered: true,
            proposal: Some(proposal),
            validation_failures: vec![],
        })
    }
}

/// 组装 propose 时的 user prompt — 仅暴露 snapshot 关键字段。
fn build_propose_prompt(snapshot: &RealitySnapshot) -> String {
    let npc_lines: Vec<String> = snapshot
        .active_npcs
        .iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let name = n.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let disp = n.get("disposition").and_then(|v| v.as_str()).unwrap_or("");
            if id.is_empty() {
                None
            } else {
                Some(format!("  - id={id} name={name} disposition={disp}"))
            }
        })
        .collect();
    let locked_lines: Vec<String> = snapshot
        .locked_variables
        .iter()
        .map(|(k, v)| format!("  - {k} = {v}"))
        .collect();
    let recent_lines: Vec<String> = snapshot
        .recent_events
        .iter()
        .filter(|s| !s.is_empty())
        .map(|s| format!("  - {s}"))
        .collect();

    format!(
        "## reality snapshot\n\
         - current_phase: {phase}\n\
         - current_location: {loc}\n\
         - current_time: {time}\n\
         - turn: {turn}\n\n\
         ### active_npcs\n{npcs}\n\n\
         ### locked_variables\n{locked}\n\n\
         ### recent_events\n{recent}\n\n\
         请调用 propose_black_swan_event 给出一个【小规模】事件;若不合适请用 event_kind=\"no_op\"。",
        phase = snapshot.current_phase,
        loc = snapshot.current_location,
        time = snapshot.current_time,
        turn = snapshot.turn,
        npcs = if npc_lines.is_empty() {
            "  (无)".to_string()
        } else {
            npc_lines.join("\n")
        },
        locked = if locked_lines.is_empty() {
            "  (无)".to_string()
        } else {
            locked_lines.join("\n")
        },
        recent = if recent_lines.is_empty() {
            "  (无)".to_string()
        } else {
            recent_lines.join("\n")
        },
    )
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
        "weather"
            if !summary.is_empty() => {
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

#[cfg(test)]
mod tests {
    use super::*;
    use rpg_state::state::GameState;
    use serde_json::json;

    fn make_state() -> GameState {
        GameState::new("test_user")
    }

    // ── reality_snapshot ────────────────────────────────────────────

    #[test]
    fn test_reality_snapshot_defaults() {
        let state = make_state();
        let snap = reality_snapshot(&state, None);
        assert!(snap.current_phase.is_empty());
        assert!(snap.active_npcs.is_empty());
        assert!(snap.recent_events.is_empty());
        assert_eq!(snap.turn, 0);
    }

    // ── validators ─────────────────────────────────────────────────

    #[test]
    fn test_validator_token_blacklist_blocks() {
        let proposal = json!({"summary": "时间倒流了,回到过去"});
        let blacklist = &["时间倒流", "回到过去"];
        let (pass, reason) = validator_token_blacklist(&proposal, blacklist);
        assert!(!pass);
        assert!(reason.contains("时间倒流"));
    }

    #[test]
    fn test_validator_token_blacklist_passes_clean() {
        let proposal = json!({"summary": "一场轻柔的夜雨开始下起来"});
        let (pass, _) = validator_token_blacklist(&proposal, DEFAULT_BLACKLIST);
        assert!(pass);
    }

    #[test]
    fn test_validator_timeline_anchor_passes_same_phase() {
        let snap = RealitySnapshot {
            current_phase: "东征".to_string(),
            ..Default::default()
        };
        let proposal = json!({"event_kind": "weather", "summary": "大雨"});
        // proposal 没有 phase 字段 → 默认通过
        let (pass, _) = validator_timeline_anchor(&proposal, &snap);
        assert!(pass);
    }

    #[test]
    fn test_validator_timeline_anchor_rejects_wrong_phase() {
        let snap = RealitySnapshot {
            current_phase: "东征".to_string(),
            ..Default::default()
        };
        let proposal = json!({"phase": "西征", "event_kind": "weather", "summary": "大雨"});
        let (pass, reason) = validator_timeline_anchor(&proposal, &snap);
        assert!(!pass);
        assert!(reason.contains("西征"));
    }

    #[test]
    fn test_validator_npc_presence_passes_empty() {
        let snap = RealitySnapshot::default();
        let proposal = json!({"event_kind": "rumor", "summary": "消息传来"});
        let (pass, _) = validator_npc_presence(&proposal, &snap);
        assert!(pass);
    }

    #[test]
    fn test_validator_npc_presence_rejects_unknown_npc() {
        let snap = RealitySnapshot {
            active_npcs: vec![json!({"id": "npc_1", "name": "李大人"})],
            ..Default::default()
        };
        let proposal = json!({"event_kind": "npc_action", "affected_npc_ids": ["npc_999"]});
        let (pass, _) = validator_npc_presence(&proposal, &snap);
        assert!(!pass);
    }

    // ── dispatch_swan ────────────────────────────────────────────────

    #[test]
    fn test_dispatch_swan_no_op_skips() {
        let mut state = make_state();
        let proposal = json!({"event_kind": "no_op", "summary": ""});
        let (applied, errors) = dispatch_swan(&mut state, &proposal, "test");
        assert_eq!(applied, 0);
        assert!(!errors.is_empty()); // "no_op,跳过" 算 errors
    }

    #[test]
    fn test_dispatch_swan_weather_writes_state() {
        let mut state = make_state();
        let proposal = json!({"event_kind": "weather", "summary": "暴风雪"});
        let (applied, errors) = dispatch_swan(&mut state, &proposal, "test");
        assert!(applied > 0, "applied={applied} errors={errors:?}");
    }

    // ── maybe_trigger — probability gate + LLM 路径(stub backend)─────
    //
    // 这三个测试覆盖 LLM 真路径的入口:
    // 1) probability=0 → 立即 short-circuit,绝不调用 stub backend(stub key 也不会触网)
    // 2) probability=1.0 + stub key → LLM 调用必然失败 → 返回 triggered=false 含 "LLM 未返回"
    // 3) proposal_tool_schema 暴露 active_npcs 给 enum,确保 schema 自身被正确生成

    fn stub_llm() -> SharedLlm {
        use rpg_llm::anthropic::AnthropicBackend;
        use rpg_llm::AnyBackend;
        use std::sync::Arc;
        let b = AnthropicBackend::new("stub-key").expect("build stub backend");
        Arc::new(AnyBackend::Anthropic(b))
    }

    #[tokio::test]
    async fn test_maybe_trigger_probability_zero_skips_llm() {
        let agent = BlackSwanAgent::new(stub_llm());
        let state = make_state();
        let out = agent
            .maybe_trigger(
                BlackSwanInput {
                    user_id: Some(1),
                    script_id: Some(1),
                    probability: 0.0,
                },
                &state,
            )
            .await
            .expect("maybe_trigger should succeed");
        assert!(!out.triggered);
        assert!(out.proposal.is_none());
        // reason 应包含 probability<=0 提示,确认走的是概率门 short-circuit
        assert!(
            out.validation_failures
                .iter()
                .any(|s| s.contains("probability<=0")),
            "actual failures: {:?}",
            out.validation_failures
        );
    }

    #[tokio::test]
    async fn test_maybe_trigger_llm_failure_returns_safe_result() {
        // probability=1.0 → 必然走 LLM;stub-key 调用 Anthropic 必然失败,
        // call_with_tools 内部把错误转化成 None proposal,返回 "LLM 未返回有效 proposal"。
        let agent = BlackSwanAgent::new(stub_llm());
        let state = make_state();
        let out = agent
            .maybe_trigger(
                BlackSwanInput {
                    user_id: Some(1),
                    script_id: Some(1),
                    probability: 1.0,
                },
                &state,
            )
            .await
            .expect("maybe_trigger should not panic even when LLM fails");
        assert!(!out.triggered);
        // 落在 LLM 路径(而非概率门),validation_failures 应记录 LLM 失败
        assert!(
            out.validation_failures
                .iter()
                .any(|s| s.contains("LLM") || s.contains("proposal")),
            "actual failures: {:?}",
            out.validation_failures
        );
    }

    // ── validator_independent_critic_llm:mock LLM 三态 ───────────────
    //
    // 用一个最小 Mock LlmBackend(预置一段固定输出),走 stream_chat 真路径,
    // 验证:
    //   1) LLM 输出 {accept:true} → critic 通过
    //   2) LLM 输出 {accept:false, reason} → critic 拒绝(reason 透传)
    //   3) LLM 输出无法 parse → fail-soft accept(不阻塞主流程)

    struct MockLlm {
        // 预置一段 Text chunk;返回 stream 时一次性发出。stream_chat 自身永不 Err。
        reply: String,
    }

    #[async_trait::async_trait]
    impl rpg_llm::pipeline::LlmBackend for MockLlm {
        fn kind(&self) -> rpg_llm::pipeline::BackendKind {
            rpg_llm::pipeline::BackendKind::Anthropic
        }

        async fn stream_chat<'a>(
            &'a self,
            _req: rpg_llm::pipeline::ChatRequest,
        ) -> Result<rpg_llm::pipeline::ChunkStream<'a>, rpg_llm::pipeline::LlmError> {
            use rpg_llm::pipeline::ChatChunk;
            let text = self.reply.clone();
            let s = futures::stream::iter(vec![Ok::<_, rpg_llm::pipeline::LlmError>(
                ChatChunk::Text(text),
            )]);
            Ok(Box::pin(s))
        }
    }

    fn make_snap_and_proposal() -> (RealitySnapshot, Value) {
        let snap = RealitySnapshot {
            current_phase: "东征".to_string(),
            current_location: "雁门".to_string(),
            current_time: "黄昏".to_string(),
            ..Default::default()
        };
        let proposal = json!({
            "event_kind": "weather",
            "summary": "雁门城下风沙骤起",
        });
        (snap, proposal)
    }

    #[tokio::test]
    async fn test_critic_llm_accepts() {
        let mock = MockLlm {
            reply: r#"{"accept": true, "reason": "consistent with snapshot"}"#.to_string(),
        };
        let (snap, proposal) = make_snap_and_proposal();
        let (pass, reason) =
            validator_independent_critic_llm(&mock, &proposal, &snap).await;
        assert!(pass, "accept=true 应当通过: reason={reason}");
        assert!(
            reason.contains("consistent") || reason.is_empty() || reason.contains("snapshot"),
            "reason 应当透传或为空: {reason}"
        );
    }

    #[tokio::test]
    async fn test_critic_llm_rejects() {
        let mock = MockLlm {
            reply: r#"{"accept": false, "reason": "事件引入了不存在的 NPC"}"#.to_string(),
        };
        let (snap, proposal) = make_snap_and_proposal();
        let (pass, reason) =
            validator_independent_critic_llm(&mock, &proposal, &snap).await;
        assert!(!pass, "accept=false 应当拒绝");
        assert!(
            reason.contains("不存在的 NPC") || reason.contains("reject"),
            "reason 应透传 LLM reason: {reason}"
        );
    }

    #[tokio::test]
    async fn test_critic_llm_parse_failure_is_fail_soft() {
        // 非 JSON,parse 必失败 → fail-soft accept(reason 含 "fail-soft")
        let mock = MockLlm {
            reply: "this is not JSON and never will be".to_string(),
        };
        let (snap, proposal) = make_snap_and_proposal();
        let (pass, reason) =
            validator_independent_critic_llm(&mock, &proposal, &snap).await;
        assert!(pass, "parse 失败必须 fail-soft accept,不能阻塞主流程");
        assert!(
            reason.contains("fail-soft") || reason.contains("parse"),
            "reason 应标注 fail-soft: {reason}"
        );
    }

    #[test]
    fn test_proposal_tool_schema_enum_limits_to_active_npcs() {
        let snap = RealitySnapshot {
            active_npcs: vec![
                json!({"id": "npc_1", "name": "张三"}),
                json!({"id": "npc_2", "name": "李四"}),
            ],
            ..Default::default()
        };
        let schema = proposal_tool_schema(&snap);
        // 校验 enum 字段被填入 active_npc id
        let s = schema.input_schema.to_string();
        assert!(s.contains("npc_1"));
        assert!(s.contains("npc_2"));
        // event_kind enum 必须含 no_op(允许 agent 主动放弃)
        assert!(s.contains("no_op"));
    }
}
