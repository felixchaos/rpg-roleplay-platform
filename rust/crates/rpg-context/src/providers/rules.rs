//! RulesProvider — manifest.ruleset 非 none 时启用。
//! 注入 player_character 摘要、dice_log、rule_candidate_actions。
//! 对应 Python: rpg/context_providers/rules.py

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use rpg_schemas::GameStateData;
use serde_json::{json, Value};

pub struct RulesProvider;

fn has_ruleset(state_data: &GameStateData, manifest: &Manifest) -> bool {
    if !manifest.ruleset.is_empty() && manifest.ruleset != "none" {
        return true;
    }
    !state_data.ruleset.id.is_empty()
}

#[async_trait]
impl ContextProvider for RulesProvider {
    fn id(&self) -> &'static str {
        "rules"
    }

    fn applies(&self, state_data: &GameStateData, manifest: &Manifest, _demand: &Demand) -> bool {
        if !manifest.context_providers.iter().any(|p| p == self.id()) {
            return false;
        }
        has_ruleset(state_data, manifest)
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        demand: &Demand,
        _services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let pc = &state_data.player_character;
        let dice_log = &state_data.dice_log;
        let dice_recent: Vec<&Value> = dice_log.iter().rev().take(8).collect::<Vec<_>>();
        let mut dice_recent: Vec<&Value> = dice_recent.into_iter().collect();
        dice_recent.reverse();

        let mut lines: Vec<String> = Vec::new();
        let ruleset = &state_data.ruleset;
        let label = if !ruleset.public_label.is_empty() {
            &ruleset.public_label
        } else if !ruleset.id.is_empty() {
            &ruleset.id
        } else {
            "unknown"
        };
        lines.push(format!("【规则集】{}", label));

        // TODO: game_policy.gm_prompt_constraints — 等 rpg-rules-bridge 提供。
        // policy_constraints 暂时空。

        // Only emit PC block when pc.name is non-empty (matching Python `if pc:` check).
        // Empty PlayerCharacter (all zeros/empty strings) is falsy — skip to avoid
        // polluting novel-adaptation GM prompts with garbage like 'HP 0/0 · AC 0'.
        if !pc.name.is_empty() {
            let name = pc.name.as_str();
            let level = pc.level as i64;
            let class_name = pc.class_name.as_str();
            let hp = pc.hp as i64;
            let max_hp = pc.max_hp as i64;
            let ac = pc.ac as i64;
            let prof = pc.proficiency_bonus as i64;
            lines.push(format!(
                "【角色】{} · Lv {} {} · HP {}/{} · AC {} · 熟练 +{}",
                name, level, class_name, hp, max_hp, ac, prof
            ));
            let segs: Vec<String> = ["str", "dex", "con", "int", "wis", "cha"]
                .iter()
                .map(|a| {
                    let val = pc.abilities
                        .get(*a)
                        .and_then(|v| v.as_i64())
                        .unwrap_or(10);
                    format!("{} {}", a.to_uppercase(), val)
                })
                .collect();
            lines.push(format!("  · 属性：{}", segs.join(" ")));
            if !pc.conditions.is_empty() {
                let cs: Vec<String> = pc.conditions
                    .iter()
                    .filter_map(|v| v.as_str().map(String::from))
                    .collect();
                if !cs.is_empty() {
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
        // Only push HP fact when pc is non-empty (matching Python `if pc:` check, ctx-07).
        if !pc.name.is_empty() {
            facts.push(format!("角色 HP {}/{}, AC {}", pc.hp, pc.max_hp, pc.ac));
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
                "ruleset": &state_data.ruleset.id,
                "pc_hp": pc.hp,
                "dice_log_count": dice_log.len(),
                "candidate_actions_count": rcas.len(),
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Demand, Manifest};
    use rpg_schemas::GameStateData;

    fn manifest_with_rules() -> Manifest {
        Manifest {
            id: "test".into(),
            kind: "module_adventure".into(),
            ruleset: "5e_compatible".into(),
            context_providers: vec!["rules".into()],
            ..Default::default()
        }
    }

    fn manifest_no_rules() -> Manifest {
        Manifest {
            id: "test".into(),
            kind: "freeform".into(),
            ruleset: "none".into(),
            context_providers: vec!["rules".into()],
            ..Default::default()
        }
    }

    // ── Wave 9-A: RulesProvider applies() 单测 ──────────────────────

    #[test]
    fn rules_provider_applies_when_manifest_has_ruleset() {
        let state = GameStateData::default();
        let manifest = manifest_with_rules();
        let demand = Demand::default();
        assert!(
            RulesProvider.applies(&state, &manifest, &demand),
            "manifest.ruleset=5e_compatible 应返回 true"
        );
    }

    #[test]
    fn rules_provider_does_not_apply_when_ruleset_is_none_and_state_empty() {
        let mut state = GameStateData::default();
        // 清除默认 ruleset.id ("dnd5e")
        state.ruleset.id = String::new();
        let manifest = manifest_no_rules();
        let demand = Demand::default();
        assert!(
            !RulesProvider.applies(&state, &manifest, &demand),
            "ruleset=none 且 state.ruleset.id 为空时应返回 false"
        );
    }

    #[test]
    fn rules_provider_applies_when_state_ruleset_id_is_set() {
        // manifest.ruleset="none" 但 state.ruleset.id 非空 → 仍 true
        let state = GameStateData::default(); // default ruleset.id = "dnd5e"
        let manifest = manifest_no_rules();
        let demand = Demand::default();
        assert!(
            RulesProvider.applies(&state, &manifest, &demand),
            "state.ruleset.id 非空时 applies 应为 true"
        );
    }
}
