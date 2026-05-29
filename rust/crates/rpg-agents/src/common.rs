//! 公共抽象 / 错误类型 / JSON 解析工具。
//!
//! W3-1 切换:placeholder LlmBackend / GameState / ChatMessage / ToolCall /
//! ToolSchema 全部改为 re-export rpg-llm / rpg-state 真实类型。
//!
//! 由于 `rpg_llm::pipeline::LlmBackend` trait 只暴露 `stream_chat(ChatRequest)`,
//! 本模块给 agents 提供薄的 adapter helper:
//!   * [`call_text`] — 一次性文本(对应原 `call`)
//!   * [`call_structured`] — JSON-mode 文本(对应原 `call_structured`)
//!   * [`stream_text`] — 流式 String 序列(对应原 `stream`)
//!   * [`call_with_tools`] — native tool_use(对应原 `call_with_tools`)
//!   * [`supports_native_tools`] — 启发式:Anthropic / Vertex / OpenAI 都支持
//!
//! 由于 `rpg_state::state::GameState` 不再带 `turn` 字段(改成方法)/
//! `history` / `short_summary`,这里给出对应 helper:
//!   * [`state_turn`] — `state.turn() as u64`
//!   * [`state_history_messages`] — 默认空 Vec(后续接入对话历史时再扩展)
//!   * [`state_short_summary`] — player / world / memory 关键字段拼装

use std::sync::Arc;

use futures::stream::BoxStream;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use thiserror::Error;
use tokio::task::JoinHandle;

// ── re-export real types ──────────────────────────────────────────────

pub use rpg_llm::pipeline::{
    ChatChunk, ChatMessage, ChatRequest, ChatRole, ChunkStream, LlmBackend, LlmError, MessagePart,
    ToolCall, ToolSchema, Usage,
};
pub use rpg_llm::AnyBackend;
pub use rpg_state::state::GameState;

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

impl From<LlmError> for AgentError {
    fn from(e: LlmError) -> Self {
        AgentError::Llm(e.to_string())
    }
}

pub type AgentResult<T> = Result<T, AgentError>;

// ── ToolCallResponse(本地保留,rpg-llm 没有此聚合类型) ──────────────

/// 一次 `call_with_tools` 合成结果(文本 + tool_calls + usage)。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ToolCallResponse {
    pub tool_calls: Vec<ToolCall>,
    pub text: String,
    pub usage: Usage,
}

// ── Common JSON helpers ───────────────────────────────────────────────

/// 从 LLM 输出里抠 JSON block。
///
/// 顺序:
/// 1. 整段就是 JSON(顶层 `[` 或 `{`)
/// 2. 反引号包裹的 ```json ... ``` fence
/// 3. 否则报错
pub fn extract_json_block(text: &str) -> AgentResult<&str> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err(AgentError::JsonParse("空字符串".to_string()));
    }
    if trimmed.starts_with('[') || trimmed.starts_with('{') {
        return Ok(trimmed);
    }
    if let Some(start) = trimmed.find("```") {
        let after = &trimmed[start + 3..];
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

// ── 通用 Shared backend alias ─────────────────────────────────────────

/// 6B-3:`Arc<dyn LlmBackend>` → `Arc<AnyBackend>` enum 静态分派,去虚表 + 去
/// 调用点的动态分派。各 agent 调 `self.llm.stream_chat(...)` 走 enum 的 inherent
/// 方法(签名与 trait 一致),改动只在类型别名这一处。
///
/// `AnyBackend` 同时 `impl LlmBackend`,故 common.rs 里既有的 `&dyn LlmBackend`
/// adapter helper(call_text / call_structured / call_with_tools / stream_text /
/// supports_native_tools)无需改写,`llm.as_ref()` 会自动 coerce 成 `&dyn`。
pub type SharedLlm = Arc<AnyBackend>;

// ── LlmBackend adapter helpers ────────────────────────────────────────

/// 默认 model_id(硬编码回退)。真实接入 catalog 之后由 caller 自己 build ChatRequest 覆盖;
/// 此处给 agent 适配层一个保底值,避免 model 为空被 provider 拒绝。
fn default_model_for(kind: rpg_llm::pipeline::BackendKind) -> &'static str {
    use rpg_llm::pipeline::BackendKind;
    match kind {
        BackendKind::Anthropic => "claude-haiku-4-5",
        BackendKind::Vertex => "gemini-3.5-flash",
        BackendKind::Openai | BackendKind::OpenaiCompat => "gpt-5-mini",
    }
}

/// 优先从 catalog 拿 selected model_id;无 catalog 才回退硬编码。
///
/// caller 在白名单外时请保留 `default_model_for(kind)` 旧调用;
/// 白名单内 agent 新增可用此签名。
pub fn default_model_for_catalog(
    kind: rpg_llm::pipeline::BackendKind,
    catalog: Option<&rpg_llm::registry::ModelCatalog>,
) -> String {
    if let Some(cat) = catalog {
        let id = cat.selected.model_id.clone();
        if !id.is_empty() {
            return id;
        }
    }
    default_model_for(kind).to_string()
}

/// 是否支持 native tool_use(Anthropic / Vertex / OpenAI 都支持)。
pub fn supports_native_tools(llm: &dyn LlmBackend) -> bool {
    use rpg_llm::pipeline::BackendKind;
    matches!(
        llm.kind(),
        BackendKind::Anthropic | BackendKind::Vertex | BackendKind::Openai | BackendKind::OpenaiCompat
    )
}

fn base_request(
    llm: &dyn LlmBackend,
    system: &str,
    messages: &[ChatMessage],
    max_tokens: usize,
) -> ChatRequest {
    ChatRequest {
        model: default_model_for(llm.kind()).to_string(),
        system: if system.is_empty() {
            None
        } else {
            Some(system.to_string())
        },
        messages: messages.to_vec(),
        tools: Vec::new(),
        temperature: None,
        max_tokens: Some(max_tokens.min(u32::MAX as usize) as u32),
        stream: false,
        extra: Value::Null,
    }
}

/// 一次性文本调用。drain `stream_chat`,把 Text chunk join 成 String。
pub async fn call_text(
    llm: &dyn LlmBackend,
    system: &str,
    messages: &[ChatMessage],
    max_tokens: usize,
) -> AgentResult<String> {
    let req = base_request(llm, system, messages, max_tokens);
    let mut stream = llm.stream_chat(req).await?;
    let mut out = String::new();
    while let Some(chunk) = stream.next().await {
        match chunk? {
            ChatChunk::Text(t) => out.push_str(&t),
            ChatChunk::Stop { .. } | ChatChunk::Usage(_) => {}
            ChatChunk::Thinking(_) | ChatChunk::ToolCall { .. } | ChatChunk::Error(_) => {}
        }
    }
    Ok(out)
}

/// JSON-mode 调用。可能的 provider 特定参数走 extra(OpenAI response_format /
/// Vertex response_mime_type)。失败时退化到普通 [`call_text`]。
pub async fn call_structured(
    llm: &dyn LlmBackend,
    system: &str,
    messages: &[ChatMessage],
    max_tokens: usize,
) -> AgentResult<String> {
    use rpg_llm::pipeline::BackendKind;
    let mut req = base_request(llm, system, messages, max_tokens);
    req.extra = match llm.kind() {
        BackendKind::Openai | BackendKind::OpenaiCompat => json!({
            "response_format": {"type": "json_object"}
        }),
        BackendKind::Vertex => json!({
            "response_mime_type": "application/json"
        }),
        // Anthropic 没有原生 JSON mode,system prompt 里强约束即可。
        _ => Value::Null,
    };
    let mut stream = llm.stream_chat(req).await?;
    let mut out = String::new();
    while let Some(chunk) = stream.next().await {
        if let ChatChunk::Text(t) = chunk? { out.push_str(&t) }
    }
    Ok(out)
}

// ── stream_text guard:Stream 被 Drop 时 abort 后台 task ─────────────────

/// 持有 JoinHandle 的 RAII guard。Drop 时 abort 对应 tokio task。
struct AbortOnDrop(JoinHandle<()>);

impl Drop for AbortOnDrop {
    fn drop(&mut self) {
        self.0.abort();
    }
}

/// 流式 String 序列。仅 surface Text chunk(Thinking / ToolCall 过滤掉)。
///
/// 改动(W5-2):
/// - `unbounded_channel` → `mpsc::channel(16)` 背压,避免 LLM 无限堆积
/// - 返回的 stream 内含 `AbortOnDrop` guard;stream drop → task abort,防泄漏
pub async fn stream_text(
    llm: SharedLlm,
    system: &str,
    messages: &[ChatMessage],
    max_tokens: usize,
) -> AgentResult<BoxStream<'static, AgentResult<String>>> {
    let mut req = base_request(llm.as_ref(), system, messages, max_tokens);
    req.stream = true;
    let llm_clone = llm.clone();
    // 背压 channel:缓冲 16 个 token,发送端阻塞等消费端跟上
    let (tx, rx) = tokio::sync::mpsc::channel::<AgentResult<String>>(16);
    let handle = tokio::spawn(async move {
        let mut stream = match llm_clone.stream_chat(req).await {
            Ok(s) => s,
            Err(e) => {
                let _ = tx.send(Err(AgentError::Llm(e.to_string()))).await;
                return;
            }
        };
        while let Some(chunk) = stream.next().await {
            match chunk {
                Ok(ChatChunk::Text(t)) => {
                    // send 返回 Err 代表接收端已 drop → 直接退出
                    if tx.send(Ok(t)).await.is_err() {
                        return;
                    }
                }
                Ok(_) => {}
                Err(e) => {
                    let _ = tx.send(Err(AgentError::Llm(e.to_string()))).await;
                    return;
                }
            }
        }
    });
    // guard 与 rx 打包进 stream,保证 stream drop 时 abort task
    let guard = AbortOnDrop(handle);
    let s = futures::stream::unfold(
        (rx, guard),
        |(mut rx, guard)| async move {
            match rx.recv().await {
                Some(item) => Some((item, (rx, guard))),
                None => {
                    drop(guard); // 显式 drop,编译器不会因 guard 未用报 warning
                    None
                }
            }
        },
    );
    Ok(s.boxed())
}

/// native tool_use 入口。把 stream_chat 的 Text + ToolCall + Usage 合并成
/// 一次 ToolCallResponse。
pub async fn call_with_tools(
    llm: &dyn LlmBackend,
    system: &str,
    messages: &[ChatMessage],
    tools: &[ToolSchema],
    max_tokens: usize,
) -> AgentResult<ToolCallResponse> {
    let mut req = base_request(llm, system, messages, max_tokens);
    req.tools = tools.to_vec();
    let mut stream = llm.stream_chat(req).await?;
    let mut text = String::new();
    let mut tool_calls: Vec<ToolCall> = Vec::new();
    let mut usage = Usage::default();
    while let Some(chunk) = stream.next().await {
        match chunk? {
            ChatChunk::Text(t) => text.push_str(&t),
            ChatChunk::ToolCall { id, name, input } => {
                tool_calls.push(ToolCall { id, name, input });
            }
            ChatChunk::Usage(u) => usage = u,
            ChatChunk::Thinking(_) | ChatChunk::Stop { .. } | ChatChunk::Error(_) => {}
        }
    }
    Ok(ToolCallResponse {
        tool_calls,
        text,
        usage,
    })
}

// ── GameState helper(真实 GameState 无 history / short_summary 字段) ──

/// 与 Python `state.turn` 对齐;真实 GameState 用方法暴露,这里包成 u64。
pub fn state_turn(state: &GameState) -> u64 {
    state.turn().max(0) as u64
}

/// `state.history_messages()` — 真实 GameState 不再保留 ChatMessage 历史
/// (Python 端历史在 branch_commits 表里)。暂返空,等真有需要再补 DB 加载。
pub fn state_history_messages(_state: &GameState) -> Vec<ChatMessage> {
    Vec::new()
}

/// `state.short_summary()` — 拼 player / world / memory 关键字段。
pub fn state_short_summary(state: &GameState) -> String {
    format!(
        "player={} world={} memory={}",
        state.data.player.name,
        state.data.world.time,
        state.data.memory.main_quest,
    )
}
