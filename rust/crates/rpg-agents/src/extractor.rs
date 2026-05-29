//! extractor — 从 GM 叙事正文里抽出 state ops。
//!
//! 对应 Python: `rpg/agents/extractor.py`
//!
//! 设计动机:GM 同时"写小说 + 输出标签"在中等模型上错误率高。
//! 拆成两步:GM 纯叙事 → extractor(便宜模型)读叙事 + state 出 ops。
//!
//! 公开 API:
//! ```ignore
//! let agent = ExtractorAgent::new(llm);
//! let ops = agent.run(ExtractorInput { narrative, ... }, &mut state).await?;
//! ```

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::common::{
    call_structured, extract_json_block, parse_json_array_field, AgentError, AgentResult,
    ChatMessage, GameState, SharedLlm,
};

const SYSTEM_PROMPT: &str = include_str!("prompts/extractor.txt");

const MAX_NARRATIVE_CHARS: usize = 4000;
const DEFAULT_TIMEOUT_SEC: u64 = 20;
const DEFAULT_MAX_TOKENS: usize = 800;

/// 输入参数。
#[derive(Debug, Clone)]
pub struct ExtractorInput {
    pub narrative_text: String,
    pub user_id: Option<i64>,
    pub model_override: Option<String>,
    pub api_id_override: Option<String>,
    pub timeout_sec: u64,
}

impl ExtractorInput {
    pub fn new(narrative: impl Into<String>) -> Self {
        Self {
            narrative_text: narrative.into(),
            user_id: None,
            model_override: None,
            api_id_override: None,
            timeout_sec: DEFAULT_TIMEOUT_SEC,
        }
    }
}

/// 输出:state op 列表。每个 op 可能形如:
///   {"op":"set","path":"player.role","value":"史官"}
///   {"op":"question","question":"去哪","options":["A","B"]}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ExtractorOutput {
    pub ops: Vec<Value>,
}

/// 配置(rpg-llm 的 model_registry 接上以后,这里转成具体 ModelRef)。
#[derive(Debug, Clone)]
pub struct ExtractorConfig {
    pub default_api_id: String,
    pub default_model: String,
    pub max_tokens: usize,
}

impl Default for ExtractorConfig {
    fn default() -> Self {
        Self {
            default_api_id: "vertex_ai".to_string(),
            default_model: "gemini-3.5-flash".to_string(),
            max_tokens: DEFAULT_MAX_TOKENS,
        }
    }
}

pub struct ExtractorAgent {
    llm: SharedLlm,
    config: ExtractorConfig,
}

impl ExtractorAgent {
    pub fn new(llm: SharedLlm) -> Self {
        Self {
            llm,
            config: ExtractorConfig::default(),
        }
    }

    pub fn with_config(llm: SharedLlm, config: ExtractorConfig) -> Self {
        Self { llm, config }
    }

    /// 主入口。LLM/解析失败均返回空 ops(不破坏主流程,与 Python 端一致)。
    pub async fn run(
        &self,
        input: ExtractorInput,
        state: &GameState,
    ) -> AgentResult<ExtractorOutput> {
        if input.narrative_text.trim().is_empty() {
            return Ok(ExtractorOutput::default());
        }
        let user_prompt = build_user_prompt(&input.narrative_text, &state.data);
        let messages = vec![ChatMessage::user(user_prompt)];

        let raw = match call_structured(
            self.llm.as_ref(),
            SYSTEM_PROMPT,
            &messages,
            self.config.max_tokens,
        )
        .await
        {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("[extractor] call failed: {e}");
                return Ok(ExtractorOutput::default());
            }
        };

        Ok(ExtractorOutput {
            ops: parse_extractor_output(&raw),
        })
    }
}

/// 组装 user message:state 快照 + 叙事正文。
fn build_user_prompt(narrative: &str, state_data: &rpg_schemas::GameStateData) -> String {
    let resources_preview = state_data
        .memory
        .resources
        .iter()
        .take(5)
        .map(|x| x.to_string())
        .collect::<Vec<_>>()
        .join(", ");

    let rels_preview = state_data
        .relationships
        .iter()
        .take(8)
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join(", ");

    let narr_trunc: String = narrative.chars().take(MAX_NARRATIVE_CHARS).collect();

    // world.weather is stored in world.extra
    let world_weather = state_data
        .world
        .extra
        .get("weather")
        .and_then(|v| v.as_str())
        .unwrap_or("(空)")
        .to_string();

    format!(
        "## 当前状态快照(在叙事之前的值)\n\
         - player.name = {}\n\
         - player.role = {}\n\
         - player.current_location = {}\n\
         - world.time = {}\n\
         - world.weather = {}\n\
         - memory.main_quest = {}\n\
         - memory.current_objective = {}\n\
         - memory.resources = [{}]\n\
         - relationships = {{{}}}\n\
         \n\n\
         ## GM 本轮叙事\n{}",
        state_data.player.name,
        state_data.player.role,
        state_data.player.current_location,
        state_data.world.time,
        world_weather,
        state_data.memory.main_quest,
        state_data.memory.current_objective,
        resources_preview,
        rels_preview,
        narr_trunc,
    )
}

/// 从 LLM 输出抠 ops 数组。容错:整段 JSON / fence / {"ops": [...]}。
fn parse_extractor_output(text: &str) -> Vec<Value> {
    if text.trim().is_empty() {
        return vec![];
    }
    // Try {"ops": [...]} or top-level array
    if let Ok(arr) = parse_json_array_field(text, "ops") {
        if !arr.is_empty() {
            return arr.into_iter().filter(|v| v.is_object()).collect();
        }
    }
    // Try top-level array directly
    if let Ok(blk) = extract_json_block(text) {
        if let Ok(Value::Array(arr)) = serde_json::from_str::<Value>(blk) {
            return arr.into_iter().filter(|v| v.is_object()).collect();
        }
        if let Ok(Value::Object(obj)) = serde_json::from_str::<Value>(blk) {
            // 单 op 也包成数组
            return vec![Value::Object(obj)];
        }
    }
    vec![]
}

// 仅供绑定层 / 测试使用
#[doc(hidden)]
pub fn _expose_build_user_prompt(narrative: &str, state_data: &rpg_schemas::GameStateData) -> String {
    build_user_prompt(narrative, state_data)
}

#[doc(hidden)]
pub fn _expose_parse_extractor_output(text: &str) -> Vec<Value> {
    parse_extractor_output(text)
}

// 防止 unused 警告:AgentError 在 run 错误路径里没显式触发,做个透传。
#[allow(dead_code)]
fn _unused_error_taint() -> AgentResult<()> {
    Err(AgentError::Llm("placeholder".into()))
}
