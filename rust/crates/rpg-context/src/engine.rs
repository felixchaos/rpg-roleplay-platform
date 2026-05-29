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
use rpg_schemas::GameStateData;
use serde_json::{json, Value};

/// 组装单轮 prompt 上下文。
///
/// 对应 Python `build_context_bundle(state, user_input, retrieved_context, curator_plan,
/// script_id, book_id, contributions, manifest, save_id)`。
///
/// 调用方式:
/// - 新路径(推荐):传 contributions + manifest(已预先跑好 providers)。
/// - 旧路径:不传 contributions/manifest,本函数自动 resolve_content_pack + run_providers。
#[allow(clippy::too_many_arguments)]
pub async fn build_context_bundle(
    state_data: &GameStateData,
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
    // Wave 7-A: 把 services 提前 own 出来,后面 chars lazy-load 也要用 pool。
    // 旧 caller 不传 services 时构造默认值,保持 None 时不读 DB 的行为。
    let services_owned = services.unwrap_or(ProviderServices {
        user_id: None,
        script_id,
        book_id,
        save_id,
        ..Default::default()
    });
    let contributions = match contributions {
        Some(c) => c,
        None => {
            // 用空 Demand 跑
            let demand = Demand {
                player_intent: user_input.to_string(),
                retrieval_query: user_input.to_string(),
                ..Default::default()
            };
            let (contribs, _used) =
                run_providers(state_data, &manifest, &demand, &services_owned).await;
            contribs
        }
    };

    // 把 retrieved_context 当 fallback:仅在 contributions 没贡献 novel_retrieval 时用。
    let has_retrieval_layer = contributions
        .iter()
        .any(|c| c.applied && c.provider_id == "novel_retrieval");

    // 通用 GM 层
    let short_summary = state_short_summary(state_data);
    // Wave 7-A: chars 真接 — 走 chars_cache lazy-load + 60s TTL,对应 Python
    // `_safe_load_chars(script_id, book_id, manifest)`。
    //
    // 守门规则(Python `_safe_load_chars`):
    //   - manifest 为 freeform/module → 返 {}(NPC enum 不该掺小说角色卡)。
    //   - manifest 为 novel_adaptation 或 None → 走 DB 加载。
    //
    // 没注入 db_pool 时 chars_cache 直接返 {},不阻塞。
    let chars: Value = if should_load_chars(&manifest) {
        let arc = crate::chars_cache::load_chars_cached(
            services_owned.db_pool.as_ref(),
            services_owned.script_id,
            services_owned.book_id,
        )
        .await;
        // state_schema_layer 只读 keys,Arc<Value> deref 即可;但下方有 clone 需求
        // (chars 还要传给 contributions 循环之后的 schema 渲染),为了简单展开一份。
        (*arc).clone()
    } else {
        json!({})
    };
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
        .chain(provider_layers)
        .chain(tail_layers)
        .collect();
    all_layers.sort_by_key(|b| std::cmp::Reverse(b.priority));

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

/// Wave 7-A: 对应 Python `_safe_load_chars` 的 manifest.kind 守门。
///
/// 规则:
///   - manifest.kind == "novel_adaptation" → 加载小说角色卡。
///   - manifest.kind 空 / 其它 → 不加载(模组场景 NPC 不该掺角色卡 enum)。
///
/// Python:
/// ```python
/// if not manifest: return _load_characters(...)
/// if manifest.get("kind") == "novel_adaptation": return _load_characters(...)
/// return {}
/// ```
fn should_load_chars(manifest: &Manifest) -> bool {
    manifest.kind.is_empty() || manifest.kind == "novel_adaptation"
}

/// 对应 Python `state.short_summary()`。
/// 这是一个精简版:挑 GameState 里 GM 最常需要的几个公开字段,把它们渲染成一段文本。
/// 不暴露 player_private / secrets / story_intent 这些只属于玩家私域的字段。
/// rpg-state 完全成熟时这里可改成直接调 `state.short_summary()`。
fn state_short_summary(state_data: &GameStateData) -> String {
    let mut lines: Vec<String> = Vec::new();

    // ── 玩家段 ───────────────────────────────────────────────
    let p_name = &state_data.player.name;
    let p_role = &state_data.player.role;
    let p_loc = &state_data.player.current_location;
    // hp/hp_max/current_status 来自 player.extra(动态字段)
    let p_hp = state_data.player.extra.get("hp")
        .or_else(|| state_data.player.extra.get("HP"));
    let p_hp_max = state_data.player.extra.get("hp_max")
        .or_else(|| state_data.player.extra.get("max_hp"));
    let p_status = state_data.player.extra
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
    let w_time = &state_data.world.time;
    // scene.location / scene.extra["location"] 是动态字段
    let s_loc = state_data.scene.extra
        .get("location")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let s_phase = &state_data.world.timeline.current_phase;
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
    if state_data.encounter.active {
        lines.push(String::new());
        lines.push("【遭遇】".to_string());
        lines.push("状态：进行中".to_string());
        // name/enemies 在 encounter.extra 里
        if let Some(name) = state_data.encounter.extra.get("name").and_then(|v| v.as_str()) {
            if !name.is_empty() {
                lines.push(format!("名称：{}", name));
            }
        }
        if let Some(enemies) = state_data.encounter.extra.get("enemies").and_then(|v| v.as_array()) {
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

    // ── 关系 / 记忆精简 ───────────────────────────────────────
    if !state_data.relationships.is_empty() {
        lines.push(String::new());
        lines.push("【关系】".to_string());
        for (k, v) in state_data.relationships.iter().take(8) {
            lines.push(format!("· {}：{}", k, value_to_short(v)));
        }
    }
    let main_quest = &state_data.memory.main_quest;
    let cur_obj = &state_data.memory.current_objective;
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
