//! 公共抽象 / 错误类型 / JSON 解析工具。
//!
//! rpg-llm、rpg-state、rpg-context 等依赖 crate 当前还是空 TODO,本文件给
//! rpg-agents 提供本地占位 trait 与类型;外部 crate 实现完成后,可把这里
//! 的占位 alias 换成对应 re-export(API surface 不变)。

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

// ── Error ──────────────────────────────────────────────────────────────

/// 所有 agent 共享的错误类型。
#[derive(Debug, Error)]
pub enum AgentError {
    #[error("LLM 调用失败: {0}")]
    Llm(String),

    #[error("JSON 解析失败: {0}")]
    JsonParse(String),

    #[error("配置错误: {0}")]
    Config(String),

    #[error("状态访问错误: {0}")]
    State(String),

    #[error("超时: {0}")]
    Timeout(String),

    #[error("未实现: {0}")]
    NotImplemented(&'static str),

    #[error("其它: {0}")]
    Other(#[from] anyhow::Error),
}

impl From<serde_json::Error> for AgentError {
    fn from(e: serde_json::Error) -> Self {
        AgentError::JsonParse(e.to_string())
    }
}

pub type AgentResult<T> = Result<T, AgentError>;

// ── Placeholder for rpg-llm::pipeline::LlmBackend ─────────────────────
//
// rpg-llm 完成后这里改成 `pub use rpg_llm::pipeline::LlmBackend;`
// 同步把 ChatMessage / ChatRequest 也搬过去。

/// 占位 LlmBackend trait — 与 Python 端 backend.call / call_structured / stream
/// 三个接口对齐。**这里只是骨架;rpg-llm 完成后整体替换。**
#[async_trait]
pub trait LlmBackend: Send + Sync {
    /// 一次性同步调用(非流)。
    async fn call(
        &self,
        system: &str,
        messages: &[ChatMessage],
        max_tokens: usize,
    ) -> AgentResult<String>;

    /// 强 JSON 返回(provider 支持 response_format=json_object / response_mime_type 时启用)。
    async fn call_structured(
        &self,
        system: &str,
        messages: &[ChatMessage],
        max_tokens: usize,
    ) -> AgentResult<String> {
        // 默认实现:走普通 call,调用方自己抠 JSON
        self.call(system, messages, max_tokens).await
    }

    /// 流式增量文本。
    async fn stream(
        &self,
        system: &str,
        messages: &[ChatMessage],
        max_tokens: usize,
    ) -> AgentResult<ChatStream>;

    /// 是否支持 native tool_use(Anthropic / 部分 Vertex)。
    fn supports_native_tools(&self) -> bool {
        false
    }

    /// native tool_use 入口(本骨架不展开)。
    async fn call_with_tools(
        &self,
        _system: &str,
        _messages: &[ChatMessage],
        _tools: &[ToolSchema],
        _max_tokens: usize,
    ) -> AgentResult<ToolCallResponse> {
        Err(AgentError::NotImplemented("call_with_tools"))
    }
}

/// 对话消息(用 Value 持有 content 兼容文字 / 多模态 block list)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: Value,
}

impl ChatMessage {
    pub fn user(text: impl Into<String>) -> Self {
        Self {
            role: "user".to_string(),
            content: Value::String(text.into()),
        }
    }
    pub fn assistant(text: impl Into<String>) -> Self {
        Self {
            role: "assistant".to_string(),
            content: Value::String(text.into()),
        }
    }
}

/// 流式输出抽象;rpg-llm 接入后换成 BoxStream<String>。
pub type ChatStream = futures::stream::BoxStream<'static, AgentResult<String>>;

/// 工具 schema(Anthropic-style)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSchema {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
}

/// 单次 tool_use 调用结果。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallResponse {
    pub tool_calls: Vec<ToolCall>,
    pub text: String,
    pub usage: HashMap<String, u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub name: String,
    pub input: Value,
}

// ── Placeholder for rpg-state::GameState ──────────────────────────────

/// 占位 GameState — rpg-state 完成后换成 `pub use rpg_state::state::GameState;`
///
/// 设计:`data` 是 serde_json::Value(对齐 Python state.data: dict)。
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct GameState {
    pub data: Value,
    pub turn: u64,
    pub history: Vec<ChatMessage>,
}

impl GameState {
    pub fn new() -> Self {
        Self {
            data: Value::Object(Default::default()),
            turn: 0,
            history: Vec::new(),
        }
    }

    /// 仿 Python state.short_summary():把 player/world/memory 关键字段拼成 prompt 片段。
    pub fn short_summary(&self) -> String {
        let p = self.data.get("player").cloned().unwrap_or(Value::Null);
        let w = self.data.get("world").cloned().unwrap_or(Value::Null);
        let m = self.data.get("memory").cloned().unwrap_or(Value::Null);
        format!(
            "player={} world={} memory={}",
            json_str_or_empty(&p, "name"),
            json_str_or_empty(&w, "time"),
            json_str_or_empty(&m, "main_quest"),
        )
    }

    pub fn history_messages(&self) -> Vec<ChatMessage> {
        self.history.clone()
    }
}

fn json_str_or_empty(v: &Value, key: &str) -> String {
    v.get(key)
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string()
}

// ── Common JSON helpers ───────────────────────────────────────────────

/// 从 LLM 输出里抠 JSON block。
///
/// 顺序:
/// 1. 整段就是 JSON(顶层 `[` 或 `{`)
/// 2. 反引号包裹的 ```json ... ``` fence
/// 3. 否则报错
///
/// 返回 &str 切片,调用方自己 from_str。
pub fn extract_json_block(text: &str) -> AgentResult<&str> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err(AgentError::JsonParse("空字符串".to_string()));
    }
    // 顶层 [ 或 {
    if trimmed.starts_with('[') || trimmed.starts_with('{') {
        return Ok(trimmed);
    }
    // ```json fence
    if let Some(start) = trimmed.find("```") {
        let after = &trimmed[start + 3..];
        // 跳过可能的 "json\n"
        let after = after.strip_prefix("json").unwrap_or(after);
        let after = after.trim_start_matches(|c: char| c == '\n' || c.is_whitespace());
        if let Some(end) = after.find("```") {
            let inner = after[..end].trim();
            if !inner.is_empty() && (inner.starts_with('[') || inner.starts_with('{')) {
                return Ok(inner);
            }
        }
    }
    Err(AgentError::JsonParse(format!(
        "找不到 JSON block in: {}",
        &trimmed[..trimmed.len().min(160)]
    )))
}

/// 解析 `{"key": [...]}` 形态,返回 key 对应的数组。
pub fn parse_json_array_field(text: &str, key: &str) -> AgentResult<Vec<Value>> {
    let blk = extract_json_block(text)?;
    let parsed: Value = serde_json::from_str(blk)?;
    match parsed {
        Value::Array(arr) => Ok(arr),
        Value::Object(obj) => match obj.get(key) {
            Some(Value::Array(arr)) => Ok(arr.clone()),
            _ => Ok(vec![]),
        },
        _ => Ok(vec![]),
    }
}

// ── 通用 BackendRef alias ─────────────────────────────────────────────

pub type SharedLlm = Arc<dyn LlmBackend>;
