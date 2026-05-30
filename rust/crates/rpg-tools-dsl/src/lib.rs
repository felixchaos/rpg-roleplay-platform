//! rpg-tools-dsl — 工具注册表 + skill executor + MCP 管理 + 命令分发器
//! 对应 Python: rpg/tools_dsl/ + rpg/skill_executor.py + rpg/mcp_broker.py

pub mod dispatcher;
pub mod error;
pub mod tool_registry;
pub mod skill_executor;
pub mod sandbox;
pub mod mcp;
pub mod mcp_broker;
pub mod state_op_map;
pub mod chat_tool_router;

pub use error::DslError;
pub use tool_registry::{ToolDefinition, ToolRegistry, GLOBAL_REGISTRY};
// dispatcher 模块重要类型 — 使用模块前缀避免与 chat_tool_router 占位类型冲突。
// 消费方推荐: `use rpg_tools_dsl::dispatcher::{...}` 按需导入。
pub use dispatcher::{
    Scope, Origin,
    ToolSpec as DispatchToolSpec,
    ToolCallEnvelope as DispatchEnvelope,
    ToolResult as DispatchToolResult,
    ToolExecContext,
    ToolRegistry as DispatchToolRegistry,
    ToolDispatcher,
    ToolExecutor,
    DispatchError,
    MAX_TRACE_DEPTH, MAX_CALLS_PER_USER_PER_SECOND,
    AUDIT_LOG_LIMIT, RECENT_AUDIT_LIMIT,
};
pub use skill_executor::{execute_skill, import_skill_bundle, ImportedSkill, SkillOutput};
pub use sandbox::{
    default_sandbox, ContainerRuntime, ContainerSandbox, RlimitSandbox, SandboxLimits,
    SkillSandbox,
};
pub use mcp::{
    AuditEntry, McpCatalog, McpServer, AUDIT_LOG,
    list_audit_entries, mirror_to_filesystem, validate_server,
};
pub use mcp_broker::{McpBroker, McpServerStatus, HealthStatus, ToolEntry};
pub use state_op_map::{map_op_to_tool, expand_list_value_to_tool_calls};
pub use chat_tool_router::{
    DISPATCHER_SENTINEL, build_unified_tool_list,
    UnifiedToolRouter, ToolResult as RouterToolResult,
    DispatcherRegistry, DispatcherToolSpec, ToolCallEnvelope,
    DispatchResult, NoopDispatcherRegistry,
};
