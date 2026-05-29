//! acceptance_verifier — LLM 判定每条 acceptance 条款是否满足。
//!
//! 对应 Python: `rpg/agents/acceptance_verifier.py`
//!
//! 失败语义:LLM 异常 / 解析失败 → 返回 None,让调用方降级到 rule 模式。

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::common::{
    extract_json_block, AgentResult, ChatMessage, GameState, SharedLlm,
};

const SYSTEM_PROMPT: &str = include_str!("prompts/acceptance_verifier.txt");

const MAX_RESPONSE_CHARS: usize = 4000;
const MAX_UPDATES: usize = 30;
const DEFAULT_MAX_TOKENS: usize = 800;

#[derive(Debug, Clone)]
pub struct VerifierInput {
    pub acceptance: Vec<String>,
    pub response_text: String,
    pub updates: Vec<String>,
    pub user_id: Option<i64>,
    pub model_override: Option<String>,
    pub api_id_override: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct VerifierOutput {
    /// 未满足条款列表(原文)。None = LLM 不可用,调用方应降级到 rule。
    pub unmet: Option<Vec<String>>,
}

#[derive(Debug, Clone)]
pub struct VerifierConfig {
    pub default_api_id: String,
    pub default_model: String,
    pub max_tokens: usize,
}

impl Default for VerifierConfig {
    fn default() -> Self {
        Self {
            default_api_id: "vertex_ai".to_string(),
            default_model: "gemini-3.5-flash".to_string(),
            max_tokens: DEFAULT_MAX_TOKENS,
        }
    }
}

pub struct AcceptanceVerifierAgent {
    llm: SharedLlm,
    config: VerifierConfig,
}

impl AcceptanceVerifierAgent {
    pub fn new(llm: SharedLlm) -> Self {
        Self {
            llm,
            config: VerifierConfig::default(),
        }
    }

    pub async fn run(
        &self,
        input: VerifierInput,
        _state: &GameState,
    ) -> AgentResult<VerifierOutput> {
        if input.acceptance.is_empty() {
            return Ok(VerifierOutput {
                unmet: Some(vec![]),
            });
        }
        let user_prompt = build_user_prompt(&input.acceptance, &input.response_text, &input.updates);
        let messages = vec![ChatMessage::user(user_prompt)];

        let raw = match self
            .llm
            .call_structured(SYSTEM_PROMPT, &messages, self.config.max_tokens)
            .await
        {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("[verifier] call failed: {e}");
                return Ok(VerifierOutput { unmet: None });
            }
        };
        Ok(VerifierOutput {
            unmet: parse_verifier_output(&raw, &input.acceptance),
        })
    }
}

fn build_user_prompt(acceptance: &[String], response_text: &str, updates: &[String]) -> String {
    let mut lines: Vec<String> = Vec::new();
    lines.push("## GM 本轮叙事".to_string());
    let resp_trunc: String = response_text.chars().take(MAX_RESPONSE_CHARS).collect();
    lines.push(resp_trunc);
    if !updates.is_empty() {
        lines.push(String::new());
        lines.push("## 本轮 state updates(结构化变更摘要)".to_string());
        for u in updates.iter().take(MAX_UPDATES) {
            let s: String = u.chars().take(200).collect();
            lines.push(format!("- {s}"));
        }
    }
    lines.push(String::new());
    lines.push("## 待判定 acceptance 条款".to_string());
    for (i, cond) in acceptance.iter().enumerate() {
        lines.push(format!("{}. {}", i + 1, cond.trim()));
    }
    lines.join("\n")
}

/// 解析 `{"unmet": [...]}`。None = 解析失败。
fn parse_verifier_output(text: &str, acceptance: &[String]) -> Option<Vec<String>> {
    let blk = extract_json_block(text).ok()?;
    let parsed: Value = serde_json::from_str(blk).ok()?;

    let unmet_raw = match parsed {
        Value::Object(o) => o.get("unmet").cloned().unwrap_or(Value::Array(vec![])),
        Value::Array(a) => Value::Array(a),
        _ => return None,
    };
    let arr = unmet_raw.as_array()?;
    // 只保留与 acceptance 严格匹配的条款(防止 LLM 改写原文)
    let acc_set: std::collections::HashSet<&str> =
        acceptance.iter().map(|s| s.as_str()).collect();
    let result: Vec<String> = arr
        .iter()
        .filter_map(|v| v.as_str())
        .filter(|s| acc_set.contains(s))
        .map(|s| s.to_string())
        .collect();
    Some(result)
}
