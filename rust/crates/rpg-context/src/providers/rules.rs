//! RulesProvider — manifest.ruleset 非 none 时启用。
//! 注入 player_character 摘要、dice_log、rule_candidate_actions。
//! 对应 Python: rpg/context_providers/rules.py

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use serde_json::{json, Value};

pub struct RulesProvider;

fn has_ruleset(state_data: &Value, manifest: &Manifest) -> bool {
    if !manifest.ruleset.is_empty() && manifest.ruleset != "none" {
        return true;
    }
    state_data
        .pointer("/ruleset/id")
        .and_then(|v| v.as_str())
        .map(|s| !s.is_empty())
        .unwrap_or(false)
}

#[async_trait]
impl ContextProvider for RulesProvider {
    fn id(&self) -> &'static str {
        "rules"
    }

    fn applies(&self, state_data: &Value, manifest: &Manifest, _demand: &Demand) -> bool {
        if !manifest.context_providers.iter().any(|p| p == self.id()) {
            return false;
        }
        has_ruleset(state_data, manifest)
    }

    async fn collect(
        &self,
        state_data: &Value,
        _manifest: &Manifest,
        demand: &Demand,
        _services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let ruleset = state_data.get("ruleset").cloned().unwrap_or(Value::Null);
        let pc = state_data
            .get("player_character")
            .cloned()
            .unwrap_or(Value::Null);
        let dice_log = state_data
            .get("dice_log")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let dice_recent: Vec<&Value> = dice_log.iter().rev().take(8).collect::<Vec<_>>();
        let mut dice_recent: Vec<&Value> = dice_recent.into_iter().collect();
        dice_recent.reverse();

        let mut lines: Vec<String> = Vec::new();
        let label = ruleset
            .get("public_label")
            .and_then(|v| v.as_str())
            .or_else(|| ruleset.get("id").and_then(|v| v.as_str()))
            .unwrap_or("unknown");
        lines.push(format!("【规则集】{}", label));

        // TODO: game_policy.gm_prompt_constraints — 等 rpg-rules-bridge 提供。
        // policy_constraints 暂时空。

        if pc.is_object() {
            let name = pc.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let level = pc.get("level").and_then(|v| v.as_i64()).unwrap_or(0);
            let class_name = pc.get("class_name").and_then(|v| v.as_str()).unwrap_or("");
            let hp = pc.get("hp").and_then(|v| v.as_i64()).unwrap_or(0);
            let max_hp = pc.get("max_hp").and_then(|v| v.as_i64()).unwrap_or(0);
            let ac = pc.get("ac").and_then(|v| v.as_i64()).unwrap_or(0);
            let prof = pc
                .get("proficiency_bonus")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            lines.push(format!(
                "【角色】{} · Lv {} {} · HP {}/{} · AC {} · 熟练 +{}",
                name, level, class_name, hp, max_hp, ac, prof
            ));
            if let Some(abilities) = pc.get("abilities").and_then(|v| v.as_object()) {
                let segs: Vec<String> = ["str", "dex", "con", "int", "wis", "cha"]
                    .iter()
                    .map(|a| {
                        let val = abilities
                            .get(*a)
                            .and_then(|v| v.as_i64())
                            .unwrap_or(10);
                        format!("{} {}", a.to_uppercase(), val)
                    })
                    .collect();
                lines.push(format!("  · 属性：{}", segs.join(" ")));
            }
            if let Some(conds) = pc.get("conditions").and_then(|v| v.as_array()) {
                if !conds.is_empty() {
                    let cs: Vec<String> = conds
                        .iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect();
                    lines.push(format!("  · 状态：{}", cs.join(", ")));
                }
            }
        }

        let rcas = &demand.rule_candidate_actions;
        if !rcas.is_empty() {
            lines.push("\n【本轮规则候选动作】".to_string());
            for a in rcas.iter().take(6) {
                let kind = a.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                let target = a
                    .get("skill")
                    .or_else(|| a.get("ability"))
                    .or_else(|| a.get("target"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let mut desc = format!("{} {}", kind, target);
                if let Some(dc) = a.get("dc") {
                    if !dc.is_null() {
                        desc.push_str(&format!(" DC {}", dc));
                    }
                }
                if let Some(reason) = a.get("reason").and_then(|v| v.as_str()) {
                    if !reason.is_empty() {
                        desc.push_str(&format!(" — {}", reason));
                    }
                }
                lines.push(format!("  · {}", desc));
            }
            lines.push("⚠️ GM 不能自己掷骰；必须经 RulesEngine。".to_string());
        }
        if !dice_recent.is_empty() {
            lines.push("\n【最近骰子日志】".to_string());
            for d in &dice_recent {
                let kind = d.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                let actor = d.get("actor").and_then(|v| v.as_str()).unwrap_or("");
                let expression = d.get("expression").and_then(|v| v.as_str()).unwrap_or("");
                let total = d.get("total").cloned().unwrap_or(Value::Null);
                let mut summary = format!("{} · {} · {}={}", kind, actor, expression, total);
                if let Some(dc) = d.get("dc") {
                    if !dc.is_null() {
                        summary.push_str(&format!(" vs DC {}", dc));
                    }
                }
                match d.get("success").and_then(|v| v.as_bool()) {
                    Some(true) => summary.push_str(" ✓"),
                    Some(false) => summary.push_str(" ✗"),
                    None => {}
                }
                lines.push(format!("  · {}", summary));
            }
        }

        let text = lines.join("\n");
        let layer = Layer::new("rules", "规则集状态", text.clone()).with_priority(80);

        let mut facts: Vec<String> = Vec::new();
        if pc.is_object() {
            let hp = pc.get("hp").cloned().unwrap_or(Value::Null);
            let max_hp = pc.get("max_hp").cloned().unwrap_or(Value::Null);
            let ac = pc.get("ac").cloned().unwrap_or(Value::Null);
            facts.push(format!("角色 HP {}/{}, AC {}", hp, max_hp, ac));
        }
        if !rcas.is_empty() {
            facts.push(format!("本轮候选规则动作 {} 条", rcas.len()));
        }

        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "rules".to_string(),
            priority: 80,
            facts,
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({
                "ruleset": ruleset.get("id").cloned().unwrap_or(Value::Null),
                "pc_hp": pc.get("hp").cloned().unwrap_or(Value::Null),
                "dice_log_count": dice_log.len(),
                "candidate_actions_count": rcas.len(),
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}
