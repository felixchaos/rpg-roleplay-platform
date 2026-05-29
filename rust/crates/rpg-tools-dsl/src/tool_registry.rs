//! tool_registry — ToolDefinition + ToolRegistry
//! 对应 Python: rpg/tools_dsl/tool_registry.py

use std::collections::HashMap;

use once_cell::sync::Lazy;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};

/// 工具种类
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ToolKind {
    Plugin,
    Mcp,
    Skill,
    Builtin,
}

/// 单个工具的定义（对应 Python dict / Pydantic model）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinition {
    /// 唯一标识符，slug 格式
    pub id: String,
    /// 显示名称
    pub name: String,
    /// 工具种类
    pub kind: ToolKind,
    /// 是否启用
    pub enabled: bool,
    /// 额外元数据（schema、描述等），可为空
    #[serde(default)]
    pub meta: serde_json::Value,
}

impl ToolDefinition {
    pub fn new(id: impl Into<String>, name: impl Into<String>, kind: ToolKind) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            kind,
            enabled: true,
            meta: serde_json::Value::Null,
        }
    }
}

/// 工具注册表
///
/// ```text
/// Python 对等物：tool_payload() 中组装 plugins / mcp / skills 三张表
/// Rust 把三者统一为一张 HashMap，按 id 去重
/// ```
#[derive(Debug, Default)]
pub struct ToolRegistry {
    tools: HashMap<String, ToolDefinition>,
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// 注册（或覆盖）一条工具定义
    pub fn register(&mut self, def: ToolDefinition) {
        self.tools.insert(def.id.clone(), def);
    }

    /// 按 id 查找
    pub fn get(&self, id: &str) -> Option<&ToolDefinition> {
        self.tools.get(id)
    }

    /// 返回所有工具（按 id 排序）
    pub fn list(&self) -> Vec<&ToolDefinition> {
        let mut v: Vec<&ToolDefinition> = self.tools.values().collect();
        v.sort_by(|a, b| a.id.cmp(&b.id));
        v
    }

    /// 启用 / 禁用某工具
    pub fn set_enabled(&mut self, id: &str, enabled: bool) -> bool {
        if let Some(def) = self.tools.get_mut(id) {
            def.enabled = enabled;
            true
        } else {
            false
        }
    }

    /// 删除工具
    pub fn remove(&mut self, id: &str) -> Option<ToolDefinition> {
        self.tools.remove(id)
    }
}

/// 全局注册表（进程单例）
///
/// 用法：
/// ```rust,no_run
/// use rpg_tools_dsl::GLOBAL_REGISTRY;
/// let mut reg = GLOBAL_REGISTRY.write();
/// // reg.register(...)
/// ```
pub static GLOBAL_REGISTRY: Lazy<RwLock<ToolRegistry>> =
    Lazy::new(|| RwLock::new(ToolRegistry::new()));

/// 将默认插件工具列表注册到全局注册表（启动时调用一次）
pub fn register_default_plugins() {
    let defaults = default_plugin_tools();
    let mut reg = GLOBAL_REGISTRY.write();
    for def in defaults {
        reg.register(def);
    }
}

fn default_plugin_tools() -> Vec<ToolDefinition> {
    let raw = [
        ("documents", "Documents"),
        ("spreadsheets", "Spreadsheets"),
        ("presentations", "Presentations"),
        ("browser", "浏览器"),
        ("chrome", "Chrome"),
        ("computer-use", "电脑"),
        ("figma", "Figma"),
        ("github", "GitHub"),
        ("cloudflare", "Cloudflare"),
        ("build-ios-apps", "Build iOS Apps"),
        ("codex-security", "Codex Security"),
    ];
    raw.iter()
        .map(|(id, name)| ToolDefinition::new(*id, *name, ToolKind::Plugin))
        .collect()
}
