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
    let timeline = &state.data.world.timeline;

    // 优先 user_set_jump_turn (stored in timeline.extra)
    if let Some(jump_turn) = timeline.extra.get("user_set_jump_turn").and_then(|v| v.as_u64()) {
        if jump_turn == state_turn(state) {
            return true;
        }
    }
    // 回退 last_transition
    let last = timeline.last_transition.as_ref().cloned().unwrap_or_default();
    let source = last.get("source").and_then(|v| v.as_str()).unwrap_or("");
    let turn = last.get("turn").and_then(|v| v.as_u64()).unwrap_or(u64::MAX);
    source == "user_set" && turn == state_turn(state)
}

/// audit_log 保留上限,对齐 Python 端的 `audit[-200:]` 截断。
const AUDIT_LOG_CAP: usize = 200;

/// 把违规列表写入 `state.data.permissions.audit_log`。
///
/// 对应 Python `rpg/agents/timeline_narrative_guard.py::record_violations_to_audit`。
/// 字段:
///   - kind: "time_jump_narrative_violation"
///   - source: "timeline_narrative_guard"
///   - turn: 当前回合
///   - violations: [{ label, match }, ...](放到 AuditEntry.extra)
///   - hint: 让前端展示的提示文本
pub fn record_violations_to_audit(
    state: &mut GameState,
    violations: &[Violation],
) -> AgentResult<()> {
    if violations.is_empty() {
        return Ok(());
    }
    use rpg_schemas::AuditEntry;
    use serde_json::{Map, Value};

    let turn = state_turn(state);
    let violations_json: Vec<Value> = violations
        .iter()
        .map(|v| {
            serde_json::json!({
                "label": v.pattern_label,
                "match": v.matched_text,
            })
        })
        .collect();
    let hint = format!(
        "GM 在 user_set 时间跳跃当回合写了{} 处过渡叙事禁词,可考虑 /retry 重新生成。",
        violations.len()
    );

    let mut extra: Map<String, Value> = Map::new();
    extra.insert("kind".into(), Value::String("time_jump_narrative_violation".into()));
    extra.insert("violations".into(), Value::Array(violations_json));

    let entry = AuditEntry {
        ts: AuditEntry::now_ts(),
        source: "timeline_narrative_guard".into(),
        path: String::new(),
        blocked: None,
        hint: Some(hint),
        op: None,
        value: None,
        mode: None,
        turn,
        extra,
    };

    let log = &mut state.data.permissions.audit_log;
    log.push(entry);
    // Python 用 `audit[-200:]` 截尾,Rust drain 前缀实现等价。
    if log.len() > AUDIT_LOG_CAP {
        let drop = log.len() - AUDIT_LOG_CAP;
        log.drain(0..drop);
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn fixture_state_with_user_jump(turn: u64) -> GameState {
        let mut s = GameState::new("u_test");
        // 把 GameState 内部 turn 推到目标值。GameState 的 turn 路径是
        // typed: state.data.turn (u64-ish)。直接 set。
        s.data.turn = turn;
        s.data
            .world
            .timeline
            .extra
            .insert("user_set_jump_turn".into(), Value::from(turn));
        s
    }

    #[test]
    fn test_detect_no_violation_when_not_user_set_turn() {
        let mut s = GameState::new("u");
        s.data.turn = 5u64;
        // 没有 user_set_jump_turn,也没有 last_transition
        let viols = detect_time_jump_violations("穿越到过去", &s);
        assert!(viols.is_empty());
    }

    #[test]
    fn test_detect_violation_basic() {
        let s = fixture_state_with_user_jump(7);
        let text = "你忽然时空错乱,再次睁开眼睛";
        let viols = detect_time_jump_violations(text, &s);
        assert!(!viols.is_empty(), "should detect: {viols:?}");
        let labels: Vec<&str> = viols.iter().map(|v| v.pattern_label.as_str()).collect();
        assert!(labels.iter().any(|l| l.contains("时空错乱")));
        assert!(labels.iter().any(|l| l.contains("再次睁开眼")));
    }

    #[test]
    fn test_detect_empty_text() {
        let s = fixture_state_with_user_jump(1);
        assert!(detect_time_jump_violations("", &s).is_empty());
    }

    #[test]
    fn test_record_violations_writes_audit_log() {
        let mut s = fixture_state_with_user_jump(3);
        let viols = vec![Violation {
            pattern_label: "穿越叙事".into(),
            matched_text: "穿越到".into(),
            position: 0,
        }];
        assert!(s.data.permissions.audit_log.is_empty());
        record_violations_to_audit(&mut s, &viols).unwrap();
        assert_eq!(s.data.permissions.audit_log.len(), 1);
        let entry = &s.data.permissions.audit_log[0];
        assert_eq!(entry.source, "timeline_narrative_guard");
        assert_eq!(entry.turn, 3);
        assert!(entry.hint.is_some());
        // kind / violations 走 extra (AuditEntry flatten)
        let kind = entry.extra.get("kind").and_then(|v| v.as_str()).unwrap_or("");
        assert_eq!(kind, "time_jump_narrative_violation");
        let vs = entry.extra.get("violations").and_then(|v| v.as_array()).unwrap();
        assert_eq!(vs.len(), 1);
    }

    #[test]
    fn test_record_violations_empty_is_noop() {
        let mut s = fixture_state_with_user_jump(1);
        record_violations_to_audit(&mut s, &[]).unwrap();
        assert!(s.data.permissions.audit_log.is_empty());
    }

    #[test]
    fn test_record_violations_caps_audit_log() {
        let mut s = fixture_state_with_user_jump(1);
        // 预填 AUDIT_LOG_CAP 条
        for _ in 0..AUDIT_LOG_CAP {
            s.data
                .permissions
                .audit_log
                .push(rpg_schemas::AuditEntry::default());
        }
        let viols = vec![Violation {
            pattern_label: "x".into(),
            matched_text: "y".into(),
            position: 0,
        }];
        record_violations_to_audit(&mut s, &viols).unwrap();
        assert_eq!(s.data.permissions.audit_log.len(), AUDIT_LOG_CAP);
        // 最新一条应是我们刚写的
        let last = s.data.permissions.audit_log.last().unwrap();
        assert_eq!(last.source, "timeline_narrative_guard");
    }
}
