//! game.py → game.rs — 游戏核心流程路由
//! POST /api/new                  — 创建新存档
//! POST /api/opening              — SSE 开场白
//! GET  /api/chat/context-breakdown — 上下文 breakdown
//! POST /api/chat/estimate        — 实时上下文预估
//! POST /api/chat                 — 主聊天 SSE
//! POST /api/stop                 — 打断当前 chat
//! POST /api/save                 — 保存存档

use std::convert::Infallible;

use axum::{
    extract::State,
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
    routing::{get, post},
    Json, Router,
};
use futures_util::stream::StreamExt;
use http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio_stream::wrappers::ReceiverStream;

// ChatChunk / LlmChatRequest / WireChatChunk — 仅测试引用(MockBackend + drain_to_wire)。
#[cfg(test)]
use rpg_llm::pipeline::ChatChunk;
#[cfg(test)]
use rpg_llm::pipeline::ChatRequest as LlmChatRequest;
#[cfg(test)]
use rpg_llm::pipeline::WireChatChunk;
use rpg_platform::quota::{self, QuotaConfig, QuotaError};
use rpg_state::GameState;

use crate::sse_metrics::{GuardedStream, SseConnectionGuard};
use crate::{hello_payload, named_sse_event, require_user, user_id_or_anon, AppState, ResponseError};

type SseResponse = Result<Sse<GuardedStream<ReceiverStream<Result<Event, Infallible>>>>, ResponseError>;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/new", post(api_new))
        .route("/api/opening", post(api_opening))
        .route("/api/chat/estimate", post(api_chat_estimate))
        .route("/api/chat/context-breakdown", get(api_context_breakdown))
        .route("/api/chat", post(api_chat))
        .route("/api/stop", post(api_stop))
        .route("/api/save", post(api_save))
}

/// 非 SSE 路由(供 build_regular_routes 使用,排除 /api/chat 和 /api/opening)。
pub fn regular_router() -> Router<AppState> {
    Router::new()
        .route("/api/new", post(api_new))
        .route("/api/chat/estimate", post(api_chat_estimate))
        .route("/api/chat/context-breakdown", get(api_context_breakdown))
        .route("/api/stop", post(api_stop))
        .route("/api/save", post(api_save))
}


// ── request / response types ────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct NewGameRequest {
    pub name: Option<String>,
    pub role: Option<String>,
    pub background: Option<String>,
    pub persona_id: Option<i64>,
    pub user_card_id: Option<i64>,
    pub script_card_id: Option<i64>,
    pub script_id: Option<i64>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ChatRequest {
    pub message: Option<String>,
    /// 旧版前端字段 fallback
    pub text: Option<String>,
    pub attachments: Option<Vec<Value>>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ChatEstimateRequest {
    pub message: Option<String>,
    pub include_retrieval: Option<bool>,
}

// ── handlers ─────────────────────────────────────────────────────────────────

/// POST /api/new — 创建新存档
///
/// 把现有 state 重置成空白存档,并按 4 级优先链解析角色卡:
///   1. script_card_id + script_id  → character_cards WHERE id=$1 AND script_id=$2
///   2. user_card_id                → character_cards WHERE id=$1
///   3. persona_id                  → user_personas WHERE id=$1 AND user_id=$2
///   4. body.name / role / background 直接用
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_new(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<NewGameRequest>>,
) -> Result<Response, ResponseError> {
    let body = body.map(|Json(b)| b).unwrap_or_default();
    // 写状态路由:强鉴权,匿名 → 401。
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    let user_id_num: i64 = user.id.into();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));

    // ── 4 级角色卡解析 ──────────────────────────────────────────────────────
    // source_kind 记录实际命中的来源,写入 player.source_kind。
    // 注:用 sqlx::query(非宏版)避免编译期 DATABASE_URL 依赖。
    use sqlx::Row as _;
    let (name, role, background, source_kind): (String, String, String, &'static str) =
        if let Some(script_card_id) = body.script_card_id {
            // 优先级 1:剧本预置角色卡
            let script_id = body.script_id.unwrap_or(0);
            let row = sqlx::query(
                "SELECT name, identity, appearance, personality \
                 FROM character_cards WHERE id = $1 AND script_id = $2",
            )
            .bind(script_card_id)
            .bind(script_id)
            .fetch_optional(&s.db)
            .await
            .map_err(|e| {
                tracing::error!("api_new: character_cards query failed: {e}");
                ResponseError::internal(e.to_string())
            })?;
            if let Some(r) = row {
                let appearance: String = r.try_get("appearance").unwrap_or_default();
                let personality: String = r.try_get("personality").unwrap_or_default();
                let bg = if appearance.trim().is_empty() { personality } else { appearance };
                (
                    r.try_get("name").unwrap_or_default(),
                    r.try_get("identity").unwrap_or_default(),
                    bg,
                    "script_card",
                )
            } else {
                tracing::warn!(
                    script_card_id,
                    script_id,
                    "api_new: script_card not found, falling back to body fields"
                );
                (
                    body.name.clone().unwrap_or_default(),
                    body.role.clone().unwrap_or_default(),
                    body.background.clone().unwrap_or_default(),
                    "",
                )
            }
        } else if let Some(user_card_id) = body.user_card_id {
            // 优先级 2:用户自创 NPC 卡
            let row = sqlx::query(
                "SELECT name, identity, appearance, personality \
                 FROM character_cards WHERE id = $1",
            )
            .bind(user_card_id)
            .fetch_optional(&s.db)
            .await
            .map_err(|e| {
                tracing::error!("api_new: user_card query failed: {e}");
                ResponseError::internal(e.to_string())
            })?;
            if let Some(r) = row {
                let appearance: String = r.try_get("appearance").unwrap_or_default();
                let personality: String = r.try_get("personality").unwrap_or_default();
                let bg = if appearance.trim().is_empty() { personality } else { appearance };
                (
                    r.try_get("name").unwrap_or_default(),
                    r.try_get("identity").unwrap_or_default(),
                    bg,
                    "user_card",
                )
            } else {
                tracing::warn!(user_card_id, "api_new: user_card not found, falling back to body fields");
                (
                    body.name.clone().unwrap_or_default(),
                    body.role.clone().unwrap_or_default(),
                    body.background.clone().unwrap_or_default(),
                    "",
                )
            }
        } else if let Some(persona_id) = body.persona_id {
            // 优先级 3:用户 persona
            let row = sqlx::query(
                "SELECT name, role, background \
                 FROM user_personas WHERE id = $1 AND user_id = $2",
            )
            .bind(persona_id)
            .bind(user_id_num)
            .fetch_optional(&s.db)
            .await
            .map_err(|e| {
                tracing::error!("api_new: user_personas query failed: {e}");
                ResponseError::internal(e.to_string())
            })?;
            if let Some(r) = row {
                (
                    r.try_get("name").unwrap_or_default(),
                    r.try_get("role").unwrap_or_default(),
                    r.try_get("background").unwrap_or_default(),
                    "persona",
                )
            } else {
                tracing::warn!(persona_id, "api_new: persona not found, falling back to body fields");
                (
                    body.name.clone().unwrap_or_default(),
                    body.role.clone().unwrap_or_default(),
                    body.background.clone().unwrap_or_default(),
                    "",
                )
            }
        } else {
            // 优先级 4:body 直接字段
            (
                body.name.clone().unwrap_or_default(),
                body.role.clone().unwrap_or_default(),
                body.background.clone().unwrap_or_default(),
                "",
            )
        };

    // 最终默认值:与 Python 一致
    let name = {
        let t = name.trim().to_string();
        if t.is_empty() { "无名者".to_string() } else { t }
    };
    let role = {
        let t = role.trim().to_string();
        if t.is_empty() { "未指定".to_string() } else { t }
    };
    let background = background.trim().to_string();

    let shared = s.state_store.get_or_create(&user_id).await;
    {
        let mut st = shared.write();
        *st = GameState::new(user_id.clone());
        let _ = st.set_path("player.name", Value::String(name));
        let _ = st.set_path("player.role", Value::String(role));
        let _ = st.set_path("player.background", Value::String(background));
        if !source_kind.is_empty() {
            let _ = st.set_path("player.source_kind", Value::String(source_kind.to_string()));
        }
        let _ = st.set_path("is_new", Value::Bool(false));
    }
    // 6C-1 Arc 快照:读路径不再深拷贝整树,snapshot() 仅 inc Arc refcount。
    // api_new 刚写过 state(touch 已让快照缓存失效),此处重建一次后返回。
    let (data, version) = {
        let st = shared.read();
        (st.snapshot(), st.version)
    };
    Ok(Json(json!({
        "ok": true,
        "state": data,
        "version": version,
    }))
    .into_response())
}

/// POST /api/opening — SSE 开场白(接通 context engine + GameMaster)
///
/// 流程:
///   1. 鉴权 + 取 user GameState。
///   2. 查 DB `game_saves` 拿当前存档的 `script_id`(无 → 退化 stub)。
///   3. RAG 检索:从 state 的 location + objective + time 拼 query,调 bm25_search。
///   4. 构建 context bundle(rpg_context::build_context_bundle)。
///   5. 创建 GameMaster,调 `gm.generate_opening(state, &context_text)`。
///   6. 解析结构化更新(extract_json_state_ops + apply_structured_updates)。
///   7. 追加 history + increment_turn。
///   8. SSE 事件序列:hello → state_change(retrieving) → state_change(generating)
///      → chunk(完整文本) → done(带 snapshot)。
///   9. LLM 失败 → 退化 stub(空 chunk + done),不 500。
#[tracing::instrument(skip(s, headers), fields(user_id))]
pub(crate) async fn api_opening(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> SseResponse {
    // 触达 LLM 路由:强鉴权,匿名严禁触达 LLM → 401。
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    let user_id_num: i64 = user.id.into();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;

    // SSE 活跃连接 gauge +1。
    let guard = SseConnectionGuard::new("opening");

    // 所有路径统一用 ReceiverStream 出口(避免 Either 带来的 Unpin 问题)。
    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(32);

    // 取 backend(若无 catalog/无注册则 stub fallback,不报错以兼容空环境)。
    let backend_opt = s.llm_router.read().current_backend().ok();

    // 没 backend → 老 stub 路径(同旧行为)。
    if backend_opt.is_none() {
        let state_data = shared.read().snapshot();
        let _ = tx.send(Ok(named_sse_event("hello", hello_payload(&user_id)))).await;
        let _ = tx.send(Ok(named_sse_event(
            "stage",
            json!({"phase":"generating","label":"GM 构思开场中(stub)…"}),
        ))).await;
        let _ = tx.send(Ok(named_sse_event(
            "stage",
            json!({"phase":"done"}),
        ))).await;
        let _ = tx.send(Ok(named_sse_event("token", json!({"text":""})))).await;
        let _ = tx.send(Ok(named_sse_event(
            "done",
            json!({"status": {"state": state_data}, "interrupted": false}),
        ))).await;
        let stream = GuardedStream::new(ReceiverStream::new(rx), guard);
        return Ok(Sse::new(stream).keep_alive(KeepAlive::default()));
    }

    // ── Phase 1: 查存档的 script_id + save_id ──────────────────────────
    use sqlx::Row as _;
    let save_row = sqlx::query(
        "SELECT id, script_id FROM game_saves \
         WHERE user_id = $1 \
         ORDER BY updated_at DESC LIMIT 1",
    )
    .bind(user_id_num)
    .fetch_optional(&s.db)
    .await
    .ok()
    .flatten();
    let (save_id, script_id): (Option<i64>, Option<i64>) = match &save_row {
        Some(row) => (
            row.try_get("id").ok(),
            row.try_get("script_id").ok(),
        ),
        None => (None, None),
    };

    // 首帧 hello。
    let _ = tx
        .send(Ok(named_sse_event("hello", hello_payload(&user_id))))
        .await;

    // 把所有 heavyweight 工作放到 spawned task,避免阻塞 SSE 握手。
    let state_handle = shared.clone();
    let db = s.db.clone();
    let llm_router = s.llm_router.clone();
    let user_id_clone = user_id.clone();
    let state_store_clone = s.state_store.clone();
    tokio::spawn(async move {
        // ── Phase 2: RAG 检索 ──────────────────────────────────────────
        let _ = tx
            .send(Ok(named_sse_event(
                "stage",
                json!({"phase":"retrieving","label":"正在检索相关剧情…"}),
            )))
            .await;

        let retrieved = if let Some(sid) = script_id {
            // 从 state 拼 retrieval query(对齐 Python api_opening)
            let query = {
                let st = state_handle.read();
                let location = &st.data.player.current_location;
                let time = &st.data.world.time;
                let objective = &st.data.memory.current_objective;
                let events: Vec<String> = st.data.world.known_events
                    .iter()
                    .take(2)
                    .filter_map(|e| e.as_str().map(String::from))
                    .collect();
                let parts: Vec<&str> = [
                    location.as_str(),
                    time.as_str(),
                    objective.as_str(),
                ]
                .into_iter()
                .chain(events.iter().map(|s| s.as_str()))
                .filter(|p| !p.is_empty())
                .collect();
                if parts.is_empty() {
                    "开场".to_string()
                } else {
                    parts.join(" ")
                }
            };
            // TODO: Python 端用 phase 算法限定 chapter_min/chapter_max;
            // 此处暂传 None(全量搜索),后续接 _resolve_active_phase_range。
            match rpg_retrieval::bm25_search(&db, sid as i32, &query, 6, None, None).await {
                Ok(hits) if !hits.is_empty() => rpg_retrieval::format_chunks_fallback(&hits),
                Ok(_) => String::new(),
                Err(e) => {
                    tracing::warn!(script_id = sid, error = %e, "opening RAG 检索失败,传空 retrieved");
                    String::new()
                }
            }
        } else {
            String::new()
        };

        // ── Phase 3: Context bundle ────────────────────────────────────
        let _ = tx
            .send(Ok(named_sse_event(
                "stage",
                json!({"phase":"building_context","label":"正在构建上下文…"}),
            )))
            .await;
        // 先 clone state data(parking_lot guard 不能跨 await),再调 async。
        let state_data_snapshot = {
            let st = state_handle.read();
            st.data.clone()
        };
        // Gap 9: query book_id from books table + construct ProviderServices with db_pool
        let book_id: Option<i64> = if let Some(sid) = script_id {
            sqlx::query_scalar(
                "SELECT b.id FROM books b WHERE b.script_id = $1 ORDER BY b.id LIMIT 1",
            )
            .bind(sid)
            .fetch_optional(&db)
            .await
            .ok()
            .flatten()
        } else {
            None
        };
        let services = rpg_context::ProviderServices {
            db_pool: Some(db.clone()),
            script_id,
            book_id,
            save_id,
            user_id: Some(user_id_num),
            ..Default::default()
        };
        let context_text = {
            let bundle = rpg_context::build_context_bundle(
                &state_data_snapshot,
                "开场",
                &retrieved,
                None,           // curator_plan
                script_id,
                book_id,
                None,           // contributions (auto-resolve)
                None,           // manifest (auto-resolve)
                save_id,
                Some(services),
            )
            .await;
            // bundle["prompt"] 是 context engine 拼好的完整 prompt 文本
            bundle
                .get("prompt")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string()
        };

        // ── Phase 4: 生成开场白 ────────────────────────────────────────
        let _ = tx
            .send(Ok(named_sse_event(
                "stage",
                json!({"phase":"generating","label":"GM 正在构思开场白…"}),
            )))
            .await;

        let opening_result = {
            // 按需创建 GameMaster(不修改 AppState struct,只用局部变量)
            let gm = {
                let llm_res = llm_router.read().current_backend();
                match llm_res {
                    Ok(llm) => {
                        std::sync::Arc::new(rpg_agents::gm::GameMaster::new(llm))
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "opening: 无法获取 LLM backend");
                        // stub fallback
                        let state_data = state_handle.read().snapshot();
                        let _ = tx.send(Ok(named_sse_event("stage", json!({"phase":"done"})))).await;
                        let _ = tx.send(Ok(named_sse_event("token", json!({"text":""})))).await;
                        let _ = tx.send(Ok(named_sse_event(
                            "done",
                            json!({"status": {"state": state_data}, "interrupted": false}),
                        ))).await;
                        return;
                    }
                }
            };
            let state_snapshot = state_handle.read().clone();
            gm.generate_opening(&state_snapshot, &context_text).await
        };

        let opening_text = match opening_result {
            Ok(text) if !text.is_empty() => text,
            Ok(_) => {
                tracing::warn!("opening: GM 返回空文本,走 stub fallback");
                let state_data = state_handle.read().snapshot();
                let _ = tx.send(Ok(named_sse_event("stage", json!({"phase":"done"})))).await;
                let _ = tx.send(Ok(named_sse_event("token", json!({"text":""})))).await;
                let _ = tx.send(Ok(named_sse_event(
                    "done",
                    json!({"status": {"state": state_data}, "interrupted": false}),
                ))).await;
                return;
            }
            Err(e) => {
                tracing::warn!(error = %e, "opening: GM generate_opening 失败,走 stub fallback");
                let state_data = state_handle.read().snapshot();
                let _ = tx.send(Ok(named_sse_event("stage", json!({"phase":"done"})))).await;
                let _ = tx.send(Ok(named_sse_event("token", json!({"text":""})))).await;
                let _ = tx.send(Ok(named_sse_event(
                    "done",
                    json!({"status": {"state": state_data}, "interrupted": false}),
                ))).await;
                return;
            }
        };

        // ── Phase 5: 解析结构化更新 + 写 history ───────────────────────
        {
            let mut st = state_handle.write();
            // 结构化 ops 提取 + 应用(对齐 Python state.apply_structured_updates)
            // 失败不阻塞,只 warn。
            if let Err(e) = rpg_state::apply_structured_updates(&mut st, &opening_text) {
                tracing::warn!(error = %e, "opening: apply_structured_updates 失败");
            }
            st.append_history("assistant", &opening_text);
            st.increment_turn();
        }

        // 持久化到 DB,对齐 Python state.save() + _persist_runtime_checkpoint。
        state_store_clone.flush(&user_id_clone).await;

        // ── Phase 6: SSE 事件 ──────────────────────────────────────────
        // stage(done) 告知前端 GM 生成完毕,紧跟 token 事件发完整文本
        let _ = tx
            .send(Ok(named_sse_event("stage", json!({"phase":"done"}))))
            .await;
        // 一次性发完整文本(gm.generate_opening 不是 stream)
        let _ = tx
            .send(Ok(named_sse_event("token", json!({"text": opening_text}))))
            .await;

        // done — 带最新 snapshot。
        let state_data = state_handle.read().snapshot();
        let _ = tx
            .send(Ok(named_sse_event(
                "done",
                json!({"status": {"state": state_data}, "interrupted": false}),
            )))
            .await;
        let _ = user_id_clone;
    });

    let stream = GuardedStream::new(ReceiverStream::new(rx), guard);
    Ok(Sse::new(stream).keep_alive(KeepAlive::default()))
}

/// opening max_tokens(保留常量,供测试引用;实际 max_tokens 由 GameMaster::config 控制)。
#[allow(dead_code)]
const OPENING_MAX_TOKENS: u32 = 600;

/// POST /api/chat/estimate — 实时上下文预估
///
/// 本翻译期没接 platform_app.usage,给一个极轻量估算:
/// 输入 = system(1200) + history_char/3 + retrieval(800 if include) + message_char/3。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_chat_estimate(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ChatEstimateRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let message = body.message.unwrap_or_default();
    let include_retrieval = body.include_retrieval.unwrap_or(true);
    let history_text = {
        let st = shared.read();
        st.data
            .history
            .iter()
            .filter_map(|m| m.get("content").and_then(|v| v.as_str()))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let est = |s: &str| (s.chars().count() / 3) as i64;
    let system_est: i64 = 1200;
    let retrieval_est: i64 = if include_retrieval { 800 } else { 0 };
    let history_est = est(&history_text);
    let input_tokens = system_est + history_est + retrieval_est + est(&message);
    let output_estimate: i64 = 600;
    let ctx_max: i64 = 1_000_000;
    let ctx_pct = (input_tokens as f64 * 100.0 / ctx_max as f64 * 10.0).round() / 10.0;
    let total = input_tokens + output_estimate;
    let will_overflow = total > ctx_max;
    Ok(Json(json!({
        "ok": true,
        "api_id": "",
        "model": "",
        "context_used": input_tokens,
        "context_max": ctx_max,
        "context_pct": ctx_pct,
        "estimated_output_tokens": output_estimate,
        "estimated_total_tokens": total,
        "will_overflow": will_overflow,
        "breakdown": {
            "system_prompt": system_est,
            "history": history_est,
            "retrieval_budget": retrieval_est,
            "current_input": est(&message),
        },
        "headroom_tokens": (ctx_max - input_tokens - output_estimate).max(0),
    }))
    .into_response())
}

/// GET /api/chat/context-breakdown — 返回上次 context 各层 token 分布
///
/// 直接读 state.memory.last_context,layer 分类映射放到 inline 表;
/// 没有 last_context 时返回 0 token + 单一 free 项。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_context_breakdown(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let last_ctx = {
        let st = shared.read();
        Value::Object(st.data.memory.last_context.clone())
    };
    let total_tokens = last_ctx
        .get("estimated_tokens")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let layers = last_ctx
        .get("layers")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    // category 累加
    let category_of = |id: &str| -> &'static str {
        match id {
            "recent_chat" | "user_input" => "history",
            "rag" | "novel_retrieval" => "retrieved_chunks",
            "fact_groups" | "hypotheses" | "memory" => "memory_facts",
            "player_card" | "npc_cards" | "novel_characters" => "character_cards",
            "worldbook" | "novel_worldbook" | "module_worldbook" => "worldbook",
            "novel_timeline" | "runtime_phase_digests" => "phase_digests",
            _ => "system_prompt",
        }
    };
    let mut cat_tokens: std::collections::HashMap<&'static str, i64> = Default::default();
    for layer in &layers {
        let id = layer.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let tok = layer
            .get("estimated_tokens")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        *cat_tokens.entry(category_of(id)).or_insert(0) += tok;
    }

    let order: &[(&str, &str, &str)] = &[
        ("history", "对话历史", "#4f8ef7"),
        ("system_prompt", "系统提示", "#9b6bdf"),
        ("retrieved_chunks", "RAG 召回", "#2bae8a"),
        ("memory_facts", "长期记忆", "#e6a817"),
        ("character_cards", "角色卡", "#e05c7a"),
        ("worldbook", "世界书", "#3dbad4"),
        ("phase_digests", "阶段摘要", "#f07a3c"),
        ("tools", "工具/MCP", "#8899aa"),
    ];
    let ctx_limit: i64 = 1_000_000;
    let mut used_sum = 0i64;
    let mut breakdown = Vec::new();
    for (key, label, color) in order {
        let tok = cat_tokens.get(key).copied().unwrap_or(0);
        used_sum += tok;
        let pct = if ctx_limit > 0 {
            (tok as f64 * 100.0 / ctx_limit as f64 * 10.0).round() / 10.0
        } else {
            0.0
        };
        breakdown.push(json!({"key": key, "label": label, "tokens": tok, "pct": pct, "color": color}));
    }
    let free = (ctx_limit - used_sum).max(0);
    let free_pct = if ctx_limit > 0 {
        (free as f64 * 100.0 / ctx_limit as f64 * 10.0).round() / 10.0
    } else {
        0.0
    };
    breakdown.push(
        json!({"key": "free", "label": "剩余空间", "tokens": free, "pct": free_pct, "color": "#555e6a"}),
    );
    Ok(Json(json!({
        "ok": true,
        "total_tokens": if total_tokens > 0 { total_tokens } else { used_sum },
        "ctx_limit": ctx_limit,
        "breakdown": breakdown,
    }))
    .into_response())
}

/// POST /api/chat — 主聊天 SSE(5 阶段 pipeline)
///
/// 完整 pipeline 对齐 Python `chat_pipeline.py`:
///   1. **Player Directives** — `/set` 解析 + 过期旧 pending questions
///   2. **Context Assembly** — DB 查 script_id → RAG bm25_search → build_context_bundle
///   3. **Rules Preflight** — encounter 检测(TODO: 完整 rules engine 集成)
///   4. **GM Response** — `GameMaster::respond_stream` 流式叙事
///   5. **Persist** — `apply_structured_updates` + `append_history` + `increment_turn`
///
/// 无 backend 时退化成 stub fallback(空 chunk + done)。
/// 检索 / 上下文 / 规则任一步失败均退化空上下文继续,不阻塞 GM。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
pub(crate) async fn api_chat(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<ChatRequest>>,
) -> Response {
    let body = body.map(|Json(b)| b).unwrap_or_default();

    // ── 强鉴权:匿名严禁触达 LLM → 401。本 handler 返回裸 Response,手动渲染。
    let user = match require_user(&s, &headers).await {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));

    // SSE 活跃连接 gauge +1(guard drop 时 -1)。
    let sse_guard = SseConnectionGuard::new("chat");

    let message = body
        .message
        .or(body.text)
        .unwrap_or_default()
        .trim()
        .to_string();
    if message.is_empty() {
        let (err_tx, err_rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(4);
        let _ = err_tx.send(Ok(named_sse_event("hello", hello_payload(&user_id)))).await;
        let _ = err_tx.send(Ok(named_sse_event(
            "error",
            json!({"message":"空消息","code":"bad_request"}),
        ))).await;
        drop(err_tx);
        let guarded = GuardedStream::new(ReceiverStream::new(err_rx), sse_guard);
        return Sse::new(guarded).keep_alive(KeepAlive::default()).into_response();
    }

    // ── 调 LLM 前过配额闸:预算 / 日配额 / 速率 / 并发。失败 → 429 + Retry-After。
    let cfg = QuotaConfig::from_env();
    // 预估总 token:输入按字符粗估 + 预期 output 上限(hard cap)。
    let est_input = rpg_platform::usage::estimate_input_tokens(&message);
    let est_tokens = est_input + cfg.hard_max_tokens as i64;
    // 从 catalog 读当前选用的 api_id / model_id,对齐 Python selected_model()。
    let (api_id_owned, model_owned) = {
        let cat = s.llm_router.read().catalog().cloned().unwrap_or_default();
        (cat.selected.api_id.clone(), cat.selected.model_id.clone())
    };
    let grant = match quota::check_and_reserve(
        &s.db, &cfg, user.id, &api_id_owned, &model_owned, est_tokens,
    )
    .await
    {
        Ok(g) => g,
        Err(e) => return quota_error_response(&user_id, e),
    };

    // 清零该 user 的 stop notify(重新建一个,旧的 awaiter 自动 drop)。
    s.stop_events.remove(&user_id);
    let _stop = s.stop_notify(&user_id);

    // 6C-1 跨 pod stop:为本次 chat 分配 run_id 并登记;清掉上一轮可能残留的
    // 跨进程 stop_signals(避免新 run 一上来就被旧信号打断)。
    let run_id = s.next_run_id(user.id);
    rpg_platform::cluster::clear_stop(&s.db, user.id.get(), run_id).await;

    let shared = s.state_store.get_or_create(&user_id).await;

    // 取 backend;无 backend → 退化老 stub 路径(保持兼容)。
    let backend_opt = s.llm_router.read().current_backend().ok();

    // 为让 spawned task 拿走 grant,把 db pool 也克隆进去。
    let db = s.db.clone();
    let user_id_str = user_id.clone();
    let user_id_i64 = user.id.get();
    let state_handle = shared.clone();

    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(32);
    // 首帧。
    let _ = tx
        .send(Ok(named_sse_event("hello", hello_payload(&user_id))))
        .await;
    let _ = tx
        .send(Ok(named_sse_event(
            "stage",
            json!({"phase":"generating","label":"GM 思考中…"}),
        )))
        .await;

    // 无 backend → stub fallback:只 emit 一个空 chunk + done。
    let Some(backend) = backend_opt else {
        let state_after = shared.read().snapshot();
        let actual = rpg_platform::usage::UsageBreakdown {
            input_tokens: est_input.clamp(0, i32::MAX as i64) as i32,
            output_tokens: 0,
            cached_input_tokens: 0,
            reasoning_tokens: 0,
            total_tokens: est_input.clamp(0, i32::MAX as i64) as i32,
        };
        quota::record_actual(&db, grant, None, None, &actual, est_input as i32, 1_000_000).await;
        let _ = tx
            .send(Ok(named_sse_event("token", json!({"text":""}))))
            .await;
        let _ = tx
            .send(Ok(named_sse_event(
                "done",
                json!({"status": {"state": state_after}, "interrupted": false}),
            )))
            .await;
        drop(tx);
        let guarded = GuardedStream::new(ReceiverStream::new(rx), sse_guard);
        return Sse::new(guarded).keep_alive(KeepAlive::default()).into_response();
    };

    // ── 有 backend:5 阶段 pipeline(在 spawned task 内跑) ──────────
    let stop_notify = s.stop_notify(&user_id);
    let user_id_u = user.id;
    tokio::spawn(async move {
        let mut full = String::new();
        let usage_total: u32 = 0;
        let mut interrupted = false;

        // ── Phase 1: Player Directives ──────────────────────────────
        {
            let mut st = state_handle.write();
            let _ = rpg_state::apply_player_directives(&mut st, &message);
            rpg_state::expire_stale_gm_questions(&mut st, None, "chat_new_turn");
        }

        // ── Phase 2: Context Assembly ───────────────────────────────
        let _ = tx.send(Ok(named_sse_event(
            "stage",
            json!({"phase":"context","label":"正在整理上下文…"}),
        ))).await;

        // 2a. 从 DB 取当前用户活跃存档的 script_id + save_id
        use sqlx::Row as _;
        let save_row_chat = sqlx::query(
            "SELECT id, script_id FROM game_saves WHERE user_id = $1 \
             ORDER BY updated_at DESC LIMIT 1",
        )
        .bind(user_id_i64)
        .fetch_optional(&db)
        .await
        .ok()
        .flatten();
        let (chat_save_id, script_id): (Option<i64>, Option<i64>) = match &save_row_chat {
            Some(row) => (row.try_get("id").ok(), row.try_get("script_id").ok()),
            None => (None, None),
        };
        // Gap 8: query book_id for NovelCharactersProvider / NovelWorldbookProvider
        let chat_book_id: Option<i64> = if let Some(sid) = script_id {
            sqlx::query_scalar(
                "SELECT b.id FROM books b WHERE b.script_id = $1 ORDER BY b.id LIMIT 1",
            )
            .bind(sid)
            .fetch_optional(&db)
            .await
            .ok()
            .flatten()
        } else {
            None
        };

        // 2b. RAG 检索
        let retrieved = if let Some(sid) = script_id {
            let retrieval_query = {
                let st = state_handle.read();
                let loc = &st.data.player.current_location;
                let obj = &st.data.memory.current_objective;
                let mut parts: Vec<&str> = Vec::new();
                if !loc.is_empty() { parts.push(loc.as_str()); }
                if !obj.is_empty() { parts.push(obj.as_str()); }
                parts.push(&message);
                parts.join(" ")
            };
            match rpg_retrieval::bm25_search(
                &db, sid as i32, &retrieval_query, 8, None, None,
            ).await {
                Ok(hits) => rpg_retrieval::format_chunks_fallback(&hits),
                Err(e) => {
                    tracing::warn!(error = %e, "Phase 2 RAG 检索失败,退化空上下文");
                    String::new()
                }
            }
        } else {
            String::new()
        };

        // 2c. build_context_bundle
        let context_text = if let Some(sid) = script_id {
            let state_data = state_handle.read().data.clone();
            let chat_services = rpg_context::ProviderServices {
                db_pool: Some(db.clone()),
                script_id: Some(sid),
                book_id: chat_book_id,
                save_id: chat_save_id,
                user_id: Some(user_id_i64),
                ..Default::default()
            };
            let bundle = rpg_context::build_context_bundle(
                &state_data,
                &message,
                &retrieved,
                None, Some(sid), chat_book_id, None, None, chat_save_id, Some(chat_services),
            ).await;
            bundle.get("prompt")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string())
                .unwrap_or_else(|| retrieved.clone())
        } else {
            retrieved.clone()
        };

        // ── Phase 2.5: Worldbook Agent stubs ────────────────────────
        // TODO: replace stubs with real worldbook agent calls when the crate is ready.
        let _ = tx.send(Ok(named_sse_event(
            "worldbook_consulting",
            json!({"query": message, "phase": "consulting", "time": chrono::Utc::now().to_rfc3339()}),
        ))).await;
        let _ = tx.send(Ok(named_sse_event(
            "worldbook_ready",
            json!({"confidence": 0.0, "sources": [], "elapsed_ms": 0}),
        ))).await;

        // ── Phase 3: Rules Preflight ────────────────────────────────
        let _ = tx.send(Ok(named_sse_event(
            "stage",
            json!({"phase":"rules","label":"正在检查规则…"}),
        ))).await;

        let has_encounter = {
            let st = state_handle.read();
            st.data.encounter.active
        };
        if has_encounter {
            // TODO: 调 rpg_rules::get_engine().skill_check / initiative 等
            tracing::debug!("Phase 3: 活跃 encounter,跳过规则预检 (TODO)");
        }

        // ── Phase 4: GM Response ────────────────────────────────────
        let _ = tx.send(Ok(named_sse_event(
            "stage",
            json!({"phase":"generating","label":"GM 正在回应…"}),
        ))).await;

        let gm = rpg_agents::gm::GameMaster::new(backend);
        let state_snapshot = state_handle.read().clone();

        match gm.respond_stream(&message, &context_text, &state_snapshot).await {
            Ok(mut stream) => {
                loop {
                    tokio::select! {
                        _ = stop_notify.notified() => {
                            interrupted = true;
                            break;
                        }
                        chunk_opt = stream.next() => {
                            let Some(chunk_result) = chunk_opt else { break };
                            match chunk_result {
                                Ok(text) => {
                                    if rpg_platform::cluster::is_stop_requested(
                                        &db, user_id_u.get(), run_id,
                                    ).await {
                                        interrupted = true;
                                        break;
                                    }
                                    full.push_str(&text);
                                    if tx.send(Ok(named_sse_event(
                                        "token",
                                        json!({"text": text}),
                                    ))).await.is_err() {
                                        return;
                                    }
                                }
                                Err(e) => {
                                    let _ = tx.send(Ok(named_sse_event(
                                        "error",
                                        json!({"message": e.to_string(), "code": "llm_error"}),
                                    ))).await;
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            Err(e) => {
                let _ = tx.send(Ok(named_sse_event("token", json!({"text":""})))).await;
                tracing::error!(error = %e, "GM respond_stream failed");
                let _ = tx.send(Ok(named_sse_event(
                    "error",
                    json!({"message": e.to_string(), "code": "llm_error"}),
                ))).await;
            }
        }

        // ── Phase 5: Persist ────────────────────────────────────────
        if !full.is_empty() {
            let mut st = state_handle.write();
            // 结构化更新(【…】tags + ```json``` ops)
            if let Err(e) = rpg_state::apply_structured_updates(&mut st, &full) {
                tracing::warn!(error = %e, "Phase 5: apply_structured_updates 部分失败");
            }
            // append history + increment turn
            st.append_history("user", &message);
            st.append_history("assistant", &full);
            st.increment_turn();
            // Gap 6: clear revealed_this_turn flag (matches Python record_turn behavior).
            // The /reveal directive sets this flag for one-shot GM visibility; must clear after each turn.
            if st.data.player_private.flags.contains_key("revealed_this_turn") {
                st.data.player_private.flags.insert(
                    "revealed_this_turn".to_string(),
                    Value::String(String::new()),
                );
            }
        }

        // TODO: record_runtime_turn — 需要 parent_commit_id / ref_id 等分支上下文,
        // 当前 handler 未持有,留后续 Wave 接入。

        let state_after = state_handle.read().snapshot();

        // 跨 pod stop 二次确认
        if !interrupted {
            interrupted = rpg_platform::cluster::is_stop_requested(
                &db, user_id_u.get(), run_id,
            ).await;
        }
        rpg_platform::cluster::clear_stop(&db, user_id_u.get(), run_id).await;

        // 配额回填 usage
        let out_tokens = usage_total.clamp(0, i32::MAX as u32) as i32;
        let actual = rpg_platform::usage::UsageBreakdown {
            input_tokens: est_input.clamp(0, i32::MAX as i64) as i32,
            output_tokens: out_tokens,
            cached_input_tokens: 0,
            reasoning_tokens: 0,
            total_tokens: est_input.clamp(0, i32::MAX as i64) as i32 + out_tokens,
        };
        quota::record_actual(
            &db, grant, None, None, &actual, est_input as i32, 1_000_000,
        ).await;

        let _ = tx
            .send(Ok(named_sse_event(
                "done",
                json!({"status": {"state": state_after}, "interrupted": interrupted}),
            )))
            .await;
        let _ = user_id_str;
    });

    let guarded = GuardedStream::new(ReceiverStream::new(rx), sse_guard);
    Sse::new(guarded).keep_alive(KeepAlive::default()).into_response()
}

/// chat 用的 system prompt。GameMaster 内部有自己的 system prompt(gm_master.txt),
/// 此常量保留供测试引用。
#[allow(dead_code)]
const CHAT_SYSTEM: &str = "你是这局 TRPG 的 Game Master,根据玩家输入推进剧情。第二人称叙事,描写场景与可触发的下一步。";

/// chat 默认 max_tokens(实际由 GameMaster::config 控制)。
#[allow(dead_code)]
const CHAT_MAX_TOKENS: u32 = 800;

/// 把 [`QuotaError`] 渲染成 429 响应 + `Retry-After` 头(若有建议),
/// body 沿用 `{ok:false, detail, code}` 协议。
fn quota_error_response(user_id: &str, err: QuotaError) -> Response {
    tracing::warn!(user_id, code = err.code(), error = %err, "quota 闸拦截");
    let mut resp = (
        StatusCode::TOO_MANY_REQUESTS,
        Json(json!({
            "ok": false,
            "detail": err.to_string(),
            "code": err.code(),
        })),
    )
        .into_response();
    if let Some(secs) = err.retry_after_sec() {
        if let Ok(v) = http::HeaderValue::from_str(&secs.to_string()) {
            resp.headers_mut().insert(http::header::RETRY_AFTER, v);
        }
    }
    resp
}

/// POST /api/stop — 打断当前 chat(本 pod 快速路径 + 跨 pod DB 信号)
///
/// 两条路径并发生效:
///   1. **本 pod 快速路径**:`Notify::notify_waiters()` —— chat 在**同一 pod** 时
///      awaiter 立即短路(零延迟)。
///   2. **跨 pod 路径**:`cluster::request_stop` 往 `stop_signals` 表写一行 ——
///      chat 跑在**别的 pod** 时,那边的轮询(`is_stop_requested`)会读到并停。
///      这正是状态外置后多 pod 水平扩展的必备:进程内 Notify 命中不了别的 pod。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_stop(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    // 写状态(stop_signal)路由:强鉴权。
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    // 1. 本 pod 快速路径
    if let Some(n) = s.stop_events.get(&user_id) {
        n.notify_waiters();
    }
    // 2. 跨 pod 路径:对该 user 当前 run_id 写 stop_signals(cluster 用 i64 user_id,
    //    经 UserId::get() 桥接)。没有进行中的 run(run_id 0)则跳过,避免写空信号。
    let run_id = s.current_run_id(user.id);
    if run_id != 0 {
        if let Err(e) =
            rpg_platform::cluster::request_stop(&s.db, user.id.get(), run_id).await
        {
            tracing::warn!(user_id = %user_id, run_id, error = %e, "cluster request_stop 失败");
        }
    }
    // 同步标记 state.permissions.stop_signal,便于 GM 子模块感知。
    if let Some(shared) = s.state_store.get(&user_id) {
        let mut st = shared.write();
        let _ = st.set_path("permissions.stop_signal", Value::Bool(true));
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// POST /api/save — 保存存档
///
/// 6C-1 状态外置:写 `saved_at` 戳后**调 `state_store.flush` 落库**(经注入的 saver
/// 闭包写回 `game_saves.state_snapshot`),实现跨 pod 持久化 / pod 重启不丢档。
/// 纯内存部署(无 saver 注入)时 flush 返回 false,退化为旧的"只 touch + 返回快照"。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_save(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    // 写状态(持久化存档)路由:强鉴权。
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let (data, version) = {
        let mut st = shared.write();
        // 触一次 version+updated_at(同时让 Arc 快照缓存失效)。
        let _ = st.set_path("saved_at", Value::String(chrono::Utc::now().to_rfc3339()));
        // Arc 快照(snapshot 重建一次后返回,仅 inc refcount)。
        (st.snapshot(), st.version)
    };
    // 落库(read-through cache 的写回端)。saver 未注入(纯内存)→ false,不影响响应。
    let persisted = s.state_store.flush(&user_id).await;
    Ok(Json(json!({
        "ok": true,
        "state": data,
        "version": version,
        "persisted": persisted,
    }))
    .into_response())
}

// ── tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use futures_util::stream;
    use rpg_llm::pipeline::{
        BackendKind, ChunkStream, LlmBackend, LlmError, Usage,
    };

    /// 受控 mock backend — 用于 Wave 6-A SSE 接 LLM 流的单测。
    /// 注入一段固定 `ChatChunk` 序列,逐项 surface 出来。
    struct MockBackend {
        chunks: Vec<Result<ChatChunk, LlmError>>,
    }

    #[async_trait]
    impl LlmBackend for MockBackend {
        fn kind(&self) -> BackendKind {
            BackendKind::Openai
        }
        async fn stream_chat<'a>(
            &'a self,
            _req: LlmChatRequest,
        ) -> Result<ChunkStream<'a>, LlmError> {
            let items: Vec<Result<ChatChunk, LlmError>> = self
                .chunks
                .iter()
                .map(|r| match r {
                    Ok(c) => Ok(c.clone()),
                    Err(e) => Err(LlmError::Other(e.to_string())),
                })
                .collect();
            Ok(Box::pin(stream::iter(items)))
        }
    }

    /// 跑一遍 mock backend,把发出来的 ChatChunk 投影成 WireChatChunk 列表。
    /// 这是 SSE handler 内部"chunk → SSE 帧"的核心转换。
    async fn drain_to_wire(backend: &MockBackend) -> Vec<WireChatChunk> {
        let req = LlmChatRequest::default();
        let mut s = backend.stream_chat(req).await.expect("stream ok");
        let mut out = Vec::new();
        while let Some(item) = s.next().await {
            if let Ok(c) = item {
                out.push(WireChatChunk::from_chunk(&c));
            }
        }
        out
    }

    /// Text + Stop → wire 帧分别带 kind=text(text=...) 与 kind=stop。
    #[tokio::test]
    async fn test_opening_text_chunk_then_stop_projects_to_wire() {
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Text("早上好".into())),
                Ok(ChatChunk::Stop {
                    reason: "end_turn".into(),
                }),
            ],
        };
        let wires = drain_to_wire(&backend).await;
        assert_eq!(wires.len(), 2);
        assert_eq!(wires[0].kind, "text");
        assert_eq!(wires[0].text.as_deref(), Some("早上好"));
        assert_eq!(wires[1].kind, "stop");
        assert_eq!(wires[1].stop_reason.as_deref(), Some("end_turn"));
    }

    /// Thinking + Text + Usage 三种 chunk 都能正确投影。
    #[tokio::test]
    async fn test_chat_thinking_and_usage_chunks_project_correctly() {
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Thinking("(深思)".into())),
                Ok(ChatChunk::Text("你看到一只猫".into())),
                Ok(ChatChunk::Usage(Usage {
                    input_tokens: 10,
                    output_tokens: 5,
                    ..Default::default()
                })),
            ],
        };
        let wires = drain_to_wire(&backend).await;
        assert_eq!(wires.len(), 3);
        assert_eq!(wires[0].kind, "thinking");
        assert_eq!(wires[1].kind, "text");
        assert_eq!(wires[2].kind, "usage");
        let u = wires[2].usage.as_ref().expect("usage payload");
        assert_eq!(u.input_tokens, 10);
        assert_eq!(u.output_tokens, 5);
    }

    /// ToolCall chunk 投影成 wire 帧带 tool_call_id / tool_name / tool_input。
    #[tokio::test]
    async fn test_chat_tool_call_chunk_projects_correctly() {
        let backend = MockBackend {
            chunks: vec![Ok(ChatChunk::ToolCall {
                id: "call_123".into(),
                name: "read_file".into(),
                input: json!({"path":"/tmp/x"}),
            })],
        };
        let wires = drain_to_wire(&backend).await;
        assert_eq!(wires.len(), 1);
        assert_eq!(wires[0].kind, "tool_call");
        assert_eq!(wires[0].tool_call_id.as_deref(), Some("call_123"));
        assert_eq!(wires[0].tool_name.as_deref(), Some("read_file"));
        assert_eq!(
            wires[0]
                .tool_input
                .as_ref()
                .and_then(|v| v.get("path"))
                .and_then(|v| v.as_str()),
            Some("/tmp/x"),
        );
    }

    /// stream_chat 抛 Err → drain 看不到 chunk(handler 应转 SSE error 帧)。
    #[tokio::test]
    async fn test_chat_backend_error_surfaces_as_error_item() {
        let backend = MockBackend {
            chunks: vec![Err(LlmError::Other("boom".into()))],
        };
        let req = LlmChatRequest::default();
        let mut stream = backend.stream_chat(req).await.expect("stream ok");
        let first = stream.next().await.expect("one item");
        assert!(first.is_err(), "first item should be Err");
    }

    /// Wave 6-A 关键约束:assistant 文本累积要拿到 ChatChunk::Text 的 text,
    /// 而不会被 Thinking / Stop 污染。
    #[tokio::test]
    async fn test_accumulated_text_skips_thinking_and_stop() {
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Thinking("(内心)".into())),
                Ok(ChatChunk::Text("Hello".into())),
                Ok(ChatChunk::Text(" world".into())),
                Ok(ChatChunk::Stop {
                    reason: "end_turn".into(),
                }),
            ],
        };
        let req = LlmChatRequest::default();
        let mut stream = backend.stream_chat(req).await.expect("stream ok");
        let mut full = String::new();
        while let Some(item) = stream.next().await {
            if let Ok(ChatChunk::Text(t)) = item {
                full.push_str(&t);
            }
        }
        assert_eq!(full, "Hello world");
    }

    /// Opening / Chat prompt 常量非空,避免硬退化掉。
    #[test]
    fn test_opening_prompts_non_empty() {
        const { assert!(OPENING_MAX_TOKENS > 0) };
        assert!(!CHAT_SYSTEM.is_empty());
        const { assert!(CHAT_MAX_TOKENS > 0) };
    }

    // ── Wave 10-A:Extended thinking SSE envelope 端到端 ──────────────────

    /// MockBackend 自带 thinking → text → usage → stop 全序列时,投影出的
    /// SSE envelope 顺序与 kind 都正确,且 thinking 文本不被吞。
    #[tokio::test]
    async fn test_wave10a_thinking_chunks_project_through_sse_envelope() {
        use crate::sse_events::{SseChunkPayload, SseEnvelope};
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Thinking("分析剧情中…".into())),
                Ok(ChatChunk::Thinking("决定回应".into())),
                Ok(ChatChunk::Text("你看到月光下".into())),
                Ok(ChatChunk::Text("一只白猫".into())),
                Ok(ChatChunk::Usage(Usage {
                    input_tokens: 100,
                    output_tokens: 20,
                    reasoning_tokens: 50,
                    ..Default::default()
                })),
                Ok(ChatChunk::Stop {
                    reason: "end_turn".into(),
                }),
            ],
        };
        let wires = drain_to_wire(&backend).await;
        // 序列:thinking ×2 → text ×2 → usage → stop
        let kinds: Vec<&str> = wires.iter().map(|w| w.kind.as_str()).collect();
        assert_eq!(
            kinds,
            vec!["thinking", "thinking", "text", "text", "usage", "stop"]
        );

        // 每个 wire 都能被反序列化成 SseChunkPayload(routes 用同字段名),
        // 进一步包成 envelope 仍是 SseEnvelope::Chunk discriminant。
        for w in &wires {
            let json = serde_json::to_value(w).expect("wire serializes");
            let payload: SseChunkPayload =
                serde_json::from_value(json).expect("payload decodes");
            let env = SseEnvelope::Chunk {
                payload: payload.clone(),
            };
            let env_json = serde_json::to_value(&env).expect("envelope ok");
            assert_eq!(env_json["event"], "chunk");
            // kind 不丢失
            assert_eq!(payload.kind.as_deref(), Some(w.kind.as_str()));
        }

        // thinking 文本完整保留
        assert_eq!(wires[0].text.as_deref(), Some("分析剧情中…"));
        assert_eq!(wires[1].text.as_deref(), Some("决定回应"));

        // reasoning_tokens 透传
        let usage_wire = wires.iter().find(|w| w.kind == "usage").unwrap();
        assert_eq!(usage_wire.usage.as_ref().unwrap().reasoning_tokens, 50);
    }

    /// `rpg_llm::merge_thinking_extra` 被 caller 注入后,ChatRequest::extra
    /// 同时含 thinking_budget(Anthropic/Vertex)和 reasoning_effort(OpenAI/Responses)。
    #[test]
    fn test_wave10a_merge_thinking_extra_covers_all_backends() {
        let mut req = LlmChatRequest::default();
        rpg_llm::merge_thinking_extra(&mut req.extra, 3000);
        // Anthropic/Vertex 读 thinking_budget
        assert_eq!(req.extra["thinking_budget"], 3000);
        // OpenAI/Responses 读 reasoning_effort
        assert_eq!(req.extra["reasoning_effort"], "medium");
    }

    /// thinking_budget=0 走 default(关闭),extra 维持原状,不污染请求。
    #[test]
    fn test_wave10a_disabled_budget_keeps_extra_clean() {
        let mut req = LlmChatRequest::default();
        rpg_llm::merge_thinking_extra(&mut req.extra, 0);
        let clean = req.extra.is_null()
            || req
                .extra
                .as_object()
                .map(|o| o.is_empty())
                .unwrap_or(true);
        assert!(
            clean,
            "0 budget must not inject any thinking field, got {:?}",
            req.extra
        );
    }
}
