//! timeline_narrative_guard — 时间线跳跃后 GM 叙事的禁词过滤。
//!
//! 对应 Python: `rpg/agents/timeline_narrative_guard.py`
//!
//! 主防线是 context_engine._timeline_layer() 给 GM 明示禁止这类措辞。
//! 这个 agent 是 belt-and-suspenders:user_set 时间跳跃**当回合**扫 GM 输出,
//! 检测禁词,命中则在 audit_log 写违规,前端展示警告。
//!
//! 不强 strip(避免误删合法叙事),仅 surface 让玩家决定是否 /retry。

use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::common::{state_turn, AgentResult, GameState};

/// 禁词模式 — 涵盖"穿越/重置/醒来发现/时间倒流"类过渡叙事的常见表达。
const FORBIDDEN_PATTERNS_RAW: &[(&str, &str)] = &[
    // 穿越类
    (r"穿越(?:事件|到|回去|回|回到|过去|时空)", "穿越叙事"),
    (r"时空(?:错乱|穿梭|裂缝|乱流)", "时空错乱叙事"),
    (r"回到\s*过去", "回到过去叙事"),
    (r"时间(?:倒流|流逝|被改写)", "时间倒流叙事"),
    // 醒来/失忆开场
    (r"再次睁开(?:眼睛|眼眸|双眼)", "再次睁开眼叙事"),
    (r"(?:当你|玩家)?醒来(?:发现|时)", "醒来发现叙事"),
    (r"从(?:昏迷|沉睡|失神|意识)中(?:醒来|惊醒|苏醒)", "失神苏醒叙事"),
    // 时间被拨回 / 时钟被拨回
    (r"时间被[^,,。!?]{0,30}?[拉拨][^,,。]{0,5}?回", "时间被拨回叙事"),
    (r"时钟被[^,,。!?]{0,20}?[拨拉][^,,。]{0,5}?回", "时钟被拨回叙事"),
    (r"[拨拉][^,,。]{0,5}?回(?:最初|原点|开始|起点)", "拨回原点叙事"),
    // 重启/重置世界
    (r"重启(?:世界|时间|场景|剧情)", "重启世界叙事"),
    (r"重置(?:世界|时间|场景|剧情)", "重置世界叙事"),
    (r"世界(?:被|又|忽然)?\s*重写", "世界被重写叙事"),
    // 惊厥/失忆/无意识开场
    (r"^冷[,,]\s*刺骨的冷", "刺骨的冷开场"),
    (r"^冷得发[抖颤栗]", "发抖开场"),
    (r"当你再次[^,,。]{0,8}时", "当你再次X时模板"),
];

static FORBIDDEN_PATTERNS: Lazy<Vec<(Regex, &'static str)>> = Lazy::new(|| {
    FORBIDDEN_PATTERNS_RAW
        .iter()
        .filter_map(|(pat, label)| Regex::new(pat).ok().map(|re| (re, *label)))
        .collect()
});

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Violation {
    pub pattern_label: String,
    pub matched_text: String,
    pub position: usize,
}

/// 检测 GM 文本是否在 user_set 时间跳跃当回合写了禁止叙事。
///
/// 判定优先级:
/// 1. state.world.timeline.user_set_jump_turn == state_turn(state)
/// 2. state.world.timeline.last_transition.source == "user_set"
///    AND last_transition.turn == state_turn(state)
pub fn detect_time_jump_violations(text: &str, state: &GameState) -> Vec<Violation> {
    if !is_user_set_jump_turn(state) {
        return Vec::new();
    }
    if text.is_empty() {
        return Vec::new();
    }

    let mut out: Vec<Violation> = Vec::new();
    for (re, label) in FORBIDDEN_PATTERNS.iter() {
        for m in re.find_iter(text) {
            out.push(Violation {
                pattern_label: label.to_string(),
                matched_text: m.as_str().to_string(),
                position: m.start(),
            });
        }
    }
    out
}

fn is_user_set_jump_turn(state: &GameState) -> bool {
    let timeline = state
        .data
        .get("world")
        .and_then(|w| w.get("timeline"))
        .cloned()
        .unwrap_or_default();

    // 优先 user_set_jump_turn
    if let Some(jump_turn) = timeline.get("user_set_jump_turn").and_then(|v| v.as_u64()) {
        if jump_turn == state_turn(state) {
            return true;
        }
    }
    // 回退 last_transition
    let last = timeline.get("last_transition").cloned().unwrap_or_default();
    let source = last.get("source").and_then(|v| v.as_str()).unwrap_or("");
    let turn = last.get("turn").and_then(|v| v.as_u64()).unwrap_or(u64::MAX);
    source == "user_set" && turn == state_turn(state)
}

/// 把违规列表写入 state.audit_log。
///
/// TODO[rpg-state]: 当 GameState 实装 audit_log 字段后接上 push。
pub fn record_violations_to_audit(state: &mut GameState, violations: &[Violation]) -> AgentResult<()> {
    if violations.is_empty() {
        return Ok(());
    }
    let entry = serde_json::json!({
        "type": "narrative_guard_violation",
        "turn": state_turn(state),
        "violations": violations.iter().map(|v| {
            serde_json::json!({
                "label": v.pattern_label,
                "match": v.matched_text,
                "position": v.position,
            })
        }).collect::<Vec<_>>(),
    });
    // 占位:塞到 state.data.audit_log 数组
    let log = state
        .data
        .as_object_mut()
        .and_then(|o| {
            if !o.contains_key("audit_log") {
                o.insert("audit_log".into(), serde_json::Value::Array(vec![]));
            }
            o.get_mut("audit_log")
        })
        .and_then(|v| v.as_array_mut());
    if let Some(arr) = log {
        arr.push(entry);
    }
    Ok(())
}

pub struct TimelineNarrativeGuard;

impl TimelineNarrativeGuard {
    pub fn new() -> Self {
        Self
    }

    pub fn detect(&self, text: &str, state: &GameState) -> Vec<Violation> {
        detect_time_jump_violations(text, state)
    }
}

impl Default for TimelineNarrativeGuard {
    fn default() -> Self {
        Self::new()
    }
}
