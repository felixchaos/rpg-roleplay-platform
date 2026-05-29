//! 上下文管线核心数据结构。
//! 对应 Python: rpg/context_providers/base.py (Demand / ContextContribution / ProviderServices)。

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

/// 玩家本轮需求账本。Demand Resolver 输出,由 LLM 子代理或本地规则产出。
///
/// 对应 Python `Demand` dataclass。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Demand {
    #[serde(default)]
    pub player_intent: String,
    #[serde(default)]
    pub active_goal: String,
    #[serde(default)]
    pub hard_constraints: Vec<String>,
    #[serde(default)]
    pub soft_preferences: Vec<String>,
    #[serde(default)]
    pub target_entities: Vec<String>,
    #[serde(default)]
    pub target_location: String,
    #[serde(default)]
    pub target_time: String,
    /// 玩家显式时间跳跃请求;novel provider 才理解
    #[serde(default)]
    pub timeline_target: String,
    /// 一个开放查询;具体怎么用由 provider 决定
    #[serde(default)]
    pub retrieval_query: String,
    /// provider 可选的细化需求
    #[serde(default)]
    pub retrieval_needs: Value,
    /// rule_candidate_actions: 给 RulesProvider 使用
    #[serde(default)]
    pub rule_candidate_actions: Vec<Value>,
    #[serde(default)]
    pub risk_flags: Vec<String>,
    #[serde(default = "default_confidence")]
    pub confidence: f64,
    #[serde(default)]
    pub clarifying_question: String,
    #[serde(default)]
    pub reason: String,
    /// 保留 LLM 原始输出便于审计
    #[serde(default)]
    pub raw_curator_plan: Option<Value>,
}

fn default_confidence() -> f64 {
    1.0
}

impl Demand {
    pub fn empty() -> Self {
        Self::default()
    }
}

/// 单 layer。build_context_bundle 直接拼到 prompt。
///
/// 对应 Python `make_layer()` 返回的 dict。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Layer {
    pub id: String,
    pub title: String,
    pub content: String,
    #[serde(default)]
    pub sticky: bool,
    #[serde(default = "default_priority")]
    pub priority: i32,
    #[serde(default)]
    pub items: Vec<Value>,
    #[serde(default)]
    pub source: String,
}

fn default_priority() -> i32 {
    50
}

impl Layer {
    pub fn new(id: impl Into<String>, title: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            title: title.into(),
            content: content.into(),
            sticky: false,
            priority: 50,
            items: Vec::new(),
            source: String::new(),
        }
    }

    pub fn with_sticky(mut self, sticky: bool) -> Self {
        self.sticky = sticky;
        self
    }

    pub fn with_priority(mut self, priority: i32) -> Self {
        self.priority = priority;
        self
    }

    pub fn with_items(mut self, items: Vec<Value>) -> Self {
        self.items = items;
        self
    }

    pub fn with_source(mut self, source: impl Into<String>) -> Self {
        self.source = source.into();
        self
    }
}

/// 一个 provider 在一轮里贡献的上下文。
///
/// 对应 Python `ContextContribution` dataclass。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextContribution {
    pub provider_id: String,
    #[serde(default = "default_kind")]
    pub kind: String,
    /// 0-100,决定 prompt 层顺序
    #[serde(default = "default_priority")]
    pub priority: i32,
    /// 短句事实清单。GM 必读,进入 state/memory 摘要层。
    #[serde(default)]
    pub facts: Vec<String>,
    /// 结构化文本层。
    #[serde(default)]
    pub layers: Vec<Layer>,
    /// 检索片段(小说才用;模组通常为空)。
    #[serde(default)]
    pub retrieval_items: Vec<Value>,
    /// 需要传递给 GM 或 UI 的告警。
    #[serde(default)]
    pub warnings: Vec<String>,
    /// 调试信息,前端 Run Feed 显示。
    #[serde(default)]
    pub debug: Value,
    #[serde(default)]
    pub tokens_estimate: u32,
    /// provider 显式跳过时置 False。
    #[serde(default = "default_applied")]
    pub applied: bool,
}

fn default_kind() -> String {
    "generic".to_string()
}

fn default_applied() -> bool {
    true
}

impl ContextContribution {
    pub fn new(provider_id: impl Into<String>) -> Self {
        Self {
            provider_id: provider_id.into(),
            kind: default_kind(),
            priority: 50,
            facts: Vec::new(),
            layers: Vec::new(),
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: Value::Object(Default::default()),
            tokens_estimate: 0,
            applied: true,
        }
    }

    /// 对应 Python `ContextContribution.skipped(provider_id, reason)`。
    pub fn skipped(provider_id: impl Into<String>, reason: impl Into<String>) -> Self {
        let mut debug = serde_json::Map::new();
        debug.insert("skipped".to_string(), Value::String(reason.into()));
        Self {
            provider_id: provider_id.into(),
            kind: default_kind(),
            priority: 50,
            facts: Vec::new(),
            layers: Vec::new(),
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: Value::Object(debug),
            tokens_estimate: 0,
            applied: false,
        }
    }

    /// 异常包装。
    pub fn failed(provider_id: impl Into<String>, err: impl std::fmt::Display) -> Self {
        let pid = provider_id.into();
        let mut debug = serde_json::Map::new();
        debug.insert("error".to_string(), Value::String(err.to_string()));
        Self {
            provider_id: pid,
            kind: default_kind(),
            priority: 50,
            facts: Vec::new(),
            layers: Vec::new(),
            retrieval_items: Vec::new(),
            warnings: vec![format!("provider 异常：{}", err)],
            debug: Value::Object(debug),
            tokens_estimate: 0,
            applied: false,
        }
    }
}

/// ContentPack manifest。
/// 对应 Python `DEFAULT_*_MANIFEST` / `resolve_content_pack` 输出。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    #[serde(default)]
    pub id: String,
    #[serde(default = "default_kind_freeform")]
    pub kind: String,
    #[serde(default = "default_ruleset")]
    pub ruleset: String,
    #[serde(default)]
    pub context_providers: Vec<String>,
    #[serde(default)]
    pub retrieval_policy: Value,
    #[serde(default)]
    pub gm_policy: Value,
    /// 兜底 — 允许任意额外字段。
    #[serde(default, flatten)]
    pub extra: BTreeMap<String, Value>,
}

fn default_kind_freeform() -> String {
    "freeform".to_string()
}

fn default_ruleset() -> String {
    "none".to_string()
}

impl Default for Manifest {
    fn default() -> Self {
        Self {
            id: String::new(),
            kind: default_kind_freeform(),
            ruleset: default_ruleset(),
            context_providers: Vec::new(),
            retrieval_policy: Value::Object(Default::default()),
            gm_policy: Value::Object(Default::default()),
            extra: BTreeMap::new(),
        }
    }
}

impl Manifest {
    pub fn get_retrieval_bool(&self, key: &str, default: bool) -> bool {
        self.retrieval_policy
            .get(key)
            .and_then(|v| v.as_bool())
            .unwrap_or(default)
    }
}
