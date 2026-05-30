//! context_agent — Demand Resolver。
//!
//! 对应 Python: `rpg/agents/context_agent.py`
//!
//! 把玩家自然语言翻成结构化 Demand(intent / constraints / retrieval_plan /
//! candidate_actions / acceptance / confidence),让主 GM 在受控的候选范围
//! 内决策。
//!
//! 主回路:
//!   1) `call_structured` 出 Demand JSON
//!   2) `resolve_content_pack(state)` → manifest(选脚本/书)
//!   3) `run_providers(state, manifest, demand, services)` → contributions
//!   4) `build_bundle_text(contributions)` 拼成最终 GM prompt 段
//!
//! Provider 调度由 rpg-context 内置的 registry 路由(rules / agent_runtime /
//! player_card / module / novel_retrieval 等),本 agent 只负责把 Demand 翻
//! 给它们。

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

        let mut demand = parse_demand(&raw).unwrap_or_default();

        // G8: Demand resolver fallback — 若 LLM 没有填充 retrieval_query,
        // 从 state 拼接兜底查询串:location + current_objective + user_input。
        // 对应 Python context_agent.py 的 fallback_retrieval_query 逻辑。
        if demand.retrieval_query.is_empty() {
            let loc = &state.data.player.current_location;
            let obj = &state.data.memory.current_objective;
            let mut parts: Vec<&str> = Vec::new();
            if !loc.is_empty() { parts.push(loc.as_str()); }
            if !obj.is_empty() { parts.push(obj.as_str()); }
            if !input.user_input.is_empty() { parts.push(input.user_input.as_str()); }
            demand.retrieval_query = parts.join(" ");
        }

        // 2) Provider 调度。直接传 typed GameStateData — rpg-context 内部统一处理
        let manifest: RcManifest = resolve_content_pack(&state.data, self.default_script_id);
        let rc_demand = to_rc_demand(&demand);
        let (contributions, used) =
            run_providers(&state.data, &manifest, &rc_demand, &self.services).await;

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

// ── 辅助:把玩家输入识别为时间跳转指令 ──
//
// 对应 Python `rpg/timeline_state.py::detect_time_directives`。返回归一化后
// 的 target 字符串列表(去重,保留首次出现顺序)。
//
// Rust 的 `regex` crate 不支持 Python 的某些 lookaround/重复语法,但本组
// 表达式是纯 forward,可以直接搬。Python 用 `re.findall` 取的是第一个分组
// (因为模式里第一组是 target);Rust 用 `Captures::get(1)` 对齐。
static TIME_DIRECTIVE_PATTERNS: once_cell::sync::Lazy<Vec<regex::Regex>> =
    once_cell::sync::Lazy::new(|| {
        let raw = [
            r"(?:时间线|时间|剧情|镜头|场景)?\s*(?:跳到|跳转到|快进到|切到|来到|推进到|过渡到|直接到|直接进入|进入|等到|等至|直到|跳过到|略过到|越过到)\s*([^，。！？\n]{2,48})",
            r"(?:/time|/timeline)\s+([^\n]{2,80})",
            r"(?:跳到|跳转到|快进到|切到|来到|进入)?\s*(第\s*\d{1,5}\s*章[^，。！？\n]{0,24})",
            r"(?:跳到|跳转到|快进到|切到|来到|进入)?\s*((?:公元)?\d{3,5}\s*年[^，。！？\n]{0,24})",
        ];
        raw.iter().filter_map(|p| regex::Regex::new(p).ok()).collect()
    });

pub fn detect_time_directives(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    let mut out: Vec<String> = Vec::new();
    for re in TIME_DIRECTIVE_PATTERNS.iter() {
        for caps in re.captures_iter(text) {
            let Some(m) = caps.get(1) else { continue };
            let target = clean_time_value(m.as_str());
            if looks_like_time_value(&target) && !out.iter().any(|t| t == &target) {
                out.push(target);
            }
        }
    }
    out
}

/// 对应 Python `timeline_state.clean_time_value`。
fn clean_time_value(text: &str) -> String {
    static WS: once_cell::sync::Lazy<regex::Regex> =
        once_cell::sync::Lazy::new(|| regex::Regex::new(r"\s+").unwrap());
    static LEADING: once_cell::sync::Lazy<regex::Regex> =
        once_cell::sync::Lazy::new(|| regex::Regex::new(r"^(?:到|至|在)\s*").unwrap());
    static TRAILING: once_cell::sync::Lazy<regex::Regex> = once_cell::sync::Lazy::new(|| {
        regex::Regex::new(r"(?:后?再)?(?:行动|出发|继续|调查|处理|会合|潜入|开场|开始)$").unwrap()
    });
    let trim_set: &[char] = &[' ', '\n', '\t', ':', '：', '-', '—'];
    let s = text.trim_matches(trim_set);
    let s = WS.replace_all(s, " ");
    let s = LEADING.replace(&s, "").into_owned();
    let s = TRAILING.replace(&s, "").into_owned();
    let s = WS.replace_all(&s, " ").into_owned();
    s.trim_matches(trim_set).to_string()
}

/// 对应 Python `timeline_state.looks_like_time_value`。
fn looks_like_time_value(value: &str) -> bool {
    let len = value.chars().count();
    if !(2..=80).contains(&len) {
        return false;
    }
    static RE: once_cell::sync::Lazy<regex::Regex> = once_cell::sync::Lazy::new(|| {
        regex::Regex::new(r"日|天|夜|晨|早|午|晚|周|月|年|后|前|翌|次|清晨|傍晚|深夜|黎明|柏林|图卢兹|基地|第\s*\d{1,5}\s*章").unwrap()
    });
    RE.is_match(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_time_directives_empty() {
        assert!(detect_time_directives("").is_empty());
    }

    #[test]
    fn test_detect_time_directives_simple_jump() {
        let out = detect_time_directives("时间线跳到第二天上午十点");
        assert!(!out.is_empty(), "should detect: {out:?}");
        assert!(
            out.iter().any(|t| t.contains("第二天")),
            "should contain 第二天: {out:?}"
        );
    }

    #[test]
    fn test_detect_time_directives_chapter() {
        let out = detect_time_directives("进入第10章");
        assert!(
            out.iter().any(|t| t.contains("第") && t.contains("章")),
            "should match chapter: {out:?}"
        );
    }

    #[test]
    fn test_detect_time_directives_year() {
        let out = detect_time_directives("快进到公元2049年");
        assert!(
            out.iter().any(|t| t.contains("2049年")),
            "should match year: {out:?}"
        );
    }

    #[test]
    fn test_detect_time_directives_no_time_word() {
        // 不含时间相关词,looks_like_time_value 应过滤掉
        let out = detect_time_directives("跳到xxx");
        assert!(out.is_empty(), "should reject non-time-looking: {out:?}");
    }

    #[test]
    fn test_clean_time_value_strips_trailing_verbs() {
        assert_eq!(clean_time_value("第二天清晨行动"), "第二天清晨");
        assert_eq!(clean_time_value(" 到 明日早晨 "), "明日早晨");
    }

    #[test]
    fn test_detect_time_directives_dedupes() {
        let out = detect_time_directives("跳到明天早晨,然后进入明天早晨调查");
        let count = out.iter().filter(|t| t.contains("明天早晨")).count();
        assert_eq!(count, 1, "dup target should be deduped: {out:?}");
    }
}
