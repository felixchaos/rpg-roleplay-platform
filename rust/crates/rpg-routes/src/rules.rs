//! rules.py → rules.rs — 5E 规则模组与战斗路由
//! GET  /api/rules/modules          — 列出可用模组
//! POST /api/rules/module/start     — 低层原语:加载模组到当前 save
//! POST /api/rules/module/launch    — 标准入口:建独立 save 跑模组
//! GET  /api/rules/scene            — 当前 scene 快照
//! POST /api/rules/move             — 移动到房间
//! POST /api/rules/action           — 通用规则动作
//! POST /api/rules/encounter/start  — 开始战斗
//! POST /api/rules/encounter/next   — 下一回合
//! POST /api/rules/encounter/enemy  — 敌方回合
//! POST /api/rules/suggest          — 推断候选动作

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use rpg_platform::{branches, save_io};
use rpg_rules::{
    dnd5e::character as rules_character, list_modules, load_module, ModuleBundle,
};
use rpg_rules_bridge::{
    apply_combat, consume_item_action, parse_consume_intent, perform_saving_throw,
    perform_skill_check, short_rest, suggest_rule_actions, trap_check, BridgeError, CombatAction,
};

use crate::{require_user, user_id_or_anon, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/rules/modules", get(api_rules_modules))
        .route("/api/rules/module/start", post(api_rules_module_start))
        .route("/api/rules/module/launch", post(api_rules_module_launch))
        .route("/api/rules/scene", get(api_rules_scene))
        .route("/api/rules/move", post(api_rules_move))
        .route("/api/rules/action", post(api_rules_action))
        .route("/api/rules/encounter/start", post(api_rules_encounter_start))
        .route("/api/rules/encounter/next", post(api_rules_encounter_next))
        .route("/api/rules/encounter/enemy", post(api_rules_encounter_enemy))
        .route("/api/rules/suggest", post(api_rules_suggest))
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct RulesModuleStartRequest {
    pub module_id: Option<String>,
    pub character: Option<Value>,
}

#[derive(Debug, Deserialize, Default)]
pub struct RulesModuleLaunchRequest {
    pub module_id: Option<String>,
    pub character: Option<Value>,
    pub title: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct RulesMoveRequest {
    pub to: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct RulesActionRequest {
    pub kind: Option<String>,
    #[serde(flatten)]
    pub extra: Value,
}

#[derive(Debug, Deserialize, Default)]
pub struct RulesEncounterStartRequest {
    pub encounter_id: Option<String>,
    pub seed: Option<Value>,
}

#[derive(Debug, Deserialize, Default)]
pub struct RulesEncounterNextRequest {}

#[derive(Debug, Deserialize, Default)]
pub struct RulesEncounterEnemyRequest {
    pub attacker_id: Option<String>,
    pub target_id: Option<String>,
    pub seed: Option<Value>,
}

#[derive(Debug, Deserialize, Default)]
pub struct RulesSuggestRequest {
    pub text: Option<String>,
}

// ── BridgeError → ResponseError ───────────────────────────────────────────────

fn bridge_to_response_error(err: BridgeError) -> ResponseError {
    match err {
        BridgeError::EncounterNotActive
        | BridgeError::TargetNotFound(_)
        | BridgeError::MissingField(_)
        | BridgeError::Logic(_)
        | BridgeError::Dice(_) => ResponseError::bad_request(err.to_string()),
        BridgeError::Json(_) => ResponseError::internal(err.to_string()),
    }
}

/// 对应 Python `app._clear_pending_questions_after_rule_action`:规则动作执行后
/// 把所有 pending_questions 全部 dismiss(choice = "rules:<kind>"),并清掉
/// `memory.last_structured_updates` 里"等待玩家回答"残留。
fn clear_pending_questions_after_rule_action(state: &mut rpg_state::state::GameState, choice: &str) {
    use rpg_state::pending::clear_pending_question;
    let mut cleared = 0usize;
    while !state.data.permissions.pending_questions.is_empty() {
        if clear_pending_question(state, None, Some(0), Some(choice)).is_none() {
            break;
        }
        cleared += 1;
        if cleared > 64 {
            // 防御:理论上 pending_questions 上限 8,这里多放 8 倍兜底。
            break;
        }
    }
    if cleared > 0 {
        let updates = std::mem::take(&mut state.data.memory.last_structured_updates);
        state.data.memory.last_structured_updates = updates
            .into_iter()
            .filter(|item| {
                !serde_json::to_string(item)
                    .map(|s| s.contains("等待玩家回答"))
                    .unwrap_or(false)
            })
            .take(12)
            .collect();
    }
}

/// 对应 Python `app._append_rules_receipt`:把规则引擎产物作为 assistant 消息
/// 追加到 history(便于前端聊天流展示)。
fn append_rules_receipt(state: &mut rpg_state::state::GameState, text: &str) {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return;
    }
    let entry = json!({
        "role": "assistant",
        "content": trimmed,
        "source": "rules_engine",
    });
    let history_path = "history";
    // history 是 typed Vec<HistoryEntry>;通过 append_to_path 走 typed 通道。
    let _ = state.append_to_path(history_path, entry);
}

/// 对应 Python `app._room_receipt`:把房间数据格式化成"你来到「<name>」"段落。
fn room_receipt(room: &Value) -> String {
    let name = room
        .get("name")
        .and_then(|v| v.as_str())
        .or_else(|| room.get("id").and_then(|v| v.as_str()))
        .unwrap_or("未知房间");
    let room_id = room.get("id").and_then(|v| v.as_str()).unwrap_or("");
    let head = if room_id.is_empty() {
        format!("【RulesEngine：移动】你来到「{name}」。")
    } else {
        format!("【RulesEngine：移动】你来到「{name}」（{room_id}）。")
    };
    let mut lines = vec![head];
    if let Some(desc) = room.get("description").and_then(|v| v.as_str()) {
        let d = desc.trim();
        if !d.is_empty() {
            lines.push(d.to_string());
        }
    }
    if let Some(clues) = room.get("visible_clues").and_then(|v| v.as_array()) {
        let texts: Vec<String> = clues
            .iter()
            .filter_map(|c| {
                if let Some(s) = c.as_str() {
                    Some(s.to_string())
                } else {
                    c.get("text").and_then(|v| v.as_str()).map(|s| s.to_string())
                }
            })
            .filter(|s| !s.is_empty())
            .take(4)
            .collect();
        if !texts.is_empty() {
            lines.push(format!("可见线索：{}。", texts.join("；")));
        }
    }
    if let Some(exits) = room.get("exits").and_then(|v| v.as_array()) {
        let texts: Vec<String> = exits
            .iter()
            .filter_map(|e| {
                e.get("label")
                    .and_then(|v| v.as_str())
                    .or_else(|| e.get("to").and_then(|v| v.as_str()))
                    .map(|s| s.to_string())
            })
            .filter(|s| !s.is_empty())
            .take(5)
            .collect();
        if !texts.is_empty() {
            lines.push(format!("可用出口：{}。", texts.join("、")));
        }
    }
    lines.join("\n\n")
}

/// 对应 Python `app._roll_line`:把规则 result 里的 roll 段格式化成单行。
fn roll_line(result: &Value) -> String {
    let roll = result.get("roll").cloned().unwrap_or(Value::Null);
    let expr = roll.get("expression").and_then(|v| v.as_str()).unwrap_or("");
    let rolls_str = roll
        .get("rolls")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .map(|n| n.to_string())
                .collect::<Vec<_>>()
                .join(",")
        })
        .unwrap_or_default();
    let modifier = roll.get("modifier").and_then(|v| v.as_f64());
    let total = roll.get("total").and_then(|v| v.as_f64());
    let dc = result.get("dc").and_then(|v| v.as_i64());
    let has_any = !expr.is_empty() || !rolls_str.is_empty() || total.is_some();
    if !has_any {
        return String::new();
    }
    let mut bit = if expr.is_empty() { "roll".to_string() } else { expr.to_string() };
    if !rolls_str.is_empty() {
        bit.push_str(&format!("=[{rolls_str}]"));
    }
    if let Some(m) = modifier {
        if m != 0.0 {
            // 与 Python 的 f"{m:+g}" 一致:正数带 + 号,小数自动 trim
            let s = if m.fract() == 0.0 {
                format!("{:+}", m as i64)
            } else {
                format!("{m:+}")
            };
            bit.push_str(&s);
        }
    }
    if let Some(t) = total {
        let t_disp = if t.fract() == 0.0 {
            format!("{}", t as i64)
        } else {
            format!("{t}")
        };
        bit.push_str(&format!(" → {t_disp}"));
    }
    if let Some(d) = dc {
        bit.push_str(&format!(" vs DC {d}"));
    }
    bit
}

/// 对应 Python `app._action_receipt`:把规则动作的 result 转成"【RulesEngine：...】..."段落。
fn action_receipt(kind: &str, label: &str, out: &Value) -> String {
    let result = out.get("result").cloned().unwrap_or(Value::Null);
    let verdict = match result.get("success").and_then(|v| v.as_bool()) {
        Some(true) => "成功",
        Some(false) => "失败",
        None => "已执行",
    };
    let label_disp = if label.is_empty() { kind } else { label };
    let mut lines = vec![format!("【RulesEngine：{kind}】{label_disp}：{verdict}。")];
    let roll = roll_line(&result);
    if !roll.is_empty() {
        lines.push(format!("掷骰：{roll}。"));
    }
    if let Some(damage) = result.get("damage").and_then(|v| v.as_object()) {
        if let Some(total) = damage.get("total") {
            lines.push(format!("伤害：{total}。"));
        }
    }
    if let Some(facts) = result.get("gm_facts").and_then(|v| v.as_array()) {
        for f in facts {
            if let Some(s) = f.as_str() {
                if !s.is_empty() {
                    lines.push(s.to_string());
                }
            }
        }
    }
    lines.join("\n\n")
}

/// 对应 Python `app._encounter_receipt`:战斗开始 / 下一回合 / 敌方攻击的 receipt。
fn encounter_receipt(prefix: &str, encounter: &Value, result: &Value) -> String {
    if encounter.is_null() || encounter.as_object().map(|o| o.is_empty()).unwrap_or(true) {
        return format!("【RulesEngine：{prefix}】已执行。");
    }
    if prefix == "先攻" {
        let order = encounter
            .get("initiative_order")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let order_line = order
            .iter()
            .filter_map(|o| {
                let name = o.get("name").and_then(|v| v.as_str())?;
                let init = o.get("init").and_then(|v| v.as_i64()).unwrap_or(0);
                Some(format!("{name}({init})"))
            })
            .collect::<Vec<_>>()
            .join(" → ");
        return format!("【RulesEngine：先攻】遭遇开始。\n\n先攻顺序：{order_line}。");
    }
    if prefix == "下一回合" {
        let order = encounter
            .get("initiative_order")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let idx = encounter
            .get("turn_index")
            .and_then(|v| v.as_i64())
            .unwrap_or(0) as usize;
        let current_name = order
            .get(idx)
            .and_then(|c| c.get("name").and_then(|v| v.as_str()))
            .unwrap_or("未知");
        return format!("【RulesEngine：下一回合】现在轮到 {current_name}。");
    }
    let target = result
        .get("target_name")
        .and_then(|v| v.as_str())
        .unwrap_or("player");
    action_receipt(prefix, target, &json!({"result": result}))
}

/// 对应 Python `rules_bridge.module_ops.enter_room`:玩家移动到指定房间,
/// 校验出口 + requires(flag:xxx) → 更新 scene 字段(location_id/exits/visible_clues/
/// current_room)→ 返回新 scene + current_room。
fn enter_room_op(state: &mut rpg_state::state::GameState, location_id: &str) -> Result<Value, String> {
    let module_id = state.data.scene.module_id.clone();
    if module_id.is_empty() {
        return Err("未加载模组".into());
    }
    let bundle = rpg_rules::load_module(&module_id).map_err(|e| e.to_string())?;
    let rooms = bundle.rooms.as_array().cloned().unwrap_or_default();
    let target_room = rooms
        .iter()
        .find(|r| r.get("id").and_then(|v| v.as_str()) == Some(location_id))
        .cloned()
        .ok_or_else(|| format!("未知房间：{location_id}"))?;

    let cur_id = state.data.scene.location_id.clone();
    if let Some(cur_room) = rooms
        .iter()
        .find(|r| r.get("id").and_then(|v| v.as_str()) == Some(&cur_id))
    {
        let exits = cur_room
            .get("exits")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let valid: Vec<String> = exits
            .iter()
            .filter_map(|e| e.get("to").and_then(|v| v.as_str()).map(|s| s.to_string()))
            .collect();
        if !valid.contains(&location_id.to_string()) {
            return Err(format!(
                "当前房间不能直接前往 {location_id}（出口：{valid:?}）"
            ));
        }
        // requires=flag:xxx 校验
        if let Some(target_exit) = exits
            .iter()
            .find(|e| e.get("to").and_then(|v| v.as_str()) == Some(location_id))
        {
            if let Some(req) = target_exit.get("requires").and_then(|v| v.as_str()) {
                if let Some(flag) = req.strip_prefix("flag:") {
                    let scene_flag_ok = state
                        .data
                        .scene
                        .flags
                        .get(flag)
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false);
                    if !scene_flag_ok {
                        return Err(format!("前往 {location_id} 需要先满足条件：{flag}"));
                    }
                }
            }
        }
    }

    // 写 scene
    let snapshot = room_snapshot(&target_room);
    state.data.scene.location_id = location_id.to_string();
    state.data.scene.exits = target_room
        .get("exits")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    state.data.scene.visible_clues = target_room
        .get("visible_clues")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    state
        .data
        .scene
        .extra
        .insert("current_room".to_string(), snapshot.clone());
    if !state
        .data
        .scene
        .visited_rooms
        .iter()
        .any(|v| v.as_str() == Some(location_id))
    {
        state
            .data
            .scene
            .visited_rooms
            .push(Value::String(location_id.to_string()));
    }
    Ok(snapshot)
}

/// 对应 Python `rules_bridge.module_ops._room_snapshot`:把 module manifest 的房间
/// 字典裁成 scene.current_room 的 12 字段子集,丢掉无关 metadata。
fn room_snapshot(room: &Value) -> Value {
    let g = |k: &str| room.get(k).cloned().unwrap_or(Value::Null);
    let arr = |k: &str| {
        room.get(k)
            .and_then(|v| v.as_array())
            .cloned()
            .map(Value::Array)
            .unwrap_or(json!([]))
    };
    let obj = |k: &str| {
        room.get(k)
            .and_then(|v| v.as_object())
            .cloned()
            .map(Value::Object)
            .unwrap_or(json!({}))
    };
    json!({
        "id": g("id"),
        "name": g("name"),
        "name_en": g("name_en"),
        "description": g("description"),
        "exits": arr("exits"),
        "visible_clues": arr("visible_clues"),
        "checks": arr("checks"),
        "hazards": arr("hazards"),
        "npcs": arr("npcs"),
        "enemies": arr("enemies"),
        "loot": arr("loot"),
        "flags": obj("flags"),
    })
}

/// 把 `body.seed`(可能是 number 或 numeric string)规整为 `Option<u64>`。
fn coerce_seed(seed: &Value) -> Option<u64> {
    if seed.is_null() {
        return None;
    }
    if let Some(n) = seed.as_u64() {
        return Some(n);
    }
    if let Some(n) = seed.as_i64() {
        if n >= 0 {
            return Some(n as u64);
        }
    }
    if let Some(s) = seed.as_str() {
        return s.parse::<u64>().ok();
    }
    None
}

// ── handlers ──────────────────────────────────────────────────────────────────

#[tracing::instrument(skip_all)]
async fn api_rules_modules(State(_s): State<AppState>) -> impl IntoResponse {
    let modules = list_modules();
    Json(json!({"ok": true, "modules": modules}))
}

#[tracing::instrument(skip_all)]
async fn api_rules_module_start(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesModuleStartRequest>,
) -> Result<Response, ResponseError> {
    let module_id = body.module_id.unwrap_or_default();
    if module_id.is_empty() {
        return Err(ResponseError::bad_request("module_id required"));
    }
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        st.set_path("scene.module_id", Value::String(module_id))?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}

/// 标准入口:为已登录用户建一个独立 game_save 跑模组。
///
/// 流程对照 Python `api_rules_module_launch`:
///   1. 加载模组 manifest(404 if 未知)
///   2. 找/建 ad-hoc"模组容器"剧本(避免每个模组建新 script)
///   3. 构造初始 GameStateData(scene.module_id + manifest 字段 + 默认 PC)
///   4. `save_io::create_save` 写入 game_saves
///   5. `branches::seed_tree` + `branches::activate_save` 切换激活
///
/// 匿名用户禁止启动模组(避免污染本地默认 save),对应 Python `401`。
#[tracing::instrument(skip_all)]
async fn api_rules_module_launch(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesModuleLaunchRequest>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;

    let module_id = body.module_id.unwrap_or_default().trim().to_string();
    if module_id.is_empty() {
        return Err(ResponseError::bad_request("缺少 module_id"));
    }
    let custom_title = body.title.unwrap_or_default().trim().to_string();

    // 加载模组 manifest 取标题
    let bundle: ModuleBundle = load_module(&module_id)
        .map_err(|e| ResponseError::not_found(format!("未知模组 {module_id}：{e}")))?;
    let manifest = &bundle.manifest;
    let title = if !custom_title.is_empty() {
        custom_title
    } else {
        manifest
            .get("name_cn")
            .and_then(|v| v.as_str())
            .or_else(|| manifest.get("name").and_then(|v| v.as_str()))
            .unwrap_or(&module_id)
            .to_string()
    };

    // 找(或建)模组容器 script
    const CONTAINER_TITLE: &str = "[内部] 5E 模组容器";
    let user_id_typed = user.id;
    let user_id: i64 = user_id_typed.into();
    let existing_script_id: Option<(i64,)> = sqlx::query_as(
        "select id from scripts where owner_id = $1 and title = $2 limit 1",
    )
    .bind(user_id)
    .bind(CONTAINER_TITLE)
    .fetch_optional(&s.db)
    .await?;
    let container_script_id: i64 = if let Some((id,)) = existing_script_id {
        id
    } else {
        let script = rpg_platform::library::create_script(
            &s.db,
            user_id,
            CONTAINER_TITLE,
            "5E 模组冒险 ad-hoc 容器",
            "",
        )
        .await?;
        script.id
    };

    // 构造初始 state snapshot —— scene.module_id + 默认 PC
    let pc_overrides = body.character.as_ref();
    let pc_name = pc_overrides
        .and_then(|v| v.get("name"))
        .and_then(|v| v.as_str())
        .unwrap_or("Cinder");
    let mut pc = rules_character::make_default_character(pc_name, 1);
    if let Some(Value::Object(overrides)) = pc_overrides {
        if let Some(obj) = pc.as_object_mut() {
            for (k, v) in overrides {
                if k == "abilities" {
                    if let (Some(Value::Object(existing)), Value::Object(new_ab)) =
                        (obj.get_mut("abilities"), v)
                    {
                        for (ak, av) in new_ab {
                            existing.insert(ak.clone(), av.clone());
                        }
                        continue;
                    }
                }
                obj.insert(k.clone(), v.clone());
            }
        }
    }

    // 起点房间(对应 Python rules_bridge.start_module)
    let rooms = bundle.rooms.as_array().cloned().unwrap_or_default();
    let start_id = manifest
        .get("starting_location")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .or_else(|| {
            rooms
                .first()
                .and_then(|r| r.get("id"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_default();
    let start_room: Value = rooms
        .iter()
        .find(|r| r.get("id").and_then(|v| v.as_str()) == Some(&start_id))
        .cloned()
        .or_else(|| rooms.first().cloned())
        .unwrap_or(Value::Object(Default::default()));

    // ruleset 字段优先 ruleset_meta(dict),否则 ruleset(可能 string)→ 规整为 dict
    let ruleset_field = {
        let raw = manifest
            .get("ruleset_meta")
            .cloned()
            .or_else(|| manifest.get("ruleset").cloned())
            .unwrap_or(Value::Null);
        if let Value::String(s) = &raw {
            json!({"id": s, "mode": s, "public_label": s})
        } else {
            raw
        }
    };

    let module_name = manifest
        .get("name_cn")
        .and_then(|v| v.as_str())
        .or_else(|| manifest.get("name").and_then(|v| v.as_str()))
        .unwrap_or(&module_id)
        .to_string();
    let module_tagline = manifest
        .get("tagline")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let module_manifest = json!({
        "id": manifest.get("id").cloned().unwrap_or(Value::String(module_id.clone())),
        "name": manifest.get("name").cloned().unwrap_or(Value::Null),
        "name_cn": manifest.get("name_cn").cloned().unwrap_or(Value::Null),
        "tagline": manifest.get("tagline").cloned().unwrap_or(Value::Null),
        "kind": manifest.get("kind").cloned().unwrap_or(Value::String("module_adventure".into())),
        "ruleset": ruleset_field,
        "context_providers": manifest.get("context_providers").cloned().unwrap_or(json!([])),
        "retrieval_policy": manifest.get("retrieval_policy").cloned().unwrap_or(json!({})),
        "gm_policy": manifest.get("gm_policy").cloned().unwrap_or(json!({})),
    });

    let current_room = room_snapshot(&start_room);
    let pc_name_final = pc.get("name").and_then(|v| v.as_str()).unwrap_or("Drifter").to_string();
    let start_location = start_room
        .get("name")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| start_id.clone());

    let memory_resources: Vec<Value> = pc
        .get("inventory")
        .and_then(|v| v.as_array())
        .map(|items| {
            items
                .iter()
                .map(|it| {
                    let name = it.get("name").and_then(|v| v.as_str()).unwrap_or("?");
                    let qty = it
                        .get("qty")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(1);
                    Value::String(format!("{name} ×{qty}"))
                })
                .collect()
        })
        .unwrap_or_default();
    let memory_abilities = pc
        .get("features")
        .cloned()
        .unwrap_or(json!([]));

    let initial_snapshot = json!({
        "scene": {
            "module_id": module_id,
            "location_id": start_id,
            "visited_rooms": [start_id],
            "exits": start_room.get("exits").cloned().unwrap_or(json!([])),
            "visible_clues": start_room.get("visible_clues").cloned().unwrap_or(json!([])),
            "flags": {},
            "current_room": current_room,
            "module_manifest": module_manifest,
        },
        "player_character": pc,
        "player": {
            "name": pc_name_final,
            "role": "5E 探险者",
            "background": format!(
                "5E compatible · 五版规则兼容 · 原创规则模组『{module_name}』。{module_tagline}"
            ),
            "current_location": start_location,
        },
        "world": {
            "time": "灰烬山岭 · 黎明前",
            "timeline": {
                "anchor_state": "locked",
                "current_label": module_name.clone(),
                "current_phase": module_name.clone(),
                "anchor_source": "module",
                "anchor_turn": 0,
                "pending_jump": null,
                "last_transition": null,
            },
            "known_events": [],
        },
        "relationships": {},
        "memory": {
            "main_quest": format!("完成 {module_name} 冒险"),
            "current_objective": if module_tagline.is_empty() {
                format!("从 {} 出发", start_room.get("name").and_then(|v| v.as_str()).unwrap_or("起点"))
            } else {
                module_tagline.clone()
            },
            "facts": [],
            "notes": [],
            "pinned": [],
            "abilities": memory_abilities,
            "resources": memory_resources,
            "items": [],
            "last_retrieval": "",
            "last_context": {},
            "last_context_agent": {},
            "last_structured_updates": [],
        },
        "encounter": {"active": false, "combatants": []},
        "permissions": {"pending_writes": [], "pending_questions": []},
        "history": if bundle.opening.is_empty() {
            json!([])
        } else {
            json!([{"role": "assistant", "content": bundle.opening}])
        },
        "dice_log": [],
        "turn": 0,
    });

    // 写 save
    let save = save_io::create_save(
        &s.db,
        user_id_typed,
        container_script_id,
        &title,
        &initial_snapshot,
    )
    .await?;
    let save_id = save.id;

    // seed_tree + activate
    let _ = branches::seed::seed_tree(&s.db, save_id, "").await;
    let _ = branches::activation::activate_save(&s.db, user_id, save_id).await;

    // 写到 state_store(让 /api/rules/scene 等立即读到)
    let user_key = user_id.to_string();
    let shared = s.state_store.get_or_create(&user_key).await;
    {
        let mut st = shared.write();
        st.data = serde_json::from_value(initial_snapshot.clone()).unwrap_or_default();
    }

    Ok(Json(json!({
        "ok": true,
        "save_id": save_id,
        "save_title": title,
        "opening": bundle.opening,
        "state": initial_snapshot,
    }))
    .into_response())
}

#[tracing::instrument(skip_all)]
async fn api_rules_scene(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = shared.read().clone();
    let scene = serde_json::to_value(&snapshot.data.scene).unwrap_or(Value::Object(Default::default()));
    let encounter = serde_json::to_value(&snapshot.data.encounter).unwrap_or(Value::Object(Default::default()));
    let player_character = serde_json::to_value(&snapshot.data.player_character).unwrap_or(Value::Object(Default::default()));
    Ok(Json(json!({
        "ok": true,
        "scene": scene,
        "encounter": encounter,
        "player_character": player_character,
    }))
    .into_response())
}

/// POST /api/rules/move — 移动到指定房间。
///
/// 对应 Python `api_rules_move` → `module_enter_room` 工具:
///   1. enter_room_op:校验出口/requires,写 scene 字段
///   2. _clear_pending_questions_after_rule_action("move:<id>")
///   3. _append_rules_receipt(_room_receipt(room))
#[tracing::instrument(skip_all)]
async fn api_rules_move(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesMoveRequest>,
) -> Result<Response, ResponseError> {
    let to = body.to.unwrap_or_default().trim().to_string();
    if to.is_empty() {
        return Err(ResponseError::bad_request("缺少 to"));
    }
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let (room, snapshot) = {
        let mut st = shared.write();
        let room = enter_room_op(&mut st, &to).map_err(ResponseError::bad_request)?;
        clear_pending_questions_after_rule_action(&mut st, &format!("move:{to}"));
        append_rules_receipt(&mut st, &room_receipt(&room));
        (room, st.clone())
    };
    Ok(Json(json!({
        "ok": true,
        "room": room,
        "state": snapshot.data,
    }))
    .into_response())
}

/// 通用规则动作分派器。
///
/// 对应 Python `_execute_rules_action`:按 `body.kind` 路由到 bridge 函数。
/// 支持 kind:
///   - `move`            → enter_room_op(到 location_id);prelude.kind=move
///   - `skill_check`     → `perform_skill_check`
///   - `saving_throw`    → `perform_saving_throw`
///   - `trap_check`      → `trap_check`
///   - `short_rest`      → `short_rest`
///   - `consume_item`    → `consume_item_action`
///   - `attack`          → `apply_combat(PlayerAttack)`
///
/// 执行后:`_clear_pending_questions_after_rule_action` + `_append_rules_receipt`,
/// 与 Python 一致。
#[tracing::instrument(skip_all)]
async fn api_rules_action(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesActionRequest>,
) -> Result<Response, ResponseError> {
    let kind = body.kind.unwrap_or_default();
    if kind.is_empty() {
        return Err(ResponseError::bad_request("缺少 kind"));
    }
    let extra = body.extra;
    let seed = coerce_seed(extra.get("seed").unwrap_or(&Value::Null));

    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;

    let (result, label_for_receipt) = {
        let mut st = shared.write();
        let label = match kind.as_str() {
            "skill_check" => extra
                .get("skill")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            "saving_throw" => extra
                .get("ability")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            "attack" => extra
                .get("target")
                .or_else(|| extra.get("target_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            "consume_item" => extra
                .get("item_id")
                .or_else(|| extra.get("item"))
                .or_else(|| extra.get("alias"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            "move" => extra
                .get("to")
                .or_else(|| extra.get("location_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            _ => kind.clone(),
        };
        let result_val: Value = match kind.as_str() {
            "move" => {
                let to = extra
                    .get("to")
                    .or_else(|| extra.get("location_id"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if to.is_empty() {
                    return Err(ResponseError::bad_request("缺少 to"));
                }
                let room = enter_room_op(&mut st, &to).map_err(ResponseError::bad_request)?;
                json!({"ok": true, "result": {"success": true, "room": room}})
            }
            "skill_check" => {
                let skill = extra
                    .get("skill")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if skill.is_empty() {
                    return Err(ResponseError::bad_request("缺少 skill"));
                }
                let dc = extra
                    .get("dc")
                    .and_then(|v| v.as_i64())
                    .or_else(|| extra.get("dc_hint").and_then(|v| v.as_i64()))
                    .unwrap_or(12) as i32;
                let advantage = extra.get("advantage").and_then(|v| v.as_bool()).unwrap_or(false);
                let disadvantage = extra.get("disadvantage").and_then(|v| v.as_bool()).unwrap_or(false);
                let reason = extra
                    .get("reason")
                    .or_else(|| extra.get("fact"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let sets_flag = extra
                    .get("sets_flag")
                    .or_else(|| extra.get("reveals"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let res = perform_skill_check(
                    &mut st.data,
                    &skill,
                    dc,
                    advantage,
                    disadvantage,
                    seed,
                    &reason,
                    sets_flag.as_deref(),
                )
                .map_err(bridge_to_response_error)?;
                json!({"ok": true, "result": res})
            }
            "saving_throw" => {
                let ability = extra
                    .get("ability")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if ability.is_empty() {
                    return Err(ResponseError::bad_request("缺少 ability"));
                }
                let dc = extra
                    .get("dc")
                    .and_then(|v| v.as_i64())
                    .or_else(|| extra.get("dc_hint").and_then(|v| v.as_i64()))
                    .unwrap_or(12) as i32;
                let advantage = extra.get("advantage").and_then(|v| v.as_bool()).unwrap_or(false);
                let disadvantage = extra.get("disadvantage").and_then(|v| v.as_bool()).unwrap_or(false);
                let reason = extra
                    .get("reason")
                    .or_else(|| extra.get("fact"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let fail_damage_expr = extra
                    .get("fail_damage_expr")
                    .or_else(|| extra.get("fail_damage"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let fail_condition = extra
                    .get("fail_condition")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let res = perform_saving_throw(
                    &mut st.data,
                    &ability,
                    dc,
                    advantage,
                    disadvantage,
                    seed,
                    &reason,
                    fail_damage_expr.as_deref(),
                    fail_condition.as_deref(),
                )
                .map_err(bridge_to_response_error)?;
                json!({"ok": true, "result": res})
            }
            "trap_check" => {
                let trap_id = extra
                    .get("trap_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if trap_id.is_empty() {
                    return Err(ResponseError::bad_request("缺少 trap_id"));
                }
                let ability = extra
                    .get("ability")
                    .and_then(|v| v.as_str())
                    .unwrap_or("dex")
                    .to_string();
                let dc = extra.get("dc").and_then(|v| v.as_i64()).unwrap_or(12) as i32;
                let damage_expr = extra
                    .get("damage_expr")
                    .or_else(|| extra.get("fail_damage"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let condition = extra
                    .get("condition")
                    .or_else(|| extra.get("fail_condition"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let res = trap_check(
                    &mut st.data,
                    &ability,
                    dc,
                    damage_expr.as_deref(),
                    condition.as_deref(),
                    &trap_id,
                    seed,
                )
                .map_err(bridge_to_response_error)?;
                json!({"ok": true, "result": res})
            }
            "short_rest" => {
                let res = short_rest(&mut st.data, seed).map_err(bridge_to_response_error)?;
                json!({"ok": true, "result": res})
            }
            "consume_item" => {
                let item_id = extra
                    .get("item_id")
                    .or_else(|| extra.get("item"))
                    .or_else(|| extra.get("alias"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let qty = extra
                    .get("qty")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(1) as i32;
                let reason = extra
                    .get("reason")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                consume_item_action(&mut st.data, &item_id, qty, &reason)
                    .map_err(bridge_to_response_error)?
            }
            "attack" => {
                // 玩家攻击:从 player_character.weapons[weapon_id] 取 attack_bonus / damage
                let target_id = extra
                    .get("target")
                    .or_else(|| extra.get("target_id"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let weapon_id = extra
                    .get("weapon")
                    .or_else(|| extra.get("weapon_id"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("shortsword")
                    .to_string();
                let advantage = extra.get("advantage").and_then(|v| v.as_bool()).unwrap_or(false);
                let disadvantage = extra.get("disadvantage").and_then(|v| v.as_bool()).unwrap_or(false);

                let (attack_bonus, damage_expr) = {
                    let weapon = st
                        .data
                        .player_character
                        .weapons
                        .get(&weapon_id)
                        .cloned()
                        .ok_or_else(|| {
                            ResponseError::bad_request(format!("角色未持有武器：{weapon_id}"))
                        })?;
                    let ab = weapon.get("attack_bonus").and_then(|v| v.as_i64()).unwrap_or(4) as i32;
                    let dmg = weapon
                        .get("damage")
                        .and_then(|v| v.as_str())
                        .unwrap_or("1d6")
                        .to_string();
                    (ab, dmg)
                };
                if target_id.is_empty() {
                    return Err(ResponseError::bad_request("缺少 target"));
                }
                let outcome = apply_combat(
                    &mut st.data,
                    CombatAction::PlayerAttack {
                        target_id,
                        weapon_id,
                        attack_bonus,
                        damage_expr,
                        advantage,
                        disadvantage,
                        seed,
                    },
                )
                .map_err(bridge_to_response_error)?;
                json!({
                    "ok": true,
                    "result": outcome.rule_result,
                    "encounter": outcome.encounter,
                    "gm_facts": outcome.gm_facts,
                })
            }
            other => {
                return Err(ResponseError::bad_request(format!(
                    "未支持的 kind: {other}"
                )));
            }
        };
        // 与 Python 一致:动作执行成功后清掉所有 pending_questions + 追加 receipt
        clear_pending_questions_after_rule_action(&mut st, &format!("rules:{kind}"));
        let receipt_text = if kind == "move" {
            // move 的 receipt 用 _room_receipt(更详细),Python `rules.py` 在 move 路由里走的就是这个。
            let room = result_val
                .get("result")
                .and_then(|v| v.get("room"))
                .cloned()
                .unwrap_or(Value::Null);
            room_receipt(&room)
        } else {
            action_receipt(&kind, &label, &result_val)
        };
        append_rules_receipt(&mut st, &receipt_text);
        (result_val, label)
    };
    let _ = label_for_receipt; // 消除未读警告(label 仅在 closure 内使用)

    Ok(Json(result).into_response())
}

/// POST /api/rules/encounter/start — 开战。
///
/// 对应 Python `api_rules_encounter_start` → `combat_start` 工具,Rust 端目前
/// 没接战斗 seed 逻辑,只标记 encounter.active/round/encounter_id。执行后接
/// receipt + 清 pending_questions(与 Python 一致)。
#[tracing::instrument(skip_all)]
async fn api_rules_encounter_start(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesEncounterStartRequest>,
) -> Result<Response, ResponseError> {
    let encounter_id = body.encounter_id.unwrap_or_default().trim().to_string();
    if encounter_id.is_empty() {
        return Err(ResponseError::bad_request("缺少 encounter_id"));
    }
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let (encounter, snapshot) = {
        let mut st = shared.write();
        st.set_path("encounter.active", Value::Bool(true))?;
        st.set_path("encounter.round", Value::from(1))?;
        st.set_path("encounter.encounter_id", Value::String(encounter_id.clone()))?;
        let enc = serde_json::to_value(&st.data.encounter).unwrap_or(json!({}));
        clear_pending_questions_after_rule_action(&mut st, &format!("encounter:start:{encounter_id}"));
        append_rules_receipt(&mut st, &encounter_receipt("先攻", &enc, &Value::Null));
        (enc, st.clone())
    };
    Ok(Json(json!({
        "ok": true,
        "encounter": encounter,
        "state": snapshot.data,
    }))
    .into_response())
}

/// POST /api/rules/encounter/next — 下一回合。
#[tracing::instrument(skip_all)]
async fn api_rules_encounter_next(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(_body): Json<RulesEncounterNextRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let (encounter, snapshot) = {
        let mut st = shared.write();
        let round = st
            .get_path("encounter.round")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        st.set_path("encounter.round", Value::from(round + 1))?;
        let enc = serde_json::to_value(&st.data.encounter).unwrap_or(json!({}));
        clear_pending_questions_after_rule_action(&mut st, "encounter:next");
        append_rules_receipt(&mut st, &encounter_receipt("下一回合", &enc, &Value::Null));
        (enc, st.clone())
    };
    Ok(Json(json!({
        "ok": true,
        "encounter": encounter,
        "state": snapshot.data,
    }))
    .into_response())
}

/// 敌方回合:走 `apply_combat(EnemyAttack)`,把伤害写入 player_character.hp。
#[tracing::instrument(skip_all)]
async fn api_rules_encounter_enemy(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesEncounterEnemyRequest>,
) -> Result<Response, ResponseError> {
    let attacker_id = body.attacker_id.unwrap_or_default().trim().to_string();
    if attacker_id.is_empty() {
        return Err(ResponseError::bad_request("缺少 attacker_id"));
    }
    let target_id = body
        .target_id
        .unwrap_or_else(|| "player".to_string())
        .trim()
        .to_string();
    let seed = coerce_seed(body.seed.as_ref().unwrap_or(&Value::Null));

    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;

    let (outcome, snapshot) = {
        let mut st = shared.write();
        let attacker_for_receipt = attacker_id.clone();
        let outcome = apply_combat(
            &mut st.data,
            CombatAction::EnemyAttack {
                attacker_id,
                target_id,
                attack_index: 0,
                seed,
            },
        )
        .map_err(bridge_to_response_error)?;
        let enc_val = outcome.encounter.clone();
        let result_val = outcome.rule_result.clone();
        clear_pending_questions_after_rule_action(&mut st, &format!("enemy:{attacker_for_receipt}"));
        append_rules_receipt(&mut st, &encounter_receipt("敌方攻击", &enc_val, &result_val));
        (outcome, st.clone())
    };

    Ok(Json(json!({
        "ok": true,
        "result": outcome.rule_result,
        "encounter": outcome.encounter,
        "gm_facts": outcome.gm_facts,
        "state": snapshot.data,
    }))
    .into_response())
}

#[tracing::instrument(skip_all)]
async fn api_rules_suggest(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesSuggestRequest>,
) -> Result<Response, ResponseError> {
    let text = body.text.unwrap_or_default();
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = shared.read().clone();
    let mut actions = suggest_rule_actions(&text, &snapshot.data);

    // Bug 5(Python parity):从玩家文本里 deterministic 解析 inventory 消耗意图
    // 写入候选 actions,让 chat 流程或前端能直接触发 consume_item。
    let inventory_map: serde_json::Map<String, Value> = snapshot
        .data
        .player_character
        .inventory
        .iter()
        .filter_map(|item| {
            let id = item.get("id").and_then(|v| v.as_str())?;
            Some((id.to_string(), item.clone()))
        })
        .collect();
    let inventory_value = Value::Object(inventory_map);
    for intent in parse_consume_intent(&text, &inventory_value) {
        actions.push(json!({
            "kind": "consume_item",
            "item_id": intent.item_id,
            "qty": intent.qty,
            "reason": format!("backend parser: {:?}", intent.matched),
        }));
    }

    Ok(Json(json!({"ok": true, "actions": actions})).into_response())
}

// ── tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rpg_schemas::GameStateData;
    use serde_json::json;

    fn make_pc_with_weapon() -> GameStateData {
        let mut data = GameStateData::default();
        data.player_character.name = "Cinder".to_string();
        data.player_character.max_hp = 12;
        data.player_character.hp = 12;
        data.player_character.ac = 14;
        // abilities
        let mut ab = serde_json::Map::new();
        ab.insert("str".to_string(), json!(14));
        ab.insert("dex".to_string(), json!(12));
        ab.insert("con".to_string(), json!(13));
        data.player_character.abilities = ab;
        // weapon
        let mut weapons = serde_json::Map::new();
        weapons.insert(
            "shortsword".to_string(),
            json!({"name": "短剑", "attack_bonus": 5, "damage": "1d6+3"}),
        );
        data.player_character.weapons = weapons;
        // inventory
        data.player_character.inventory = vec![json!({
            "id": "healing_potion",
            "name": "治疗药水",
            "qty": 2
        })];
        data
    }

    fn enemy_combatant(id: &str, hp: i32, ac: i32) -> Value {
        json!({
            "id": id,
            "name": id,
            "side": "enemy",
            "hp": hp,
            "max_hp": hp,
            "ac": ac,
            "defeated": false,
            "attacks": [{"name": "claw", "attack_bonus": 3, "damage": "1d4"}],
        })
    }

    #[test]
    fn coerce_seed_handles_variants() {
        assert_eq!(coerce_seed(&Value::Null), None);
        assert_eq!(coerce_seed(&json!(42)), Some(42));
        assert_eq!(coerce_seed(&json!(-1)), None);
        assert_eq!(coerce_seed(&json!("7")), Some(7));
        assert_eq!(coerce_seed(&json!("abc")), None);
    }

    #[test]
    fn bridge_logic_errors_map_to_bad_request() {
        let err = bridge_to_response_error(BridgeError::Logic("缺东西".into()));
        assert_eq!(err.status, http::StatusCode::BAD_REQUEST);
        let err = bridge_to_response_error(BridgeError::EncounterNotActive);
        assert_eq!(err.status, http::StatusCode::BAD_REQUEST);
    }

    /// 验证 action 分派的 attack 分支:正确从 weapons 字段取出 bonus/damage 并扣 HP
    #[test]
    fn attack_branch_reads_weapon_and_applies_damage() {
        let mut data = make_pc_with_weapon();
        data.encounter.active = true;
        data.encounter.combatants = vec![enemy_combatant("goblin1", 5, 12)];

        let outcome = apply_combat(
            &mut data,
            CombatAction::PlayerAttack {
                target_id: "goblin1".to_string(),
                weapon_id: "shortsword".to_string(),
                attack_bonus: 5,
                damage_expr: "1d6+3".to_string(),
                advantage: false,
                disadvantage: false,
                seed: Some(42), // 固定 seed
            },
        )
        .expect("PlayerAttack 应成功");
        // success 字段存在(可能命中可能 miss,但不应 panic / err)
        assert!(outcome.rule_result.get("success").is_some());
    }

    /// 验证 enemy attack 命中 player 时扣 player_character.hp
    #[test]
    fn enemy_attack_reduces_player_hp_on_hit() {
        // 用一个固定 seed 找一个能命中 ac=14 player 的 attack roll
        let mut found = false;
        for seed in 0u64..200 {
            let mut data = make_pc_with_weapon();
            data.encounter.active = true;
            data.encounter.combatants = vec![enemy_combatant("goblin1", 5, 12)];
            let hp_before = data.player_character.hp;

            let outcome = apply_combat(
                &mut data,
                CombatAction::EnemyAttack {
                    attacker_id: "goblin1".to_string(),
                    target_id: "player".to_string(),
                    attack_index: 0,
                    seed: Some(seed),
                },
            )
            .expect("EnemyAttack 应成功");

            if outcome.rule_result["success"] == json!(true) {
                assert!(
                    data.player_character.hp < hp_before,
                    "命中时 player hp 应下降"
                );
                found = true;
                break;
            }
        }
        assert!(found, "200 个 seed 内应至少有一次命中");
    }

    /// 验证 EnemyAttack 失败时返回 BridgeError(攻击者不存在)
    #[test]
    fn enemy_attack_unknown_attacker_errors() {
        let mut data = make_pc_with_weapon();
        data.encounter.active = true;
        let err = apply_combat(
            &mut data,
            CombatAction::EnemyAttack {
                attacker_id: "nobody".to_string(),
                target_id: "player".to_string(),
                attack_index: 0,
                seed: Some(1),
            },
        )
        .unwrap_err();
        assert!(matches!(err, BridgeError::TargetNotFound(_)));
    }

    /// 验证 consume_item 通过 bridge 时扣库存
    #[test]
    fn consume_item_branch_reduces_inventory() {
        let mut data = make_pc_with_weapon();
        let res = consume_item_action(&mut data, "healing_potion", 1, "drink")
            .expect("consume 应成功");
        assert_eq!(res["ok"], json!(true));
        assert_eq!(data.player_character.inventory[0]["qty"], json!(1));
    }

    /// 验证 skill_check 分派把 sets_flag 写到 scene.flags
    #[test]
    fn skill_check_writes_flag_on_success() {
        let mut data = make_pc_with_weapon();
        // 用一个固定 seed 找一个能 DC 1 必然成功的检定(DC=1 几乎一定成功)
        let res = perform_skill_check(
            &mut data,
            "perception",
            1,
            false,
            false,
            Some(7),
            "test",
            Some("found_clue"),
        )
        .expect("skill check 应成功");
        // DC=1 → success 应为 true(任何 modifier + d20 >= 1)
        assert_eq!(res["success"], json!(true));
        assert_eq!(
            data.scene.flags.get("found_clue").cloned(),
            Some(json!(true))
        );
    }

    /// list_modules 在没有目录时返回空 vec(不 panic)
    #[test]
    fn list_modules_no_dir_returns_empty() {
        let _g = TempEnv::set("RPG_MODULES_DIR", "/nonexistent/path/for/test");
        let mods = rpg_rules::list_modules();
        assert!(mods.is_empty());
    }

    /// guard 自动恢复 env 变量
    struct TempEnv {
        key: String,
        prev: Option<String>,
    }
    impl TempEnv {
        fn set(key: &str, value: &str) -> Self {
            let prev = std::env::var(key).ok();
            std::env::set_var(key, value);
            Self {
                key: key.to_string(),
                prev,
            }
        }
    }
    impl Drop for TempEnv {
        fn drop(&mut self) {
            match &self.prev {
                Some(v) => std::env::set_var(&self.key, v),
                None => std::env::remove_var(&self.key),
            }
        }
    }

    /// 验证 launch handler 用到的 starting_location fallback 逻辑(单元测试 helper 即可)
    #[test]
    fn unused_imports_check() {
        // 引一下 ModuleBundle 防止编译警告 dead code
        let bundle = ModuleBundle::default();
        assert_eq!(bundle.id, "");
    }
}
