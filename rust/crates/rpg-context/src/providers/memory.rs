//! MemoryProvider — 通用记忆层。所有 manifest 都启用。
//! 对应 Python: rpg/context_providers/memory.py

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use serde_json::{json, Value};

pub struct MemoryProvider;

#[async_trait]
impl ContextProvider for MemoryProvider {
    fn id(&self) -> &'static str {
        "memory"
    }

    async fn collect(
        &self,
        state_data: &Value,
        _manifest: &Manifest,
        _demand: &Demand,
        _services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let memory = state_data.get("memory").cloned().unwrap_or(Value::Null);
        let mut lines: Vec<String> = Vec::new();
        if let Some(s) = memory.get("main_quest").and_then(|v| v.as_str()) {
            if !s.is_empty() {
                lines.push(format!("主线：{}", s));
            }
        }
        if let Some(s) = memory.get("current_objective").and_then(|v| v.as_str()) {
            if !s.is_empty() {
                lines.push(format!("当前目标：{}", s));
            }
        }

        for (key, label) in [
            ("pinned", "固定记忆"),
            ("abilities", "能力"),
            ("resources", "资源"),
            ("facts", "事实"),
            ("notes", "笔记"),
        ] {
            if let Some(arr) = memory.get(key).and_then(|v| v.as_array()) {
                for item in arr.iter().take(5) {
                    if let Some(s) = item.as_str() {
                        lines.push(format!("{}：{}", label, s));
                    }
                }
            }
        }

        // hypotheses
        if let Some(items) = memory.get("items").and_then(|v| v.as_array()) {
            for it in items {
                if it.get("kind").and_then(|v| v.as_str()) == Some("hypothesis")
                    && it.get("status").and_then(|v| v.as_str()).unwrap_or("active")
                        == "active"
                {
                    let text = it.get("text").and_then(|v| v.as_str()).unwrap_or("");
                    lines.push(format!("未确认推测：{}", text));
                }
            }
        }

        let text = if lines.is_empty() {
            "（暂无长期记忆）".to_string()
        } else {
            lines.join("\n")
        };
        let layer = Layer::new("memory", "长期记忆", text.clone()).with_priority(60);

        let mem_mode = memory.get("mode").cloned().unwrap_or(Value::Null);
        let items_count = memory
            .get("items")
            .and_then(|v| v.as_array())
            .map(|a| a.len())
            .unwrap_or(0);

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
