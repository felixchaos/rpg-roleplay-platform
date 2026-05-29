//! directives.rs — 玩家 /set 等指令解析 + 应用
//!
//! 对应 Python: `rpg/state/core.py::apply_set_directive` +
//! `rpg/state/extractors.py::_extract_set_*` + `rpg/state/_mixins/apply_ops.py
//! ::apply_player_directives` 中的玩家路径。
//!
//! 协议:玩家输入以 `/set` / `/设定` / `/设置` 开头时,后续文本是一组
//! "强制设定"。解析层抽出:
//! 1. 时间目标(`时间线=X` / `时间改为X` / `跳到X`)→ 直接 update_time(user_set)
//! 2. 位置覆盖(`当前位置改为X` / `现在在X`)→ player.current_location
//! 3. 散段 path=value 赋值 → 走 ops::apply_op 带 force=true
//! 4. /reveal <text> → player_private.flags.revealed_this_turn
//!
//! 应用顺序与 Python 一致(task 28):时间/位置自动派生先做,
//! 显式 path=value 最后兜底,保证用户硬约束最后赢。

use chrono::Utc;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};
use thiserror::Error;

use crate::ops::{self, ApplyKind, Op, OpError};
use crate::state::GameState;
use crate::timeline_jump::{clean_time_value, looks_like_time_value, update_time};

#[derive(Debug, Error)]
pub enum DirectiveError {
    #[error("op error: {0}")]
    Op(#[from] OpError),
}

#[derive(Debug, Clone, Default)]
pub struct DirectiveResult {
    /// 每条已应用 directive 的人类可读消息(对应 Python 的 updates list)
    pub updates: Vec<String>,
    /// 是否触发了 /reveal(本轮注入到 GM prompt)
    pub revealed_this_turn: Option<String>,
}

/// 顶层入口 — 应用玩家本轮的全部 directives。
///
/// 对应 Python `apply_player_directives(player_input)`。
pub fn apply_player_directives(
    state: &mut GameState,
    player_input: &str,
) -> Result<DirectiveResult, DirectiveError> {
    let mut result = DirectiveResult::default();
    let raw = player_input.trim();

    // 1) 清上一轮 /reveal 残留(防御:正常 record_turn 会清,但异常路径可能漏)
    clear_revealed_flag(state);

    // 2) /reveal <text>
    if let Some(text) = raw.strip_prefix("/reveal ") {
        let reveal_text = text.trim();
        if !reveal_text.is_empty() {
            apply_reveal(state, reveal_text);
            result.revealed_this_turn = Some(reveal_text.to_string());
            let snippet: String = reveal_text.chars().take(40).collect();
            result.updates.push(format!("玩家揭示秘密(本轮):{snippet}"));
        }
    }

    // 3) /set 指令体
    let mut set_updates = apply_set_directive(state, player_input)?;
    result.updates.append(&mut set_updates);

    // 4) 玩家自然语言时间跳跃 — 不属于 /set,但 Python 的
    //    apply_player_directives 把它合并在这一函数。复刻同样的位置。
    for target in detect_time_directives_for_player(player_input) {
        let current = state
            .data
            .get("world")
            .and_then(|w| w.get("time"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if target == current {
            continue;
        }
        let _ = crate::timeline_jump::request_time_jump(state, &target, player_input);
        result.updates.push(format!("时间跳跃待确认:{target}"));
    }

    // 5) 写入 memory.last_structured_updates(对应 Python 同名字段)
    if !result.updates.is_empty() {
        write_last_structured_updates(state, &result.updates);
    }

    Ok(result)
}

/// 核心 /set 解析。对应 Python `apply_set_directive(text)`。
pub fn apply_set_directive(
    state: &mut GameState,
    text: &str,
) -> Result<Vec<String>, DirectiveError> {
    let mut updates = Vec::new();
    let Some(directive) = extract_set_directive(text) else {
        return Ok(updates);
    };
    if directive.is_empty() {
        return Ok(updates);
    }

    // /set 自身入 user_variables 留痕
    let set_key = next_user_variable_key(state);
    if set_user_variable(state, &set_key, &directive, "user:/set") {
        updates.push(format!("强制设定:{directive}"));
    }
    // pinned 记忆(走 add_memory_item legacy_bucket=pinned)
    if push_pinned_memory(state, &format!("玩家强制设定:{directive}")) {
        updates.push("固定记忆:玩家强制设定".to_string());
    }

    // task 28:时间 → 位置 → 散段 path=value(最后赢)
    for target in extract_set_time_targets(&directive) {
        if target.is_empty() {
            continue;
        }
        let current = state
            .data
            .get("world")
            .and_then(|w| w.get("time"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if target == current {
            continue;
        }
        update_time(state, &target, "user_set");
        updates.push(format!("时间线强制设定:{target}"));
    }

    if let Some(loc) = extract_location_override(&directive) {
        if !loc.is_empty() {
            update_location(state, &loc);
            updates.push(format!("位置强制设定:{loc}"));
        }
    }

    for spec in extract_set_assignments(&directive) {
        let (path, value) = parse_assignment(&spec);
        if path.is_empty() {
            continue;
        }
        let op = Op::Set {
            path: path.clone(),
            value: Value::String(value),
        };
        match ops::apply_op(state, op, "user:/set", /*force=*/ true) {
            Ok(outcome) => match outcome.kind {
                ApplyKind::Applied => updates.push(format!("状态写入:{path}")),
                ApplyKind::Pending => updates.push(format!("状态写入待审:{path}")),
                ApplyKind::Rejected => updates.push(format!("状态写入拒绝:{path}")),
            },
            Err(e) => updates.push(format!("状态写入失败({path}):{e}")),
        }
    }

    Ok(updates)
}

// ─────────────────────────────────────────────────────────────
// 抽取器(对应 extractors.py)
// ─────────────────────────────────────────────────────────────

static SET_PREFIX_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?is)^/(?:set|设定|设置)\s+(.+)$").expect("set prefix regex")
});

fn extract_set_directive(text: &str) -> Option<String> {
    let raw = text.trim();
    let caps = SET_PREFIX_RE.captures(raw)?;
    let directive = caps.get(1)?.as_str();
    Some(clean_item(directive))
}

static SEGMENT_SPLIT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[；;\n]+").expect("segment split regex"));

/// 在逗号/中文逗号处切段,但只在"逗号后 32 字符内存在 `=` / `:` / `：` 并且
/// 沿途没遇到段终止符(`，` `,` `。` `！` `？` `；` `;` 换行)"时才切。
///
/// 等价于 Python 侧 `[，,]\s*(?=[^，,。！？；;\n]{1,32}(?:=|：|:))`,但 Rust
/// 标准 regex 不支持 lookahead — 改手写状态机。无外部依赖,O(n)。
fn comma_split_assignment(segment: &str) -> Vec<String> {
    let bytes_chars: Vec<(usize, char)> = segment.char_indices().collect();
    let mut out: Vec<String> = Vec::new();
    let mut start = 0usize; // 当前 piece 的 byte 起点
    let len = bytes_chars.len();
    let mut i = 0usize;
    while i < len {
        let (byte_pos, ch) = bytes_chars[i];
        if ch == ',' || ch == '，' {
            // 检查后 32 个 char 内是否在不遇到段终止符的情况下出现 `=`/`:`/`：`
            let mut should_split = false;
            let mut scanned = 0usize;
            let mut j = i + 1;
            // 跳过空白(对应 Python 的 `\s*`)
            while j < len {
                let (_, c) = bytes_chars[j];
                if c.is_whitespace() {
                    j += 1;
                } else {
                    break;
                }
            }
            while j < len && scanned < 32 {
                let (_, c) = bytes_chars[j];
                if matches!(
                    c,
                    '，' | ',' | '。' | '！' | '？' | '；' | ';' | '\n'
                ) {
                    break;
                }
                if c == '=' || c == ':' || c == '：' {
                    should_split = true;
                    break;
                }
                scanned += 1;
                j += 1;
            }
            if should_split {
                let piece = &segment[start..byte_pos];
                out.push(piece.to_string());
                // 跳过逗号本身,以及后续空白(同 `\s*`),供下一段使用
                let mut next = i + 1;
                while next < len {
                    let (_, c) = bytes_chars[next];
                    if c.is_whitespace() {
                        next += 1;
                    } else {
                        break;
                    }
                }
                start = if next < len {
                    bytes_chars[next].0
                } else {
                    segment.len()
                };
                i = next;
                continue;
            }
        }
        i += 1;
    }
    out.push(segment[start..].to_string());
    out
}

fn extract_set_assignments(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for segment in SEGMENT_SPLIT_RE.split(text) {
        for raw in comma_split_assignment(segment) {
            let item = clean_item(&raw);
            if item.is_empty() {
                continue;
            }
            if !item.contains('=') && !item.contains('：') && !item.contains(':') {
                continue;
            }
            let (path, value) = parse_assignment(&item);
            if !path.is_empty() && !value.is_empty() {
                out.push(format!("{path}={value}"));
            }
        }
    }
    out
}

static LOC_RE_1: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:当前位置|地点|位置)\s*(?:改为|设为|设置为|切到|跳到|在|位于|=|：|:)\s*([^，。！？\n；;]{1,48})",
    )
    .expect("loc regex 1")
});
static LOC_RE_2: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?:现在|当前)\s*(?:在|位于)\s*([^，。！？\n；;]{1,48})")
        .expect("loc regex 2")
});
static LOC_RE_3: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:不在|不是)\s*[^，。！？\n；;]{1,32}[，,；; ]+(?:而是|现在在|应在|改在)\s*([^，。！？\n；;]{1,48})",
    )
    .expect("loc regex 3")
});

fn extract_location_override(text: &str) -> Option<String> {
    for re in [&*LOC_RE_1, &*LOC_RE_2, &*LOC_RE_3] {
        if let Some(c) = re.captures(text) {
            if let Some(m) = c.get(1) {
                let v = clean_item(m.as_str());
                if !v.is_empty() {
                    return Some(v);
                }
            }
        }
    }
    None
}

static TIME_RE_1: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:当前时间线|时间线|当前时间|时间|时点)\s*(?:改为|设为|设置为|锁定为|=|：|:)\s*([^，,。！？\n；;]{2,80})",
    )
    .expect("time regex 1")
});
static TIME_RE_2: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:设置|设定|设|锁定|改|更改|更新|切换|切换到|跳转到|切到)\s*(?:当前时间线|时间线|当前时间|时间|时点)\s*(?:为|到|至|改为|设为|=|：|:)\s*([^，,。！？\n；;]{2,80})",
    )
    .expect("time regex 2")
});

/// 同 Python `_extract_set_time_targets` — 显式时间设置 + 时间跳跃指令。
fn extract_set_time_targets(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    // 1) 自然语言隐式跳跃
    for t in detect_time_directives_for_player(text) {
        if !out.contains(&t) {
            out.push(t);
        }
    }
    // 2) 显式 /set 时间设置
    for re in [&*TIME_RE_1, &*TIME_RE_2] {
        for caps in re.captures_iter(text) {
            if let Some(m) = caps.get(1) {
                let v = clean_time_value(m.as_str());
                let len = v.chars().count();
                if (2..=80).contains(&len) && !out.contains(&v) {
                    out.push(v);
                }
            }
        }
    }
    out
}

// 玩家自然语言时间跳跃 — 同 timeline_state.detect_time_directives
static TIME_DIR_RE_1: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:时间线|时间|剧情|镜头|场景)?\s*(?:跳到|跳转到|快进到|切到|来到|推进到|过渡到|直接到|直接进入|进入|等到|等至|直到|跳过到|略过到|越过到)\s*([^，。！？\n]{2,48})",
    )
    .expect("time dir regex 1")
});
static TIME_DIR_RE_2: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?:/time|/timeline)\s+([^\n]{2,80})")
        .expect("time dir regex 2")
});
static TIME_DIR_RE_3: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:跳到|跳转到|快进到|切到|来到|进入)?\s*(第\s*\d{1,5}\s*章[^，。！？\n]{0,24})",
    )
    .expect("time dir regex 3")
});
static TIME_DIR_RE_4: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:跳到|跳转到|快进到|切到|来到|进入)?\s*((?:公元)?\d{3,5}\s*年[^，。！？\n]{0,24})",
    )
    .expect("time dir regex 4")
});

fn detect_time_directives_for_player(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for re in [
        &*TIME_DIR_RE_1,
        &*TIME_DIR_RE_2,
        &*TIME_DIR_RE_3,
        &*TIME_DIR_RE_4,
    ] {
        for caps in re.captures_iter(text) {
            if let Some(m) = caps.get(1) {
                let v = clean_time_value(m.as_str());
                if looks_like_time_value(&v) && !out.contains(&v) {
                    out.push(v);
                }
            }
        }
    }
    out
}

// ─────────────────────────────────────────────────────────────
// 副作用 helpers
// ─────────────────────────────────────────────────────────────

fn update_location(state: &mut GameState, loc: &str) {
    if let Some(root) = state.data.as_object_mut() {
        if !root.get("player").map(Value::is_object).unwrap_or(false) {
            root.insert("player".to_string(), Value::Object(serde_json::Map::new()));
        }
        if let Some(player) = root.get_mut("player").and_then(Value::as_object_mut) {
            player.insert(
                "current_location".to_string(),
                Value::String(loc.to_string()),
            );
        }
    }
    state.touch();
}

fn set_user_variable(state: &mut GameState, key: &str, value: &str, source: &str) -> bool {
    let key = clean_item(key);
    let value = clean_item(value);
    if key.is_empty() || value.is_empty() {
        return false;
    }
    let turn = state.turn();
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data object");
    if !root
        .get("worldline")
        .map(Value::is_object)
        .unwrap_or(false)
    {
        root.insert("worldline".to_string(), Value::Object(serde_json::Map::new()));
    }
    let worldline = root
        .get_mut("worldline")
        .and_then(Value::as_object_mut)
        .expect("worldline object");
    if !worldline
        .get("user_variables")
        .map(Value::is_object)
        .unwrap_or(false)
    {
        worldline.insert("user_variables".to_string(), Value::Object(serde_json::Map::new()));
    }
    let vars = worldline
        .get_mut("user_variables")
        .and_then(Value::as_object_mut)
        .expect("user_variables object");
    let old_value = vars
        .get(&key)
        .and_then(|v| v.get("value"))
        .and_then(Value::as_str)
        .map(|s| s.to_string());
    vars.insert(
        key.clone(),
        json!({
            "value": value,
            "source": source,
            "locked": true,
            "turn": turn,
            "updated_at": Utc::now().to_rfc3339(),
        }),
    );
    state.touch();
    old_value.map(|s| s != value).unwrap_or(true)
}

fn next_user_variable_key(state: &GameState) -> String {
    let turn = state.turn();
    let count = state
        .data
        .get("worldline")
        .and_then(|w| w.get("user_variables"))
        .and_then(Value::as_object)
        .map(|m| m.len())
        .unwrap_or(0);
    format!("set_{}_{}", turn + 1, count + 1)
}

fn push_pinned_memory(state: &mut GameState, text: &str) -> bool {
    let cleaned = clean_item(text);
    if cleaned.is_empty() {
        return false;
    }
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data object");
    if !root.get("memory").map(Value::is_object).unwrap_or(false) {
        root.insert("memory".to_string(), Value::Object(serde_json::Map::new()));
    }
    let memory = root
        .get_mut("memory")
        .and_then(Value::as_object_mut)
        .expect("memory object");
    if !memory.get("pinned").map(Value::is_array).unwrap_or(false) {
        memory.insert("pinned".to_string(), Value::Array(Vec::new()));
    }
    let pinned = memory
        .get_mut("pinned")
        .and_then(Value::as_array_mut)
        .expect("pinned array");
    let already = pinned
        .iter()
        .any(|v| v.as_str().map(|s| s == cleaned).unwrap_or(false));
    if already {
        return false;
    }
    pinned.push(Value::String(cleaned));
    state.touch();
    true
}

fn clear_revealed_flag(state: &mut GameState) {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data object");
    if !root
        .get("player_private")
        .map(Value::is_object)
        .unwrap_or(false)
    {
        root.insert(
            "player_private".to_string(),
            Value::Object(serde_json::Map::new()),
        );
    }
    let pp = root
        .get_mut("player_private")
        .and_then(Value::as_object_mut)
        .expect("player_private object");
    if !pp.get("flags").map(Value::is_object).unwrap_or(false) {
        pp.insert("flags".to_string(), Value::Object(serde_json::Map::new()));
    }
    if let Some(flags) = pp.get_mut("flags").and_then(Value::as_object_mut) {
        if flags.contains_key("revealed_this_turn") {
            flags.insert(
                "revealed_this_turn".to_string(),
                Value::String(String::new()),
            );
        }
    }
}

fn apply_reveal(state: &mut GameState, reveal_text: &str) {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data object");
    if !root
        .get("player_private")
        .map(Value::is_object)
        .unwrap_or(false)
    {
        root.insert(
            "player_private".to_string(),
            Value::Object(serde_json::Map::new()),
        );
    }
    let pp = root
        .get_mut("player_private")
        .and_then(Value::as_object_mut)
        .expect("player_private object");
    if !pp.get("flags").map(Value::is_object).unwrap_or(false) {
        pp.insert("flags".to_string(), Value::Object(serde_json::Map::new()));
    }
    if let Some(flags) = pp.get_mut("flags").and_then(Value::as_object_mut) {
        flags.insert(
            "revealed_this_turn".to_string(),
            Value::String(reveal_text.to_string()),
        );
    }
    if !pp.get("secrets").map(Value::is_array).unwrap_or(false) {
        pp.insert("secrets".to_string(), Value::Array(Vec::new()));
    }
    if let Some(secrets) = pp.get_mut("secrets").and_then(Value::as_array_mut) {
        let already = secrets
            .iter()
            .any(|v| v.as_str().map(|s| s == reveal_text).unwrap_or(false));
        if !already {
            secrets.push(Value::String(reveal_text.to_string()));
        }
    }
    state.touch();
}

fn write_last_structured_updates(state: &mut GameState, updates: &[String]) {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data object");
    if !root.get("memory").map(Value::is_object).unwrap_or(false) {
        root.insert("memory".to_string(), Value::Object(serde_json::Map::new()));
    }
    if let Some(memory) = root.get_mut("memory").and_then(Value::as_object_mut) {
        let tail: Vec<Value> = updates
            .iter()
            .rev()
            .take(12)
            .rev()
            .map(|s| Value::String(s.clone()))
            .collect();
        memory.insert("last_structured_updates".to_string(), Value::Array(tail));
    }
}

// ─────────────────────────────────────────────────────────────
// 文本解析(对应 parsers.py)
// ─────────────────────────────────────────────────────────────

fn clean_item(text: &str) -> String {
    let trimmed = text
        .trim_matches(|c: char| c.is_whitespace() || matches!(c, ':' | '：' | '-' | '—'));
    let mut out = String::with_capacity(trimmed.len());
    let mut prev_ws = false;
    for c in trimmed.chars() {
        if c.is_whitespace() {
            if !prev_ws {
                out.push(' ');
                prev_ws = true;
            }
        } else {
            out.push(c);
            prev_ws = false;
        }
    }
    out.trim().to_string()
}

/// 对应 Python `_parse_assignment`:依次尝试 `+= = ：:` 切分。
/// 返回 (path, value)。无分隔符返回 ("", text)。
pub fn parse_assignment(text: &str) -> (String, String) {
    let cleaned = clean_item(text);
    for sep in ["+=", "=", "：", ":"] {
        if let Some(pos) = cleaned.find(sep) {
            let (l, r) = cleaned.split_at(pos);
            let right = &r[sep.len()..];
            let path = crate::path::clean_path(l);
            return (path, clean_item(right));
        }
    }
    (String::new(), cleaned)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_comma_split_keeps_in_value() {
        // 关键场景:"x=1,y=2,3,4" → ["x=1", "y=2,3,4"]
        // 第一个 `,` 后面 32 字符内有 `=`(y=2),所以切;
        // 第二个 `,` 后面 32 字符内没有 `=`/`:`/`：`,所以不切,值原样保留。
        let segs = comma_split_assignment("x=1,y=2,3,4");
        assert_eq!(segs, vec!["x=1".to_string(), "y=2,3,4".to_string()]);
    }

    #[test]
    fn test_comma_split_chinese_comma() {
        // 中文逗号同样工作
        let segs = comma_split_assignment("a=1,b:2,c=3");
        assert_eq!(
            segs,
            vec!["a=1".to_string(), "b:2".to_string(), "c=3".to_string()]
        );
    }

    #[test]
    fn test_comma_split_no_assignment_no_split() {
        // 整段没有任何 = / : / : 就不切
        let segs = comma_split_assignment("一句话,无赋值,继续");
        assert_eq!(segs, vec!["一句话,无赋值,继续".to_string()]);
    }

    #[test]
    fn test_comma_split_terminator_stops_lookahead() {
        // 逗号后第一个非空白就是终止符 → 不切
        let segs = comma_split_assignment("x=1,。后面 y=2");
        // 第一个逗号后立刻是 `。`(段终止符),不切
        assert_eq!(segs, vec!["x=1,。后面 y=2".to_string()]);
    }

    #[test]
    fn test_extract_set_assignments_via_state_machine() {
        // 端到端:extract_set_assignments 走的就是新状态机;
        // 中文别名 `姓名` → `player.name` 由 clean_path 处理。
        let out = extract_set_assignments("姓名=Alice, age=18, hobby=吃 喝 玩");
        assert_eq!(
            out,
            vec![
                "player.name=Alice".to_string(),
                "age=18".to_string(),
                "hobby=吃 喝 玩".to_string(),
            ]
        );
    }
}
