//! 轻量工具函数。
//! 对应 Python: rpg/context_engine/_utils.py

use crate::types::Layer;
use serde_json::Value;
use std::collections::HashMap;

/// 对应 Python `_layer()` helper。
pub fn make_layer(id: &str, title: &str, content: &str) -> Layer {
    Layer::new(id, title, content)
}

/// 截断文本到 max_chars 字符。对应 Python `_trim`。
pub fn trim_text(text: &str, max_chars: usize) -> String {
    let trimmed = text.trim();
    let char_count = trimmed.chars().count();
    if char_count <= max_chars {
        return trimmed.to_string();
    }
    let cut: String = trimmed.chars().take(max_chars.saturating_sub(20)).collect();
    let cut = cut.trim_end();
    format!("{}\n……（已按预算截断）", cut)
}

/// 预览文本(把空白压扁,截断到 limit 字符)。对应 Python `_preview`。
pub fn preview(text: &str, limit: usize) -> String {
    let mut result = String::with_capacity(text.len());
    let mut in_ws = false;
    for c in text.chars() {
        if c.is_whitespace() {
            if !in_ws {
                result.push(' ');
                in_ws = true;
            }
        } else {
            in_ws = false;
            result.push(c);
        }
    }
    let trimmed = result.trim();
    let char_count = trimmed.chars().count();
    if char_count <= limit {
        trimmed.to_string()
    } else {
        let cut: String = trimmed.chars().take(limit).collect();
        format!("{}...", cut)
    }
}

/// 简单 token 估算:char_count / 2,最少 1。对应 Python `_estimate_tokens`。
pub fn estimate_tokens(text: &str) -> u32 {
    let n = text.chars().count() as u32 / 2;
    n.max(1)
}

/// MAX_LAYER_CHARS 表。对应 Python `_constants.py` 同名 dict。
pub fn max_layer_chars() -> HashMap<&'static str, usize> {
    HashMap::from([
        ("rules", 1800),
        ("agent_runtime", 1200),
        ("timeline", 1400),
        ("worldline", 1800),
        ("context_agent", 1200),
        ("player_card", 1300),
        ("npc_cards", 1800),
        ("worldbook", 2200),
        ("rag", 2200),
        ("state", 2200),
        ("state_schema", 1600),
        ("write_results", 800),
        ("fact_groups", 1600),
        ("hypotheses", 700),
        ("candidate_actions", 800),
        ("recent_chat", 2200),
        ("user_input", 900),
        ("runtime_phase_digests", 1800),
        ("script_phase_anticipation", 1200),
    ])
}

/// P0 #2:从检索内容里中和【】系列指令标签。对应 Python `_neutralize_state_write_tags`。
pub fn neutralize_state_write_tags(text: &str) -> String {
    text.replace('【', "［").replace('】', "］")
}

/// cache_plan 信息(分析 prompt 中可缓存前缀部分)。对应 Python `_cache_plan`。
/// 注:hash 用 std 的 DefaultHasher 取代 SHA-256(避免引入额外依赖);
/// 等 rpg-llm 真要消费 hash 时,迁到稳定 hash(blake3/sha2)。
pub fn cache_plan(debug_layers: &[Value], prompt_parts: &[String]) -> Value {
    use std::hash::{Hash, Hasher};

    fn short_hash(input: &str) -> String {
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        input.hash(&mut hasher);
        format!("{:016x}", hasher.finish())
    }

    let strict_stable_ids = ["rules", "agent_runtime", "player_card"];
    let semi_stable_ids = ["npc_cards", "worldbook"];

    let mut stable_chars: i64 = 0;
    let mut stable_tokens: i64 = 0;
    let mut stable_titles: Vec<String> = Vec::new();
    let mut semi_chars: i64 = 0;
    let mut semi_tokens: i64 = 0;
    let mut semi_titles: Vec<String> = Vec::new();
    let mut i = 0usize;

    for layer in debug_layers {
        let lid = layer.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let chars = layer.get("chars").and_then(|v| v.as_i64()).unwrap_or(0);
        let tokens = layer.get("estimated_tokens").and_then(|v| v.as_i64()).unwrap_or(0);
        let title = layer
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if i < strict_stable_ids.len() && lid == strict_stable_ids[i] {
            stable_chars += chars;
            stable_tokens += tokens;
            stable_titles.push(title);
            i += 1;
            continue;
        }
        if semi_stable_ids.contains(&lid) && i >= strict_stable_ids.len() {
            semi_chars += chars;
            semi_tokens += tokens;
            semi_titles.push(title);
            continue;
        }
        break;
    }

    let total_tokens: i64 = debug_layers
        .iter()
        .map(|l| l.get("estimated_tokens").and_then(|v| v.as_i64()).unwrap_or(0))
        .sum();

    let joined_stable = prompt_parts
        .iter()
        .take(stable_titles.len())
        .cloned()
        .collect::<Vec<_>>()
        .join("\n\n");

    let extended_titles: Vec<String> =
        stable_titles.iter().chain(semi_titles.iter()).cloned().collect();
    let extended_chars = stable_chars + semi_chars;
    let extended_tokens = stable_tokens + semi_tokens;
    let joined_extended = prompt_parts
        .iter()
        .take(extended_titles.len())
        .cloned()
        .collect::<Vec<_>>()
        .join("\n\n");

    let stable_hash = if joined_stable.is_empty() {
        String::new()
    } else {
        short_hash(&joined_stable)
    };
    let ext_hash = if joined_extended.is_empty() {
        String::new()
    } else {
        short_hash(&joined_extended)
    };

    let cacheable_ratio = if total_tokens > 0 {
        (extended_tokens as f64 / total_tokens as f64 * 1000.0).round() / 1000.0
    } else {
        0.0
    };
    let strict_ratio = if total_tokens > 0 {
        (stable_tokens as f64 / total_tokens as f64 * 1000.0).round() / 1000.0
    } else {
        0.0
    };

    serde_json::json!({
        "strategy": "stable-prefix-first",
        "request_shape": "rules -> agent_runtime -> player_card -> (npc/world) -> dynamic -> user_input",
        "stable_prefix_layers": stable_titles,
        "stable_prefix_chars": stable_chars,
        "stable_prefix_tokens": stable_tokens,
        "cacheable_prefix_layers": extended_titles,
        "cacheable_prefix_chars": extended_chars,
        "cacheable_prefix_tokens": extended_tokens,
        "volatile_tail_tokens": (total_tokens - extended_tokens).max(0),
        "estimated_cacheable_ratio": cacheable_ratio,
        "strict_stable_ratio": strict_ratio,
        "stable_prefix_hash": stable_hash,
        "cacheable_prefix_hash": ext_hash,
        "note": "真实缓存命中率由模型厂商返回的用量字段确认；当前请求形状把动态 RAG/context_agent/recent_chat 都放到末尾。",
    })
}
