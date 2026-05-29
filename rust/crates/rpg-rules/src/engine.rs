//! engine — RulesEngine 统一入口 (facade)
//! 对应 Python: rpg/rules/engine.py

use once_cell::sync::OnceCell;
use serde_json::Value;
use std::collections::HashMap;

use crate::dice::{roll as dice_roll, DiceError, RollResult};
use crate::dnd5e::{
    actions::{apply_damage, attack_roll as dnd_attack_roll, damage_roll as dnd_damage_roll,
               short_rest as dnd_short_rest},
    character::{
        consume_inventory_item, find_inventory_item, make_default_character,
        normalize_item_alias, resources_from_inventory,
    },
    checks::{saving_throw as dnd_saving_throw, skill_check as dnd_skill_check},
    combat::{
        initiative as dnd_initiative, is_encounter_resolved, mark_defeated_by_hp,
        next_turn as dnd_next_turn, start_encounter as dnd_start_encounter,
    },
    monsters::{build_combatant as dnd_build_combatant, get_stat_block, list_stat_blocks},
    ruleset::{ability_modifier, proficiency_bonus},
    RuleResult,
};

// ── 单例 ─────────────────────────────────────────────────────────────────
static DEFAULT_ENGINE: OnceCell<RulesEngine> = OnceCell::new();

/// 5E-compatible 规则集 facade。
/// 所有方法都是确定性纯函数（给 seed 可重现）。
pub struct RulesEngine {
    pub ruleset_id: String,
    pub mode: String,
}

impl RulesEngine {
    pub fn new(ruleset_id: &str, mode: &str) -> Result<Self, String> {
        if ruleset_id != "dnd5e" {
            return Err(format!("未支持的 ruleset: {}", ruleset_id));
        }
        Ok(Self {
            ruleset_id: ruleset_id.to_string(),
            mode: mode.to_string(),
        })
    }

    // ── 元信息 ────────────────────────────────────────────────────────────
    pub fn info(&self) -> HashMap<&'static str, &str> {
        let mut m = HashMap::new();
        m.insert("id", self.ruleset_id.as_str());
        m.insert("mode", self.mode.as_str());
        m.insert("label", "5E compatible / 五版规则兼容");
        m.insert("rules_version", "1.0");
        m
    }

    // ── 数学 ──────────────────────────────────────────────────────────────
    pub fn ability_modifier(&self, score: i32) -> i32 {
        ability_modifier(score)
    }

    pub fn proficiency_bonus(&self, level: i32) -> i32 {
        proficiency_bonus(level)
    }

    // ── 掷骰 ──────────────────────────────────────────────────────────────
    pub fn roll(
        &self,
        expression: &str,
        seed: Option<u64>,
        advantage: bool,
        disadvantage: bool,
    ) -> Result<RollResult, DiceError> {
        dice_roll(expression, seed, advantage, disadvantage)
    }

    pub fn damage_roll(
        &self,
        expression: &str,
        seed: Option<u64>,
        critical: bool,
    ) -> Result<HashMap<String, serde_json::Value>, DiceError> {
        dnd_damage_roll(expression, seed, critical)
    }

    // ── 角色 ──────────────────────────────────────────────────────────────
    pub fn make_default_character(&self, name: &str, level: i32) -> Value {
        make_default_character(name, level)
    }

    // ── Inventory ─────────────────────────────────────────────────────────
    pub fn consume_inventory_item(
        &self,
        character: &mut Value,
        alias: &str,
        qty: i32,
    ) -> Value {
        consume_inventory_item(character, alias, qty)
    }

    pub fn find_inventory_item<'a>(&self, character: &'a Value, alias: &str) -> Option<&'a Value> {
        find_inventory_item(character, alias)
    }

    pub fn normalize_item_alias(&self, alias: &str) -> String {
        normalize_item_alias(alias)
    }

    pub fn resources_from_inventory(&self, character: &Value) -> Vec<String> {
        resources_from_inventory(character)
    }

    // ── 检定 ──────────────────────────────────────────────────────────────
    pub fn skill_check(
        &self,
        character: &Value,
        skill: &str,
        dc: i32,
        advantage: bool,
        disadvantage: bool,
        seed: Option<u64>,
        actor_name: Option<&str>,
        reason: &str,
    ) -> Result<RuleResult, DiceError> {
        dnd_skill_check(character, skill, dc, advantage, disadvantage, seed, actor_name, reason)
    }

    pub fn saving_throw(
        &self,
        character: &Value,
        ability: &str,
        dc: i32,
        advantage: bool,
        disadvantage: bool,
        seed: Option<u64>,
        actor_name: Option<&str>,
        reason: &str,
    ) -> Result<RuleResult, DiceError> {
        dnd_saving_throw(character, ability, dc, advantage, disadvantage, seed, actor_name, reason)
    }

    // ── 战斗 ──────────────────────────────────────────────────────────────
    pub fn initiative(
        &self,
        combatants: &[Value],
        seed: Option<u64>,
    ) -> Result<Vec<Value>, DiceError> {
        dnd_initiative(combatants, seed)
    }

    pub fn start_encounter(
        &self,
        party: &[Value],
        enemies: &[Value],
        seed: Option<u64>,
        encounter_id: &str,
    ) -> Result<Value, DiceError> {
        dnd_start_encounter(party, enemies, seed, encounter_id)
    }

    pub fn next_turn<'a>(&self, encounter: &'a mut Value) -> &'a mut Value {
        dnd_next_turn(encounter)
    }

    pub fn attack_roll(
        &self,
        attacker: &Value,
        target: &Value,
        attack_bonus: i32,
        damage_expr: &str,
        advantage: bool,
        disadvantage: bool,
        seed: Option<u64>,
        attacker_name: Option<&str>,
        target_name: Option<&str>,
        weapon_name: &str,
    ) -> Result<RuleResult, DiceError> {
        dnd_attack_roll(
            attacker, target, attack_bonus, damage_expr,
            advantage, disadvantage, seed,
            attacker_name, target_name, weapon_name,
        )
    }

    pub fn apply_damage(&self, target: &mut Value, amount: i32) -> RuleResult {
        apply_damage(target, amount)
    }

    pub fn short_rest(
        &self,
        character: &mut Value,
        hit_die: &str,
        seed: Option<u64>,
    ) -> Result<RuleResult, DiceError> {
        dnd_short_rest(character, hit_die, seed)
    }

    // ── encounter 工具 ────────────────────────────────────────────────────
    pub fn is_encounter_resolved(&self, encounter: &Value) -> (bool, &'static str) {
        is_encounter_resolved(encounter)
    }

    pub fn mark_defeated_by_hp(&self, encounter: &mut Value) -> Vec<String> {
        mark_defeated_by_hp(encounter)
    }

    // ── 怪物 ──────────────────────────────────────────────────────────────
    pub fn get_stat_block(&self, stat_block_id: &str) -> Result<Value, String> {
        get_stat_block(stat_block_id)
    }

    pub fn build_combatant(
        &self,
        stat_block_id: &str,
        instance_id: Option<&str>,
        name: Option<&str>,
    ) -> Result<Value, String> {
        dnd_build_combatant(stat_block_id, instance_id, name)
    }

    pub fn list_stat_blocks(&self) -> Vec<&'static str> {
        list_stat_blocks()
    }

    // ── dice_log 辅助 ─────────────────────────────────────────────────────
    /// 把 RuleResult 压扁成 dice_log 条目（前端 UI 显示用）。
    pub fn make_dice_log_entry(result: &RuleResult, reason: &str) -> HashMap<String, serde_json::Value> {
        use serde_json::json;
        use std::time::{SystemTime, UNIX_EPOCH};

        // 简单 ts：秒级 unix timestamp（no chrono/time dependency）
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);

        let roll = &result.roll;
        let expression = roll
            .get("expression")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let rolls = roll
            .get("rolls")
            .cloned()
            .unwrap_or_else(|| json!([]));
        let modifier = roll
            .get("modifier")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let total = roll.get("total").cloned().unwrap_or(serde_json::Value::Null);
        let advantage = roll
            .get("advantage")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let disadvantage = roll
            .get("disadvantage")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        let final_reason = if !reason.is_empty() {
            reason.to_string()
        } else {
            result
                .extra
                .get("reason")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string()
        };

        let mut entry: HashMap<String, serde_json::Value> = HashMap::new();
        entry.insert("kind".to_string(), json!(result.kind));
        entry.insert("actor".to_string(), json!(result.actor));
        entry.insert("target".to_string(), json!(result.target));
        entry.insert("expression".to_string(), json!(expression));
        entry.insert("rolls".to_string(), rolls);
        entry.insert("modifier".to_string(), json!(modifier));
        entry.insert("total".to_string(), total);
        entry.insert("dc".to_string(), json!(result.dc));
        entry.insert("success".to_string(), json!(result.success));
        entry.insert("advantage".to_string(), json!(advantage));
        entry.insert("disadvantage".to_string(), json!(disadvantage));
        entry.insert("damage".to_string(), json!(result.damage));
        entry.insert("reason".to_string(), json!(final_reason));
        entry.insert("ts".to_string(), json!(ts));

        // 抬升 skill / ability / weapon
        for key in &["skill", "ability", "weapon"] {
            if let Some(v) = result.extra.get(*key) {
                if !v.is_null() && v.as_str().map(|s| !s.is_empty()).unwrap_or(true) {
                    entry.insert(key.to_string(), v.clone());
                }
            }
        }

        entry
    }
}

/// 全局规则引擎单例（dnd5e / 5e_compatible）。
pub fn get_engine() -> &'static RulesEngine {
    DEFAULT_ENGINE.get_or_init(|| {
        RulesEngine::new("dnd5e", "5e_compatible")
            .expect("RulesEngine 初始化失败")
    })
}
