//! intent — 战斗意图分类器（deterministic，不依赖 LLM）。
//! 对应 Python: rpg/rules_bridge/intent.py

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};

// ── 词表 ──────────────────────────────────────────────────────────────────

static MOVEMENT_VERBS: &[&str] = &[
    "靠近", "前往", "走向", "走到", "走过", "穿过", "翻过", "回到", "进入", "退回",
    "去", "沿", "往", "向", "通过", "潜入", "溜过去", "钻进", "上到", "下到",
];

static ATTACK_PHRASES: &[&str] = &[
    "攻击", "射击", "射杀", "袭击", "开火", "放箭", "出手攻击", "突袭",
];

static ATTACK_SOFT_VERBS: &[&str] = &[
    "射", "放", "瞄准", "拉弓", "扣弦", "投掷", "掷", "扔",
    "砍", "刺", "戳", "杀", "斩",
];

static RANGED_WEAPON_HINTS: &[&str] = &[
    "短弓", "长弓", "弩", "弓箭", "弓", "箭", "标枪", "飞刀", "远程",
];

static MELEE_WEAPON_HINTS: &[&str] = &[
    "短剑", "长剑", "匕首", "战斧", "战锤", "短棍", "近战", "肉搏",
];

static DISENGAGE_HINTS: &[&str] = &[
    "脱离", "脱身", "Disengage", "解除接触", "解开接触",
];

static DODGE_HINTS: &[&str] = &[
    "闪避", "防御姿态", "Dodge", "招架",
];

static MOVE_AWAY_HINTS: &[&str] = &[
    "拉开距离", "拉远距离", "拉远", "保持距离",
    "后退", "退后", "退开", "退一步", "退两步", "向后", "往后",
    "远离", "撤离", "撤退", "脱身",
];

/// 方向 exit 解析用的 CJK Token 正则
static CJK_TOKEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"[一-鿿]{2,}").unwrap()
});

// ── 辅助函数 ──────────────────────────────────────────────────────────────

fn has_any(text: &str, needles: &[&str]) -> bool {
    needles.iter().any(|n| text.contains(n))
}

/// 是否包含移动意图动词
pub fn has_movement_intent(text: &str) -> bool {
    has_any(text, MOVEMENT_VERBS)
}

fn detect_attack_verb(text: &str) -> bool {
    if has_any(text, ATTACK_PHRASES) {
        return true;
    }
    has_any(text, ATTACK_SOFT_VERBS)
        && (has_any(text, RANGED_WEAPON_HINTS) || has_any(text, MELEE_WEAPON_HINTS))
}

fn detect_ranged(text: &str) -> bool { has_any(text, RANGED_WEAPON_HINTS) }
fn detect_melee(text: &str) -> bool  { has_any(text, MELEE_WEAPON_HINTS)  }
fn detect_move_away(text: &str) -> bool { has_any(text, MOVE_AWAY_HINTS)  }
fn detect_disengage(text: &str) -> bool { has_any(text, DISENGAGE_HINTS)  }

/// 把玩家自然语言移动意图解析为当前房间真实 exit.to。
/// 优先全词匹配 exit.label / to，再做 CJK token 模糊匹配。
pub fn direction_to_exit<'a>(text: &str, current_room: &'a Value) -> Option<&'a str> {
    let exits = current_room["exits"].as_array()?;
    if exits.is_empty() {
        return None;
    }
    let text_lower = text.to_lowercase();

    struct Best<'x> {
        id: Option<&'x str>,
        score: i32,
    }
    let mut best = Best { id: None, score: 0 };

    for ex in exits {
        let to_id = ex["to"].as_str().unwrap_or("");
        let label  = ex["label"].as_str().unwrap_or("");
        let mut score: i32 = 0;

        // CJK token 匹配 label
        for cap in CJK_TOKEN_RE.find_iter(label) {
            if text.contains(cap.as_str()) {
                score += 3;
            }
        }
        // 方向词
        for (dir, kws) in [
            ("东", vec!["东"]), ("西", vec!["西"]), ("北", vec!["北"]), ("南", vec!["南"]),
            ("下", vec!["下", "降"]), ("上", vec!["上", "升"]),
        ] {
            if text.contains(dir) && kws.iter().any(|kw| label.contains(kw)) {
                score += 2;
            }
        }
        // 英文 to_id
        if !to_id.is_empty() && text_lower.contains(&to_id.to_lowercase()) {
            score += 5;
        }
        if score > best.score {
            best.score = score;
            best.id = Some(to_id);
        }
    }

    if best.score >= 2 { best.id } else { None }
}

// ── 主分类器 ─────────────────────────────────────────────────────────────

/// `classify_combat_intent` — deterministic 战斗意图分类。
///
/// 返回 `None` = 非战斗意图，正常走 GM。
/// 返回 `Some(Value)` = 阻挡块，含 `kind` / `question` / `options`。
pub fn classify_combat_intent(text: &str, data: &Value) -> Option<Value> {
    if text.is_empty() {
        return None;
    }

    // 只对模组场景生效
    let scene = &data["scene"];
    if scene["module_id"].as_str().map(|s| s.is_empty()).unwrap_or(true) {
        return None;
    }

    let has_attack   = detect_attack_verb(text);
    let has_ranged   = detect_ranged(text);
    let has_melee    = detect_melee(text);
    let has_move_away = detect_move_away(text);
    let has_disengage = detect_disengage(text);
    let _has_dodge   = has_any(text, DODGE_HINTS);

    if !(has_attack || has_ranged || has_melee || has_move_away) {
        return None;
    }

    let enc = &data["encounter"];
    let encounter_active = enc["active"].as_bool().unwrap_or(false);
    let empty = vec![];
    let combatants = enc["combatants"].as_array().unwrap_or(&empty);
    let live_enemies: Vec<&Value> = combatants.iter()
        .filter(|c| c["side"].as_str() == Some("enemy") && c["defeated"].as_bool() != Some(true))
        .collect();

    let room_enemies = scene["current_room"]["enemies"].as_array()
        .map(|a| a.len())
        .unwrap_or(0);

    // case 1: 想战斗但没有合法敌人
    let wants_combat = has_attack || has_ranged || has_melee;
    if wants_combat && !encounter_active && room_enemies == 0 {
        return Some(json!({
            "kind": "no_target_combat",
            "question": "你做出战斗姿态,但当下视野里没有明确的敌人或目标。要先做什么?",
            "options": [
                "仔细观察四周",
                "保持警戒慢慢推进",
                "出声试探或呼喊",
                "保持隐蔽继续探索",
            ],
            "source": "rules_engine",
            "reason": "wants_combat 但无敌人 + 无 encounter — GM 不应幻觉敌人",
            "signals": {
                "has_attack": has_attack,
                "has_ranged": has_ranged,
                "has_melee": has_melee,
                "room_enemies": room_enemies,
                "encounter_active": encounter_active,
            }
        }));
    }

    // case 2: encounter 中 move_away + ranged，且未 disengage
    if encounter_active && !live_enemies.is_empty() && has_move_away && has_ranged && !has_disengage {
        let enemy_names: String = live_enemies.iter().take(3)
            .map(|e| e["name"].as_str().or_else(|| e["id"].as_str()).unwrap_or("敌人"))
            .collect::<Vec<_>>()
            .join("、");
        return Some(json!({
            "kind": "combat_pending_question",
            "question": format!(
                "敌人 ({}) 在你的近战威胁范围 (~5 ft) 内。\
                短弓在这个距离会有不利攻击;直接后退会触发借机攻击。请明确选一个:", enemy_names
            ),
            "options": [
                "Disengage 后撤 (使用动作,免借机)",
                "直接后退 (敌人借机攻击 1 次,然后离开)",
                "切换近战 (短剑) 原地砍",
                "原地短弓射击 (不利攻击)",
            ],
            "source": "rules_engine",
            "reason": "encounter 中含糊战斗: move_away + ranged 同现",
            "signals": {
                "encounter_active": encounter_active,
                "live_enemies": live_enemies.len(),
                "has_move_away": has_move_away,
                "has_ranged": has_ranged,
            }
        }));
    }

    // case 3: encounter 中只 move_away，未 disengage
    if encounter_active && !live_enemies.is_empty() && has_move_away && !has_disengage {
        let enemy_names: String = live_enemies.iter().take(3)
            .map(|e| e["name"].as_str().or_else(|| e["id"].as_str()).unwrap_or("敌人"))
            .collect::<Vec<_>>()
            .join("、");
        return Some(json!({
            "kind": "combat_pending_question",
            "question": format!(
                "你想离开敌人 ({}) 的威胁区,但没说怎么处理借机攻击:", enemy_names
            ),
            "options": [
                "Disengage 后撤 (使用动作,免借机)",
                "直接后退 (承受借机攻击)",
                "原地不动改用其他动作",
            ],
            "source": "rules_engine",
            "reason": "encounter 中含糊离场",
            "signals": {
                "encounter_active": encounter_active,
                "live_enemies": live_enemies.len(),
                "has_move_away": has_move_away,
            }
        }));
    }

    None
}
