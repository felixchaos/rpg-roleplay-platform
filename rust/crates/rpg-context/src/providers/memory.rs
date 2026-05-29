//! MemoryProvider — 通用记忆层。所有 manifest 都启用。
//! 对应 Python: rpg/context_providers/memory.py

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use rpg_schemas::GameStateData;
use serde_json::json;

pub struct MemoryProvider;

#[async_trait]
impl ContextProvider for MemoryProvider {
    fn id(&self) -> &'static str {
        "memory"
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        _demand: &Demand,
        _services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let memory = &state_data.memory;
        let mut lines: Vec<String> = Vec::new();
        if !memory.main_quest.is_empty() {
            lines.push(format!("主线：{}", memory.main_quest));
        }
        if !memory.current_objective.is_empty() {
            lines.push(format!("当前目标：{}", memory.current_objective));
        }

        for (arr, label) in [
            (memory.pinned.as_slice(), "固定记忆"),
            (memory.abilities.as_slice(), "能力"),
            (memory.resources.as_slice(), "资源"),
            (memory.facts.as_slice(), "事实"),
            (memory.notes.as_slice(), "笔记"),
        ] {
            for item in arr.iter().take(5) {
                if let Some(s) = item.as_str() {
                    lines.push(format!("{}：{}", label, s));
                }
            }
        }

        // hypotheses — limit to 5 matching Python `active_hypos[:5]` (ctx-15).
        // ctx-16: only include items with explicit status == "active" (not missing status).
        let mut hypo_count = 0usize;
        for it in &memory.items {
            if hypo_count >= 5 {
                break;
            }
            if it.get("kind").and_then(|v| v.as_str()) == Some("hypothesis")
                && it.get("status").and_then(|v| v.as_str()) == Some("active")
            {
                let text = it.get("text").and_then(|v| v.as_str()).unwrap_or("");
                lines.push(format!("未确认推测：{}", text));
                hypo_count += 1;
            }
        }

        let text = if lines.is_empty() {
            "（暂无长期记忆）".to_string()
        } else {
            lines.join("\n")
        };
        let layer = Layer::new("memory", "长期记忆", text.clone()).with_priority(60);

        let mem_mode = &memory.mode;
        let items_count = memory.items.len();

        let facts: Vec<String> = lines.iter().take(3).cloned().collect();
        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "memory".to_string(),
            priority: 60,
            facts,
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({
                "memory_mode": mem_mode,
                "items_count": items_count,
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}
