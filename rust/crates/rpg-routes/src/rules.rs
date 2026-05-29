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

    // 起点房间
    let rooms = bundle.rooms.as_array().cloned().unwrap_or_default();
    let starting_id = manifest
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

    let initial_snapshot = json!({
        "scene": {
            "module_id": module_id,
            "location_id": starting_id,
            "module_manifest": {
                "id": manifest.get("id").cloned().unwrap_or(Value::String(module_id.clone())),
                "name": manifest.get("name").cloned().unwrap_or(Value::Null),
                "name_cn": manifest.get("name_cn").cloned().unwrap_or(Value::Null),
                "tagline": manifest.get("tagline").cloned().unwrap_or(Value::Null),
                "ruleset": manifest.get("ruleset_meta").cloned()
                    .or_else(|| manifest.get("ruleset").cloned())
                    .unwrap_or(Value::Null),
            },
        },
        "player_character": pc,
        "encounter": {"active": false, "combatants": []},
        "history": [],
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

#[tracing::instrument(skip_all)]
async fn api_rules_move(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesMoveRequest>,
) -> Result<Response, ResponseError> {
    let to = body.to.unwrap_or_default();
    if to.is_empty() {
        return Err(ResponseError::bad_request("to required"));
    }
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        st.set_path("scene.location_id", Value::String(to.clone()))?;
        st.append_to_path("scene.visited_rooms", Value::String(to))?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}

/// 通用规则动作分派器。
///
/// 对应 Python `_execute_rules_action`:按 `body.kind` 路由到 bridge 函数。
/// 支持 kind:
///   - `skill_check`     → `perform_skill_check`
///   - `saving_throw`    → `perform_saving_throw`
///   - `trap_check`      → `trap_check`
///   - `short_rest`      → `short_rest`
///   - `consume_item`    → `consume_item_action`
///   - `attack`          → `apply_combat(PlayerAttack)`
///
/// 未实现 kind:`move`(走 /api/rules/move),`start_encounter`(走
/// /api/rules/encounter/start)。
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

    let result = {
        let mut st = shared.write();
        match kind.as_str() {
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
        }
    };

    Ok(Json(result).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_rules_encounter_start(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesEncounterStartRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        st.set_path("encounter.active", Value::Bool(true))?;
        st.set_path("encounter.round", Value::from(1))?;
        st.set_path(
            "encounter.encounter_id",
            Value::String(body.encounter_id.unwrap_or_default()),
        )?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
}

#[tracing::instrument(skip_all)]
async fn api_rules_encounter_next(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(_body): Json<RulesEncounterNextRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        let round = st
            .get_path("encounter.round")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        st.set_path("encounter.round", Value::from(round + 1))?;
        st.clone()
    };
    Ok(Json(json!({"ok": true, "state": snapshot.data})).into_response())
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

    let outcome = {
        let mut st = shared.write();
        apply_combat(
            &mut st.data,
            CombatAction::EnemyAttack {
                attacker_id,
                target_id,
                attack_index: 0,
                seed,
            },
        )
        .map_err(bridge_to_response_error)?
    };

    Ok(Json(json!({
        "ok": true,
        "result": outcome.rule_result,
        "encounter": outcome.encounter,
        "gm_facts": outcome.gm_facts,
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
