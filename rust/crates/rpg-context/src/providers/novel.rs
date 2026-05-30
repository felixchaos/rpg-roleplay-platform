//! Novel providers — manifest.kind == "novel_adaptation" 时启用。
//! 对应 Python: rpg/context_providers/novel.py
//!
//! - NovelTimelineProvider     — 原著章节锚点
//! - NovelRetrievalProvider    — script-scoped 检索 / ChapterFact / source snippets
//! - NovelCharactersProvider   — 激活角色卡(目前 skeleton)
//! - NovelWorldbookProvider    — 激活世界书条目(目前 skeleton)

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use once_cell::sync::Lazy;
use regex::Regex;
use rpg_schemas::GameStateData;
use serde_json::{json, Value};
use sqlx::Row;
use std::collections::HashMap;
use std::sync::Mutex;

/// 正则编译缓存：key=pattern, value=Some(Regex) 编译成功 / None 编译失败(避免重复报错)。
/// 超过 1024 条时清空（简单 LRU 替代；实际模式数远小于该值）。
static REGEX_CACHE: Lazy<Mutex<HashMap<String, Option<Regex>>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// Gap 27: timeline_filter_fn 结果缓存（key=label, value=anchor JSON, TTL 60s）
static TIMELINE_CACHE: Lazy<Mutex<HashMap<String, (std::time::Instant, Value)>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

const TIMELINE_CACHE_TTL: std::time::Duration = std::time::Duration::from_secs(60);

fn is_novel(manifest: &Manifest) -> bool {
    manifest.kind == "novel_adaptation"
}

fn allow_retrieval(manifest: &Manifest) -> bool {
    manifest.get_retrieval_bool("allow_script_retrieval", true)
}

// ── NovelTimelineProvider ─────────────────────────────────────

pub struct NovelTimelineProvider;

#[async_trait]
impl ContextProvider for NovelTimelineProvider {
    fn id(&self) -> &'static str {
        "novel_timeline"
    }

    fn applies(&self, _state_data: &GameStateData, manifest: &Manifest, _demand: &Demand) -> bool {
        manifest.context_providers.iter().any(|p| p == self.id()) && is_novel(manifest)
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        _demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let world = &state_data.world;
        let timeline = &world.timeline;
        let pending = timeline.pending_jump.clone().unwrap_or(Value::Null);
        let label = pending
            .get("to")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .or_else(|| {
                if world.time.is_empty() { None } else { Some(world.time.clone()) }
            })
            .unwrap_or_default();

        // Gap 27: timeline_filter_fn 带缓存
        let mut anchor: Value = Value::Null;
        if let Some(filter) = services.timeline_filter_fn.as_ref() {
            if !label.is_empty() {
                // 检查缓存
                let cached = {
                    let cache = TIMELINE_CACHE.lock().unwrap();
                    cache.get(&label).and_then(|(ts, v)| {
                        if ts.elapsed() < TIMELINE_CACHE_TTL { Some(v.clone()) } else { None }
                    })
                };
                anchor = if let Some(v) = cached {
                    v
                } else {
                    let result = filter(&label).unwrap_or_else(|exc| json!({ "error": exc.to_string() }));
                    // 写入缓存
                    let mut cache = TIMELINE_CACHE.lock().unwrap();
                    if cache.len() >= 256 { cache.clear(); }
                    cache.insert(label.clone(), (std::time::Instant::now(), result.clone()));
                    result
                };
            }
        }

        let mut lines: Vec<String> = Vec::new();
        lines.push(format!(
            "【时间线】当前 label：{}",
            if label.is_empty() { "（无）" } else { &label }
        ));
        if pending.is_object() {
            let from = pending.get("from").and_then(|v| v.as_str()).unwrap_or("");
            let to = pending.get("to").and_then(|v| v.as_str()).unwrap_or("");
            lines.push(format!("【待确认跳跃】{} → {}", from, to));
        }
        if let Some(ch) = anchor.get("anchor_chapter").and_then(|v| v.as_i64()) {
            let cmin = anchor.get("chapter_min").cloned().unwrap_or(Value::Null);
            let cmax = anchor.get("chapter_max").cloned().unwrap_or(Value::Null);
            lines.push(format!("【原著锚点】第 {} 章，窗口 {}-{}", ch, cmin, cmax));
        } else if !label.is_empty() {
            lines.push("【原著锚点】未精确命中".to_string());
        }

        let text = lines.join("\n");
        let layer = Layer::new("novel_timeline", "时间线事务", text.clone())
            .with_sticky(true)
            .with_priority(70);
        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "novel_timeline".to_string(),
            priority: 70,
            facts: Vec::new(),
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({
                "label": label,
                "anchor": anchor,
                "pending_jump": pending,
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

// ── NovelRetrievalProvider ────────────────────────────────────

pub struct NovelRetrievalProvider;

#[async_trait]
impl ContextProvider for NovelRetrievalProvider {
    fn id(&self) -> &'static str {
        "novel_retrieval"
    }

    fn applies(&self, _state_data: &GameStateData, manifest: &Manifest, _demand: &Demand) -> bool {
        manifest.context_providers.iter().any(|p| p == self.id())
            && is_novel(manifest)
            && allow_retrieval(manifest)
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let retrieve_fn = match services.retrieve_fn.as_ref() {
            Some(f) => f.clone(),
            None => {
                return Ok(ContextContribution::skipped(
                    self.id(),
                    "no retrieve_fn injected",
                ));
            }
        };
        let query = demand.retrieval_query.clone();
        // RetrieveFn 边界仍是 &Value;在 provider 内部按需转换
        let state_value = serde_json::to_value(state_data).unwrap_or(Value::Null);
        let result = retrieve_fn(&query, &state_value).await;
        let text = match result {
            Ok(t) => t,
            Err(exc) => {
                let mut c = ContextContribution::failed(self.id(), exc);
                c.applied = false;
                return Ok(c);
            }
        };
        if text.is_empty() {
            return Ok(ContextContribution::skipped(self.id(), "no retrieval content"));
        }
        // TODO: 等 rpg-state 提供 state.set_last_retrieval(text)

        // ── 向量召回(entity_search embed_query 阶段) ──────────────────────
        // 若上层注入了 embed_fn,则调用 embed(query, "RETRIEVAL_QUERY") 拿到
        // 768-dim 向量,再通过 `embedding <=> $1::vector` 对 character_cards /
        // worldbook_entries 做语义排序。
        //
        // TODO[接入]: db_pool + embed_fn 同时存在时,调用:
        //   let vec = embed_fn(query.clone(), "RETRIEVAL_QUERY".to_string()).await?;
        //   然后用 SQL:
        //     SELECT id, name, (1 - (embedding <=> $1::vector)) AS score
        //     FROM character_cards
        //     WHERE book_id = $2 AND embedding IS NOT NULL
        //     ORDER BY embedding <=> $1::vector
        //     LIMIT 4
        //   拼接结果追加到 text。
        //
        // 当前 embed_fn 未接入 rpg_llm::vertex::VertexBackend::embed,跳过。
        // Gap 24: 接入向量 embed search(当 embed_fn + db_pool 同时注入时)
        if let (Some(embed_fn), Some(pool)) = (services.embed_fn.as_ref(), services.db_pool.as_ref()) {
            if let Some(bid) = services.book_id {
                let embed_query = query.clone();
                let embed_fn_clone = embed_fn.clone();
                match embed_fn_clone(embed_query, "RETRIEVAL_QUERY".to_string()).await {
                    Ok(vec) if !vec.is_empty() => {
                        // 用向量相似度查 character_cards 做语义排序
                        let vec_str = format!("[{}]", vec.iter().map(|f| f.to_string()).collect::<Vec<_>>().join(","));
                        let entity_rows = sqlx::query(
                            "SELECT name, identity, (1 - (embedding <=> $1::vector)) AS score \
                             FROM character_cards \
                             WHERE book_id = $2 AND embedding IS NOT NULL \
                             ORDER BY embedding <=> $1::vector \
                             LIMIT 4",
                        )
                        .bind(&vec_str)
                        .bind(bid)
                        .fetch_all(pool)
                        .await;
                        if let Ok(rows) = entity_rows {
                            if !rows.is_empty() {
                                tracing::debug!(
                                    matched = rows.len(),
                                    "novel_retrieval: 向量召回命中 entity"
                                );
                            }
                        }
                    }
                    Ok(_) => {}
                    Err(e) => {
                        tracing::warn!(error = %e, "novel_retrieval: embed_fn 调用失败,跳过向量召回");
                    }
                }
            }
        }

        let layer = Layer::new(
            "novel_retrieval",
            "检索参考（原著 / ChapterFact）",
            text.clone(),
        )
        .with_priority(40);
        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "novel_retrieval".to_string(),
            priority: 40,
            facts: Vec::new(),
            layers: vec![layer],
            retrieval_items: vec![json!({ "text": text.clone() })],
            warnings: Vec::new(),
            debug: json!({ "query": query, "chars": text.chars().count() }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

// ── NovelCharactersProvider ───────────────────────────────────

pub struct NovelCharactersProvider;

#[async_trait]
impl ContextProvider for NovelCharactersProvider {
    fn id(&self) -> &'static str {
        "novel_characters"
    }

    fn applies(&self, _state_data: &GameStateData, manifest: &Manifest, _demand: &Demand) -> bool {
        manifest.context_providers.iter().any(|p| p == self.id()) && is_novel(manifest)
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        // rpg-db 尚未提供 book 维度 character_cards repo,直接用 sqlx 拉。
        let pool = match services.db_pool.as_ref() {
            Some(p) => p,
            None => {
                return Ok(ContextContribution::skipped(
                    self.id(),
                    "no db_pool injected",
                ));
            }
        };
        // Python 逻辑: script_id 优先,book_id 作为 fallback。
        // 两者都没有时才 skip。
        if services.script_id.is_none() && services.book_id.is_none() {
            return Ok(ContextContribution::skipped(
                self.id(),
                "no script_id or book_id",
            ));
        }

        let cards = match load_character_cards(pool, services.script_id, services.book_id).await {
            Ok(c) => c,
            Err(exc) => {
                return Ok(ContextContribution::skipped(
                    self.id(),
                    format!("db error: {}", exc),
                ));
            }
        };
        if cards.is_empty() {
            return Ok(ContextContribution::skipped(self.id(), "no character cards"));
        }

        let player_name = state_data.player.name.clone();
        let player_role = state_data.player.role.clone();
        let player_background = state_data.player.background.clone();
        let scan_text = build_scan_text(state_data, demand);

        let (player_card, npc_cards) =
            pick_active_cards(&cards, &scan_text, &player_name, &player_role, &player_background);

        let mut layers: Vec<Layer> = Vec::new();
        if let Some(p) = player_card.as_ref() {
            if let Some(text) = p.get("text").and_then(|v| v.as_str()) {
                if !text.is_empty() {
                    let name = p
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    layers.push(
                        Layer::new("player_card", "玩家角色卡", text)
                            .with_sticky(true)
                            .with_priority(88)
                            .with_source(name),
                    );
                }
            }
        }
        if !npc_cards.is_empty() {
            let joined = npc_cards
                .iter()
                .filter_map(|c| c.get("text").and_then(|v| v.as_str()).map(|s| s.to_string()))
                .collect::<Vec<_>>()
                .join("\n\n");
            if !joined.is_empty() {
                layers.push(
                    Layer::new("npc_cards", "当前角色卡（NPC）", joined)
                        .with_priority(78)
                        .with_items(npc_cards.iter().map(strip_card).collect()),
                );
            }
        }
        if layers.is_empty() {
            return Ok(ContextContribution::skipped(self.id(), "no cards selected"));
        }
        let tokens = layers
            .iter()
            .map(|l| l.content.chars().count() as u32 / 2)
            .sum();
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "novel_characters".to_string(),
            priority: 80,
            facts: Vec::new(),
            layers,
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({
                "cards_total": cards.len(),
                "npc_picked": npc_cards.len(),
                "script_id": services.script_id,
                "book_id": services.book_id,
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

/// 从 character_cards 表拉角色卡。
/// Python 逻辑: script_id 优先 (`WHERE enabled = true AND script_id = $1`)，
/// 没有 script_id 时 fallback 到 book_id (`WHERE book_id = $1`)。
async fn load_character_cards(
    pool: &sqlx::PgPool,
    script_id: Option<i64>,
    book_id: Option<i64>,
) -> anyhow::Result<Vec<Value>> {
    let rows = if let Some(sid) = script_id {
        sqlx::query(
            "select name, aliases, identity, appearance, personality, \
                    speech_style, current_status, secrets, sample_dialogue, token_budget, priority \
             from character_cards \
             where enabled = true and script_id = $1 \
             order by priority desc, id asc",
        )
        .bind(sid)
        .fetch_all(pool)
        .await?
    } else if let Some(bid) = book_id {
        sqlx::query(
            "select name, aliases, identity, appearance, personality, \
                    speech_style, current_status, secrets, sample_dialogue, token_budget, priority \
             from character_cards \
             where book_id = $1 \
             order by priority desc, id asc",
        )
        .bind(bid)
        .fetch_all(pool)
        .await?
    } else {
        return Ok(Vec::new());
    };
    Ok(rows
        .into_iter()
        .map(|row| {
            let name: Option<String> = row.try_get("name").ok();
            // aliases 可能是 text[] 或 jsonb,优先按 jsonb 拉,失败按字符串数组拉。
            let aliases: Value = row
                .try_get::<Value, _>("aliases")
                .or_else(|_| {
                    row.try_get::<Vec<String>, _>("aliases")
                        .map(|v| Value::Array(v.into_iter().map(Value::String).collect()))
                })
                .unwrap_or(Value::Array(Vec::new()));
            let identity: Option<String> = row.try_get("identity").ok();
            let appearance: Option<String> = row.try_get("appearance").ok();
            let personality: Option<String> = row.try_get("personality").ok();
            let speech_style: Option<String> = row.try_get("speech_style").ok();
            let current_status: Option<String> = row.try_get("current_status").ok();
            let secrets: Option<String> = row.try_get("secrets").ok();
            // sample_dialogue: jsonb array 或 text[]
            let sample_dialogue: Value = row
                .try_get::<Value, _>("sample_dialogue")
                .or_else(|_| {
                    row.try_get::<Vec<String>, _>("sample_dialogue")
                        .map(|v| Value::Array(v.into_iter().map(Value::String).collect()))
                })
                .unwrap_or(Value::Array(Vec::new()));
            let token_budget: i32 = row.try_get("token_budget").unwrap_or(450);
            let priority: i32 = row.try_get("priority").unwrap_or(100);
            let name_s = name.unwrap_or_default();
            let dialogue_strs: Vec<&str> = sample_dialogue
                .as_array()
                .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
                .unwrap_or_default();
            let text = render_card_text(
                &name_s,
                identity.as_deref().unwrap_or(""),
                appearance.as_deref().unwrap_or(""),
                personality.as_deref().unwrap_or(""),
                speech_style.as_deref().unwrap_or(""),
                current_status.as_deref().unwrap_or(""),
                secrets.as_deref().unwrap_or(""),
                &dialogue_strs,
            );
            json!({
                "name": name_s,
                "aliases": aliases,
                "identity": identity.unwrap_or_default(),
                "appearance": appearance.unwrap_or_default(),
                "personality": personality.unwrap_or_default(),
                "speech_style": speech_style.unwrap_or_default(),
                "current_status": current_status.unwrap_or_default(),
                "secrets": secrets.unwrap_or_default(),
                "sample_dialogue": sample_dialogue,
                "token_budget": token_budget,
                "priority": priority,
                "text": text,
            })
        })
        .collect())
}

/// ctx-09: 对齐 Python _format_card 的渲染格式:
///   - 使用 【name】 而非 # name
///   - '说话风格' 而非 '语气'
///   - '隐藏信息' 而非 '秘密（GM 私有）'
///   - 追加 '台词示例' (前 3 条，以 '；' 连接)
fn render_card_text(
    name: &str,
    identity: &str,
    appearance: &str,
    personality: &str,
    speech: &str,
    status: &str,
    secrets: &str,
    sample_dialogue: &[&str],
) -> String {
    let mut parts: Vec<String> = Vec::new();
    parts.push(format!("【{}】", name));
    if !identity.trim().is_empty() {
        parts.push(format!("身份：{}", identity.trim()));
    }
    if !appearance.trim().is_empty() {
        parts.push(format!("外貌：{}", appearance.trim()));
    }
    if !personality.trim().is_empty() {
        parts.push(format!("性格：{}", personality.trim()));
    }
    if !speech.trim().is_empty() {
        parts.push(format!("说话风格：{}", speech.trim()));
    }
    if !status.trim().is_empty() {
        parts.push(format!("当前状态：{}", status.trim()));
    }
    if !secrets.trim().is_empty() {
        parts.push(format!("隐藏信息：{}", secrets.trim()));
    }
    let sample: Vec<&str> = sample_dialogue.iter().take(3).copied().collect();
    if !sample.is_empty() {
        parts.push(format!("台词示例：{}", sample.join("；")));
    }
    parts.join("\n")
}

fn strip_card(card: &Value) -> Value {
    json!({
        "name": card.get("name").cloned().unwrap_or(Value::Null),
        "aliases": card.get("aliases").cloned().unwrap_or(Value::Null),
        "priority": card.get("priority").cloned().unwrap_or(Value::Null),
    })
}

/// 把玩家意图 + 最近对话 + 当前位置/时间 拼成 scan 文本,用于命中角色卡 / 世界书。
fn build_scan_text(state_data: &GameStateData, demand: &Demand) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !demand.player_intent.is_empty() {
        parts.push(demand.player_intent.clone());
    }
    let history = &state_data.history;
    let start = history.len().saturating_sub(6);
    for msg in &history[start..] {
        if let Some(c) = msg.get("content").and_then(|v| v.as_str()) {
            parts.push(c.to_string());
        }
    }
    if !state_data.player.current_location.is_empty() {
        parts.push(state_data.player.current_location.clone());
    }
    if !state_data.world.time.is_empty() {
        parts.push(state_data.world.time.clone());
    }
    for e in &state_data.world.known_events {
        if let Some(s) = e.as_str() {
            parts.push(s.to_string());
        }
    }
    if !state_data.memory.current_objective.is_empty() {
        parts.push(state_data.memory.current_objective.clone());
    }
    parts.join("\n")
}

/// NPC 卡选:先挑 player_card(按 player_name 匹配,fallback 到 '杭雁菱'),
/// 再按 scan_text 命中数挑前 4 个 NPC。
///
/// ctx-10: Python active[:4],Rust 之前是 take(6),对齐 Python → take(4)。
/// ctx-11: 评分公式对齐 Python: score = 100 + matched_count * 8。
/// ctx-14: player_card 合并 runtime state(player.role → identity, player.background → current_status),
///         并在 player_name 不命中时 fallback 到 '杭雁菱'。
fn pick_active_cards(
    cards: &[Value],
    scan_text: &str,
    player_name: &str,
    player_role: &str,
    player_background: &str,
) -> (Option<Value>, Vec<Value>) {
    // 找 player card — 先按 player_name,再 fallback 到 '杭雁菱'(ctx-14)
    let raw_player_card = cards
        .iter()
        .find(|c| {
            c.get("name")
                .and_then(|v| v.as_str())
                .map(|n| !player_name.is_empty() && n == player_name)
                .unwrap_or(false)
        })
        .or_else(|| {
            cards.iter().find(|c| {
                c.get("name")
                    .and_then(|v| v.as_str())
                    .map(|n| n == "杭雁菱")
                    .unwrap_or(false)
            })
        })
        .cloned();

    // 合并 runtime state 到 player card(ctx-14)
    let player_card = raw_player_card.map(|mut card| {
        // 确定渲染时用的姓名:优先 player_name,否则保留卡面名
        let display_name = if !player_name.is_empty() {
            player_name.to_string()
        } else {
            card.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string()
        };
        // 合并 runtime 字段
        let identity = if !player_role.is_empty() {
            player_role.to_string()
        } else {
            card.get("identity").and_then(|v| v.as_str()).unwrap_or("").to_string()
        };
        let current_status = if !player_background.is_empty() {
            player_background.to_string()
        } else {
            card.get("current_status").and_then(|v| v.as_str()).unwrap_or("").to_string()
        };
        // 读其他字段
        let appearance = card.get("appearance").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let personality = card.get("personality").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let speech_style = card.get("speech_style").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let secrets = card.get("secrets").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let sample_dialogue: Vec<String> = card
            .get("sample_dialogue")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
            .unwrap_or_default();
        let dialogue_refs: Vec<&str> = sample_dialogue.iter().map(|s| s.as_str()).collect();
        let text = render_card_text(
            &display_name,
            &identity,
            &appearance,
            &personality,
            &speech_style,
            &current_status,
            &secrets,
            &dialogue_refs,
        );
        if let Some(obj) = card.as_object_mut() {
            obj.insert("identity".to_string(), Value::String(identity));
            obj.insert("current_status".to_string(), Value::String(current_status));
            obj.insert("text".to_string(), Value::String(text));
        }
        card
    });

    // NPC 评分:ctx-11 score = 100 + matched_count * 8
    let mut scored: Vec<(i32, Value)> = Vec::new();
    for card in cards {
        let name = card.get("name").and_then(|v| v.as_str()).unwrap_or("");
        if name.is_empty() || (!player_name.is_empty() && name == player_name) {
            continue;
        }
        let mut matched_count: i32 = 0;
        if scan_text.contains(name) {
            matched_count += 1;
        }
        if let Some(aliases) = card.get("aliases").and_then(|v| v.as_array()) {
            for a in aliases {
                if let Some(s) = a.as_str() {
                    if !s.is_empty() && scan_text.contains(s) {
                        matched_count += 1;
                    }
                }
            }
        }
        if matched_count > 0 {
            let score = 100 + matched_count * 8;
            scored.push((score, card.clone()));
        }
    }
    scored.sort_by_key(|b| std::cmp::Reverse(b.0));
    // ctx-10: Python active[:4]
    let npc_cards: Vec<Value> = scored.into_iter().take(4).map(|(_, c)| c).collect();
    (player_card, npc_cards)
}

// ── NovelWorldbookProvider ────────────────────────────────────

pub struct NovelWorldbookProvider;

#[async_trait]
impl ContextProvider for NovelWorldbookProvider {
    fn id(&self) -> &'static str {
        "novel_worldbook"
    }

    fn applies(&self, _state_data: &GameStateData, manifest: &Manifest, _demand: &Demand) -> bool {
        manifest.context_providers.iter().any(|p| p == self.id()) && is_novel(manifest)
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let pool = match services.db_pool.as_ref() {
            Some(p) => p,
            None => return Ok(ContextContribution::skipped(self.id(), "no db_pool injected")),
        };
        // Python 逻辑: script_id 优先,book_id 作为 fallback。
        if services.script_id.is_none() && services.book_id.is_none() {
            return Ok(ContextContribution::skipped(
                self.id(),
                "no script_id or book_id",
            ));
        }

        let entries = match load_worldbook_entries(pool, services.script_id, services.book_id).await {
            Ok(e) => e,
            Err(exc) => {
                return Ok(ContextContribution::skipped(
                    self.id(),
                    format!("db error: {}", exc),
                ));
            }
        };
        if entries.is_empty() {
            return Ok(ContextContribution::skipped(self.id(), "no worldbook entries"));
        }

        let scan_text = build_scan_text(state_data, demand);
        let active = pick_active_worldbook(&entries, &scan_text);
        if active.is_empty() {
            return Ok(ContextContribution::skipped(
                self.id(),
                "no worldbook entries matched",
            ));
        }
        let content = active
            .iter()
            .filter_map(|e| e.get("text").and_then(|v| v.as_str()).map(|s| s.to_string()))
            .collect::<Vec<_>>()
            .join("\n\n");
        let tokens = (content.chars().count() / 2) as u32;
        let items: Vec<Value> = active.iter().map(strip_worldbook).collect();
        let layer = Layer::new("novel_worldbook", "激活世界书", content)
            .with_priority(72)
            .with_items(items);
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "novel_worldbook".to_string(),
            priority: 72,
            facts: Vec::new(),
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({
                "entries_total": entries.len(),
                "entries_active": active.len(),
                "script_id": services.script_id,
                "book_id": services.book_id,
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

/// 从 worldbook_entries 表拉世界书条目。
/// Python 逻辑: script_id 优先 (`WHERE enabled = true AND script_id = $1`)，
/// 没有 script_id 时 fallback 到 book_id (`WHERE book_id = $1 AND enabled = true`)。
async fn load_worldbook_entries(
    pool: &sqlx::PgPool,
    script_id: Option<i64>,
    book_id: Option<i64>,
) -> anyhow::Result<Vec<Value>> {
    let rows = if let Some(sid) = script_id {
        sqlx::query(
            "select title, content, keys, regex_keys, priority, token_budget, enabled \
             from worldbook_entries \
             where enabled = true and script_id = $1 \
             order by priority desc, id asc",
        )
        .bind(sid)
        .fetch_all(pool)
        .await?
    } else if let Some(bid) = book_id {
        sqlx::query(
            "select title, content, keys, regex_keys, priority, token_budget, enabled \
             from worldbook_entries \
             where book_id = $1 and enabled = true \
             order by priority desc, id asc",
        )
        .bind(bid)
        .fetch_all(pool)
        .await?
    } else {
        return Ok(Vec::new());
    };
    Ok(rows
        .into_iter()
        .map(|row| {
            let title: Option<String> = row.try_get("title").ok();
            let content: Option<String> = row.try_get("content").ok();
            let keys: Value = row
                .try_get::<Value, _>("keys")
                .or_else(|_| {
                    row.try_get::<Vec<String>, _>("keys")
                        .map(|v| Value::Array(v.into_iter().map(Value::String).collect()))
                })
                .unwrap_or(Value::Array(Vec::new()));
            let regex_keys: Value = row
                .try_get::<Value, _>("regex_keys")
                .or_else(|_| {
                    row.try_get::<Vec<String>, _>("regex_keys")
                        .map(|v| Value::Array(v.into_iter().map(Value::String).collect()))
                })
                .unwrap_or(Value::Array(Vec::new()));
            let priority: i32 = row.try_get("priority").unwrap_or(50);
            let token_budget: i32 = row.try_get("token_budget").unwrap_or(600);
            let title_s = title.unwrap_or_default();
            let content_s = content.unwrap_or_default();
            let text = if title_s.is_empty() {
                content_s.clone()
            } else {
                format!("# {}\n{}", title_s, content_s)
            };
            json!({
                "title": title_s,
                "content": content_s,
                "keys": keys,
                "regex_keys": regex_keys,
                "priority": priority,
                "token_budget": token_budget,
                "text": text,
            })
        })
        .collect())
}

fn pick_active_worldbook(entries: &[Value], scan_text: &str) -> Vec<Value> {
    let mut scored: Vec<(i32, i32, Value)> = Vec::new();
    for e in entries {
        let mut hits = 0;
        if let Some(keys) = e.get("keys").and_then(|v| v.as_array()) {
            for k in keys {
                if let Some(s) = k.as_str() {
                    if !s.is_empty() && scan_text.contains(s) {
                        hits += 1;
                    }
                }
            }
        }
        // regex_keys: 查缓存或编译 Regex 并 match;编译失败的 key 退回朴素 contains。
        if let Some(keys) = e.get("regex_keys").and_then(|v| v.as_array()) {
            for k in keys {
                if let Some(s) = k.as_str() {
                    if s.is_empty() {
                        continue;
                    }
                    let matched = {
                        let mut cache = REGEX_CACHE.lock().unwrap();
                        // 超上限时清空（简单策略）
                        if cache.len() >= 1024 {
                            cache.clear();
                        }
                        let entry = cache
                            .entry(s.to_string())
                            .or_insert_with(|| Regex::new(s).ok());
                        match entry {
                            Some(re) => re.is_match(scan_text),
                            None => scan_text.contains(s),
                        }
                    };
                    if matched {
                        hits += 1;
                    }
                }
            }
        }
        if hits > 0 {
            let pri = e
                .get("priority")
                .and_then(|v| v.as_i64())
                .unwrap_or(50) as i32;
            // ctx-13: 对齐 Python score = priority + len(matched) * 6
            let score = pri + hits * 6;
            scored.push((score, pri, e.clone()));
        }
    }
    // ctx-13: sort by (score, priority) descending,对齐 Python sort key
    scored.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.cmp(&a.1)));
    // ctx-12: Python active[:6]
    scored.into_iter().take(6).map(|(_, _, e)| e).collect()
}

fn strip_worldbook(e: &Value) -> Value {
    json!({
        "title": e.get("title").cloned().unwrap_or(Value::Null),
        "keys": e.get("keys").cloned().unwrap_or(Value::Null),
        "priority": e.get("priority").cloned().unwrap_or(Value::Null),
    })
}
