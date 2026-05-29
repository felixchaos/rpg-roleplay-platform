//! consume — 物品消耗与短休。
//! 对应 Python: rpg/rules_bridge/consume.py

use serde_json::{json, Value};
use rpg_rules::dnd5e::actions;
use rpg_schemas::GameStateData;
use crate::error::BridgeError;
use crate::combat::sync_player_combatant;

// ── 中/英文消耗动词 ────────────────────────────────────────────────────────
const CONSUME_VERBS_CN: &[&str] = &[
    "点燃", "使用", "消耗", "用掉", "喝", "饮", "服下", "服用",
    "吃", "用上", "用一", "拿出", "点亮", "拿来",
];
const CONSUME_VERBS_EN: &[&str] = &[
    "use", "consume", "burn", "light", "drink", "eat", "spend",
];

/// 中文数字 → i32
fn zh_numeral_to_int(ch: char) -> Option<i32> {
    match ch {
        '一' => Some(1), '二' => Some(2), '两' => Some(2), '三' => Some(3),
        '四' => Some(4), '五' => Some(5), '六' => Some(6), '七' => Some(7),
        '八' => Some(8), '九' => Some(9), '十' => Some(10), '零' => Some(0),
        _ => None,
    }
}

/// ConsumeIntent — 从玩家文本里抽取的消耗意图
#[derive(Debug, Clone, serde::Serialize)]
pub struct ConsumeIntent {
    pub item_id: String,
    pub qty: i32,
    pub matched: String,
}

/// 简化版 parse_consume_intent：
/// 定位每个消耗动词，在动词后 24 字符窗口内找 inventory key，
/// 解析数字量词（默认 1）。
/// 不依赖 _ITEM_ALIASES，直接扫 inventory keys。
pub fn parse_consume_intent(text: &str, inventory: &Value) -> Vec<ConsumeIntent> {
    if text.is_empty() {
        return vec![];
    }
    let inv_keys: Vec<String> = inventory
        .as_object()
        .map(|m| m.keys().cloned().collect())
        .unwrap_or_default();
    if inv_keys.is_empty() {
        return vec![];
    }
    // 按长度降序，防止短 key 遮蔽
    let mut keys_sorted = inv_keys.clone();
    keys_sorted.sort_by_key(|b| std::cmp::Reverse(b.len()));

    let all_verbs: Vec<&str> = CONSUME_VERBS_CN.iter().chain(CONSUME_VERBS_EN.iter()).copied().collect();
    let mut out: Vec<ConsumeIntent> = vec![];
    let text_lower = text.to_lowercase();

    for verb in &all_verbs {
        let search_in = text_lower.as_str();
        let mut start = 0;
        while let Some(pos) = search_in[start..].find(verb) {
            let verb_end = start + pos + verb.len();
            let window_end = (verb_end + 24).min(text.len());
            let window = &text_lower[verb_end..window_end];

            // 找 inventory key
            let mut found_key: Option<String> = None;
            let mut key_offset: Option<usize> = None;
            for key in &keys_sorted {
                if let Some(idx) = window.find(key.to_lowercase().as_str()) {
                    if key_offset.is_none() || idx < key_offset.unwrap() {
                        found_key = Some(key.clone());
                        key_offset = Some(idx);
                    }
                }
            }
            if let (Some(item_id), Some(koff)) = (found_key, key_offset) {
                // 量词解析
                let between = &window[..koff];
                let qty = parse_qty(between);
                let matched = text[start + pos..verb_end + koff + item_id.len()].to_string();
                // 去重
                if !out.iter().any(|x: &ConsumeIntent| x.item_id == item_id) {
                    out.push(ConsumeIntent { item_id, qty, matched });
                }
            }
            start = verb_end;
        }
    }
    out
}

fn parse_qty(between: &str) -> i32 {
    // 先找阿拉伯数字
    let mut num_str = String::new();
    for ch in between.chars() {
        if ch.is_ascii_digit() {
            num_str.push(ch);
        } else if !num_str.is_empty() {
            break;
        }
    }
    if let Ok(n) = num_str.parse::<i32>() {
        if n > 0 { return n; }
    }
    // 再找中文数字
    for ch in between.chars() {
        if let Some(n) = zh_numeral_to_int(ch) {
            if n > 0 { return n; }
        }
    }
    1
}

/// consume_item_action：消耗 inventory 中 item_id 的 qty 件。
/// inventory 结构：`player_character.inventory.<item_id>.qty`。
pub fn consume_item_action(
    data: &mut Value,
    item_id: &str,
    qty: i32,
    reason: &str,
) -> Result<Value, BridgeError> {
    if item_id.is_empty() {
        return Err(BridgeError::Logic("缺少 item_id".into()));
    }
    let item = data["player_character"]["inventory"][item_id].clone();
    if item.is_null() {
        return Err(BridgeError::TargetNotFound(format!("inventory 中不存在：{}", item_id)));
    }
    let qty_before = item["qty"].as_i64().unwrap_or(0) as i32;
    if qty_before < qty {
        return Err(BridgeError::Logic(format!("库存不足：{} (有 {}, 需 {})", item_id, qty_before, qty)));
    }
    let qty_after = qty_before - qty;
    data["player_character"]["inventory"][item_id]["qty"] = json!(qty_after);

    let item_name = item["name"].as_str().unwrap_or(item_id).to_string();
    let actor = data["player_character"]["name"].as_str().unwrap_or("player").to_string();
    let log_reason = if reason.is_empty() {
        format!("消耗 {} ×{}", item_name, qty)
    } else {
        reason.to_string()
    };

    Ok(json!({
        "ok": true,
        "result": {
            "kind": "consume_item",
            "actor": actor,
            "target": item_name,
            "success": true,
            "gm_facts": [format!("{} 消耗 {} ×{}（剩余 {}）。", actor, item_name, qty, qty_after)],
            "extra": {
                "item_id": item_id,
                "qty_before": qty_before,
                "qty_after": qty_after,
            }
        },
        "dice_log_entry": {
            "kind": "consume_item",
            "actor": actor,
            "target": item_name,
            "expression": "",
            "rolls": [],
            "modifier": 0,
            "total": qty,
            "dc": null,
            "success": true,
            "reason": log_reason,
            "extra": {
                "item_id": item_id,
                "qty_before": qty_before,
                "qty_after": qty_after,
            }
        }
    }))
}

/// short_rest：花 1 个生命骰 + CON 修正回血。
pub fn short_rest(data: &mut GameStateData, seed: Option<u64>) -> Result<Value, BridgeError> {
    // 检查房间 flag(current_room 存在 scene.extra 里,属于动态字段)
    let can_rest = data.scene.extra
        .get("current_room")
        .and_then(|cr| cr.get("flags"))
        .and_then(|f| f.get("can_short_rest"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !can_rest {
        return Err(BridgeError::Logic("当前房间不适合短休".into()));
    }

    // rpg-rules actions::short_rest 仍接受 &mut Value,在边界转换
    let mut pc_val = serde_json::to_value(&data.player_character)?;
    let result = actions::short_rest(&mut pc_val, "1d8", seed)?;
    data.player_character = serde_json::from_value(pc_val)
        .map_err(|e| BridgeError::Json(e))?;

    sync_player_combatant(data);
    Ok(serde_json::to_value(&result)?)
}
