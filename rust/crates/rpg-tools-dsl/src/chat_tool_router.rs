//! chat_tool_router — Unified tool router for GM tool_use
//!
//! Corresponds to Python: rpg/tools_dsl/chat_tool_router.py (task 87 Phase 5)
//!
//! When a GM streaming response calls tools, the router decides:
//! - dispatcher tools (server_id == DISPATCHER_SENTINEL or name in registry) -> ToolDispatcher
//! - MCP tools (real server_id) -> McpBroker.call_tool
//!
//! The unified router is constructed within the chat handler, carrying
//! current user_id / save_id / trace_id context.

use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tracing::warn;

use crate::mcp_broker::McpBroker;

// ── Sentinel ─────────────────────────────────────────────────────────────────

/// Sentinel server_id for dispatcher tools.
///
/// MUST NOT contain "__" (the backend uses "__" as server_id / tool_name separator;
/// if the sentinel contained "__", full_name parsing would break and the router
/// would fall through to mcp_broker).
pub const DISPATCHER_SENTINEL: &str = "dispatcher";

// ── Dispatcher placeholder types ─────────────────────────────────────────────
//
// The full ToolDispatcher / ToolCallEnvelope / ToolResult / DispatcherRegistry
// are being built by another agent in the `dispatcher` module. We define the
// minimal trait + types needed here so the router compiles independently.
// When the real dispatcher lands, replace these with `use crate::dispatcher::*`.

/// Result of a dispatcher tool call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DispatchResult {
    pub ok: bool,
    pub result: Value,
    pub error: Option<String>,
}

/// Envelope for a single dispatcher tool call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallEnvelope {
    pub user_id: i64,
    pub save_id: Option<i64>,
    pub script_id: Option<i64>,
    pub tool: String,
    pub args: Value,
    pub origin: String,
    pub trace_id: String,
    pub depth: u32,
}

/// Spec entry from the dispatcher registry (for building tool lists).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DispatcherToolSpec {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
}

/// Trait abstracting the dispatcher registry so the router is testable
/// without a full dispatcher implementation.
pub trait DispatcherRegistry: Send + Sync {
    /// Check if a tool name is registered in the dispatcher.
    fn has(&self, tool_name: &str) -> bool;

    /// List tools available for a given origin.
    fn list_for_origin(&self, origin: &str) -> Vec<DispatcherToolSpec>;

    /// Synchronously dispatch a tool call.
    fn dispatch_sync(&self, envelope: ToolCallEnvelope) -> DispatchResult;
}

/// A no-op dispatcher registry used when no real dispatcher is available yet.
/// All calls return "not implemented" errors.
#[derive(Debug, Default)]
pub struct NoopDispatcherRegistry;

impl DispatcherRegistry for NoopDispatcherRegistry {
    fn has(&self, _tool_name: &str) -> bool {
        false
    }

    fn list_for_origin(&self, _origin: &str) -> Vec<DispatcherToolSpec> {
        Vec::new()
    }

    fn dispatch_sync(&self, envelope: ToolCallEnvelope) -> DispatchResult {
        DispatchResult {
            ok: false,
            result: Value::Null,
            error: Some(format!(
                "dispatcher not initialized; cannot call tool '{}'",
                envelope.tool
            )),
        }
    }
}

// ── build_unified_tool_list ──────────────────────────────────────────────────

/// Merge MCP tool entries + dispatcher-registered tools for the given origin.
///
/// Output format matches `McpBroker::discover_all_tools` entries:
/// ```json
/// {"server_id": "...", "name": "...", "description": "...", "schema": {...}}
/// ```
/// Dispatcher tools use `server_id = DISPATCHER_SENTINEL`.
pub fn build_unified_tool_list(
    mcp_tools: Option<&[Value]>,
    origin: &str,
    registry: &dyn DispatcherRegistry,
) -> Vec<Value> {
    let mut out: Vec<Value> = mcp_tools.unwrap_or(&[]).to_vec();

    for spec in registry.list_for_origin(origin) {
        out.push(json!({
            "server_id": DISPATCHER_SENTINEL,
            "name": spec.name,
            "description": spec.description,
            "schema": spec.input_schema,
        }));
    }

    out
}

// ── ToolResult (router output) ───────────────────────────────────────────────

/// Unified tool call result, compatible with MCP broker response format.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    pub ok: bool,
    pub result: Value,
    pub error: Option<String>,
}

// ── UnifiedToolRouter ────────────────────────────────────────────────────────

/// Unified tool router wrapping a dispatcher registry + MCP broker fallback.
///
/// Constructed per-request in the chat handler with the current session context
/// (user_id, save_id, script_id, trace_id).
pub struct UnifiedToolRouter {
    /// Dispatcher registry for built-in game tools.
    registry: Arc<dyn DispatcherRegistry>,
    /// MCP broker for external tool servers.
    mcp_broker: Arc<McpBroker>,
    /// Current user context.
    user_id: i64,
    save_id: Option<i64>,
    script_id: Option<i64>,
    trace_id: String,
    /// MCP call timeout in seconds.
    mcp_timeout_secs: u64,
}

impl UnifiedToolRouter {
    /// Create a new router bound to a specific session context.
    pub fn new(
        registry: Arc<dyn DispatcherRegistry>,
        mcp_broker: Arc<McpBroker>,
        user_id: i64,
        save_id: Option<i64>,
        script_id: Option<i64>,
        trace_id: String,
    ) -> Self {
        Self {
            registry,
            mcp_broker,
            user_id,
            save_id,
            script_id,
            trace_id,
            mcp_timeout_secs: crate::mcp_broker::DEFAULT_CALL_TIMEOUT_SECS,
        }
    }

    /// Set the MCP call timeout (default: 30s).
    pub fn with_mcp_timeout(mut self, secs: u64) -> Self {
        self.mcp_timeout_secs = secs;
        self
    }

    /// Route a tool call to either the dispatcher or an MCP server.
    ///
    /// Routing logic:
    /// 1. If `server_id == DISPATCHER_SENTINEL` -> dispatcher
    /// 2. If registry.has(tool_name) -> dispatcher (regardless of server_id)
    /// 3. Otherwise -> mcp_broker.call_tool
    pub async fn route(
        &self,
        server_id: &str,
        tool_name: &str,
        arguments: Value,
    ) -> ToolResult {
        // Check if this should go to the dispatcher
        let is_dispatcher = server_id == DISPATCHER_SENTINEL
            || server_id.is_empty()
            || self.registry.has(tool_name);

        if is_dispatcher {
            let envelope = ToolCallEnvelope {
                user_id: self.user_id,
                save_id: self.save_id,
                script_id: self.script_id,
                tool: tool_name.to_owned(),
                args: arguments,
                origin: "llm_chat".to_owned(),
                trace_id: self.trace_id.clone(),
                depth: 1, // GM response path is already in a trace; mark depth=1
            };

            let result = self.registry.dispatch_sync(envelope);
            return ToolResult {
                ok: result.ok,
                result: result.result,
                error: result.error,
            };
        }

        // MCP tool -- delegate to the broker
        let resp = self
            .mcp_broker
            .call_tool(server_id, tool_name, arguments, self.mcp_timeout_secs)
            .await;

        // Parse the broker's JSON response into our ToolResult
        let ok = resp
            .get("ok")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let result = resp
            .get("result")
            .cloned()
            .unwrap_or(Value::Null);
        let error = resp
            .get("error")
            .and_then(|v| v.as_str())
            .map(|s| s.to_owned());

        if !ok {
            warn!(
                server_id = server_id,
                tool_name = tool_name,
                error = ?error,
                "MCP tool call failed"
            );
        }

        ToolResult { ok, result, error }
    }

    /// Build the unified tool list for this router's context.
    pub fn tool_list(&self, mcp_tools: Option<&[Value]>, origin: &str) -> Vec<Value> {
        build_unified_tool_list(mcp_tools, origin, self.registry.as_ref())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// Test dispatcher registry that records calls.
    struct MockRegistry {
        tools: Vec<String>,
        calls: Mutex<Vec<(String, Value)>>,
    }

    impl MockRegistry {
        fn new(tools: Vec<&str>) -> Self {
            Self {
                tools: tools.into_iter().map(|s| s.to_owned()).collect(),
                calls: Mutex::new(Vec::new()),
            }
        }
    }

    impl DispatcherRegistry for MockRegistry {
        fn has(&self, tool_name: &str) -> bool {
            self.tools.iter().any(|t| t == tool_name)
        }

        fn list_for_origin(&self, _origin: &str) -> Vec<DispatcherToolSpec> {
            self.tools
                .iter()
                .map(|name| DispatcherToolSpec {
                    name: name.clone(),
                    description: format!("Mock tool {name}"),
                    input_schema: json!({"type": "object"}),
                })
                .collect()
        }

        fn dispatch_sync(&self, envelope: ToolCallEnvelope) -> DispatchResult {
            self.calls
                .lock()
                .unwrap()
                .push((envelope.tool.clone(), envelope.args.clone()));
            DispatchResult {
                ok: true,
                result: json!({"dispatched": envelope.tool}),
                error: None,
            }
        }
    }

    #[test]
    fn test_dispatcher_sentinel_no_double_underscore() {
        assert!(
            !DISPATCHER_SENTINEL.contains("__"),
            "DISPATCHER_SENTINEL must not contain '__'"
        );
    }

    #[test]
    fn test_build_unified_tool_list_merges() {
        let registry = MockRegistry::new(vec!["set_world_time", "add_world_event"]);
        let mcp = vec![json!({
            "server_id": "my_server",
            "name": "mcp_tool_1",
            "description": "An MCP tool",
            "schema": {"type": "object"}
        })];

        let result = build_unified_tool_list(Some(&mcp), "llm_chat", &registry);

        // 1 MCP + 2 dispatcher
        assert_eq!(result.len(), 3);

        // First entry is the MCP tool
        assert_eq!(result[0]["server_id"], "my_server");
        assert_eq!(result[0]["name"], "mcp_tool_1");

        // Remaining are dispatcher tools
        assert_eq!(result[1]["server_id"], DISPATCHER_SENTINEL);
        assert_eq!(result[1]["name"], "set_world_time");
        assert_eq!(result[2]["server_id"], DISPATCHER_SENTINEL);
        assert_eq!(result[2]["name"], "add_world_event");
    }

    #[test]
    fn test_build_unified_tool_list_empty_mcp() {
        let registry = MockRegistry::new(vec!["tool_a"]);
        let result = build_unified_tool_list(None, "llm_chat", &registry);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0]["name"], "tool_a");
    }

    #[tokio::test]
    async fn test_route_dispatcher_by_sentinel() {
        let registry = Arc::new(MockRegistry::new(vec!["set_world_time"]));
        let broker = Arc::new(McpBroker::default());

        let router = UnifiedToolRouter::new(
            registry.clone(),
            broker,
            1,
            Some(10),
            None,
            "trace-001".into(),
        );

        let result = router
            .route(DISPATCHER_SENTINEL, "set_world_time", json!({"target": "dawn"}))
            .await;

        assert!(result.ok);
        assert_eq!(result.result["dispatched"], "set_world_time");

        // Verify the call was recorded
        let calls = registry.calls.lock().unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].0, "set_world_time");
        assert_eq!(calls[0].1["target"], "dawn");
    }

    #[tokio::test]
    async fn test_route_dispatcher_by_registry_lookup() {
        let registry = Arc::new(MockRegistry::new(vec!["set_world_time"]));
        let broker = Arc::new(McpBroker::default());

        let router = UnifiedToolRouter::new(
            registry.clone(),
            broker,
            1,
            Some(10),
            None,
            "trace-002".into(),
        );

        // Even with a different server_id, if registry.has(tool_name) is true,
        // route to dispatcher.
        let result = router
            .route("some_other_server", "set_world_time", json!({}))
            .await;

        assert!(result.ok);
        assert_eq!(result.result["dispatched"], "set_world_time");
    }

    #[tokio::test]
    async fn test_route_mcp_fallback() {
        let registry = Arc::new(MockRegistry::new(vec!["set_world_time"]));
        let broker = Arc::new(McpBroker::default());

        let router = UnifiedToolRouter::new(
            registry.clone(),
            broker,
            1,
            Some(10),
            None,
            "trace-003".into(),
        );

        // Tool not in registry, server_id not sentinel -> MCP fallback
        // The broker has no running server, so this will return an error.
        let result = router
            .route("unknown_server", "unknown_tool", json!({}))
            .await;

        assert!(!result.ok);
        assert!(result.error.is_some());
    }

    #[test]
    fn test_noop_registry() {
        let noop = NoopDispatcherRegistry;
        assert!(!noop.has("anything"));
        assert!(noop.list_for_origin("llm_chat").is_empty());

        let result = noop.dispatch_sync(ToolCallEnvelope {
            user_id: 1,
            save_id: None,
            script_id: None,
            tool: "test".into(),
            args: json!({}),
            origin: "llm_chat".into(),
            trace_id: "t1".into(),
            depth: 0,
        });
        assert!(!result.ok);
        assert!(result.error.unwrap().contains("not initialized"));
    }
}
