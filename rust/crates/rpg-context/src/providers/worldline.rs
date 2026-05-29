//! WorldlineProvider — 通用。玩家硬约束变量 / 当前目标 / 位置。
//! 对应 Python: rpg/context_providers/worldline.py

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use rpg_schemas::GameStateData;
use serde_json::{json, Value};

pub struct WorldlineProvider;

#[async_trait]
impl ContextProvider for WorldlineProvider {
    fn id(&self) -> &'static str {
        "worldline"
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        _demand: &Demand,
        _services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let worldline = &state_data.worldline;
        let variables = &worldline.user_variables;
        let constraints = &worldline.constraints;
        let player = &state_data.player;

        let mut lines: Vec<String> = Vec::new();
        let mut facts: Vec<String> = Vec::new();
        if !variables.is_empty() {
            lines.push("【用户硬约束变量】".to_string());
            for (i, (name, info)) in variables.iter().enumerate() {
                if i >= 12 {
                    break;
                }
                let val = match info.get("value") {
                    Some(v) => value_to_text(v),
                    None => value_to_text(info),
                };
                lines.push(format!("  · {}={}", name, val));
                if i < 3 {
                    facts.push(format!("{}={}", name, val));
                }
            }
        } else {
            lines.push("（暂无用户变量）".to_string());
        }
        if !constraints.is_empty() {
            lines.push(String::new());
            lines.push("【世界线推演约束】".to_string());
            for c in constraints.iter().take(8) {
                lines.push(format!("  · {}", c));
            }
        }
        if !player.current_location.is_empty() {
            lines.push(String::new());
            lines.push(format!("【玩家当前位置】{}", player.current_location));
        }

        let text = lines.join("\n");
        let layer = Layer::new("worldline", "世界线 / 用户变量", text.clone())
            .with_sticky(true)
            .with_priority(70);
        let tokens = (text.chars().count() / 2) as u32;

        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "worldline".to_string(),
            priority: 70,
            facts,
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({
                "vars_count": variables.len(),
                "constraints_count": constraints.len(),
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

fn value_to_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}
