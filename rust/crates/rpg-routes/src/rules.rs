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

use rpg_rules_bridge::suggest::suggest_rule_actions;

use crate::{user_id_or_anon, AppState, ResponseError};

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

// ── handlers ──────────────────────────────────────────────────────────────────

async fn api_rules_modules(State(_s): State<AppState>) -> impl IntoResponse {
    // TODO: list_modules() — rpg_rules 没有 Python module catalog 等价物;翻译期返回空列表。
    Json(json!({"ok": true, "modules": []}))
}

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

async fn api_rules_module_launch(
    State(_s): State<AppState>,
    Json(_body): Json<RulesModuleLaunchRequest>,
) -> impl IntoResponse {
    // TODO: 创建独立 save (rpg_platform.runtime),启动 module。翻译期返回 stub。
    Json(json!({"ok": true, "save_id": 0}))
}

async fn api_rules_scene(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = shared.read().clone();
    let scene = snapshot
        .data
        .get("scene")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));
    let encounter = snapshot
        .data
        .get("encounter")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));
    let player_character = snapshot
        .data
        .get("player_character")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));
    Ok(Json(json!({
        "ok": true,
        "scene": scene,
        "encounter": encounter,
        "player_character": player_character,
    }))
    .into_response())
}

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

async fn api_rules_action(
    State(_s): State<AppState>,
    Json(body): Json<RulesActionRequest>,
) -> impl IntoResponse {
    // TODO: 真 RulesEngine action 分派;翻译期 echo kind。
    Json(json!({"ok": true, "kind": body.kind.unwrap_or_default()}))
}

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

async fn api_rules_encounter_enemy(
    State(_s): State<AppState>,
    Json(_body): Json<RulesEncounterEnemyRequest>,
) -> impl IntoResponse {
    // TODO: rpg_rules::RulesEngine attack_roll → state 应用伤害;翻译期 stub。
    Json(json!({"ok": true, "result": {"hit": false, "damage": 0}}))
}

async fn api_rules_suggest(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RulesSuggestRequest>,
) -> Result<Response, ResponseError> {
    let text = body.text.unwrap_or_default();
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = shared.read().clone();
    let actions = suggest_rule_actions(&text, &snapshot.data);
    Ok(Json(json!({"ok": true, "actions": actions})).into_response())
}
