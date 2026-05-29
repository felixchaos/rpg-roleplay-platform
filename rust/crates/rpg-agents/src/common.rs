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

/// `state.history_messages()` — 从 `state.data.history` 读取对话历史。
///
/// 对应 Python `state.history_messages()`:返回 history 数组最近 MAX_HISTORY_TURNS*2 条,
/// role=="user" 转 ChatMessage::user,其他转 ChatMessage::assistant。
pub fn state_history_messages(state: &GameState) -> Vec<ChatMessage> {
    const MAX_HISTORY_TURNS: usize = 6;
    const MAX_MSGS: usize = MAX_HISTORY_TURNS * 2;
    let history = &state.data.history;
    let slice = if history.len() > MAX_MSGS {
        &history[history.len() - MAX_MSGS..]
    } else {
        history.as_slice()
    };
    slice
        .iter()
        .filter_map(|entry| {
            let role = entry.get("role").and_then(|v| v.as_str()).unwrap_or("");
            let content = entry.get("content").and_then(|v| v.as_str()).unwrap_or("");
            if content.is_empty() {
                return None;
            }
            if role == "user" {
                Some(ChatMessage::user(content))
            } else {
                Some(ChatMessage::assistant(content))
            }
        })
        .collect()
}

/// SM-05: 从 markdown 文本里剥离常见秘密段(## 秘密 / ## 隐藏 / ## 元知识 等)
/// + 句子级元知识关键词,返回 NPC 可见部分。
/// 对应 Python `_strip_secret_sections` + `_strip_meta_knowledge_sentences`。
pub fn strip_secret_sections(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    // 第一步: 按 markdown 标题剥除秘密段
    // 匹配 "##+ 秘密|隐藏|内心|元知识|真实身份|来历|背景秘密|未公开" 直到下一个标题或结尾
    let secret_headings = [
        "秘密", "隐藏", "内心", "元知识", "真实身份", "来历", "背景秘密", "未公开",
    ];
    let lines: Vec<&str> = text.lines().collect();
    let mut result_lines: Vec<&str> = Vec::new();
    let mut skip = false;
    for line in &lines {
        let trimmed = line.trim();
        // 检查是否是 ##+ 标题
        if trimmed.starts_with("##") {
            let heading = trimmed.trim_start_matches('#').trim();
            if secret_headings.contains(&heading) {
                skip = true;
                continue;
            } else {
                // 遇到其他 ## 标题则结束跳过
                skip = false;
            }
        }
        if !skip {
            result_lines.push(line);
        }
    }
    let no_sections = result_lines.join("\n").trim().to_string();

    // 第二步: 句子级元知识关键词过滤
    // 按 。!?;；\n 分句,含元知识关键词的整句移除
    let meta_patterns = [
        "穿越", "重生回", "重生至", "重生到", "重生成", "转生",
        "原著", "原书", "原作",
        "知道剧情", "知道未来", "知道历史", "知道结局", "知道走向",
        "记得剧情", "记得未来", "记得历史", "记得结局", "记得走向",
        "预知剧情", "预知未来", "预知历史", "预知结局", "预知走向",
        "穿越前", "穿越以前", "穿越之前", "来这之前", "来到这",
        "前世", "21世纪", "22世纪",
    ];
    let mut kept = String::new();
    let mut buf = String::new();
    let delimiters = ['。', '!', '?', ';', '；', '\n'];
    for ch in no_sections.chars() {
        if delimiters.contains(&ch) {
            buf.push(ch);
            let has_meta = meta_patterns.iter().any(|p| buf.contains(p));
            if !has_meta {
                kept.push_str(&buf);
            }
            buf.clear();
        } else {
            buf.push(ch);
        }
    }
    if !buf.is_empty() {
        let has_meta = meta_patterns.iter().any(|p| buf.contains(p));
        if !has_meta {
            kept.push_str(&buf);
        }
    }

    // 压缩多余空行
    let cleaned = kept.trim().to_string();
    let mut out = String::new();
    let mut blank_count = 0u32;
    for line in cleaned.lines() {
        if line.trim().is_empty() {
            blank_count += 1;
            if blank_count <= 2 {
                out.push('\n');
            }
        } else {
            blank_count = 0;
            out.push_str(line);
            out.push('\n');
        }
    }
    out.trim().to_string()
}

/// `state.short_summary()` — 对应 Python `state.short_summary()`,包含玩家、时间线、
/// 关系、长期记忆、世界线变量和当前回合等关键字段。
pub fn state_short_summary(state: &GameState) -> String {
    let p = &state.data.player;
    let w = &state.data.world;
    let m = &state.data.memory;
    let permissions = &state.data.permissions;
    let worldline = &state.data.worldline;
    let pp = &state.data.player_private;

    // 角色卡详情段(appearance, personality, speech_style, aliases, identity_role_desc)
    // SM-05: appearance 和 personality 注入前用 strip_secret_sections 剥离秘密段
    let mut card_lines: Vec<String> = Vec::new();
    for key in &["appearance", "personality", "speech_style", "aliases", "identity_role_desc"] {
        if let Some(val) = p.extra.get(*key) {
            let raw = match val {
                serde_json::Value::String(s) if !s.is_empty() => s.clone(),
                serde_json::Value::Array(arr) if !arr.is_empty() => {
                    arr.iter().filter_map(|v| v.as_str()).collect::<Vec<_>>().join(", ")
                }
                _ => continue,
            };
            // 对 appearance / personality 做秘密段剥离
            let text = match *key {
                "appearance" | "personality" => strip_secret_sections(&raw),
                _ => raw,
            };
            if text.is_empty() {
                continue;
            }
            let label = match *key {
                "appearance" => "外貌",
                "personality" => "性格",
                "speech_style" => "说话风格",
                "aliases" => "别名",
                "identity_role_desc" => "身份描述",
                _ => *key,
            };
            card_lines.push(format!("{}：{}", label, text));
        }
    }
    let card_text = if card_lines.is_empty() {
        String::new()
    } else {
        format!("\n{}", card_lines.join("\n"))
    };

    // 玩家本轮揭示(revealed_this_turn)
    let revealed_text = pp.flags.get("revealed_this_turn")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| format!("\n\n【玩家本轮揭示】\n{}", s))
        .unwrap_or_default();

    // 关系段
    let rel_text = if state.data.relationships.is_empty() {
        "  （尚未与任何人建立明确关系）".to_string()
    } else {
        state.data.relationships.iter().take(12).map(|(k, v)| {
            let status = match v {
                serde_json::Value::String(s) => s.clone(),
                serde_json::Value::Object(o) => o.get("status")
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string(),
                other => other.to_string(),
            };
            format!("  · {}：{}", k, status)
        }).collect::<Vec<_>>().join("\n")
    };

    // 已知事件段
    let known_events: Vec<String> = w.known_events.iter().filter_map(|e| {
        e.as_str().map(|s| format!("  · {}", s))
    }).collect();
    let known = if known_events.is_empty() {
        "  （暂无已知事件）".to_string()
    } else {
        known_events.join("\n")
    };

    // 记忆段
    let mut memory_lines: Vec<String> = Vec::new();
    if !m.main_quest.is_empty() {
        memory_lines.push(format!("主线：{}", m.main_quest));
    }
    if !m.current_objective.is_empty() {
        memory_lines.push(format!("当前目标：{}", m.current_objective));
    }
    let ability_limit = 6usize;
    let resource_limit = 6usize;
    let pinned_limit = 6usize;
    let (fact_limit, note_limit) = match m.mode.as_str() { "deep" => (10, 8), "normal" => (5, 3), _ => (0, 0) };
    for x in m.abilities.iter().take(ability_limit) {
        if let Some(s) = x.as_str() { memory_lines.push(format!("能力：{}", s)); }
    }
    for x in m.resources.iter().take(resource_limit) {
        if let Some(s) = x.as_str() { memory_lines.push(format!("资源：{}", s)); }
    }
    for x in m.pinned.iter().take(pinned_limit) {
        if let Some(s) = x.as_str() { memory_lines.push(format!("固定记忆：{}", s)); }
    }
    for x in m.facts.iter().take(fact_limit) {
        if let Some(s) = x.as_str() { memory_lines.push(format!("事实：{}", s)); }
    }
    for x in m.notes.iter().take(note_limit) {
        if let Some(s) = x.as_str() { memory_lines.push(format!("笔记：{}", s)); }
    }
    let memory_text = if memory_lines.is_empty() {
        "  （暂无长期记忆）".to_string()
    } else {
        memory_lines.iter().map(|l| format!("  · {}", l)).collect::<Vec<_>>().join("\n")
    };

    // 权限模式标签
    let perm_label = rpg_state::permission_label(&permissions.mode);

    // 世界线变量段
    let variable_text = if worldline.user_variables.is_empty() {
        "  （暂无用户变量）".to_string()
    } else {
        worldline.user_variables.iter().filter(|(name, _)| name.as_str() != "story_intent").take(12).map(|(name, info)| {
            let val = info.get("value").and_then(|v| v.as_str()).unwrap_or("");
            format!("  · {}={}", name, val)
        }).collect::<Vec<_>>().join("\n")
    };

    // 时间线锚定
    let anchor_state = &w.timeline.anchor_state;
    let current_phase = if w.timeline.current_phase.is_empty() {
        "未知".to_string()
    } else {
        w.timeline.current_phase.clone()
    };
    let pending_jump = match &w.timeline.pending_jump {
        Some(v) if !v.is_null() => v.to_string(),
        _ => "无".to_string(),
    };

    // SM-05: background 注入前剥离秘密段
    let background_stripped = strip_secret_sections(&p.background);
    format!(
        "【玩家档案】\n姓名：{}\n定位：{}\n背景：{}\n当前位置：{}{}\n\n\
【当前时间线】{}\n\
【时间线锚定】\n  · 状态：{}\n  · 阶段：{}\n  · 待确认跳跃：{}\n\n\
【已知事件】\n{}\n\n\
【关系状态】\n{}\n\n\
【长期记忆】\n{}\n\n\
【权限与世界线】\n  · LLM写入权限：{}\n  · 用户变量：\n{}\n\n\
【当前回合】第 {} 回合{}",
        p.name, p.role, background_stripped, p.current_location, card_text,
        w.time,
        anchor_state, current_phase, pending_jump,
        known,
        rel_text,
        memory_text,
        perm_label,
        variable_text,
        state.data.turn,
        revealed_text,
    )
}
