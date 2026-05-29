//! context_agent — Demand Resolver。
//!
//! 对应 Python: `rpg/agents/context_agent.py`
//!
//! 把玩家自然语言翻成结构化 Demand(intent / constraints / retrieval_plan /
//! candidate_actions / acceptance / confidence),让主 GM 在受控的候选范围
//! 内决策。
//!
//! ⚠️ Python 端深度耦合 context_engine / context_providers / retrieval。
//! Rust 端这些 crate 还都是 TODO,本骨架只完成「调 LLM 出 Demand JSON」
//! 这一最小回路,Provider 调度留 TODO。

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::common::{
    call_structured, extract_json_block, state_short_summary, AgentResult, ChatMessage, GameState,
    SharedLlm,
};

use rpg_context::{
    resolve_content_pack, run_providers, ContextContribution, Demand as RcDemand,
    Manifest as RcManifest, ProviderServices,
};

const AGENT_PROMPT: &str = include_str!("prompts/context_agent.txt");

const DEFAULT_MAX_TOKENS: usize = 900;

#[derive(Debug, Clone)]
pub struct ContextAgentInput {
    pub user_input: String,
    pub directives: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Demand {
    #[serde(default)]
    pub intent: String,
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
    #[serde(default)]
    pub timeline_target: String,
    #[serde(default)]
    pub retrieval_query: String,
    #[serde(default)]
    pub retrieval_plan: RetrievalPlan,
    #[serde(default)]
    pub candidate_actions: Vec<String>,
    #[serde(default)]
    pub rule_candidate_actions: Vec<Value>,
    #[serde(default)]
    pub acceptance: Vec<String>,
    #[serde(default)]
    pub risk_flags: Vec<String>,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub clarifying_question: String,
    #[serde(default)]
    pub reason: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RetrievalPlan {
    #[serde(default)]
    pub must_include: Vec<String>,
    #[serde(default)]
    pub should_include: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct ContextAgentOutput {
    pub demand: Demand,
    /// 给主 GM 的最终 prompt 段(从 demand + 各 provider 结果合成)。
    pub context_bundle: String,
    /// 各 provider 的 contributions(给上层做审计 / debug)。
    pub contributions: Vec<ContextContribution>,
    /// 实际启用的 provider id 列表。
    pub used_providers: Vec<String>,
}

pub struct ContextAgent {
    llm: SharedLlm,
    max_tokens: usize,
    /// 注入的 services(db_pool / retrieve_fn 等)。可为空。
    services: ProviderServices,
    /// 默认 script_id / book_id(可被 input override)。
    default_script_id: Option<i64>,
}

impl ContextAgent {
    pub fn new(llm: SharedLlm) -> Self {
        Self {
            llm,
            max_tokens: DEFAULT_MAX_TOKENS,
            services: ProviderServices::default(),
            default_script_id: None,
        }
    }

    pub fn with_services(mut self, services: ProviderServices) -> Self {
        self.services = services;
        self
    }

    pub fn with_default_script_id(mut self, script_id: Option<i64>) -> Self {
        self.default_script_id = script_id;
        self
    }

    /// 主入口。
    /// 1) LLM 出 Demand JSON。
    /// 2) resolve_content_pack(state) → manifest。
    /// 3) run_providers(state, manifest, demand, services) → contributions。
    /// 4) 把 contributions 拼成 context_bundle 文本(layers 拼接,priority 倒序)。
    pub async fn run(
        &self,
        input: ContextAgentInput,
        state: &GameState,
    ) -> AgentResult<ContextAgentOutput> {
        let user_prompt = build_curator_task_prompt(state, &input.user_input, &input.directives);
        let messages = vec![ChatMessage::user(user_prompt)];

        let raw = match call_structured(
            self.llm.as_ref(),
            AGENT_PROMPT,
            &messages,
            self.max_tokens,
        )
        .await
        {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("[context_agent] curator call failed: {e}");
                String::new()
            }
        };

        let demand = parse_demand(&raw).unwrap_or_default();

        // 2) Provider 调度。序列化为 Value 供 rpg-context 函数使用(跨 crate 接口保持 Value)。
        let state_data_value = serde_json::to_value(&state.data).unwrap_or(Value::Null);
        let manifest: RcManifest = resolve_content_pack(&state_data_value, self.default_script_id);
        let rc_demand = to_rc_demand(&demand);
        let (contributions, used) =
            run_providers(&state_data_value, &manifest, &rc_demand, &self.services).await;

        // 3) 拼接 context_bundle:按 priority 倒序的 layers,sticky 优先。
        let bundle = build_bundle_text(&contributions);

        Ok(ContextAgentOutput {
            demand,
            context_bundle: bundle,
            contributions,
            used_providers: used,
        })
    }
}

/// 把本地 Demand → rpg-context Demand(字段名差一点)。
fn to_rc_demand(d: &Demand) -> RcDemand {
    RcDemand {
        player_intent: d.intent.clone(),
        active_goal: d.active_goal.clone(),
        hard_constraints: d.hard_constraints.clone(),
        soft_preferences: d.soft_preferences.clone(),
        target_entities: d.target_entities.clone(),
        target_location: d.target_location.clone(),
        target_time: d.target_time.clone(),
        timeline_target: d.timeline_target.clone(),
        retrieval_query: d.retrieval_query.clone(),
        retrieval_needs: Value::Null,
        rule_candidate_actions: d.rule_candidate_actions.clone(),
        risk_flags: d.risk_flags.clone(),
        confidence: if d.confidence == 0.0 { 1.0 } else { d.confidence },
        clarifying_question: d.clarifying_question.clone(),
        reason: d.reason.clone(),
        raw_curator_plan: None,
    }
}

/// 把 contributions 拼成给 GM 用的 prompt 段。
fn build_bundle_text(contributions: &[ContextContribution]) -> String {
    let mut layers: Vec<(i32, bool, String, String)> = Vec::new();
    for c in contributions {
        if !c.applied {
            continue;
        }
        for l in &c.layers {
            if l.content.trim().is_empty() {
                continue;
            }
            layers.push((l.priority, l.sticky, l.title.clone(), l.content.clone()));
        }
        for fact in &c.facts {
            if fact.trim().is_empty() {
                continue;
            }
            layers.push((50, false, format!("[{}]", c.provider_id), fact.clone()));
        }
    }
    // sticky 先,然后按 priority 倒序。
    layers.sort_by(|a, b| {
        match (a.1, b.1) {
            (true, false) => std::cmp::Ordering::Less,
            (false, true) => std::cmp::Ordering::Greater,
            _ => b.0.cmp(&a.0),
        }
    });
    let mut out = String::new();
    for (_p, _s, title, content) in layers {
        if !out.is_empty() {
            out.push_str("\n\n");
        }
        out.push('【');
        out.push_str(&title);
        out.push_str("】\n");
        out.push_str(&content);
    }
    out
}

fn build_curator_task_prompt(
    state: &GameState,
    user_input: &str,
    directives: &[String],
) -> String {
    let mut out = String::new();
    out.push_str("## 当前剧情状态\n");
    out.push_str(&state_short_summary(state));
    out.push_str("\n\n## 玩家本轮输入\n");
    out.push_str(user_input);
    if !directives.is_empty() {
        out.push_str("\n\n## 系统指令(优先级最高)\n");
        for d in directives {
            out.push_str(&format!("- {d}\n"));
        }
    }
    out
}

fn parse_demand(text: &str) -> Option<Demand> {
    let blk = extract_json_block(text).ok()?;
    serde_json::from_str::<Demand>(blk).ok()
}

// ── 辅助:把玩家输入识别为时间跳转指令(简化版,Python 端用 timeline_state.detect_time_directives) ──
//
// TODO[rpg-context]: 调 rpg-context 的 timeline 实现。
#[doc(hidden)]
pub fn detect_time_directives(_text: &str) -> Vec<String> {
    Vec::new()
}
