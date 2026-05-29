//! rpg-agents — 9 个代理 + GM 主控。
//!
//! 对应 Python: `rpg/agents/`。
//!
//! 公开 module:
//! - [`common`]                  — 共享 trait / 类型 / JSON 解析工具
//! - [`extractor`]               — 叙事 → state ops 抽取器
//! - [`acceptance_verifier`]     — LLM 验收条件判定
//! - [`anchor_seed_agent`]       — 世界线锚点 seed(纯启发式)
//! - [`black_swan_agent`]        — 主动触发世界事件 + 5 层 validator
//! - [`command_agent`]           — /set 命令 LLM 工具调用解析
//! - [`context_agent`]           — Demand Resolver
//! - [`phase_digest_agent`]      — save 级阶段摘要
//! - [`timeline_narrative_guard`] — 时间线跳跃后禁词扫描
//! - [`worldbook_agent`]         — 世界书分层信息架构
//! - [`gm`]                      — GameMaster(整套 LLM 调用 + 子代理串联)
//!
//! 当前实现深度:
//! - extractor / acceptance_verifier:LLM 调用 + JSON 解析完整。
//! - command_agent:native tool_use 优先 + JSON mode 回退。
//! - context_agent:LLM Demand 出 + rpg-context::run_providers 调度
//!   + context_bundle 拼装。
//! - gm:
//!     - respond / respond_stream:同步 / 流式叙事完整。
//!     - step:子代理串联 + extractor → ops 完整。
//!     - respond_stream_with_tools:native tool_use 迭代循环
//!       (call_with_tools → tool_router → 结果回灌 → 继续)。
//! - phase_digest_agent:DB 加载(save_phase_digests / branch_commits)
//!   + persist(upsert)实装,db pool 可选注入。
//! - anchor_seed_agent:DB seed(chapter_facts → save_anchor_states upsert)
//!   完整;支持 force / keep_satisfied / update_anchor。
//! - worldbook_agent::consult:timeline_anchor / phase_digest / chapter_facts /
//!   worldbook_entries 四层 ILIKE 模糊匹配 + confidence。
//! - black_swan_agent:snapshot + schema + 5 validator + dispatch_swan
//!   (apply_op 落地)完整;maybe_trigger 仅 stub LLM propose。
//! - timeline_narrative_guard:全量实装(禁词正则)。

pub mod common;

pub mod acceptance_verifier;
pub mod anchor_seed_agent;
pub mod black_swan_agent;
pub mod command_agent;
pub mod context_agent;
pub mod extractor;
pub mod phase_digest_agent;
pub mod timeline_narrative_guard;
pub mod worldbook_agent;

pub mod gm;

pub use common::{
    extract_json_block, AgentError, AgentResult, ChatMessage, GameState, LlmBackend, SharedLlm,
    ToolCall, ToolCallResponse, ToolSchema,
};
