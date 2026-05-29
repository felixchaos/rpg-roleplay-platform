//! combat — 战斗初始化、攻击、回合推进。
//! 对应 Python: rpg/rules_bridge/combat.py

use serde_json::{json, Value};
use rpg_rules::dnd5e::{actions, combat as rules_combat, RuleResult};
use rpg_schemas::GameStateData;
use crate::error::BridgeError;

// ── 类型别名 ──────────────────────────────────────────────────────────────

/// CombatAction 对应调用哪个战斗操作
#[derive(Debug, Clone)]
pub enum CombatAction {
    PlayerAttack {
        target_id: String,
        weapon_id: String,
        attack_bonus: i32,
        damage_expr: String,
        advantage: bool,
        disadvantage: bool,
        seed: Option<u64>,
    },
    EnemyAttack {
        attacker_id: String,
        target_id: String,
        attack_index: usize,
        seed: Option<u64>,
    },
    AdvanceTurn,
    StartEncounter {
        encounter_id: String,
        party: Vec<Value>,
        enemies: Vec<Value>,
        seed: Option<u64>,
    },
}

/// CombatOutcome 包含本次操作结果与更新后的 encounter 快照
#[derive(Debug, Clone, serde::Serialize)]
pub struct CombatOutcome {
    pub rule_result: Value,
    pub encounter: Value,
    pub gm_facts: Vec<String>,
}

// ── 内部工具 ─────────────────────────────────────────────────────────────

/// 从 state.data.encounter.combatants 中把 player 的 HP/AC 与 player_character 同步。
pub fn sync_player_combatant(data: &mut GameStateData) {
    let pc_hp = data.player_character.hp;
    let pc_max_hp = data.player_character.max_hp;
    let pc_ac = data.player_character.ac;
    let pc_conditions = serde_json::to_value(&data.player_character.conditions)
        .unwrap_or(Value::Array(vec![]));

    for c in data.encounter.combatants.iter_mut() {
        if c["id"].as_str() == Some("player") {
            c["hp"] = json!(pc_hp);
            c["max_hp"] = json!(pc_max_hp);
            c["ac"] = json!(pc_ac);
            c["conditions"] = pc_conditions.clone();
            c["defeated"] = json!(pc_hp <= 0);
            break;
        }
    }
}

/// 把 state_ops（path 形如 `_combatant.<id>.hp`）应用到 encounter.combatants。
pub fn apply_combatant_ops(encounter: &mut Value, state_ops: &[rpg_rules::dnd5e::StateOp]) {
    for op in state_ops {
        if op.op == "subtract" && op.path.starts_with("_combatant.") {
            let parts: Vec<&str> = op.path.splitn(3, '.').collect();
            if parts.len() == 3 {
                let comb_id = parts[1];
                let field = parts[2];
                let amount = op.value.as_ref().and_then(|v| v.as_i64()).unwrap_or(0) as i32;
                if let Some(combs) = encounter["combatants"].as_array_mut() {
                    for c in combs.iter_mut() {
                        if c["id"].as_str() == Some(comb_id) {
                            let cur = c[field].as_i64().unwrap_or(0) as i32;
                            c[field] = json!((cur - amount).max(0));
                            break;
                        }
                    }
                }
            }
        }
    }
}

// ── 公开 dispatcher ───────────────────────────────────────────────────────

/// 主入口：根据 CombatAction 调度战斗逻辑，原地修改 state.data，返回 CombatOutcome。
pub fn apply_combat(data: &mut GameStateData, action: CombatAction) -> Result<CombatOutcome, BridgeError> {
    match action {
        CombatAction::StartEncounter { encounter_id, party, enemies, seed } => {
            let enc = rules_combat::start_encounter(&party, &enemies, seed, &encounter_id)?;
            data.encounter = serde_json::from_value(enc.clone())
                .unwrap_or_default();
            let gm_facts = vec![format!("遭遇 {} 已开始。", encounter_id)];
            Ok(CombatOutcome {
                rule_result: enc.clone(),
                encounter: enc,
                gm_facts,
            })
        }

        CombatAction::PlayerAttack {
            target_id, weapon_id: _, attack_bonus, damage_expr,
            advantage, disadvantage, seed,
        } => {
            if !data.encounter.active {
                return Err(BridgeError::EncounterNotActive);
            }

            // 找目标
            let target = {
                data.encounter.combatants.iter()
                    .find(|c| c["id"].as_str() == Some(&target_id) && c["side"].as_str() == Some("enemy"))
                    .cloned()
                    .ok_or_else(|| BridgeError::TargetNotFound(target_id.clone()))?
            };

            if target["defeated"].as_bool() == Some(true) {
                return Err(BridgeError::Logic(format!("目标已倒下：{}", target_id)));
            }

            let pc = serde_json::to_value(&data.player_character)?;
            let result: RuleResult = actions::attack_roll(
                &pc,
                &target,
                attack_bonus,
                &damage_expr,
                advantage,
                disadvantage,
                seed,
                pc["name"].as_str(),
                target["name"].as_str(),
                &damage_expr,
            )?;

            // 应用 state_ops 到 encounter
            let mut enc_value = serde_json::to_value(&data.encounter)?;
            apply_combatant_ops(&mut enc_value, &result.state_ops);

            // 检查阵亡
            let newly = rules_combat::mark_defeated_by_hp(&mut enc_value);
            let (resolved, outcome) = rules_combat::is_encounter_resolved(&enc_value);

            let mut gm_facts = result.gm_facts.clone();
            if !newly.is_empty() {
                gm_facts.push(format!("{} 倒下。", newly.join("、")));
            }
            if resolved {
                enc_value["active"] = json!(false);
                enc_value["outcome"] = json!(outcome);
                gm_facts.push(format!("战斗结束：{}。", outcome));
                // 胜利标志
                if outcome == "victory" {
                    if let Some(vf) = enc_value["definition"]["victory_flag"].as_str() {
                        let vf = vf.to_string();
                        data.scene.flags.insert(vf, json!(true));
                    }
                }
            }
            let enc_snap = enc_value.clone();
            data.encounter = serde_json::from_value(enc_value).unwrap_or_default();

            Ok(CombatOutcome {
                rule_result: serde_json::to_value(&result)?,
                encounter: enc_snap,
                gm_facts,
            })
        }

        CombatAction::EnemyAttack { attacker_id, target_id, attack_index, seed } => {
            if !data.encounter.active {
                return Err(BridgeError::EncounterNotActive);
            }

            // 找攻击者
            let attacker = data.encounter.combatants.iter()
                .find(|c| c["id"].as_str() == Some(&attacker_id))
                .cloned()
                .ok_or_else(|| BridgeError::TargetNotFound(attacker_id.clone()))?;

            if attacker["defeated"].as_bool() == Some(true) {
                return Err(BridgeError::Logic(format!("攻击者已阵亡：{}", attacker_id)));
            }

            let attacks = attacker["attacks"].as_array().cloned().unwrap_or_default();
            if attacks.is_empty() {
                return Err(BridgeError::Logic(format!("攻击者无攻击动作：{}", attacker_id)));
            }
            let atk_def = &attacks[attack_index.min(attacks.len() - 1)];
            let atk_bonus = atk_def["attack_bonus"].as_i64().unwrap_or(3) as i32;
            let dmg_expr = atk_def["damage"].as_str().unwrap_or("1d6").to_string();
            let weapon_name = atk_def["name"].as_str().unwrap_or("Attack").to_string();

            // 构造 target
            let target: Value = if target_id == "player" {
                json!({
                    "id": "player",
                    "name": &data.player_character.name,
                    "ac": data.player_character.ac
                })
            } else {
                data.encounter.combatants.iter()
                    .find(|c| c["id"].as_str() == Some(&target_id))
                    .cloned()
                    .ok_or_else(|| BridgeError::TargetNotFound(target_id.clone()))?
            };

            let result: RuleResult = actions::attack_roll(
                &attacker,
                &target,
                atk_bonus,
                &dmg_expr,
                false,
                false,
                seed,
                attacker["name"].as_str(),
                target["name"].as_str(),
                &weapon_name,
            )?;

            let mut gm_facts = result.gm_facts.clone();

            // 命中时扣 HP
            if result.success == Some(true) {
                if target_id == "player" {
                    let dmg = result.damage.as_ref()
                        .and_then(|d| d.get("total"))
                        .and_then(|v| v.as_i64())
                        .unwrap_or(0) as i32;
                    let new_hp = (data.player_character.hp - dmg).max(0);
                    data.player_character.hp = new_hp;
                    sync_player_combatant(data);
                    let max_hp = data.player_character.max_hp;
                    gm_facts.push(format!(
                        "玩家受到 {} 点伤害（HP {}/{}）。", dmg, new_hp, max_hp
                    ));
                } else {
                    let mut enc_value = serde_json::to_value(&data.encounter)?;
                    apply_combatant_ops(&mut enc_value, &result.state_ops);
                    data.encounter = serde_json::from_value(enc_value).unwrap_or_default();
                }
            }

            let mut enc_value = serde_json::to_value(&data.encounter)?;
            rules_combat::mark_defeated_by_hp(&mut enc_value);
            let (resolved, outcome) = rules_combat::is_encounter_resolved(&enc_value);
            if resolved {
                enc_value["active"] = json!(false);
                enc_value["outcome"] = json!(outcome);
                gm_facts.push(format!("战斗结束：{}。", outcome));
            }
            let enc_snap = enc_value.clone();
            data.encounter = serde_json::from_value(enc_value).unwrap_or_default();

            Ok(CombatOutcome {
                rule_result: serde_json::to_value(&result)?,
                encounter: enc_snap,
                gm_facts,
            })
        }

        CombatAction::AdvanceTurn => {
            if !data.encounter.active {
                return Err(BridgeError::EncounterNotActive);
            }
            sync_player_combatant(data);
            let mut enc_value = serde_json::to_value(&data.encounter)?;
            rules_combat::next_turn(&mut enc_value);
            let enc_snap = enc_value.clone();
            data.encounter = serde_json::from_value(enc_value).unwrap_or_default();
            Ok(CombatOutcome {
                rule_result: json!({"kind": "advance_turn"}),
                encounter: enc_snap,
                gm_facts: vec![],
            })
        }
    }
}
