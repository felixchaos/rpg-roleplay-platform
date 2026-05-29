//! RecentChatProvider — 通用最近对话注入。
//! 对应 Python: rpg/context_providers/recent_chat.py

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use rpg_schemas::GameStateData;
use serde_json::json;

pub struct RecentChatProvider;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::provider::ProviderServices;
    use crate::types::{Demand, Manifest};
    use rpg_schemas::GameStateData;
    use serde_json::json;

    // ── Wave 9-A: RecentChatProvider 单测 ───────────────────────────

    #[tokio::test]
    async fn recent_chat_skips_when_no_history() {
        let state = GameStateData::default(); // history = []
        let manifest = Manifest::default();
        let demand = Demand::default();
        let services = ProviderServices::default();
        let contrib = RecentChatProvider
            .collect(&state, &manifest, &demand, &services)
            .await
            .expect("collect 不应 Err");
        assert!(!contrib.applied, "无历史时 applied 应为 false");
    }

    #[tokio::test]
    async fn recent_chat_returns_last_6_turns() {
        let mut state = GameStateData::default();
        // 放入 8 条历史
        for i in 0..8u64 {
            state.history.push(json!({
                "role": if i % 2 == 0 { "user" } else { "assistant" },
                "content": format!("第{}条", i)
            }));
        }
        let manifest = Manifest::default();
        let demand = Demand::default();
        let services = ProviderServices::default();
        let contrib = RecentChatProvider
            .collect(&state, &manifest, &demand, &services)
            .await
            .expect("collect 不应 Err");
        assert!(contrib.applied, "有历史时 applied 应为 true");
        // 最近 6 条:第2~7条;第0/1条不应出现
        let text = &contrib.layers[0].content;
        assert!(!text.contains("第0条"), "超出 6 条窗口的历史不应出现: {text}");
        assert!(text.contains("第2条"), "最近 6 条内的历史应出现: {text}");
    }
}

#[async_trait]
impl ContextProvider for RecentChatProvider {
    fn id(&self) -> &'static str {
        "recent_chat"
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        _demand: &Demand,
        _services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let history = &state_data.history;
        if history.is_empty() {
            return Ok(ContextContribution::skipped(self.id(), "no history"));
        }
        let mut lines: Vec<String> = Vec::new();
        let start = history.len().saturating_sub(6);
        for msg in &history[start..] {
            let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("user");
            let content = msg.get("content").and_then(|v| v.as_str()).unwrap_or("");
            let content = content.trim();
            if content.is_empty() {
                continue;
            }
            let prefix = if role == "user" { "玩家" } else { "GM" };
            let truncated: String = content.chars().take(600).collect();
            lines.push(format!("{}：{}", prefix, truncated));
        }
        let text = if lines.is_empty() {
            "（暂无对话）".to_string()
        } else {
            lines.join("\n\n")
        };
        let layer = Layer::new("recent_chat", "最近对话", text.clone()).with_priority(20);
        let tokens = (text.chars().count() / 2) as u32;

        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "recent_chat".to_string(),
            priority: 20,
            facts: Vec::new(),
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({ "turns": history.len() }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}
