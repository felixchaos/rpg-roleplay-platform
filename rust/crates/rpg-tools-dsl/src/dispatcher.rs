//! dispatcher.rs — task 87: 统一命令工具调用分发器。
//!
//! 对应 Python: `rpg/tools_dsl/command_dispatcher.py`
//!
//! 四件套:
//!   - [`ToolSpec`]        — 单个工具的元数据 (name/schema/executor/scope/origins/destructive)
//!   - [`ToolRegistry`]    — 进程内注册表,按 name 查工具,按 origin 过滤可用工具
//!   - [`ToolCallEnvelope`] — 单条调用请求,带 user/save/script 作用域与 trace 元数据
//!   - [`ToolDispatcher`]  — 鉴权 / 作用域 / origin / 限流 / 审计 / 执行
//!
//! 作用域语义:
//!   global  : 任意 user 可调,无锁 (例: list_models)
//!   user    : 限当前 user_id (例: list_my_saves, set_preference)
//!   script  : 限当前 user 在指定 script_id 上 (例: get_chapter_facts)
//!   save    : 限当前 user 在指定 save_id 上 (例: set_world_time)
//!
//! origin 白名单:
//!   llm_chat          : GM 流式响应中调用的工具 (写入受限)
//!   llm_set           : /set 命令解析出的工具 (command_agent)
//!   ui_button         : 前端按钮直触 (全开)
//!   mcp_call          : 通过 /api/mcp/tool/call 进来 (受限)
//!   api_direct        : 直接调老 HTTP endpoint 兼容路径
//!   console_assistant : 侧栏控制台助手 — 独立 origin
//!   autonomous_agent  : 黑天鹅子代理 — post-GM hook 主动触发世界事件

use std::any::Any;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Instant;

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::Value;

// ────────────────────────────────────────────────────────────
// 枚举
// ────────────────────────────────────────────────────────────

/// 作用域 — 决定工具需要哪些 ID、是否需要 state、是否需要锁。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    Global,
    User,
    Script,
    Save,
}

/// 调用来源 — 决定哪些工具对调用方可见。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Origin {
    LlmChat,
    LlmSet,
    UiButton,
    McpCall,
    ApiDirect,
    ConsoleAssistant,
    AutonomousAgent,
}

impl Origin {
    /// 返回 snake_case 字符串,用于审计日志。
    pub fn as_str(&self) -> &'static str {
        match self {
            Origin::LlmChat => "llm_chat",
            Origin::LlmSet => "llm_set",
            Origin::UiButton => "ui_button",
            Origin::McpCall => "mcp_call",
            Origin::ApiDirect => "api_direct",
            Origin::ConsoleAssistant => "console_assistant",
            Origin::AutonomousAgent => "autonomous_agent",
        }
    }
}

// ────────────────────────────────────────────────────────────
// 数据结构
// ────────────────────────────────────────────────────────────

/// 工具执行上下文 — 传给 executor callback 的全部信息。
///
/// `state` 是 type-erased 的可变引用。调用方 (rpg-server) 在构建
/// `ToolDispatcher` 时通过 `state_provider` 注入具体类型 (如 `GameState`),
/// executor 内部通过 `state.downcast_mut::<GameState>()` 取回。
pub struct ToolExecContext<'a> {
    pub args: Value,
    pub user_id: i64,
    pub save_id: Option<i64>,
    pub script_id: Option<i64>,
    pub state: Option<&'a mut dyn Any>,
}

/// 工具执行结果。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    pub ok: bool,
    pub result: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub audit: Option<Value>,
}

impl ToolResult {
    pub fn success(result: Value) -> Self {
        Self {
            ok: true,
            result,
            error: None,
            audit: None,
        }
    }

    pub fn failure(error: impl Into<String>) -> Self {
        Self {
            ok: false,
            result: Value::String(String::new()),
            error: Some(error.into()),
            audit: None,
        }
    }
}

/// Executor 回调类型别名。
///
/// 每个工具注册时提供一个闭包,接收 `ToolExecContext` 返回 `ToolResult`。
/// 要求 `Send + Sync` 以支持跨线程注册表共享。
pub type ToolExecutor = Box<dyn Fn(ToolExecContext<'_>) -> ToolResult + Send + Sync>;

/// 单个工具的元数据。
///
/// 对应 Python `ToolSpec`。`executor` 是 type-erased callback,注册时提供。
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
    pub executor: ToolExecutor,
    pub scope: Scope,
    pub origins: HashSet<Origin>,
    pub destructive: bool,
    /// 工具成功后要广播的 state-event topic。
    pub side_effect_topics: Vec<String>,
    /// 给 LLM 看的调用样本 (Anthropic 2025-11 advanced tool use)。
    pub input_examples: Vec<Value>,
}

// ToolSpec 不能 derive Debug 因为包含 dyn Fn
impl std::fmt::Debug for ToolSpec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ToolSpec")
            .field("name", &self.name)
            .field("scope", &self.scope)
            .field("origins", &self.origins)
            .field("destructive", &self.destructive)
            .finish()
    }
}

impl ToolSpec {
    /// 转换为 Anthropic tool_use schema。
    ///
    /// examples 注入到 description 末尾,兼容所有 backend (Anthropic 原生支持
    /// input_examples 字段;Gemini/OpenAI 没有但能从 description 学)。
    pub fn to_anthropic_tool(&self) -> Value {
        let mut desc = self.description.clone();
        if !self.input_examples.is_empty() {
            desc.push_str("\n\n示例调用:");
            for ex in self.input_examples.iter().take(3) {
                desc.push_str("\n  ");
                desc.push_str(&serde_json::to_string(ex).unwrap_or_default());
            }
        }
        let mut out = serde_json::json!({
            "name": self.name,
            "description": desc,
            "input_schema": self.input_schema,
        });
        // Anthropic 原生 input_examples 字段也带上
        if !self.input_examples.is_empty() {
            out["input_examples"] = Value::Array(self.input_examples.clone());
        }
        out
    }

    /// 默认 origin 集合: ui_button + api_direct + llm_set。
    pub fn default_origins() -> HashSet<Origin> {
        let mut s = HashSet::new();
        s.insert(Origin::UiButton);
        s.insert(Origin::ApiDirect);
        s.insert(Origin::LlmSet);
        s
    }
}

/// 单条调用请求,带 user/save/script 作用域与 trace 元数据。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallEnvelope {
    pub user_id: i64,
    pub tool: String,
    pub args: Value,
    pub origin: Origin,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub save_id: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub script_id: Option<i64>,
    #[serde(default)]
    pub trace_id: String,
    #[serde(default)]
    pub depth: u32,
    #[serde(default)]
    pub call_id: String,
    #[serde(default)]
    pub ts: String,
}

impl ToolCallEnvelope {
    /// 创建一个新的 envelope,自动生成 call_id 和 ts。
    pub fn new(user_id: i64, tool: impl Into<String>, args: Value, origin: Origin) -> Self {
        Self {
            user_id,
            tool: tool.into(),
            args,
            origin,
            save_id: None,
            script_id: None,
            trace_id: String::new(),
            depth: 0,
            call_id: generate_call_id(),
            ts: now_iso(),
        }
    }
}

// ────────────────────────────────────────────────────────────
// 异常 / 错误
// ────────────────────────────────────────────────────────────

/// Dispatcher 拒绝执行的明确原因。
#[derive(Debug, Clone, thiserror::Error)]
pub enum DispatchError {
    #[error("[auth_failed] {0}")]
    AuthFailed(String),
    #[error("[unknown_tool] {0}")]
    UnknownTool(String),
    #[error("[origin_forbidden] {0}")]
    OriginForbidden(String),
    #[error("[scope_missing_save] {0}")]
    ScopeMissingSave(String),
    #[error("[scope_missing_script] {0}")]
    ScopeMissingScript(String),
    #[error("[depth_exceeded] {0}")]
    DepthExceeded(String),
    #[error("[rate_limited] {0}")]
    RateLimited(String),
    #[error("[missing_required] {0}")]
    MissingRequired(String),
    #[error("[trace_duplicate] {0}")]
    TraceDuplicate(String),
    #[error("[destructive_blocked] {0}")]
    DestructiveBlocked(String),
}

impl DispatchError {
    /// 返回 kind 字符串,对应 Python `exc.kind`。
    pub fn kind(&self) -> &'static str {
        match self {
            DispatchError::AuthFailed(_) => "auth_failed",
            DispatchError::UnknownTool(_) => "unknown_tool",
            DispatchError::OriginForbidden(_) => "origin_forbidden",
            DispatchError::ScopeMissingSave(_) => "scope_missing_save",
            DispatchError::ScopeMissingScript(_) => "scope_missing_script",
            DispatchError::DepthExceeded(_) => "depth_exceeded",
            DispatchError::RateLimited(_) => "rate_limited",
            DispatchError::MissingRequired(_) => "missing_required",
            DispatchError::TraceDuplicate(_) => "trace_duplicate",
            DispatchError::DestructiveBlocked(_) => "destructive_blocked",
        }
    }

    /// 返回详情字符串。
    pub fn detail(&self) -> &str {
        match self {
            DispatchError::AuthFailed(s)
            | DispatchError::UnknownTool(s)
            | DispatchError::OriginForbidden(s)
            | DispatchError::ScopeMissingSave(s)
            | DispatchError::ScopeMissingScript(s)
            | DispatchError::DepthExceeded(s)
            | DispatchError::RateLimited(s)
            | DispatchError::MissingRequired(s)
            | DispatchError::TraceDuplicate(s)
            | DispatchError::DestructiveBlocked(s) => s,
        }
    }
}

// ────────────────────────────────────────────────────────────
// 注册器
// ────────────────────────────────────────────────────────────

/// 进程内工具注册表。按 name 索引;按 origin 过滤暴露给特定调用方的子表。
///
/// 对应 Python `ToolRegistry`。与旧的 `tool_registry::ToolRegistry` 不同:
/// 本注册表持有 executor callback,旧的只持有元数据。
pub struct ToolRegistry {
    tools: HashMap<String, ToolSpec>,
}

impl std::fmt::Debug for ToolRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ToolRegistry")
            .field("tool_count", &self.tools.len())
            .field("tools", &self.tools.keys().collect::<Vec<_>>())
            .finish()
    }
}

impl Default for ToolRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self {
            tools: HashMap::new(),
        }
    }

    /// 注册工具。重复注册同名工具会返回 Err。
    pub fn register(&mut self, spec: ToolSpec) -> Result<(), String> {
        if self.tools.contains_key(&spec.name) {
            return Err(format!("工具 {:?} 已注册", spec.name));
        }
        self.tools.insert(spec.name.clone(), spec);
        Ok(())
    }

    /// 用于测试/热更新,允许覆盖已有工具。生产代码用 `register`。
    pub fn replace(&mut self, spec: ToolSpec) {
        self.tools.insert(spec.name.clone(), spec);
    }

    /// 按 name 查找工具。
    pub fn get(&self, name: &str) -> Option<&ToolSpec> {
        self.tools.get(name)
    }

    /// 是否已注册。
    pub fn has(&self, name: &str) -> bool {
        self.tools.contains_key(name)
    }

    /// 返回当前 origin 可见的工具列表 (用于 LLM prompt 注入)。
    pub fn list_for_origin(&self, origin: Origin) -> Vec<&ToolSpec> {
        self.tools
            .values()
            .filter(|s| s.origins.contains(&origin))
            .collect()
    }

    /// 返回所有已注册工具。
    pub fn list_all(&self) -> Vec<&ToolSpec> {
        self.tools.values().collect()
    }

    /// 仅供测试用。
    pub fn clear(&mut self) {
        self.tools.clear();
    }
}

// ────────────────────────────────────────────────────────────
// 分发器
// ────────────────────────────────────────────────────────────

/// 递归深度上限。
pub const MAX_TRACE_DEPTH: u32 = 3;
/// 每用户每秒工具调用数上限。
pub const MAX_CALLS_PER_USER_PER_SECOND: usize = 20;
/// state 级审计日志上限。
pub const AUDIT_LOG_LIMIT: usize = 200;
/// 进程级滚动审计缓冲上限。
pub const RECENT_AUDIT_LIMIT: usize = 1000;

/// 授权回调类型。
pub type AuthorizeFn = Box<dyn Fn(i64) -> bool + Send + Sync>;

/// State provider 回调类型。
///
/// 接收 envelope,返回 `Option<Box<dyn Any>>` — 调用方负责把 `GameState`
/// 装箱传入。返回 None 表示不需要 state (global scope)。
///
/// 注意: 我们返回的是一个可变引用的 wrapper。实际使用中,调用方会通过
/// `Arc<Mutex<GameState>>` 等方式管理,在调 dispatch 前锁好传入 `&mut GameState`。
/// 但由于 executor 是 `Fn` 不是 `FnMut`,且生命周期约束复杂,我们把 state
/// 放在 `ToolExecContext` 中按 `Option<&mut dyn Any>` 传递。
pub type StateProviderFn = Box<dyn Fn(&ToolCallEnvelope) -> Option<Box<dyn Any>> + Send + Sync>;

/// 中央分发器。所有工具调用必须通过它。
///
/// 对应 Python `ToolDispatcher`。
///
/// 用法:
/// ```rust,no_run
/// use rpg_tools_dsl::dispatcher::*;
/// use std::sync::Arc;
///
/// let registry = ToolRegistry::new();
/// let dispatcher = ToolDispatcher::new(Arc::new(registry));
/// ```
pub struct ToolDispatcher {
    registry: Arc<ToolRegistry>,
    authorize: AuthorizeFn,
    /// 限流: per user_id 最近 1 秒内调用的时间戳。
    rate_buckets: Mutex<HashMap<i64, Vec<Instant>>>,
    /// trace 内去重: trace_id → set of (tool, stable_json(args))。
    trace_seen: Mutex<HashMap<String, HashSet<(String, String)>>>,
    /// 滚动审计缓冲 (进程级,所有 user)。
    recent_audit: Mutex<Vec<Value>>,
}

impl std::fmt::Debug for ToolDispatcher {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ToolDispatcher")
            .field("registry", &self.registry)
            .finish()
    }
}

impl ToolDispatcher {
    /// 创建分发器。
    ///
    /// `registry` 通过 `Arc` 共享,因为注册表通常在启动时构建后不再修改。
    pub fn new(registry: Arc<ToolRegistry>) -> Self {
        Self {
            registry,
            authorize: Box::new(|_| true),
            rate_buckets: Mutex::new(HashMap::new()),
            trace_seen: Mutex::new(HashMap::new()),
            recent_audit: Mutex::new(Vec::new()),
        }
    }

    /// 设置鉴权回调。
    pub fn with_authorize(mut self, authorize: impl Fn(i64) -> bool + Send + Sync + 'static) -> Self {
        self.authorize = Box::new(authorize);
        self
    }

    // ── 公共 API ───────────────────────────────────────────

    /// 同步分发入口。
    ///
    /// `state` 是调用方持有的可变 state 引用。对于 save/script/user 级工具,
    /// 调用方应在调用前取得对应的锁并传入 `Some(&mut game_state)`。
    /// 对于 global 级工具,传 `None` 即可。
    pub fn dispatch(
        &self,
        env: &ToolCallEnvelope,
        state: Option<&mut dyn Any>,
    ) -> ToolResult {
        // 验证
        match self.validate(env) {
            Ok(()) => {}
            Err(err) => return self.reject(env, &err),
        }

        // 执行 — 从注册表中取 spec 并调用 executor
        self.execute(env, state)
    }

    /// 取最近 N 条审计记录。
    pub fn recent_audit(&self, limit: usize) -> Vec<Value> {
        let audit = self.recent_audit.lock();
        let start = audit.len().saturating_sub(limit);
        audit[start..].to_vec()
    }

    /// 重置限流与 trace 去重 (测试用)。
    pub fn reset_rate_limits(&self) {
        self.rate_buckets.lock().clear();
        self.trace_seen.lock().clear();
    }

    // ── 9 步验证管线 ─────────────────────────────────────

    fn validate(&self, env: &ToolCallEnvelope) -> Result<(), DispatchError> {
        // 1) 鉴权
        if !(self.authorize)(env.user_id) {
            return Err(DispatchError::AuthFailed(format!(
                "user_id={} 未通过鉴权",
                env.user_id
            )));
        }

        // 2) 工具是否存在
        let spec = self.registry.get(&env.tool).ok_or_else(|| {
            DispatchError::UnknownTool(format!("未注册工具: {}", env.tool))
        })?;

        // 3) origin 白名单
        if !spec.origins.contains(&env.origin) {
            let allowed: Vec<&str> = {
                let mut v: Vec<&str> = spec.origins.iter().map(|o| o.as_str()).collect();
                v.sort();
                v
            };
            return Err(DispatchError::OriginForbidden(format!(
                "工具 {} 不允许从 origin={} 调用 (允许: {:?})",
                env.tool,
                env.origin.as_str(),
                allowed,
            )));
        }

        // 4) save 级工具必须带 save_id
        if spec.scope == Scope::Save && env.save_id.is_none() {
            return Err(DispatchError::ScopeMissingSave(format!(
                "save 级工具 {} 必须带 save_id",
                env.tool
            )));
        }

        // 5) script 级工具必须带 script_id (允许从 save 派生)
        if spec.scope == Scope::Script && env.script_id.is_none() && env.save_id.is_none() {
            return Err(DispatchError::ScopeMissingScript(format!(
                "script 级工具 {} 必须带 script_id 或 save_id",
                env.tool
            )));
        }

        // 6) 递归深度
        if env.depth > MAX_TRACE_DEPTH {
            return Err(DispatchError::DepthExceeded(format!(
                "trace 深度 {} 超过上限 {} (防递归死锁)",
                env.depth, MAX_TRACE_DEPTH
            )));
        }

        // 7) 限流: per-user 每秒上限
        if !self.rate_ok(env.user_id) {
            return Err(DispatchError::RateLimited(format!(
                "user_id={} 每秒工具调用数超 {}",
                env.user_id, MAX_CALLS_PER_USER_PER_SECOND
            )));
        }

        // 8) required 字段检查
        if let Some(schema_obj) = spec.input_schema.as_object() {
            if let Some(Value::Array(required)) = schema_obj.get("required") {
                let mut missing = Vec::new();
                for fld_val in required {
                    if let Some(fld) = fld_val.as_str() {
                        let v = env.args.get(fld);
                        if v.is_none() || v == Some(&Value::Null) {
                            missing.push(fld.to_string());
                        }
                    }
                }
                if !missing.is_empty() {
                    return Err(DispatchError::MissingRequired(format!(
                        "工具 {} 缺必填字段: {}. \
                         请用 ask_user_choice 让用户选 (给 3-4 个候选 + allow_free_text=true)。",
                        env.tool,
                        missing.join(", ")
                    )));
                }
            }
        }

        // 9a) trace 内去重 (同 trace 同 tool+args 只执行一次)
        if !env.trace_id.is_empty() {
            let sig = (env.tool.clone(), stable_json(&env.args));
            let mut trace_map = self.trace_seen.lock();
            let seen = trace_map.entry(env.trace_id.clone()).or_default();
            if seen.contains(&sig) {
                return Err(DispatchError::TraceDuplicate(format!(
                    "trace_id={} 已执行过相同 ({}, args)",
                    env.trace_id, env.tool
                )));
            }
            seen.insert(sig);
        }

        // 9b) destructive 工具不能从 llm_chat origin 调
        if spec.destructive && env.origin == Origin::LlmChat {
            return Err(DispatchError::DestructiveBlocked(format!(
                "破坏性工具 {} 不允许从 llm_chat 调用 (需 ui_button 显式审批)",
                env.tool
            )));
        }

        Ok(())
    }

    // ── 执行 ─────────────────────────────────────────────

    fn execute(
        &self,
        env: &ToolCallEnvelope,
        mut state: Option<&mut dyn Any>,
    ) -> ToolResult {
        let spec = match self.registry.get(&env.tool) {
            Some(s) => s,
            None => {
                // 理论上 validate 已经检查过,但以防万一
                return ToolResult::failure(format!("未注册工具: {}", env.tool));
            }
        };

        let exec_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let ctx = ToolExecContext {
                args: env.args.clone(),
                user_id: env.user_id,
                save_id: env.save_id,
                script_id: env.script_id,
                state: state.take(),
            };
            (spec.executor)(ctx)
        }));

        match exec_result {
            Ok(result) => {
                let ok = result.ok;
                self.record(env, spec, ok, result.result.clone(), result.error.clone());
                ToolResult {
                    ok,
                    result: result.result,
                    error: result.error,
                    audit: None, // audit 存在 recent_audit 里,不重复返回避免大 payload
                }
            }
            Err(panic_info) => {
                let error_msg = if let Some(s) = panic_info.downcast_ref::<String>() {
                    format!("panic: {}", s)
                } else if let Some(s) = panic_info.downcast_ref::<&str>() {
                    format!("panic: {}", s)
                } else {
                    "panic: unknown".to_string()
                };
                let result = ToolResult::failure(&error_msg);
                self.record(env, spec, false, Value::String(String::new()), Some(error_msg.clone()));
                result
            }
        }
    }

    // ── 审计 ─────────────────────────────────────────────

    fn record(
        &self,
        env: &ToolCallEnvelope,
        spec: &ToolSpec,
        ok: bool,
        result: Value,
        error: Option<String>,
    ) {
        // 审计 result 截断到 240 字符
        let audit_result = match &result {
            Value::String(s) => {
                let truncated: String = s.chars().take(240).collect();
                truncated
            }
            other => {
                let s = serde_json::to_string(other).unwrap_or_default();
                let truncated: String = s.chars().take(240).collect();
                truncated
            }
        };

        let audit = serde_json::json!({
            "ts": env.ts,
            "kind": "tool_call",
            "tool": env.tool,
            "origin": env.origin.as_str(),
            "user_id": env.user_id,
            "save_id": env.save_id,
            "script_id": env.script_id,
            "trace_id": env.trace_id,
            "call_id": env.call_id,
            "depth": env.depth,
            "args": env.args,
            "result": audit_result,
            "error": error,
            "ok": ok,
        });

        // 进程级滚动缓冲
        {
            let mut recent = self.recent_audit.lock();
            recent.push(audit);
            if recent.len() > RECENT_AUDIT_LIMIT {
                let drain_count = recent.len() - RECENT_AUDIT_LIMIT;
                recent.drain(..drain_count);
            }
        }

        // NOTE: state-level audit (写入 state.permissions.audit_log) 由调用方
        // (rpg-server) 在 dispatch 返回后处理,因为 dispatcher 不持有 state 引用。
        // 这是 Rust 所有权模型与 Python 的差异 — Python dispatcher 持有 state_provider
        // 可以在 _record 内再次获取 state 引用,Rust 不行 (state 已经被 move 进
        // executor)。调用方应在 dispatch 返回后将 audit 追加到 state。
        let _ = spec; // 消除 unused 警告; side_effect_topics 也由调用方处理
    }

    fn reject(&self, env: &ToolCallEnvelope, err: &DispatchError) -> ToolResult {
        let audit = serde_json::json!({
            "ts": env.ts,
            "kind": "tool_call_rejected",
            "tool": env.tool,
            "origin": env.origin.as_str(),
            "user_id": env.user_id,
            "save_id": env.save_id,
            "script_id": env.script_id,
            "reject_kind": err.kind(),
            "detail": err.detail(),
        });

        // 进程级滚动缓冲
        {
            let mut recent = self.recent_audit.lock();
            recent.push(audit.clone());
            if recent.len() > RECENT_AUDIT_LIMIT {
                let drain_count = recent.len() - RECENT_AUDIT_LIMIT;
                recent.drain(..drain_count);
            }
        }

        ToolResult {
            ok: false,
            result: Value::Null,
            error: Some(err.to_string()),
            audit: Some(audit),
        }
    }

    // ── 限流 ─────────────────────────────────────────────

    fn rate_ok(&self, user_id: i64) -> bool {
        let now = Instant::now();
        let mut buckets = self.rate_buckets.lock();
        let bucket = buckets.entry(user_id).or_default();

        // 丢掉 1 秒前的
        bucket.retain(|&ts| now.duration_since(ts).as_secs_f64() < 1.0);

        if bucket.len() >= MAX_CALLS_PER_USER_PER_SECOND {
            return false;
        }
        bucket.push(now);
        true
    }
}

// ────────────────────────────────────────────────────────────
// helpers
// ────────────────────────────────────────────────────────────

/// 稳定 JSON 序列化 (sort_keys),用于 trace 去重签名。
fn stable_json(val: &Value) -> String {
    // serde_json 默认不排序 map keys。手动排序。
    fn sort_value(v: &Value) -> Value {
        match v {
            Value::Object(map) => {
                let mut sorted: serde_json::Map<String, Value> = serde_json::Map::new();
                let mut keys: Vec<&String> = map.keys().collect();
                keys.sort();
                for k in keys {
                    sorted.insert(k.clone(), sort_value(&map[k]));
                }
                Value::Object(sorted)
            }
            Value::Array(arr) => Value::Array(arr.iter().map(sort_value).collect()),
            other => other.clone(),
        }
    }
    let sorted = sort_value(val);
    serde_json::to_string(&sorted).unwrap_or_default()
}

/// 生成 call_id (URL-safe base64, 11 chars)。
fn generate_call_id() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    // 简单但足够唯一: 纳秒时间戳的低 64 位 + 线程 ID 的哈希
    let thread_id = std::thread::current().id();
    let hash = {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        thread_id.hash(&mut hasher);
        nanos.hash(&mut hasher);
        hasher.finish()
    };
    // 编码为 11 字符的 base64url (无 padding)
    let bytes = hash.to_le_bytes();
    base64url_encode(&bytes)
}

/// 简易 base64url 编码 (无 padding)。
fn base64url_encode(bytes: &[u8]) -> String {
    const CHARS: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut result = String::new();
    let mut bits: u32 = 0;
    let mut num_bits: u32 = 0;
    for &b in bytes {
        bits = (bits << 8) | (b as u32);
        num_bits += 8;
        while num_bits >= 6 {
            num_bits -= 6;
            let idx = ((bits >> num_bits) & 0x3F) as usize;
            result.push(CHARS[idx] as char);
        }
    }
    if num_bits > 0 {
        let idx = ((bits << (6 - num_bits)) & 0x3F) as usize;
        result.push(CHARS[idx] as char);
    }
    result
}

/// ISO-8601 当前时间 (秒精度)。
///
/// 使用 `std::time::SystemTime` 避免外部依赖。格式: `YYYY-MM-DDTHH:MM:SS`。
fn now_iso() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = duration.as_secs();

    // 手算 UTC 日期时间 — 避免引入 chrono 依赖
    let days = secs / 86400;
    let time_of_day = secs % 86400;
    let hours = time_of_day / 3600;
    let minutes = (time_of_day % 3600) / 60;
    let seconds = time_of_day % 60;

    // 从 1970-01-01 起算的天数转日期 (civil_from_days 算法)
    let (year, month, day) = civil_from_days(days as i64);

    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        year, month, day, hours, minutes, seconds
    )
}

/// 从 Unix epoch 天数转 (year, month, day)。
/// 来自 Howard Hinnant 的 civil_from_days 算法。
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365; // [0, 399]
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    let y = if m <= 2 { y + 1 } else { y };
    (y, m as u32, d as u32)
}

// ────────────────────────────────────────────────────────────
// tests
// ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_noop_spec(name: &str, scope: Scope) -> ToolSpec {
        ToolSpec {
            name: name.to_string(),
            description: format!("test tool {}", name),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
            executor: Box::new(|_ctx| ToolResult::success(Value::String("ok".into()))),
            scope,
            origins: ToolSpec::default_origins(),
            destructive: false,
            side_effect_topics: vec![],
            input_examples: vec![],
        }
    }

    #[test]
    fn test_registry_register_and_get() {
        let mut reg = ToolRegistry::new();
        let spec = make_noop_spec("test_tool", Scope::Global);
        reg.register(spec).unwrap();
        assert!(reg.has("test_tool"));
        assert!(reg.get("test_tool").is_some());
        assert!(!reg.has("nonexistent"));
    }

    #[test]
    fn test_registry_duplicate_error() {
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("dup", Scope::Global)).unwrap();
        let result = reg.register(make_noop_spec("dup", Scope::Global));
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("已注册"));
    }

    #[test]
    fn test_registry_list_for_origin() {
        let mut reg = ToolRegistry::new();
        let mut spec = make_noop_spec("tool_a", Scope::Global);
        spec.origins = {
            let mut s = HashSet::new();
            s.insert(Origin::LlmChat);
            s
        };
        reg.register(spec).unwrap();

        let mut spec2 = make_noop_spec("tool_b", Scope::Global);
        spec2.origins = {
            let mut s = HashSet::new();
            s.insert(Origin::UiButton);
            s
        };
        reg.register(spec2).unwrap();

        assert_eq!(reg.list_for_origin(Origin::LlmChat).len(), 1);
        assert_eq!(reg.list_for_origin(Origin::UiButton).len(), 1);
        assert_eq!(reg.list_for_origin(Origin::McpCall).len(), 0);
    }

    #[test]
    fn test_dispatch_unknown_tool() {
        let reg = ToolRegistry::new();
        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let env = ToolCallEnvelope::new(1, "nonexistent", serde_json::json!({}), Origin::UiButton);
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("unknown_tool"));
    }

    #[test]
    fn test_dispatch_auth_failed() {
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("tool", Scope::Global)).unwrap();
        let dispatcher = ToolDispatcher::new(Arc::new(reg))
            .with_authorize(|_| false);
        let env = ToolCallEnvelope::new(1, "tool", serde_json::json!({}), Origin::UiButton);
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("auth_failed"));
    }

    #[test]
    fn test_dispatch_origin_forbidden() {
        let mut reg = ToolRegistry::new();
        let mut spec = make_noop_spec("tool", Scope::Global);
        spec.origins = {
            let mut s = HashSet::new();
            s.insert(Origin::UiButton);
            s
        };
        reg.register(spec).unwrap();

        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let env = ToolCallEnvelope::new(1, "tool", serde_json::json!({}), Origin::LlmChat);
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("origin_forbidden"));
    }

    #[test]
    fn test_dispatch_scope_missing_save() {
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("save_tool", Scope::Save)).unwrap();
        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let env = ToolCallEnvelope::new(1, "save_tool", serde_json::json!({}), Origin::UiButton);
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("scope_missing_save"));
    }

    #[test]
    fn test_dispatch_scope_missing_script() {
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("script_tool", Scope::Script)).unwrap();
        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        // no save_id and no script_id
        let env = ToolCallEnvelope::new(1, "script_tool", serde_json::json!({}), Origin::UiButton);
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("scope_missing_script"));
    }

    #[test]
    fn test_dispatch_depth_exceeded() {
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("tool", Scope::Global)).unwrap();
        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let mut env = ToolCallEnvelope::new(1, "tool", serde_json::json!({}), Origin::UiButton);
        env.depth = MAX_TRACE_DEPTH + 1;
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("depth_exceeded"));
    }

    #[test]
    fn test_dispatch_destructive_blocked() {
        let mut reg = ToolRegistry::new();
        let mut spec = make_noop_spec("delete_thing", Scope::Global);
        spec.destructive = true;
        spec.origins.insert(Origin::LlmChat);
        reg.register(spec).unwrap();

        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let env = ToolCallEnvelope::new(1, "delete_thing", serde_json::json!({}), Origin::LlmChat);
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("destructive_blocked"));
    }

    #[test]
    fn test_dispatch_missing_required() {
        let mut reg = ToolRegistry::new();
        let mut spec = make_noop_spec("tool", Scope::Global);
        spec.input_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"}
            },
            "required": ["name", "age"]
        });
        reg.register(spec).unwrap();

        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        // missing "age"
        let env = ToolCallEnvelope::new(
            1, "tool",
            serde_json::json!({"name": "test"}),
            Origin::UiButton,
        );
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("missing_required"));
        assert!(result.error.as_ref().unwrap().contains("age"));
    }

    #[test]
    fn test_dispatch_trace_duplicate() {
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("tool", Scope::Global)).unwrap();
        let dispatcher = ToolDispatcher::new(Arc::new(reg));

        let mut env = ToolCallEnvelope::new(1, "tool", serde_json::json!({"x": 1}), Origin::UiButton);
        env.trace_id = "trace-1".to_string();

        // First call succeeds
        let r1 = dispatcher.dispatch(&env, None);
        assert!(r1.ok);

        // Second call with same trace+tool+args is duplicate
        env.call_id = generate_call_id(); // new call_id
        let r2 = dispatcher.dispatch(&env, None);
        assert!(!r2.ok);
        assert!(r2.error.as_ref().unwrap().contains("trace_duplicate"));
    }

    #[test]
    fn test_dispatch_success() {
        let mut reg = ToolRegistry::new();
        let mut spec = make_noop_spec("greet", Scope::Global);
        spec.executor = Box::new(|ctx| {
            let name = ctx.args.get("name").and_then(|v| v.as_str()).unwrap_or("world");
            ToolResult::success(Value::String(format!("hello, {}!", name)))
        });
        reg.register(spec).unwrap();

        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let env = ToolCallEnvelope::new(
            1, "greet",
            serde_json::json!({"name": "Rust"}),
            Origin::UiButton,
        );
        let result = dispatcher.dispatch(&env, None);
        assert!(result.ok);
        assert_eq!(result.result, Value::String("hello, Rust!".into()));
    }

    #[test]
    fn test_dispatch_with_state() {
        let mut reg = ToolRegistry::new();
        let mut spec = make_noop_spec("inc_counter", Scope::Save);
        spec.executor = Box::new(|mut ctx| {
            if let Some(state) = ctx.state.as_mut() {
                if let Some(counter) = state.downcast_mut::<i32>() {
                    *counter += 1;
                    return ToolResult::success(Value::Number((*counter).into()));
                }
            }
            ToolResult::failure("no state")
        });
        reg.register(spec).unwrap();

        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let mut env = ToolCallEnvelope::new(1, "inc_counter", serde_json::json!({}), Origin::UiButton);
        env.save_id = Some(42);

        let mut counter: i32 = 10;
        let result = dispatcher.dispatch(&env, Some(&mut counter));
        assert!(result.ok);
        assert_eq!(result.result, Value::Number(11.into()));
        assert_eq!(counter, 11);
    }

    #[test]
    fn test_to_anthropic_tool() {
        let spec = ToolSpec {
            name: "get_time".to_string(),
            description: "获取当前时间".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
            executor: Box::new(|_| ToolResult::success(Value::Null)),
            scope: Scope::Global,
            origins: ToolSpec::default_origins(),
            destructive: false,
            side_effect_topics: vec![],
            input_examples: vec![
                serde_json::json!({"format": "iso"}),
                serde_json::json!({"format": "unix"}),
            ],
        };

        let tool_json = spec.to_anthropic_tool();
        assert_eq!(tool_json["name"], "get_time");
        let desc = tool_json["description"].as_str().unwrap();
        assert!(desc.contains("示例调用:"));
        assert!(tool_json.get("input_examples").is_some());
    }

    #[test]
    fn test_rate_limiting() {
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("tool", Scope::Global)).unwrap();
        let dispatcher = ToolDispatcher::new(Arc::new(reg));

        for i in 0..MAX_CALLS_PER_USER_PER_SECOND {
            let env = ToolCallEnvelope::new(1, "tool", serde_json::json!({}), Origin::UiButton);
            let result = dispatcher.dispatch(&env, None);
            assert!(result.ok, "call {} should succeed", i);
        }

        // Next call should be rate limited
        let env = ToolCallEnvelope::new(1, "tool", serde_json::json!({}), Origin::UiButton);
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("rate_limited"));
    }

    #[test]
    fn test_recent_audit() {
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("tool", Scope::Global)).unwrap();
        let dispatcher = ToolDispatcher::new(Arc::new(reg));

        let env = ToolCallEnvelope::new(1, "tool", serde_json::json!({}), Origin::UiButton);
        dispatcher.dispatch(&env, None);

        let audit = dispatcher.recent_audit(10);
        assert_eq!(audit.len(), 1);
        assert_eq!(audit[0]["tool"], "tool");
        assert_eq!(audit[0]["kind"], "tool_call");
    }

    #[test]
    fn test_stable_json_sorting() {
        let v1 = serde_json::json!({"b": 2, "a": 1});
        let v2 = serde_json::json!({"a": 1, "b": 2});
        assert_eq!(stable_json(&v1), stable_json(&v2));
    }

    #[test]
    fn test_envelope_new_generates_ids() {
        let env = ToolCallEnvelope::new(1, "test", serde_json::json!({}), Origin::UiButton);
        assert!(!env.call_id.is_empty());
        assert!(!env.ts.is_empty());
        assert!(env.ts.contains('T')); // ISO format
    }

    #[test]
    fn test_script_scope_with_save_id_ok() {
        // script 级工具可以只带 save_id (从 save 派生 script_id)
        let mut reg = ToolRegistry::new();
        reg.register(make_noop_spec("script_tool", Scope::Script)).unwrap();
        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let mut env = ToolCallEnvelope::new(1, "script_tool", serde_json::json!({}), Origin::UiButton);
        env.save_id = Some(42); // no script_id, but save_id is set
        let result = dispatcher.dispatch(&env, None);
        assert!(result.ok);
    }

    #[test]
    fn test_executor_panic_caught() {
        let mut reg = ToolRegistry::new();
        let mut spec = make_noop_spec("panicker", Scope::Global);
        spec.executor = Box::new(|_| {
            panic!("intentional test panic");
        });
        reg.register(spec).unwrap();

        let dispatcher = ToolDispatcher::new(Arc::new(reg));
        let env = ToolCallEnvelope::new(1, "panicker", serde_json::json!({}), Origin::UiButton);
        let result = dispatcher.dispatch(&env, None);
        assert!(!result.ok);
        assert!(result.error.as_ref().unwrap().contains("panic"));
    }
}
