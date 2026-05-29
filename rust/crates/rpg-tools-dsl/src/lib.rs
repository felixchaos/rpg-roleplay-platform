//! rpg-tools-dsl — 工具注册表 + skill executor + MCP 管理
//! 对应 Python: rpg/tools_dsl/ + rpg/skill_executor.py + rpg/mcp_broker.py

pub mod error;
pub mod tool_registry;
pub mod skill_executor;
pub mod mcp;
pub mod mcp_broker;

pub use error::DslError;
pub use tool_registry::{ToolDefinition, ToolRegistry, GLOBAL_REGISTRY};
pub use skill_executor::{execute_skill, import_skill_bundle, ImportedSkill, SkillOutput};
pub use mcp::{
    AuditEntry, McpCatalog, McpServer, AUDIT_LOG,
    list_audit_entries, mirror_to_filesystem, validate_server,
};
pub use mcp_broker::{McpBroker, McpServerStatus, HealthStatus, ToolEntry};
