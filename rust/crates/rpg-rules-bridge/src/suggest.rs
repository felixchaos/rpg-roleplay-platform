//! suggest — 基于关键词的规则候选动作生成器。
//! 对应 Python: rpg/rules_bridge/suggest.py

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};
use rpg_schemas::GameStateData;
use crate::intent::{direction_to_exit, has_movement_intent};

// ── 意图关键词表 ──────────────────────────────────────────────────────────

/// (pattern, kind, skill?, dc_hint?)
#[derive(Debug)]
#[allow(dead_code)]
struct IntentKeyword {
    pattern: &'static str,
    kind: &'static str,
    skill: Option<&'static str>,
    dc_hint: Option<i32>,
    weapon_hint: Option<&'static str>,
    direction_hint: bool,
}

static INTENT_KEYWORDS: &[IntentKeyword] = &[
    IntentKeyword { pattern: r"(悄悄|潜行|隐蔽|偷偷|不被发现|溜过去)", kind: "skill_check", skill: Some("stealth"),       dc_hint: Some(13), weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(调查|搜查|查看|检查|搜索|翻找)",         kind: "skill_check", skill: Some("investigation"), dc_hint: Some(12), weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(察觉|留意|倾听|听一下|发现|观察)",        kind: "skill_check", skill: Some("perception"),    dc_hint: Some(12), weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(攀爬|爬上|跳过|破门|撞开|蛮力)",         kind: "skill_check", skill: Some("athletics"),     dc_hint: Some(12), weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(说服|谈判|交涉|劝说|投降|求饶|放下武器|举起?双?手|跪下投降|请降|求和)", kind: "skill_check", skill: Some("persuasion"), dc_hint: Some(14), weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(欺骗|撒谎|装作|伪装|装成)",              kind: "skill_check", skill: Some("deception"),     dc_hint: Some(13), weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(挣脱|挣开|挣扎|甩开|摆脱抓握|脱困|逃脱束缚)", kind: "skill_check", skill: Some("athletics"), dc_hint: Some(13), weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(威胁|恐吓|逼问)",                         kind: "skill_check", skill: Some("intimidation"), dc_hint: Some(13), weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(攻击|砍|射|刺|杀|出手|短弓|短剑|远程攻击|近战攻击)", kind: "attack", skill: None, dc_hint: None, weapon_hint: Some("shortsword"), direction_hint: false },
    IntentKeyword { pattern: r"(短休|休息|歇一下)",                        kind: "short_rest",  skill: None, dc_hint: None, weapon_hint: None, direction_hint: false },
    IntentKeyword { pattern: r"(沿|往|向|去|前往|走向|前进|探索|进入)",    kind: "move",        skill: None, dc_hint: None, weapon_hint: None, direction_hint: true  },
];

// 编译后的正则列表（lazy）
static COMPILED_PATTERNS: Lazy<Vec<Regex>> = Lazy::new(|| {
    INTENT_KEYWORDS.iter()
        .map(|kw| Regex::new(kw.pattern).expect("invalid regex"))
        .collect()
});

// ── 辅助 ──────────────────────────────────────────────────────────────────

fn weapon_from_text(text: &str) -> &'static str {
    if ["短弓", "弓", "远程", "射", "箭"].iter().any(|t| text.contains(t)) {
        return "shortbow";
    }
    "shortsword"
}

/// `_triggered_encounter_id` — 对应 Python `suggest.py::_triggered_encounter_id(state)`.
///
/// 加载当前模组的 encounters 列表，按以下优先级匹配：
///   1. encounter.trigger 字段命中 scene.flags 中为 true 的 flag → 返回 encounter.id
///   2. encounter.location_id 匹配 scene.location_id → 返回 encounter.id
///   3. 未命中 → 返回空串
fn triggered_encounter_id(data: &GameStateData) -> String {
    let module_id = data.scene.module_id.as_str();
    if module_id.is_empty() {
        return String::new();
    }
    let bundle = match rpg_rules::load_module(module_id) {
        Ok(b) => b,
        Err(_) => return String::new(),
    };
    let encounters = match bundle.encounters.as_array() {
        Some(arr) => arr,
        None => return String::new(),
    };
    // 活跃 flags 集合（值为 truthy）
    let active_flags: std::collections::HashSet<&str> = data.scene.flags
        .iter()
        .filter_map(|(k, v)| {
            if v.as_bool().unwrap_or(false)
                || (v.is_number() && v.as_f64().unwrap_or(0.0) != 0.0)
            {
                Some(k.as_str())
            } else {
                None
            }
        })
        .collect();

    // 优先级 1：trigger flag 命中
    for enc in encounters.iter() {
        if let Some(trigger) = enc["trigger"].as_str() {
            if active_flags.contains(trigger) {
                return enc["id"].as_str().unwrap_or("").to_string();
            }
        }
    }
    // 优先级 2：location_id 匹配
    let location_id = data.scene.location_id.as_str();
    for enc in encounters.iter() {
        if enc["location_id"].as_str() == Some(location_id) {
            return enc["id"].as_str().unwrap_or("").to_string();
        }
    }
    String::new()
}

// ── 公开 API ──────────────────────────────────────────────────────────────

/// 根据用户输入文本和当前 state.data，生成规则候选动作列表。
pub fn suggest_rule_actions(user_input: &str, data: &GameStateData) -> Vec<Value> {
    if user_input.is_empty() {
        return vec![];
    }
    let text = user_input;
    // current_room 存在 scene.extra 里(动态字段)
    let current_room = data.scene.extra
        .get("current_room")
        .cloned()
        .unwrap_or(Value::Null);
    let location_id = data.scene.location_id.as_str();

    let mut out: Vec<Value> = vec![];

    for (idx, kw) in INTENT_KEYWORDS.iter().enumerate() {
        let re = &COMPILED_PATTERNS[idx];
        if !re.is_match(text) {
            continue;
        }

        let mut action = json!({
            "kind": kw.kind,
            "matched": kw.pattern,
            "reason": format!("匹配关键词「{}」", kw.pattern),
        });

        match kw.kind {
            "skill_check" => {
                let target_skill = kw.skill.unwrap_or("");
                action["skill"] = json!(target_skill);
                action["dc_hint"] = json!(kw.dc_hint.unwrap_or(12));

                // 先在当前房间 checks 里找
                let mut matched_check = false;
                if let Some(checks) = current_room["checks"].as_array() {
                    for chk in checks {
                        if chk["kind"].as_str() == Some("skill_check")
                            && chk["skill"].as_str() == Some(target_skill)
                        {
                            action["dc"] = chk["dc"].clone();
                            action["target"] = json!(location_id);
                            action["sets_flag"] = chk["set_flag"].clone();
                            action["fact"] = chk["fact"].clone();
                            matched_check = true;
                            break;
                        }
                    }
                }
                // 若有移动意图，扫相邻房间 exits，在相邻房间 checks 里找同 skill 的 check。
                // 对应 Python suggest_rule_actions 里的跨房间 fallback 逻辑。
                if !matched_check && has_movement_intent(text) {
                    // 加载模组数据，构造 rooms_by_id 索引
                    let module_id = data.scene.module_id.as_str();
                    if !module_id.is_empty() {
                        if let Ok(bundle) = rpg_rules::load_module(module_id) {
                            // rooms_by_id: id → room Value
                            let rooms_by_id: std::collections::HashMap<&str, &serde_json::Value> =
                                bundle.rooms.as_array()
                                    .map(|arr| {
                                        arr.iter()
                                            .filter_map(|r| r["id"].as_str().map(|id| (id, r)))
                                            .collect()
                                    })
                                    .unwrap_or_default();

                            'outer: for ex in current_room["exits"].as_array().unwrap_or(&vec![]) {
                                if let Some(to_id) = ex["to"].as_str() {
                                    if let Some(room) = rooms_by_id.get(to_id) {
                                        if let Some(checks) = room["checks"].as_array() {
                                            for chk in checks {
                                                if chk["kind"].as_str() == Some("skill_check")
                                                    && chk["skill"].as_str() == Some(target_skill)
                                                {
                                                    action["dc"] = chk["dc"].clone();
                                                    action["target"] = json!(to_id);
                                                    action["move_to"] = json!(to_id);
                                                    action["sets_flag"] = chk["sets_flag"].clone();
                                                    action["fact"] = chk["fact"].clone();
                                                    let room_name = room["name"].as_str()
                                                        .unwrap_or(to_id);
                                                    action["reason"] = json!(format!(
                                                        "{}；目标在相邻房间「{}」",
                                                        action["reason"].as_str().unwrap_or(""),
                                                        room_name
                                                    ));
                                                    break 'outer;
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                action.as_object_mut().unwrap().entry("dc").or_insert(json!(kw.dc_hint.unwrap_or(12)));
                if action["target"].is_null() {
                    action["target"] = json!(location_id);
                }
            }
            "attack" => {
                action["weapon"] = json!(weapon_from_text(text));
                if data.encounter.active {
                    let enemies: Vec<&Value> = data.encounter.combatants.iter()
                        .filter(|c| c["side"].as_str() == Some("enemy") && c["defeated"].as_bool() != Some(true))
                        .collect();
                    if let Some(first) = enemies.first() {
                        action["target"] = first["id"].clone();
                        action["target_name"] = first["name"].clone();
                    }
                }
                // _triggered_encounter_id — 对应 Python suggest.py::_triggered_encounter_id()。
                // 优先找 flags 里命中 trigger 字段的 encounter；
                // 其次找 location_id 对应的 encounter。
                let enc_id = triggered_encounter_id(data);
                if !enc_id.is_empty() {
                    action["encounter_id"] = json!(enc_id);
                }
            }
            "move" => {
                // 方向词解析
                if let Some(exit_id) = direction_to_exit(text, &current_room) {
                    action["to"] = json!(exit_id);
                    action["target"] = json!(exit_id);
                    // 补充 exit label
                    if let Some(exits) = current_room["exits"].as_array() {
                        for ex in exits {
                            if ex["to"].as_str() == Some(exit_id) {
                                let label = ex["label"].as_str().unwrap_or(exit_id);
                                action["reason"] = json!(format!("方向词→出口『{}』", label));
                                break;
                            }
                        }
                    }
                } else {
                    // 无法解析出口，跳过
                    continue;
                }
            }
            _ => {}
        }

        out.push(action);
    }

    // 去重（按 kind+skill+target）
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut deduped: Vec<Value> = vec![];
    for a in out {
        let key = format!(
            "{}|{}|{}",
            a["kind"].as_str().unwrap_or(""),
            a["skill"].as_str().unwrap_or(""),
            a["target"].as_str().unwrap_or(""),
        );
        if seen.insert(key) {
            deduped.push(a);
        }
    }
    deduped
}

// ── 单测 ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rpg_schemas::GameStateData;

    fn make_state() -> GameStateData {
        GameStateData::default()
    }

    /// 空输入 → 空列表
    #[test]
    fn empty_input_returns_empty() {
        let state = make_state();
        let result = suggest_rule_actions("", &state);
        assert!(result.is_empty());
    }

    /// 潜行关键词 → 产生 stealth skill_check
    #[test]
    fn stealth_keyword_matches() {
        let state = make_state();
        let result = suggest_rule_actions("我想悄悄绕过守卫", &state);
        let found = result.iter().any(|a| {
            a["kind"].as_str() == Some("skill_check") && a["skill"].as_str() == Some("stealth")
        });
        assert!(found, "应产生 stealth 动作，实际: {:?}", result);
    }

    /// 攻击关键词 → 产生 attack 动作，weapon 正确
    #[test]
    fn attack_keyword_shortsword() {
        let state = make_state();
        let result = suggest_rule_actions("我用短剑刺向敌人", &state);
        let atk = result.iter().find(|a| a["kind"].as_str() == Some("attack"));
        assert!(atk.is_some(), "应有 attack 动作: {:?}", result);
        assert_eq!(atk.unwrap()["weapon"].as_str(), Some("shortsword"));
    }

    /// 短弓 → weapon = shortbow
    #[test]
    fn attack_keyword_shortbow() {
        let state = make_state();
        let result = suggest_rule_actions("用短弓射击", &state);
        let atk = result.iter().find(|a| a["kind"].as_str() == Some("attack"));
        assert!(atk.is_some());
        assert_eq!(atk.unwrap()["weapon"].as_str(), Some("shortbow"));
    }

    /// 去重：同一 skill_check 只出现一次
    #[test]
    fn dedup_same_skill_target() {
        let state = make_state();
        // "察觉" 和 "留意" 都匹配 perception，target 均为 location_id=""
        let result = suggest_rule_actions("我察觉一下，同时留意周围", &state);
        let perception_count = result.iter()
            .filter(|a| a["skill"].as_str() == Some("perception"))
            .count();
        assert_eq!(perception_count, 1, "同 skill+target 应去重到 1 条: {:?}", result);
    }

    /// triggered_encounter_id：无 module_id → 返回空串
    #[test]
    fn triggered_encounter_id_no_module() {
        let state = make_state();
        let enc_id = triggered_encounter_id(&state);
        assert!(enc_id.is_empty(), "无 module_id 应返回空: {:?}", enc_id);
    }

    /// triggered_encounter_id：module 不存在 → 返回空串（不 panic）
    #[test]
    fn triggered_encounter_id_bad_module() {
        let mut state = make_state();
        state.scene.module_id = "nonexistent_module_xyz".into();
        let enc_id = triggered_encounter_id(&state);
        assert!(enc_id.is_empty(), "未知模组应返回空: {:?}", enc_id);
    }

    /// 短休关键词 → short_rest
    #[test]
    fn short_rest_keyword() {
        let state = make_state();
        let result = suggest_rule_actions("我们歇一下", &state);
        let found = result.iter().any(|a| a["kind"].as_str() == Some("short_rest"));
        assert!(found, "应产生 short_rest 动作: {:?}", result);
    }
}
