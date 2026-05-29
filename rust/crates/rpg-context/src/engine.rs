//! 主入口 build_context_bundle — 组装单轮 prompt 上下文。
//! 对应 Python: rpg/context_engine/core.py::build_context_bundle

use crate::helpers::pending_jump_warning_text;
use crate::layers::{
    active_hypotheses_layer, candidate_actions_layer, fact_groups_layer, state_schema_layer,
    write_results_layer,
};
use crate::provider::ProviderServices;
use crate::registry::{resolve_content_pack, run_providers};
use crate::rules_text::{agent_runtime_rules, context_agent_debug, context_agent_decision, story_rules};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use crate::utils::{cache_plan, estimate_tokens, max_layer_chars, neutralize_state_write_tags, preview, trim_text};
use serde_json::{json, Value};

/// 组装单轮 prompt 上下文。
///
/// 对应 Python `build_context_bundle(state, user_input, retrieved_context, curator_plan,
/// script_id, book_id, contributions, manifest, save_id)`。
///
/// 调用方式:
/// - 新路径(推荐):传 contributions + manifest(已预先跑好 providers)。
/// - 旧路径:不传 contributions/manifest,本函数自动 resolve_content_pack + run_providers。
pub async fn build_context_bundle(
    state_data: &Value,
    user_input: &str,
    retrieved_context: &str,
    curator_plan: Option<&Value>,
    script_id: Option<i64>,
    book_id: Option<i64>,
    contributions: Option<Vec<ContextContribution>>,
    manifest: Option<Manifest>,
    save_id: Option<i64>,
    services: Option<ProviderServices>,
) -> Value {
    // 自动 resolve manifest + run providers(旧 caller 兼容)
    let manifest = manifest.unwrap_or_else(|| resolve_content_pack(state_data, script_id));
    let contributions = match contributions {
        Some(c) => c,
        None => {
            // 用空 Demand 跑
            let demand = Demand {
                player_intent: user_input.to_string(),
                retrieval_query: user_input.to_string(),
                ..Default::default()
            };
            let svcs = services.unwrap_or(ProviderServices {
                user_id: None,
                script_id,
                book_id,
                save_id,
                ..Default::default()
            });
            let (contribs, _used) = run_providers(state_data, &manifest, &demand, &svcs).await;
            contribs
        }
    };

    // 把 retrieved_context 当 fallback:仅在 contributions 没贡献 novel_retrieval 时用。
    let has_retrieval_layer = contributions
        .iter()
        .any(|c| c.applied && c.provider_id == "novel_retrieval");

    // 通用 GM 层
    let short_summary = state_short_summary(state_data);
    let chars = json!({}); // TODO: 等 rpg-state 提供 _safe_load_chars 等价物
    let universal_layers = vec![
        Layer::new("rules", "剧情规则", story_rules()).with_sticky(true).with_priority(100),
        Layer::new(
            "agent_runtime",
            "主GM代理运行契约",
            agent_runtime_rules(),
        )
        .with_sticky(true)
        .with_priority(99),
        Layer::new(
            "timeline_pending",
            "时间跳跃待确认",
            pending_jump_warning_text(state_data),
        )
        .with_priority(86),
        Layer::new("state", "当前状态", short_summary)
            .with_sticky(true)
            .with_priority(55),
        Layer::new(
            "fact_groups",
            "事实分组（按 kind）",
            fact_groups_layer(state_data),
        )
        .with_priority(50),
        Layer::new(
            "state_schema",
            "状态字段 schema",
            state_schema_layer(state_data, &chars),
        )
        .with_sticky(true)
        .with_priority(45),
        Layer::new(
            "write_results",
            "上轮标签处理结果",
            write_results_layer(state_data),
        )
        .with_priority(35),
        Layer::new(
            "hypotheses",
            "未确认推测",
            active_hypotheses_layer(state_data),
        )
        .with_priority(32),
        Layer::new(
            "context_agent",
            "子代理上下文决议",
            context_agent_decision(curator_plan),
        )
        .with_priority(30)
        .with_items(vec![context_agent_debug(curator_plan)]),
        Layer::new(
            "candidate_actions",
            "本轮候选动作",
            candidate_actions_layer(curator_plan),
        )
        .with_priority(28),
    ];

    // Provider contribution 层
    let mut provider_layers: Vec<Layer> = Vec::new();
    let mut contribution_meta: Vec<Value> = Vec::new();
    for contrib in &contributions {
        if !contrib.applied {
            continue;
        }
        contribution_meta.push(json!({
            "provider_id": contrib.provider_id,
            "kind": contrib.kind,
            "priority": contrib.priority,
            "facts": contrib.facts,
            "warnings": contrib.warnings,
            "tokens_estimate": contrib.tokens_estimate,
            "debug": contrib.debug,
        }));
        for layer in &contrib.layers {
            let mut lyr = layer.clone();
            if lyr.priority == 50 {
                // 没显式设过 priority,继承 contribution.priority
                lyr.priority = contrib.priority;
            }
            if lyr.source.is_empty() {
                lyr.source = contrib.provider_id.clone();
            }
            provider_layers.push(lyr);
        }
    }

    // 兜底 rag 层
    if !has_retrieval_layer && !retrieved_context.is_empty() {
        provider_layers.push(
            Layer::new(
                "rag",
                "检索参考",
                neutralize_state_write_tags(retrieved_context),
            )
            .with_priority(40),
        );
    }

    // user_input 永远最后
    let tail_layers = vec![Layer::new(
        "user_input",
        "玩家本轮输入",
        if user_input.is_empty() { "（空）" } else { user_input },
    )
    .with_priority(0)];

    // 合并 + 按 priority 降序排序
    let mut all_layers: Vec<Layer> = universal_layers
        .into_iter()
        .chain(provider_layers.into_iter())
        .chain(tail_layers.into_iter())
        .collect();
    all_layers.sort_by(|a, b| b.priority.cmp(&a.priority));

    let max_chars_map = max_layer_chars();
    let mut prompt_parts: Vec<String> = Vec::new();
    let mut debug_layers: Vec<Value> = Vec::new();
    for layer in &all_layers {
        let cap = *max_chars_map.get(layer.id.as_str()).unwrap_or(&1800);
        let trimmed = trim_text(&layer.content, cap);
        if trimmed.is_empty() {
            continue;
        }
        prompt_parts.push(format!("【{}】\n{}", layer.title, trimmed));
        debug_layers.push(json!({
            "id": layer.id,
            "title": layer.title,
            "chars": trimmed.chars().count(),
            "estimated_tokens": estimate_tokens(&trimmed),
            "sticky": layer.sticky,
            "priority": layer.priority,
            "source": layer.source,
            "preview": preview(&trimmed, 140),
            "items": layer.items.clone(),
        }));
    }

    let prompt = prompt_parts.join("\n\n");
    let plan_value = cache_plan(&debug_layers, &prompt_parts);
    let debug = json!({
        "total_chars": prompt.chars().count(),
        "estimated_tokens": estimate_tokens(&prompt),
        "layers": debug_layers,
        "cache_plan": plan_value,
        "curator_plan": curator_plan.cloned().unwrap_or(Value::Object(Default::default())),
        "manifest": json!({
            "id": manifest.id,
            "kind": manifest.kind,
            "context_providers": manifest.context_providers,
            "retrieval_policy": manifest.retrieval_policy,
            "gm_policy": manifest.gm_policy,
        }),
        "contributions": contribution_meta,
    });

    json!({
        "prompt": prompt,
        "debug": debug,
    })
}

/// 对应 Python `state.short_summary()`。
/// 这是一个精简版:挑 GameState 里 GM 最常需要的几个公开字段,把它们渲染成一段文本。
/// 不暴露 player_private / secrets / story_intent 这些只属于玩家私域的字段。
/// rpg-state 完全成熟时这里可改成直接调 `state.short_summary()`。
fn state_short_summary(state_data: &Value) -> String {
    let mut lines: Vec<String> = Vec::new();

    // ── 玩家段 ───────────────────────────────────────────────
    let player = state_data.get("player").cloned().unwrap_or(Value::Null);
    let p_name = player.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let p_role = player.get("role").and_then(|v| v.as_str()).unwrap_or("");
    let p_loc = player
        .get("current_location")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let p_hp = player.get("hp").or_else(|| player.get("HP"));
    let p_hp_max = player.get("hp_max").or_else(|| player.get("max_hp"));
    let p_status = player
        .get("current_status")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    lines.push("【玩家档案】".to_string());
    if !p_name.is_empty() {
        lines.push(format!("姓名：{}", p_name));
    }
    if !p_role.is_empty() {
        lines.push(format!("定位：{}", p_role));
    }
    if !p_loc.is_empty() {
        lines.push(format!("当前位置：{}", p_loc));
    }
    if let Some(hp) = p_hp {
        let hp_str = value_to_short(hp);
        let hp_text = match p_hp_max {
            Some(max) => format!("HP：{} / {}", hp_str, value_to_short(max)),
            None => format!("HP：{}", hp_str),
        };
        lines.push(hp_text);
    }
    if !p_status.is_empty() {
        lines.push(format!("状态：{}", p_status));
    }

    // ── 场景 / 时间 ───────────────────────────────────────────
    let world = state_data.get("world").cloned().unwrap_or(Value::Null);
    let scene = state_data
        .get("scene")
        .cloned()
        .or_else(|| world.get("scene").cloned())
        .unwrap_or(Value::Null);
    let w_time = world.get("time").and_then(|v| v.as_str()).unwrap_or("");
    let s_loc = scene.get("location").and_then(|v| v.as_str()).unwrap_or("");
    let s_phase = world
        .pointer("/timeline/current_phase")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !w_time.is_empty() || !s_loc.is_empty() || !s_phase.is_empty() {
        lines.push(String::new());
        lines.push("【场景 / 时间】".to_string());
        if !w_time.is_empty() {
            lines.push(format!("时间：{}", w_time));
        }
        if !s_loc.is_empty() {
            lines.push(format!("场景：{}", s_loc));
        }
        if !s_phase.is_empty() {
            lines.push(format!("当前阶段：{}", s_phase));
        }
    }

    // ── 战斗 / encounter ──────────────────────────────────────
    let encounter = state_data
        .get("encounter")
        .cloned()
        .or_else(|| scene.get("encounter").cloned())
        .unwrap_or(Value::Null);
    if encounter.is_object() {
        let active = encounter
            .get("active")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        if active {
            lines.push(String::new());
            lines.push("【遭遇】".to_string());
            lines.push("状态：进行中".to_string());
            if let Some(name) = encounter.get("name").and_then(|v| v.as_str()) {
                if !name.is_empty() {
                    lines.push(format!("名称：{}", name));
                }
            }
            if let Some(enemies) = encounter.get("enemies").and_then(|v| v.as_array()) {
                let names: Vec<String> = enemies
                    .iter()
                    .filter_map(|e| {
                        e.get("name")
                            .and_then(|v| v.as_str())
                            .or_else(|| e.as_str())
                            .map(|s| s.to_string())
                    })
                    .collect();
                if !names.is_empty() {
                    lines.push(format!("敌方：{}", names.join("、")));
                }
            }
        }
    }

    // ── 关系 / 记忆精简 ───────────────────────────────────────
    if let Some(rels) = state_data.get("relationships").and_then(|v| v.as_object()) {
        if !rels.is_empty() {
            lines.push(String::new());
            lines.push("【关系】".to_string());
            for (k, v) in rels.iter().take(8) {
                lines.push(format!("· {}：{}", k, value_to_short(v)));
            }
        }
    }
    let memory = state_data.get("memory").cloned().unwrap_or(Value::Null);
    let main_quest = memory
        .get("main_quest")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let cur_obj = memory
        .get("current_objective")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !main_quest.is_empty() || !cur_obj.is_empty() {
        lines.push(String::new());
        lines.push("【目标】".to_string());
        if !main_quest.is_empty() {
            lines.push(format!("主线：{}", main_quest));
        }
        if !cur_obj.is_empty() {
            lines.push(format!("当前目标：{}", cur_obj));
        }
    }

    if lines.is_empty() {
        "（状态尚未初始化）".to_string()
    } else {
        lines.join("\n")
    }
}

/// 把 Value 压成简短字符串(数字/字符串原样,其他用 to_string)。
fn value_to_short(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// 把 history 数组渲染成 GM 看的对话块。对应 Python `_format_history`。
pub fn format_history(history: &[Value]) -> String {
    if history.is_empty() {
        return "（暂无最近对话）".to_string();
    }
    history
        .iter()
        .map(|msg| {
            let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("");
            let content = msg.get("content").and_then(|v| v.as_str()).unwrap_or("");
            let prefix = if role == "user" { "玩家" } else { "GM" };
            format!("{}：{}", prefix, content)
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

/// 对应 Python `_recent_text`。
pub fn recent_text(history: &[Value]) -> String {
    history
        .iter()
        .map(|msg| {
            msg.get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string()
        })
        .collect::<Vec<_>>()
        .join("\n")
}
