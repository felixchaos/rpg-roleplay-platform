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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::provider::ProviderServices;
    use crate::types::{Demand, Manifest};
    use rpg_schemas::{GameStateData, Worldline};
    use serde_json::json;

    // ── Wave 9-A: WorldlineProvider collect() 单测 ──────────────────

    #[tokio::test]
    async fn worldline_provider_always_applies() {
        let state = GameStateData::default();
        let manifest = Manifest::default();
        let demand = Demand::default();
        // 默认 applies() 实现检查 manifest.context_providers
        // 这里只测 collect() 始终返回 applied=true
        let services = ProviderServices::default();
        let contrib = WorldlineProvider
            .collect(&state, &manifest, &demand, &services)
            .await
            .expect("collect 不应 Err");
        assert!(contrib.applied, "WorldlineProvider collect 应 applied=true");
    }

    #[tokio::test]
    async fn worldline_provider_injects_user_variables() {
        let mut state = GameStateData::default();
        let mut vars = serde_json::Map::new();
        vars.insert("命运之轮".to_string(), json!("破碎"));
        state.worldline = Worldline {
            user_variables: vars,
            ..Default::default()
        };
        let manifest = Manifest::default();
        let demand = Demand::default();
        let services = ProviderServices::default();
        let contrib = WorldlineProvider
            .collect(&state, &manifest, &demand, &services)
            .await
            .expect("collect 不应 Err");
        let text = &contrib.layers[0].content;
        assert!(text.contains("命运之轮"), "变量名应注入 layer: {text}");
        assert!(text.contains("破碎"), "变量值应注入 layer: {text}");
    }

    #[tokio::test]
    async fn worldline_provider_injects_location() {
        let mut state = GameStateData::default();
        state.player.current_location = "星落谷".to_string();
        let manifest = Manifest::default();
        let demand = Demand::default();
        let services = ProviderServices::default();
        let contrib = WorldlineProvider
            .collect(&state, &manifest, &demand, &services)
            .await
            .expect("collect 不应 Err");
        let text = &contrib.layers[0].content;
        assert!(text.contains("星落谷"), "位置应注入 layer: {text}");
    }
}
