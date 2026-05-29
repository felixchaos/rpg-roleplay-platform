//! suggest — 基于关键词的规则候选动作生成器。
//! 对应 Python: rpg/rules_bridge/suggest.py

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};
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

// ── 公开 API ──────────────────────────────────────────────────────────────

/// 根据用户输入文本和当前 state.data，生成规则候选动作列表。
pub fn suggest_rule_actions(user_input: &str, data: &Value) -> Vec<Value> {
    if user_input.is_empty() {
        return vec![];
    }
    let text = user_input;
    let scene = &data["scene"];
    let current_room = &scene["current_room"];
    let location_id = scene["location_id"].as_str().unwrap_or("");

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
                // 若有移动意图，扫相邻房间（TODO：rooms_by_id 目前不可用，留空）
                if !matched_check && has_movement_intent(text) {
                    // TODO: 跨房间 check 扫描（需模组数据加载接口）
                }
                action.as_object_mut().unwrap().entry("dc").or_insert(json!(kw.dc_hint.unwrap_or(12)));
                if action["target"].is_null() {
                    action["target"] = json!(location_id);
                }
            }
            "attack" => {
                action["weapon"] = json!(weapon_from_text(text));
                let enc = &data["encounter"];
                if enc["active"].as_bool().unwrap_or(false) {
                    let empty = vec![];
                    let enemies: Vec<&Value> = enc["combatants"].as_array().unwrap_or(&empty)
                        .iter()
                        .filter(|c| c["side"].as_str() == Some("enemy") && c["defeated"].as_bool() != Some(true))
                        .collect();
                    if let Some(first) = enemies.first() {
                        action["target"] = first["id"].clone();
                        action["target_name"] = first["name"].clone();
                    }
                }
                // TODO: _triggered_encounter_id（需模组数据加载接口）
            }
            "move" => {
                // 方向词解析
                if let Some(exit_id) = direction_to_exit(text, current_room) {
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
