//! 规则文本生成函数。
//! 对应 Python: rpg/context_engine/rules_text.py

use serde_json::Value;

pub fn story_rules() -> &'static str {
    "这是沉浸式文字 RPG。GM 只描写玩家角色能感知或通过合理渠道获知的信息。\n\
保持原著风格：克制、精确、信息密度高，不把 NPC 写成答题机器。\n\
不要替玩家决定行动。结尾可以给压力、线索或抉择，但不代替玩家选择。\n\
玩家行动可能改变原著分支，世界书和角色卡优先维持人物逻辑与势力边界。\n\
本轮发生状态变化时，在正文末尾追加结构化标签，方便系统写回存档。"
}

pub fn agent_runtime_rules() -> &'static str {
    "本轮务必执行: 读子代理决议 → 裁定世界反应 → 输出正文 → 输出 JSON ops 数组（仅当真有变化时）。\n\
如上下文不足以推进，在正文里说明不确定性并输出 question op 让玩家选择，不要瞎编。"
}

/// 对应 Python `_context_agent_decision`。
pub fn context_agent_decision(plan: Option<&Value>) -> String {
    let plan = match plan {
        Some(p) if p.is_object() => p,
        _ => return "本轮没有大模型子代理决议；主 GM 必须按时间线层和检索参考保守生成。".to_string(),
    };

    let get_str = |k: &str| {
        plan.get(k)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    let get_arr = |k: &str| plan.get(k).and_then(|v| v.as_array()).cloned().unwrap_or_default();

    let must_include = {
        let direct = get_arr("must_include");
        if !direct.is_empty() {
            direct
        } else {
            plan.get("retrieval_plan")
                .and_then(|v| v.get("must_include"))
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default()
        }
    };
    let risk_flags = get_arr("risk_flags");
    let hard = get_arr("hard_constraints");
    let soft = get_arr("soft_preferences");
    let targets_e = get_arr("target_entities");
    let acceptance = get_arr("acceptance");
    let candidates = get_arr("candidate_actions");
    let conf = plan
        .get("confidence")
        .and_then(|v| v.as_f64())
        .unwrap_or(1.0);
    let clarify = get_str("clarifying_question");
    let clarify = clarify.trim();

    let intent = get_str("intent");
    let active_goal = get_str("active_goal");
    let timeline_target = get_str("timeline_target");
    let target_location = get_str("target_location");
    let target_time = get_str("target_time");

    let mut lines: Vec<String> = Vec::new();
    lines.push(format!(
        "子代理意图：{}",
        if intent.is_empty() { "未说明" } else { &intent }
    ));
    if !active_goal.is_empty() {
        lines.push(format!("底层真实目标：{}", active_goal));
    }
    lines.push(format!(
        "目标时间线：{}",
        if timeline_target.is_empty() {
            "未请求跳转"
        } else {
            &timeline_target
        }
    ));
    if !target_location.is_empty() {
        lines.push(format!("目标地点：{}", target_location));
    }
    if !target_time.is_empty() {
        lines.push(format!("目标时间：{}", target_time));
    }
    if !targets_e.is_empty() {
        let joined = targets_e
            .iter()
            .take(8)
            .map(|v| value_to_short(v))
            .collect::<Vec<_>>()
            .join("、");
        lines.push(format!("涉及实体：{}", joined));
    }
    if !hard.is_empty() {
        lines.push("【硬约束】（必须满足）".to_string());
        for c in hard.iter().take(6) {
            lines.push(format!("  · {}", value_to_short(c)));
        }
    }
    if !soft.is_empty() {
        lines.push("【软偏好】（最好满足，可妥协）".to_string());
        for c in soft.iter().take(6) {
            lines.push(format!("  · {}", value_to_short(c)));
        }
    }
    let retrieval_query = get_str("retrieval_query");
    lines.push(format!(
        "检索查询：{}",
        if retrieval_query.is_empty() {
            "未提供"
        } else {
            &retrieval_query
        }
    ));
    if must_include.is_empty() {
        lines.push("必含事实：无".to_string());
    } else {
        let joined = must_include
            .iter()
            .map(|v| value_to_short(v))
            .collect::<Vec<_>>()
            .join("；");
        lines.push(format!("必含事实：{}", joined));
    }
    if !acceptance.is_empty() {
        lines.push("【本轮 acceptance 验收】（输出后系统会检查每条是否满足）".to_string());
        for a in acceptance.iter().take(6) {
            lines.push(format!("  · {}", value_to_short(a)));
        }
    }
    if !candidates.is_empty() {
        lines.push("【候选动作建议】（GM 可优先从中选；不强制）".to_string());
        for c in candidates.iter().take(5) {
            lines.push(format!("  · {}", value_to_short(c)));
        }
    }
    if risk_flags.is_empty() {
        lines.push("风险标记：无".to_string());
    } else {
        let joined = risk_flags
            .iter()
            .map(|v| value_to_short(v))
            .collect::<Vec<_>>()
            .join("；");
        lines.push(format!("风险标记：{}", joined));
    }
    lines.push(format!("子代理置信度：{:.2}", conf));
    if !clarify.is_empty() {
        lines.push(format!("⚠️ 子代理建议先问玩家：{}", clarify));
    }
    let reason = get_str("reason");
    lines.push(format!(
        "选择理由：{}",
        if reason.is_empty() { "未说明" } else { &reason }
    ));
    lines.push(
        "主 GM 只能把这些作为上下文选择结果使用，不得把子代理理由写成玩家可见事实。"
            .to_string(),
    );
    lines.join("\n")
}

fn value_to_short(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// 对应 Python `_context_agent_debug`。
pub fn context_agent_debug(plan: Option<&Value>) -> Value {
    let Some(plan) = plan else {
        return Value::Object(Default::default());
    };
    if !plan.is_object() {
        return Value::Object(Default::default());
    }
    let take = |k: &str| plan.get(k).cloned().unwrap_or(Value::Null);
    serde_json::json!({
        "intent": take("intent"),
        "active_goal": take("active_goal"),
        "timeline_target": take("timeline_target"),
        "retrieval_query": take("retrieval_query"),
        "must_include": take("must_include"),
        "hard_constraints": take("hard_constraints"),
        "soft_preferences": take("soft_preferences"),
        "target_entities": take("target_entities"),
        "candidate_actions": take("candidate_actions"),
        "acceptance": take("acceptance"),
        "risk_flags": take("risk_flags"),
        "confidence": plan.get("confidence").cloned().unwrap_or(Value::from(1.0)),
        "clarifying_question": take("clarifying_question"),
    })
}
