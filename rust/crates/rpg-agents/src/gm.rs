//! gm — GameMaster 统一接口。
//!
//! 对应 Python: `rpg/agents/gm/master.py` + `rpg/agents/gm/helpers.py`
//!
//! 公开接口:
//! - `GameMaster::new(llm)` — 用注入的 LlmBackend 构造
//! - `generate_opening(state)` — 一次性出开场
//! - `respond(user_input, retrieved, state)` — 同步回复
//! - `respond_stream(user_input, retrieved, state)` — 流式回复
//! - `step(user_input, state)` — Stream<GmEvent>(高层 API,内部分别调
//!   context_agent → respond_stream_with_tools → extractor)
//!
//! 这里把 GM step 主流程做得相对完整(满足任务要求),sub-agent 的
//! 内部细节走对应 module。

use std::sync::Arc;

use async_trait::async_trait;
use futures::stream::{BoxStream, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::common::{
    call_text, call_with_tools, state_history_messages, state_short_summary, stream_text,
    AgentResult, ChatMessage, GameState, SharedLlm, ToolSchema,
};
use rpg_state::{apply_op, Op};
use serde_json::json;
use crate::context_agent::{ContextAgent, ContextAgentInput, Demand};
use crate::extractor::{ExtractorAgent, ExtractorInput, ExtractorOutput};
use crate::timeline_narrative_guard::{
    detect_time_jump_violations, record_violations_to_audit, Violation,
};

const SYSTEM_BASE: &str = include_str!("prompts/gm_master.txt");
const OPENING_PROMPT: &str = include_str!("prompts/gm_opening.txt");

const DEFAULT_MAX_TOKENS: usize = 800;
const OPENING_MAX_TOKENS: usize = 600;

const DYNAMIC_CONTEXT_TPL: &str = "【当前剧情状态】\n{player_summary}\n\n【本轮上下文包】\n{retrieved_context}{transmigrator_note}";

const TRANSMIGRATOR_NOTE: &str = r#"
【穿越者特殊规则】
- 玩家角色是来自另一个世界的穿越者,读过这个世界的原著小说,对部分剧情走向有超前认知
- 拥有魔力∞,但用法尚未摸清
- 外表:白发红瞳少女,在这个世界会引发旁人注目
- 体现信息不对称的趣味:她知道一些别人不知道的,但也有很多书里没写到的盲区
- 不要让她"一眼看穿一切"
"#;

// ── 流事件 ───────────────────────────────────────────────────────────

/// GM 一次 step 期间输出的事件流。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[allow(clippy::large_enum_variant)] // Demand 字段大但非热路径，不值得引入 Box 间接层
pub enum GmEvent {
    /// 子代理出 Demand。
    Demand { demand: Demand },
    /// 中途生成的叙事文本片段。
    Text { text: String },
    /// LLM 发起 MCP 工具调用。
    ToolCall {
        server_id: String,
        tool: String,
        arguments: Value,
    },
    /// 工具返回。
    ToolResult {
        ok: bool,
        result: Option<Value>,
        error: Option<String>,
    },
    /// 工具解析失败(协议错误)。
    ToolError { error: String, raw: String },
    /// extractor 抽出的 ops。
    StateOps { ops: Vec<Value> },
    /// 时间线 guard 触发的违规。
    GuardViolations { violations: Vec<Violation> },
    /// 一轮结束。
    Done,
}

// ── World section provider 抽象 ──────────────────────────────────────

/// 给 system prompt 注入 world 段落(剧本相关)。
/// rpg-context 完成后改成 `pub trait WorldSectionProvider` 的具体实现。
#[async_trait]
pub trait WorldSection: Send + Sync {
    async fn world_section_for(&self, state: &GameState) -> String;
}

/// 默认实现:空 world section(对应 freeform 模式)。
pub struct EmptyWorldSection;

#[async_trait]
impl WorldSection for EmptyWorldSection {
    async fn world_section_for(&self, _state: &GameState) -> String {
        String::new()
    }
}

// ── MCP tool 调用路由抽象 ────────────────────────────────────────────

/// 接 mcp_broker 的入口。rpg-platform 实现后改成 trait re-export。
#[async_trait]
pub trait ToolCallRouter: Send + Sync {
    async fn call(&self, server_id: &str, tool: &str, arguments: Value) -> ToolCallResult;
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallResult {
    pub ok: bool,
    pub result: Option<Value>,
    pub error: Option<String>,
}

pub struct NoopToolRouter;

#[async_trait]
impl ToolCallRouter for NoopToolRouter {
    async fn call(&self, _server_id: &str, _tool: &str, _arguments: Value) -> ToolCallResult {
        ToolCallResult {
            ok: false,
            result: None,
            error: Some("no MCP router configured".into()),
        }
    }
}

// ── GameMaster ──────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct GmConfig {
    pub default_max_tokens: usize,
    pub opening_max_tokens: usize,
    pub max_tool_iterations: usize,
}

impl Default for GmConfig {
    fn default() -> Self {
        Self {
            default_max_tokens: DEFAULT_MAX_TOKENS,
            opening_max_tokens: OPENING_MAX_TOKENS,
            max_tool_iterations: 3,
        }
    }
}

pub struct GameMaster {
    llm: SharedLlm,
    world_section: Arc<dyn WorldSection>,
    tool_router: Arc<dyn ToolCallRouter>,
    context_agent: Arc<ContextAgent>,
    extractor: Arc<ExtractorAgent>,
    config: GmConfig,
}

impl GameMaster {
    pub fn new(llm: SharedLlm) -> Self {
        let ctx = Arc::new(ContextAgent::new(llm.clone()));
        let ext = Arc::new(ExtractorAgent::new(llm.clone()));
        Self {
            llm,
            world_section: Arc::new(EmptyWorldSection),
            tool_router: Arc::new(NoopToolRouter),
            context_agent: ctx,
            extractor: ext,
            config: GmConfig::default(),
        }
    }

    pub fn with_world_section(mut self, ws: Arc<dyn WorldSection>) -> Self {
        self.world_section = ws;
        self
    }

    pub fn with_tool_router(mut self, r: Arc<dyn ToolCallRouter>) -> Self {
        self.tool_router = r;
        self
    }

    pub fn with_config(mut self, c: GmConfig) -> Self {
        self.config = c;
        self
    }

    // ── system prompt 构造 ──────────────────────────────────

    async fn build_system(&self, state: &GameState) -> String {
        let ws = self.world_section.world_section_for(state).await;
        // SYSTEM_BASE 里 literal JSON 含 `{` / `}`;不能 format!,只能 replace。
        SYSTEM_BASE.replace("{world_section}", &ws)
    }

    fn dynamic_context(&self, player_summary: &str, retrieved: &str) -> String {
        let is_transmigrator = player_summary.contains("穿越者");
        let note = if is_transmigrator { TRANSMIGRATOR_NOTE } else { "" };
        let retr = if retrieved.is_empty() { "(本轮无额外召回)" } else { retrieved };
        DYNAMIC_CONTEXT_TPL
            .replace("{player_summary}", player_summary)
            .replace("{retrieved_context}", retr)
            .replace("{transmigrator_note}", note)
    }

    fn turn_message(&self, user_input: &str, state: &GameState, retrieved: &str) -> String {
        format!(
            "{}\n\n【玩家本轮输入】\n{}",
            self.dynamic_context(&state_short_summary(state), retrieved),
            user_input,
        )
    }

    // ── 一次性回复 ──────────────────────────────────────────

    /// 生成开场白。
    #[tracing::instrument(skip(self, state), fields(action = "generate_opening"))]
    pub async fn generate_opening(
        &self,
        state: &GameState,
        retrieved: &str,
    ) -> AgentResult<String> {
        let system = self.build_system(state).await;
        let message = self.turn_message(OPENING_PROMPT, state, retrieved);
        let messages = vec![ChatMessage::user(message)];
        call_text(
            self.llm.as_ref(),
            &system,
            &messages,
            self.config.opening_max_tokens,
        )
        .await
    }

    /// 同步主响应。
    #[tracing::instrument(skip(self, state, retrieved), fields(action = "respond"))]
    pub async fn respond(
        &self,
        user_input: &str,
        retrieved: &str,
        state: &GameState,
    ) -> AgentResult<String> {
        let system = self.build_system(state).await;
        let mut messages = state_history_messages(state);
        messages.push(ChatMessage::user(self.turn_message(user_input, state, retrieved)));
        call_text(
            self.llm.as_ref(),
            &system,
            &messages,
            self.config.default_max_tokens,
        )
        .await
    }

    /// 流式回复(简版,只产 text chunk)。
    #[tracing::instrument(skip(self, state, retrieved), fields(action = "respond_stream"))]
    pub async fn respond_stream(
        &self,
        user_input: &str,
        retrieved: &str,
        state: &GameState,
    ) -> AgentResult<BoxStream<'static, AgentResult<String>>> {
        let system = self.build_system(state).await;
        let mut messages = state_history_messages(state);
        messages.push(ChatMessage::user(self.turn_message(user_input, state, retrieved)));
        stream_text(
            self.llm.clone(),
            &system,
            &messages,
            self.config.default_max_tokens,
        )
        .await
    }

    // ── 高层 step:一站式流式回路 ───────────────────────────

    /// 一次完整的 GM step:
    ///   1. context_agent 出 Demand
    ///   2. respond_stream 出叙事
    ///   3. timeline_narrative_guard 扫禁词
    ///   4. extractor 把叙事 → ops
    ///   5. apply ops 到 state(留 TODO,等 rpg-state 完成)
    #[tracing::instrument(skip(self, state), fields(action = "step"))]
    pub async fn step(
        self: Arc<Self>,
        user_input: String,
        state: Arc<tokio::sync::RwLock<GameState>>,
    ) -> BoxStream<'static, GmEvent> {
        // 用 async-stream 风格手搓 (避开额外依赖):
        // 1) curator
        let s = self.clone();
        let user_input = user_input.clone();
        let stream = async_stream_iter(move |tx| async move {
            // 1. Demand
            let snapshot = state.read().await.clone();
            let ctx_in = ContextAgentInput {
                user_input: user_input.clone(),
                directives: Vec::new(),
            };
            match s.context_agent.run(ctx_in, &snapshot).await {
                Ok(out) => {
                    tx.send(GmEvent::Demand { demand: out.demand.clone() }).ok();
                }
                Err(e) => {
                    tracing::warn!("[gm.step] context_agent failed: {e}");
                }
            }

            // 2. respond_stream 累积叙事
            // 重新读 state(curator 可能因为 clarifying_question 直接中止;
            // 本骨架不分支,直接进 GM)。
            let snapshot = state.read().await.clone();
            let retrieved = ""; // TODO: 从 context bundle 拿
            let mut narrative = String::new();
            match s
                .respond_stream(&user_input, retrieved, &snapshot)
                .await
            {
                Ok(mut stream) => {
                    while let Some(chunk_res) = stream.next().await {
                        match chunk_res {
                            Ok(chunk) => {
                                narrative.push_str(&chunk);
                                if tx
                                    .send(GmEvent::Text { text: chunk })
                                    .is_err()
                                {
                                    return;
                                }
                            }
                            Err(e) => {
                                tracing::warn!("[gm.step] stream chunk error: {e}");
                                break;
                            }
                        }
                    }
                }
                Err(e) => {
                    tracing::warn!("[gm.step] respond_stream failed: {e}");
                }
            }

            // 3. timeline guard
            {
                let mut st = state.write().await;
                let viols = detect_time_jump_violations(&narrative, &st);
                if !viols.is_empty()
                    && record_violations_to_audit(&mut st, &viols).is_ok() {
                        tx.send(GmEvent::GuardViolations { violations: viols }).ok();
                    }
            }

            // 4. extractor
            let snapshot = state.read().await.clone();
            let ex_in = ExtractorInput::new(narrative.clone());
            match s.extractor.run(ex_in, &snapshot).await {
                Ok(ExtractorOutput { ops }) => {
                    // 5. apply ops 到真 rpg_state::GameState — force=true (GM 自主写入,
                    // 走 audit_log + user_locked 但不被权限模式拦截)。
                    if !ops.is_empty() {
                        let mut st = state.write().await;
                        for op_value in ops.iter() {
                            match serde_json::from_value::<Op>(op_value.clone()) {
                                Ok(op) => {
                                    if let Err(e) =
                                        apply_op(&mut st, op, "gm", true)
                                    {
                                        tracing::warn!(
                                            "[gm.step] apply_op 失败 ({:?}): {e}",
                                            op_value
                                        );
                                    }
                                }
                                Err(e) => {
                                    tracing::warn!(
                                        "[gm.step] op 反序列化失败 ({:?}): {e}",
                                        op_value
                                    );
                                }
                            }
                        }
                    }
                    tx.send(GmEvent::StateOps { ops }).ok();
                }
                Err(e) => {
                    tracing::warn!("[gm.step] extractor failed: {e}");
                }
            }

            tx.send(GmEvent::Done).ok();
        });
        stream.boxed()
    }

    // ── MCP tool 循环(native tool_use,迭代版) ──────────────
    //
    // 流程:
    //  1) 构造 system + 初始 messages
    //  2) call_with_tools(system, messages, tools) → ToolCallResponse
    //  3) 如果 resp.tool_calls 为空 → emit Text(resp.text) + Done,退出
    //  4) 否则:每个 tool_call emit ToolCall + 调 tool_router(server_id 来自
    //     ToolSchema.server_id 或 caller 在 tool 名里编码;空时用空 server)
    //     → emit ToolResult,把结果作为新 user message(JSON dump)拼回
    //     messages,迭代下一轮。
    //  5) 达到 max_iterations 或得到不带 tool_calls 的回复 → 收尾。
    //
    // 注:本路径不依赖 LLM 的流式 stream();因为 placeholder LlmBackend
    // trait 的 stream() 只产 String chunks,无法表达 tool_call。等 rpg-llm
    // 真正接入(其 ChatChunk::ToolCall variant)后,可改成 stream_chat 路径。
    #[tracing::instrument(skip(self, state, retrieved, tools), fields(action = "respond_stream_with_tools"))]
    pub async fn respond_stream_with_tools(
        self: Arc<Self>,
        user_input: String,
        retrieved: String,
        state: GameState,
        tools: Vec<ToolSchema>,
        max_iterations: usize,
        max_tokens: usize,
    ) -> AgentResult<BoxStream<'static, GmEvent>> {
        let s = self.clone();
        let stream = async_stream_iter(move |tx| async move {
            let system = s.build_system(&state).await;
            let mut messages = state_history_messages(&state);
            messages.push(ChatMessage::user(s.turn_message(
                &user_input,
                &state,
                &retrieved,
            )));

            let mut iter = 0usize;
            loop {
                if iter >= max_iterations {
                    tx.send(GmEvent::ToolError {
                        error: format!("超出 max_iterations={max_iterations}"),
                        raw: String::new(),
                    })
                    .ok();
                    break;
                }
                iter += 1;

                let resp_res = call_with_tools(
                    s.llm.as_ref(),
                    &system,
                    &messages,
                    &tools,
                    max_tokens,
                )
                .await;
                let resp = match resp_res {
                    Ok(r) => r,
                    Err(e) => {
                        tx.send(GmEvent::ToolError {
                            error: format!("call_with_tools 失败: {e}"),
                            raw: String::new(),
                        })
                        .ok();
                        break;
                    }
                };

                // 文本片段
                if !resp.text.is_empty() {
                    tx.send(GmEvent::Text {
                        text: resp.text.clone(),
                    })
                    .ok();
                }

                // 没有 tool_call → 收尾。
                if resp.tool_calls.is_empty() {
                    break;
                }

                // 把 assistant 这一轮(含 tool_use)写回 messages 以维护 turn 完整性。
                messages.push(ChatMessage::assistant(resp.text.clone()));

                let mut tool_results: Vec<serde_json::Value> = Vec::new();
                for tc in resp.tool_calls.iter() {
                    // 解析 tool 名:支持 "server_id__tool" 或 "server.tool" 命名空间。
                    let (server_id, tool_name) = parse_namespaced_tool(&tc.name);
                    tx.send(GmEvent::ToolCall {
                        server_id: server_id.clone(),
                        tool: tool_name.clone(),
                        arguments: tc.input.clone(),
                    })
                    .ok();
                    let res = s
                        .tool_router
                        .call(&server_id, &tool_name, tc.input.clone())
                        .await;
                    tx.send(GmEvent::ToolResult {
                        ok: res.ok,
                        result: res.result.clone(),
                        error: res.error.clone(),
                    })
                    .ok();
                    tool_results.push(json!({
                        "tool_name": tc.name,
                        "ok": res.ok,
                        "result": res.result,
                        "error": res.error,
                    }));
                }

                // 把 tool_results 作为新的 user 消息(JSON 序列化)送回。
                let tool_msg_text = serde_json::to_string(&tool_results)
                    .unwrap_or_else(|_| "[]".to_string());
                messages.push(ChatMessage::user(format!(
                    "[tool_results]\n{tool_msg_text}"
                )));
                // 继续下一轮 LLM。
            }

            tx.send(GmEvent::Done).ok();
        });
        Ok(stream.boxed())
    }
}

/// 拆 "server__tool" 或 "server.tool" → (server, tool)。
/// 无分隔符则返回 ("", full)。
fn parse_namespaced_tool(full: &str) -> (String, String) {
    if let Some(pos) = full.find("__") {
        return (full[..pos].to_string(), full[pos + 2..].to_string());
    }
    if let Some(pos) = full.find('.') {
        return (full[..pos].to_string(), full[pos + 1..].to_string());
    }
    (String::new(), full.to_string())
}

// ── async stream 工具:用 tokio mpsc + poll_fn 把 producer-future 包成 Stream ──

fn async_stream_iter<F, Fut>(f: F) -> impl futures::Stream<Item = GmEvent> + Send
where
    F: FnOnce(tokio::sync::mpsc::UnboundedSender<GmEvent>) -> Fut + Send + 'static,
    Fut: std::future::Future<Output = ()> + Send + 'static,
{
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<GmEvent>();
    tokio::spawn(async move {
        f(tx).await;
    });
    futures::stream::poll_fn(move |cx| rx.poll_recv(cx))
}
