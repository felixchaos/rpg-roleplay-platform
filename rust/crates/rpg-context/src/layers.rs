//! 通用 GM 层构建。对应 Python: rpg/context_engine/layers.py
//!
//! 包含 state_schema / fact_groups / candidate_actions / hypotheses / write_results 等
//! 不属于具体 ContentPack 的通用 GM 运行层。
//!
//! 注意:小说时间线锚点(_timeline_layer / _worldline_layer)已经被 Python 侧拆到
//! NovelTimelineProvider / WorldlineProvider 等;这里只保留通用层。

use crate::helpers::{normalize_permission_mode, permission_label};
use serde_json::Value;

/// state_schema 层。task 59:把 state 字段真实 schema + 当前 enum 喂给 LLM。
/// 对应 Python `_state_schema_layer(state, chars)`。
pub fn state_schema_layer(state_data: &Value, chars: &Value) -> String {
    let player = state_data.get("player").cloned().unwrap_or(Value::Null);
    let world = state_data.get("world").cloned().unwrap_or(Value::Null);
    let rels = state_data
        .get("relationships")
        .cloned()
        .unwrap_or(Value::Null);
    let worldline = state_data.get("worldline").cloned().unwrap_or(Value::Null);

    let get_player_str = |k: &str| {
        player
            .get(k)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    let get_world_str = |k: &str| {
        world
            .get(k)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };

    let player_name = get_player_str("name");
    let player_role = get_player_str("role");
    let player_background = get_player_str("background");
    let player_loc = get_player_str("current_location");
    let world_time = get_world_str("time");
    let world_weather = get_world_str("weather");

    let rels_keys: Vec<String> = rels
        .as_object()
        .map(|m| m.keys().cloned().collect())
        .unwrap_or_default();
    let chars_keys: Vec<String> = chars
        .as_object()
        .map(|m| {
            m.keys()
                .filter(|k| *k != &player_name)
                .cloned()
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let mut npcs: Vec<String> = Vec::new();
    npcs.extend(rels_keys);
    npcs.extend(chars_keys);
    npcs.sort();
    npcs.dedup();
    let known_npcs_str = if npcs.is_empty() {
        "（尚未识别任何 NPC）".to_string()
    } else {
        npcs.iter().take(20).cloned().collect::<Vec<_>>().join("、")
    };

    let user_vars = worldline
        .get("user_variables")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let var_names: Vec<&String> = user_vars.keys().take(10).collect();
    let var_names_str = if var_names.is_empty() {
        "（无）".to_string()
    } else {
        var_names
            .iter()
            .map(|s| s.to_string())
            .collect::<Vec<_>>()
            .join("、")
    };

    let bg_chars = player_background.chars().count();

    let lines = vec![
        "## 状态字段 schema（写入时严格遵循）".to_string(),
        String::new(),
        "**player.\\*** — 单字符串类型字段：".to_string(),
        format!(
            "- `player.name`: 字符串。当前 = {}",
            if player_name.is_empty() { "(空)" } else { &player_name }
        ),
        format!(
            "- `player.role`: 字符串。简短角色定位（如「史官」「侦探」「医师」），不是结构体。当前 = {}",
            if player_role.is_empty() { "(空)" } else { &player_role }
        ),
        format!(
            "- `player.background`: 字符串。一两句话背景。当前长度 = {} 字符",
            bg_chars
        ),
        format!(
            "- `player.current_location`: 字符串。简短地名（如「北港·灯塔下」「废弃矿道入口」「酒馆楼上」）。当前 = {}",
            if player_loc.is_empty() { "(空)" } else { &player_loc }
        ),
        String::new(),
        "**world.\\*** — 时间 / 已知事件：".to_string(),
        format!(
            "- `world.time`: 字符串。中式（如「申时三刻」）或西式（如「1937年4月12日傍晚」）均可，本档要一致。当前 = {}",
            if world_time.is_empty() { "(空)" } else { &world_time }
        ),
        format!(
            "- `world.weather`: 字符串可选。当前 = {}",
            if world_weather.is_empty() { "(空)" } else { &world_weather }
        ),
        "- `world.known_events`: 字符串数组。append 用【状态追加】或 JSON op=append。".to_string(),
        "- `world.timeline.current_phase`: 字符串。剧情阶段名。".to_string(),
        String::new(),
        "**relationships.<角色名>** — 字符串值（关系状态：信任/戒备/敌意/亲近/中立 等）：".to_string(),
        format!("- 当前已识别角色：{}", known_npcs_str),
        "- **优先使用已存在角色名**；新角色必须先在 GM 叙事里引入，再写 relationships。".to_string(),
        "- 错误写法：`relationships = {name: 张三, tier: 5}` （不是对象，是 path）".to_string(),
        "- 正确写法：`relationships.张三 = 信任` （path 含角色名，值是字符串）".to_string(),
        String::new(),
        "**memory.\\*** — 列表 vs 标量：".to_string(),
        "- 列表字段（append 用【状态追加】或 JSON op=append）：`memory.resources` / `memory.abilities` / `memory.facts` / `memory.pinned` / `memory.notes`".to_string(),
        "- 标量字段（直接覆盖）：`memory.main_quest` / `memory.current_objective` / `memory.mode`".to_string(),
        "- 列表内每项是字符串。".to_string(),
        String::new(),
        "**worldline.user_variables.<变量名>** — 玩家用 /set 创建的硬约束变量。".to_string(),
        format!("- 当前已定义变量：{}", var_names_str),
        "- 你可以读，但禁止主动新建（属于玩家硬约束领域）。".to_string(),
        String::new(),
        "**禁止写入（硬黑名单）**：`permissions.*` / `history.*` / `schema_version` / `created_at`".to_string(),
        "- 写入会被拒并写 audit_log。".to_string(),
    ];
    lines.join("\n")
}

/// task 76: 把记忆按 kind 分组渲染。对应 Python `_fact_groups_layer`。
pub fn fact_groups_layer(state_data: &Value) -> String {
    let memory = state_data.get("memory").cloned().unwrap_or(Value::Null);
    let items = memory
        .get("items")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let mut canon: Vec<Value> = Vec::new();
    let mut runtime: Vec<Value> = Vec::new();
    let mut constraints: Vec<Value> = Vec::new();

    for it in &items {
        let status = it.get("status").and_then(|v| v.as_str()).unwrap_or("active");
        if !status.is_empty() && status != "active" {
            continue;
        }
        let kind = it.get("kind").and_then(|v| v.as_str()).unwrap_or("");
        match kind {
            "canon_fact" => canon.push(it.clone()),
            "runtime_fact" => runtime.push(it.clone()),
            "user_constraint" => constraints.push(it.clone()),
            _ => {}
        }
    }

    fn sort_by_turn_desc(list: &mut [Value]) {
        list.sort_by(|a, b| {
            let at = a.get("turn").and_then(|v| v.as_i64()).unwrap_or(0);
            let bt = b.get("turn").and_then(|v| v.as_i64()).unwrap_or(0);
            bt.cmp(&at)
        });
    }
    sort_by_turn_desc(&mut canon);
    sort_by_turn_desc(&mut runtime);
    sort_by_turn_desc(&mut constraints);
    canon.truncate(8);
    runtime.truncate(12);
    constraints.truncate(6);

    let legacy_facts: Vec<String> = if runtime.is_empty() {
        memory
            .get("facts")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(String::from))
                    .filter(|s| !s.is_empty())
                    .take(10)
                    .collect()
            })
            .unwrap_or_default()
    } else {
        Vec::new()
    };

    let mut lines: Vec<String> = Vec::new();
    if !canon.is_empty() {
        lines.push("## 原著事实 (canon) —— 设定边界，不是本局发生过的".to_string());
        for it in &canon {
            let text = it.get("text").and_then(|v| v.as_str()).unwrap_or("?");
            lines.push(format!("- {}", short(text, 80)));
        }
        lines.push(String::new());
    }
    if !runtime.is_empty() {
        lines.push("## 本局已发生 (runtime) —— 玩家亲历，可叙事复述".to_string());
        for it in &runtime {
            let text = it.get("text").and_then(|v| v.as_str()).unwrap_or("?");
            let mut meta: Vec<String> = Vec::new();
            if let Some(tl) = it.get("time_label").and_then(|v| v.as_str()) {
                if !tl.is_empty() {
                    meta.push(tl.to_string());
                }
            }
            if let Some(chars_arr) = it.get("characters").and_then(|v| v.as_array()) {
                if !chars_arr.is_empty() {
                    let names: Vec<String> = chars_arr
                        .iter()
                        .take(3)
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect();
                    if !names.is_empty() {
                        meta.push(names.join("、"));
                    }
                }
            }
            let meta_str = if meta.is_empty() {
                String::new()
            } else {
                format!("（{}）", meta.join(" · "))
            };
            lines.push(format!("- {} {}", short(text, 80), meta_str));
        }
        lines.push(String::new());
    } else if !legacy_facts.is_empty() {
        lines.push("## 本局已发生 (runtime, legacy) —— 旧存档迁移前数据".to_string());
        for f in &legacy_facts {
            lines.push(format!("- {}", short(f, 80)));
        }
        lines.push(String::new());
    }
    if !constraints.is_empty() {
        lines.push("## 玩家硬约束 (user_constraint) —— 最高优先级，覆盖一切".to_string());
        for it in &constraints {
            let text = it.get("text").and_then(|v| v.as_str()).unwrap_or("?");
            lines.push(format!("- {}", short(text, 80)));
        }
    }
    if lines.is_empty() {
        return String::new();
    }
    lines.join("\n").trim_end().to_string()
}

fn short(s: &str, n: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() <= n {
        s.to_string()
    } else {
        chars.iter().take(n).collect::<String>()
    }
}

/// task 82:把 curator 的 candidate_actions 显式作为 anchor 喂给主 GM。
/// 对应 Python `_candidate_actions_layer`。
pub fn candidate_actions_layer(plan: Option<&Value>) -> String {
    let Some(plan) = plan else {
        return String::new();
    };
    let candidates = plan
        .get("candidate_actions")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    if candidates.is_empty() {
        return String::new();
    }
    let mut lines = vec![
        "Curator 为本轮列出了以下候选动作；**优先在候选范围内**叙事或写状态，".to_string(),
        "如果候选都不合适，可以选「其它」（在正文里说明你为什么偏离候选）：".to_string(),
    ];
    for (i, c) in candidates.iter().take(5).enumerate() {
        let s = match c {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        lines.push(format!("{}. {}", i + 1, short(&s, 120)));
    }
    lines.push("（候选是建议不是强制；最终输出仍由你判断。）".to_string());
    lines.join("\n")
}

/// task 75:暴露 active hypothesis 给 LLM。对应 Python `_active_hypotheses_layer`。
/// 注:这里我们假设 hypothesis 存在 `state.data.memory.items` 中(kind=="hypothesis"),
/// 而不是依赖 state 的 list_active_hypotheses 方法。
pub fn active_hypotheses_layer(state_data: &Value) -> String {
    let memory = state_data.get("memory").cloned().unwrap_or(Value::Null);
    let items = memory
        .get("items")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let hypos: Vec<&Value> = items
        .iter()
        .filter(|it| {
            it.get("kind").and_then(|v| v.as_str()) == Some("hypothesis")
                && it.get("status").and_then(|v| v.as_str()).unwrap_or("active") == "active"
        })
        .collect();
    if hypos.is_empty() {
        return String::new();
    }
    let mut lines = vec![
        "以下是本档**尚未确认的推测**（仅你/子代理的猜想，**绝不当作已发生事实复述**）：".to_string(),
    ];
    for h in hypos.iter().take(8) {
        let id = h.get("id").and_then(|v| v.as_str()).unwrap_or("?");
        let text = h.get("text").and_then(|v| v.as_str()).unwrap_or("?");
        let time_label = h.get("time_label").and_then(|v| v.as_str()).unwrap_or("");
        let chars_arr = h
            .get("characters")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let chars_str = chars_arr
            .iter()
            .filter_map(|v| v.as_str().map(String::from))
            .collect::<Vec<_>>()
            .join("、");
        let mut meta_parts: Vec<&str> = Vec::new();
        if !time_label.is_empty() {
            meta_parts.push(time_label);
        }
        if !chars_str.is_empty() {
            meta_parts.push(&chars_str);
        }
        let meta_str = if meta_parts.is_empty() {
            String::new()
        } else {
            format!("（{}）", meta_parts.join(" · "))
        };
        lines.push(format!("- [{}] {} {}", id, short(text, 60), meta_str));
    }
    lines.push(
        "如有新信息验证了某条推测，输出 `{\"op\":\"confirm_hypothesis\",\"id\":\"...\"}` 升级为事实；若被推翻输出 `{\"op\":\"reject_hypothesis\",\"id\":\"...\"}`。"
            .to_string(),
    );
    lines.join("\n")
}

/// task 54:把上轮 GM 标签的处理结果反馈给模型。对应 Python `_write_results_layer`。
pub fn write_results_layer(state_data: &Value) -> String {
    let memory = state_data.get("memory").cloned().unwrap_or(Value::Null);
    let permissions = state_data.get("permissions").cloned().unwrap_or(Value::Null);
    let last_updates = memory
        .get("last_structured_updates")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let pending = permissions
        .get("pending_writes")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let audit_log = permissions
        .get("audit_log")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let mut lines: Vec<String> = Vec::new();

    if !last_updates.is_empty() {
        lines.push("上轮你输出的标签实际结果：".to_string());
        for u in last_updates.iter().take(12) {
            lines.push(format!("- {}", value_to_text(u)));
        }
    }
    if !pending.is_empty() {
        lines.push(String::new());
        lines.push(format!(
            "当前待玩家审批的写入（共 {} 条 · 已入队，不要重写同一路径）：",
            pending.len()
        ));
        let start = pending.len().saturating_sub(8);
        for p in &pending[start..] {
            let risk = p.get("risk").and_then(|v| v.as_str()).unwrap_or("?");
            let field = p
                .get("path")
                .or_else(|| p.get("field"))
                .and_then(|v| v.as_str())
                .unwrap_or("?");
            let val = match p.get("value").or_else(|| p.get("to")) {
                Some(v) => value_to_text(v),
                None => String::new(),
            };
            lines.push(format!("- [{}] {} = {}", risk, field, short(&val, 50)));
        }
    }
    // hard blocked
    let recent_audit: Vec<&Value> = audit_log.iter().rev().take(15).collect();
    let blocked: Vec<&&Value> = recent_audit
        .iter()
        .filter(|a| a.get("blocked").and_then(|v| v.as_str()) == Some("hard_forbidden"))
        .collect();
    if !blocked.is_empty() {
        lines.push(String::new());
        lines.push("上轮被硬黑名单拒绝（permissions.* / history.* 任何形式都禁止，不要再写）：".to_string());
        for a in blocked.iter().take(5) {
            let path = a.get("path").and_then(|v| v.as_str()).unwrap_or("");
            let val = a.get("value").map(value_to_text).unwrap_or_default();
            lines.push(format!("- {} = {}", path, short(&val, 50)));
        }
    }

    // parse errors
    let recent_audit2: Vec<&Value> = audit_log.iter().rev().take(20).collect();
    let parse_errors: Vec<&&Value> = recent_audit2
        .iter()
        .filter(|a| a.get("kind").and_then(|v| v.as_str()) == Some("parse_error"))
        .collect();
    if !parse_errors.is_empty() {
        lines.push(String::new());
        lines.push("⚠️ 上轮你输出的标签**解析失败**（被静默丢弃前已记录，请改格式重试）：".to_string());
        for a in parse_errors.iter().take(5) {
            let raw = a.get("raw_spec").and_then(|v| v.as_str()).unwrap_or("?");
            lines.push(format!("- {}", short(raw, 60)));
            if let Some(hint) = a.get("hint").and_then(|v| v.as_str()) {
                if !hint.is_empty() {
                    lines.push(format!("  · 原因：{}", hint));
                }
            }
        }
        lines.push("正确格式参考：".to_string());
        lines.push("- JSON：`{\"op\":\"set\",\"path\":\"player.role\",\"value\":\"史官\"}`".to_string());
        lines.push("- 【】：`【状态写入：player.role=史官】`（半角 = 号；path 不要含空格）".to_string());
    }

    let rejected: Vec<&Value> = recent_audit
        .iter()
        .filter(|a| {
            let src = a
                .get("source")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            src.contains("rejected")
                || a.get("kind").and_then(|v| v.as_str()) == Some("rejected")
        })
        .copied()
        .collect();
    if !rejected.is_empty() {
        lines.push(String::new());
        lines.push("玩家拒绝过的最近写入（不要立即重写，先在叙事里铺垫或改用询问）：".to_string());
        for a in rejected.iter().take(5) {
            let path = a.get("path").and_then(|v| v.as_str()).unwrap_or("");
            let val = a.get("value").map(value_to_text).unwrap_or_default();
            lines.push(format!("- {} = {}", path, short(&val, 50)));
        }
    }

    if lines.is_empty() {
        return "（这是本档第一轮，或上轮没有任何标签输出）".to_string();
    }
    lines.join("\n")
}

fn value_to_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// worldline 通用层。对应 Python `_worldline_layer`(text 部分)。
pub fn worldline_layer_text(state_data: &Value) -> String {
    let permissions = state_data.get("permissions").cloned().unwrap_or(Value::Null);
    let worldline = state_data.get("worldline").cloned().unwrap_or(Value::Null);
    let variables = worldline
        .get("user_variables")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let mode = permissions
        .get("mode")
        .and_then(|v| v.as_str())
        .unwrap_or("full_access");

    let mut variable_lines: Vec<String> = Vec::new();
    for (name, info) in variables.iter() {
        let val = info
            .get("value")
            .map(value_to_text)
            .unwrap_or_else(|| value_to_text(info));
        variable_lines.push(format!("- {} = {}（硬约束）", name, val));
    }
    if variable_lines.is_empty() {
        variable_lines.push("- 暂无用户变量。".to_string());
    }

    let norm_mode = normalize_permission_mode(mode);
    let behavior = match norm_mode {
        "read_only" => "当前是【只读模式】：你的任何【状态写入】/【状态追加】都不会立即生效，全部进入玩家审批队列。所以这一轮请专注于讲叙事 + 用【询问玩家】把需要变更的地方做成选项让玩家决定，不要写多余的结构化标签。",
        "default" => "当前是【默认权限】：白名单内的字段（player.current_location / world.time / memory.main_quest / memory.current_objective / memory.resources / memory.abilities / memory.facts / world.known_events / relationships.*）会自动生效；其他字段进入审批队列。尽量只写白名单内的字段，少做需要审批的写入。",
        "auto_review" => "当前是【自动审查】：上面白名单字段 + worldline.user_variables.* + relationships.* 自动生效；其他需要审批。",
        _ => "当前是【完全访问】：除硬黑名单（permissions.* / history.* / schema_version）外，所有写入立即生效。你仍不能也不应该写 permissions.* —— 那是用户权限边界，由 UI 切换。",
    };

    let mut lines = vec![
        format!("LLM 写入权限：{}", permission_label(norm_mode)),
        behavior.to_string(),
        "用户变量与世界线推演规则：".to_string(),
    ];
    lines.extend(variable_lines);
    lines.push("推演机制：先把用户变量视作不可违背的硬条件，再结合当前时间线、世界书、角色卡和原著召回推演下一步局势。".to_string());
    lines.push("/set 生成的用户变量是最高优先级硬约束；如果它改变时间线、地点、世界观或人设，主 GM 必须按新设定写回结构化标签，而不是维护旧设定。".to_string());
    lines.push("如果推演满足全部用户变量，输出【设定校验：通过】；如果存在矛盾，输出【设定冲突：原因】，并不要把冲突推演写成事实。".to_string());
    lines.push("可输出【世界线推演：简要推演结果】供 UI 记录。".to_string());
    lines.push("当需要玩家决定下一步计划、分支方向或设定取舍时，输出【询问玩家：问题｜选项：选项A、选项B、选项C】；这类问题永远不因完全访问权限而自动跳过。".to_string());
    lines.join("\n")
}
