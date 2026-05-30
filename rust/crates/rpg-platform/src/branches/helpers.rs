//! 文本 / 状态文件 / snapshot 纯函数 helper(对应 Python `_helpers.py`)。
//!
//! 全部翻译完成,除 IO 路径相关(BRANCH_STATE_DIR)用 `state_dir()`(env-driven)封装。

use std::path::{Path, PathBuf};

use once_cell::sync::Lazy;
use regex::Regex;
use rpg_schemas::GameStateData;
use serde_json::{json, Value};

/// `refs/heads/main`(默认 ref 名)。
pub const MAIN_REF: &str = "refs/heads/main";

static RE_BOOK: Lazy<Regex> = Lazy::new(|| Regex::new(r"【[^】]*】").unwrap());
static RE_MD: Lazy<Regex> = Lazy::new(|| Regex::new(r"[*_#>`]+").unwrap());
static RE_SPLIT_CLAUSE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[。！？!?；;\n]").unwrap());
static RE_NORMALIZE_CONTINUE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[\s。！？!?,，、（）()]+").unwrap());
static RE_TRIM_OPENER: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^(我好像|我想要|我想|我要|我把|我先|我)").unwrap());

/// 默认 branch 状态目录(本地 file backend 用),对应 Python:
/// `<repo_root>/platform_data/branch_states`。
pub fn state_dir() -> PathBuf {
    let base = std::env::var("RPG_PLATFORM_DATA_DIR")
        .ok()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("./platform_data"));
    base.join("branch_states")
}

/// Python `compact(text, limit=120)`。
pub fn compact(text: &str, limit: usize) -> String {
    let joined: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if joined.chars().count() <= limit {
        joined
    } else {
        let truncated: String = joined.chars().take(limit.saturating_sub(1)).collect();
        format!("{}...", truncated)
    }
}

/// Python `clean_text(text)`。
pub fn clean_text(text: &str) -> String {
    let s = RE_BOOK.replace_all(text, " ").into_owned();
    let s = RE_MD.replace_all(&s, " ").into_owned();
    let s = s
        .replace("“", "")
        .replace("”", "")
        .replace("「", "")
        .replace("」", "")
        .replace("（", " ")
        .replace("）", " ")
        .replace("(", " ")
        .replace(")", " ");
    s.split_whitespace().collect::<Vec<_>>().join(" ").trim().to_string()
}

/// Python `first_clause(text)`。
pub fn first_clause(text: &str) -> String {
    for part in RE_SPLIT_CLAUSE.split(text) {
        let trimmed = part.trim_matches(|c: char| " ，、：:,.".contains(c)).to_string();
        if !trimmed.is_empty() {
            return trimmed;
        }
    }
    text.to_string()
}

/// Python `is_continue(text)`。
pub fn is_continue(text: &str) -> bool {
    let normalized = RE_NORMALIZE_CONTINUE.replace_all(text, "").into_owned();
    matches!(normalized.as_str(), "继续" | "续" | "接着" | "下一步")
}

/// Python `round_preview(player_text, gm_text, limit=260)`。
pub fn round_preview(player_text: &str, gm_text: &str, limit: usize) -> String {
    let mut parts: Vec<String> = Vec::new();
    let player = clean_text(player_text);
    let gm = clean_text(gm_text);
    if !player.is_empty() {
        parts.push(format!("玩家:{}", compact(&player, 90)));
    }
    if !gm.is_empty() {
        parts.push(format!("GM:{}", compact(&gm, 170)));
    }
    let joined = if parts.is_empty() {
        "空回合".to_string()
    } else {
        parts.join(" / ")
    };
    compact(&joined, limit)
}

/// Python `rough_summary(player_text, gm_text="", limit=22)`。
pub fn rough_summary(player_text: &str, gm_text: &str, limit: usize) -> String {
    let player = clean_text(player_text);
    let gm = clean_text(gm_text);
    let mut source = player.clone();
    if is_continue(&player) {
        source = if gm.is_empty() { "继续当前剧情".into() } else { gm.clone() };
    } else if source.chars().count() <= 2
        && !gm.is_empty() {
            source = gm.clone();
        }
    if source.is_empty() {
        source = "空回合".to_string();
    }
    source = first_clause(&source);
    source = RE_TRIM_OPENER.replace(&source, "").into_owned();
    source = source
        .trim_matches(|c: char| " ，。！？；:、,.!?;:-".contains(c))
        .to_string();
    if source.chars().count() <= limit {
        source
    } else {
        source.chars().take(limit).collect()
    }
}

/// Python `load_state(path)`(失败返回空 state)。
pub fn load_state(path: &Path) -> Value {
    match std::fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_else(|_| empty_state()),
        Err(_) => empty_state(),
    }
}

/// `{"history": [], "turn": 0}`。
pub fn empty_state() -> Value {
    json!({ "history": [], "turn": 0 })
}

/// Python `commit_state(row)` — 把 commit 行还原成完整 state(snapshot 优先,fallback 文件)。
pub fn commit_state(state_snapshot: Option<&Value>, state_path: &str) -> Value {
    if let Some(snap) = state_snapshot {
        if snap.is_object() && !snap.as_object().map(|o| o.is_empty()).unwrap_or(true) {
            return snap.clone();
        }
    }
    if !state_path.is_empty() {
        return load_state(Path::new(state_path));
    }
    empty_state()
}

/// Python `_snapshot_quality(state)` — 决定 root snapshot 的"信息量"。
pub fn snapshot_quality(state: &Value) -> i64 {
    let obj = match state.as_object() {
        Some(o) => o,
        None => return 0,
    };
    let player_named = obj
        .get("player")
        .and_then(|v| v.as_object())
        .and_then(|p| p.get("name"))
        .and_then(|n| n.as_str())
        .map(|s| !s.is_empty())
        .unwrap_or(false);
    let history_len = obj
        .get("history")
        .and_then(|v| v.as_array())
        .map(|a| a.len() as i64)
        .unwrap_or(0);
    let turn = obj.get("turn").and_then(|v| v.as_i64()).unwrap_or(0);
    history_len * 10 + turn + if player_named { 10 } else { 0 }
}

/// Python `snapshot_for_history(data, history_len)` — 截短 history 后写新 snapshot。
pub fn snapshot_for_history(data: &GameStateData, history_len: usize) -> Value {
    // 序列化为 Value,截短 history 后返回 — 输出层合法序列化
    let mut snap = serde_json::to_value(data).unwrap_or_else(|_| json!({}));
    if let Some(obj) = snap.as_object_mut() {
        let truncated = obj
            .get("history")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().take(history_len).cloned().collect::<Vec<_>>())
            .unwrap_or_default();
        obj.insert("history".to_string(), Value::Array(truncated));
        obj.insert("turn".to_string(), Value::from((history_len / 2) as i64));
    }
    snap
}

/// Python `write_snapshot(save_id, index, data)` — 写到
/// `<state_dir>/save_<save>_commit_seed_<index>.json`,返回路径。
pub fn write_snapshot(save_id: i64, index: usize, data: &GameStateData) -> std::io::Result<String> {
    let dir = state_dir();
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(format!("save_{save_id}_commit_seed_{index}.json"));
    // 序列化为 JSON 字符串写文件 — 输出层合法序列化
    let json_str = serde_json::to_string_pretty(data)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    std::fs::write(&path, json_str)?;
    Ok(path.to_string_lossy().into_owned())
}

/// Python `write_runtime_snapshot(save_id, data)`。
pub fn write_runtime_snapshot(save_id: i64, data: &GameStateData) -> std::io::Result<String> {
    let dir = state_dir();
    std::fs::create_dir_all(&dir)?;
    let turn = (data.turn as i64).max(0);
    let hex = random_hex(4);
    let path = dir.join(format!("save_{save_id}_runtime_turn_{turn}_{hex}.json"));
    let json_str = serde_json::to_string_pretty(data)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    std::fs::write(&path, json_str)?;
    Ok(path.to_string_lossy().into_owned())
}

/// Python `copy_state(source_path, save_id, label)`。
pub fn copy_state(source_path: &str, save_id: i64, label: &str) -> std::io::Result<String> {
    let dir = state_dir();
    std::fs::create_dir_all(&dir)?;
    let hex = random_hex(4);
    let target = dir.join(format!("save_{save_id}_{label}_{hex}.json"));
    let source = Path::new(source_path);
    if source.exists() {
        std::fs::copy(source, &target)?;
    } else {
        std::fs::write(&target, serde_json::to_string_pretty(&empty_state())?)?;
    }
    Ok(target.to_string_lossy().into_owned())
}

/// Python `write_named_snapshot(save_id, label, data)`。
pub fn write_named_snapshot(save_id: i64, label: &str, data: &GameStateData) -> std::io::Result<String> {
    let dir = state_dir();
    std::fs::create_dir_all(&dir)?;
    let hex = random_hex(4);
    let target = dir.join(format!("save_{save_id}_{label}_{hex}.json"));
    let json_str = serde_json::to_string_pretty(data)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    std::fs::write(&target, json_str)?;
    Ok(target.to_string_lossy().into_owned())
}

/// Python `_unlink_branch_state(path)` — 必须在 state_dir 下才删,防越界。
pub fn unlink_branch_state(path: &str) {
    if path.is_empty() {
        return;
    }
    let root = match state_dir().canonicalize() {
        Ok(p) => p,
        Err(_) => return,
    };
    if let Ok(state_path) = Path::new(path).canonicalize() {
        if state_path.starts_with(&root) {
            let _ = std::fs::remove_file(state_path);
        }
    }
}

fn random_hex(n_bytes: usize) -> String {
    let mut buf = vec![0u8; n_bytes];
    rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut buf);
    hex::encode(&buf)
}

// display_nodes(rows) —— 前端负责 round 合并展示。
// Python 版 ~85 行的 player/gm → round 节点合并逻辑由前端 JS 承担,后端只返回原始节点列表。

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cont() {
        assert!(is_continue("继续"));
        assert!(is_continue("继 续"));
        assert!(!is_continue("不行"));
    }
    #[test]
    fn clean() {
        assert_eq!(clean_text("【系统】你**好**"), "你 好");
    }
}
