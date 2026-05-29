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

        let mut anchor: Value = Value::Null;
        if let Some(filter) = services.timeline_filter_fn.as_ref() {
            if !label.is_empty() {
                anchor = filter(&label).unwrap_or_else(|exc| json!({ "error": exc.to_string() }));
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
        if let Some(_embed) = services.embed_fn.as_ref() {
            // TODO[接入]: 向量检索逻辑见上方注释。embed_fn 已注入时在此处实现。
            tracing::debug!("novel_retrieval: embed_fn 已注入但向量召回尚未实现,跳过");
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
        let book_id = match services.book_id {
            Some(b) => b,
            None => {
                return Ok(ContextContribution::skipped(self.id(), "no book_id"));
            }
        };

        let cards = match load_character_cards(pool, book_id).await {
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
        let scan_text = build_scan_text(state_data, demand);

        let (player_card, npc_cards) = pick_active_cards(&cards, &scan_text, &player_name);

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
                "book_id": book_id,
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

/// 从 character_cards 表拉一本书的全部角色卡。
async fn load_character_cards(pool: &sqlx::PgPool, book_id: i64) -> anyhow::Result<Vec<Value>> {
    let rows = sqlx::query(
        "select name, aliases, identity, appearance, personality, \
                speech_style, current_status, secrets, token_budget, priority \
         from character_cards \
         where book_id = $1 \
         order by priority desc",
    )
    .bind(book_id)
    .fetch_all(pool)
    .await?;
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
            let token_budget: i32 = row.try_get("token_budget").unwrap_or(450);
            let priority: i32 = row.try_get("priority").unwrap_or(100);
            let name_s = name.unwrap_or_default();
            let text = render_card_text(
                &name_s,
                identity.as_deref().unwrap_or(""),
                appearance.as_deref().unwrap_or(""),
                personality.as_deref().unwrap_or(""),
                speech_style.as_deref().unwrap_or(""),
                current_status.as_deref().unwrap_or(""),
                secrets.as_deref().unwrap_or(""),
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
                "token_budget": token_budget,
                "priority": priority,
                "text": text,
            })
        })
        .collect())
}

fn render_card_text(
    name: &str,
    identity: &str,
    appearance: &str,
    personality: &str,
    speech: &str,
    status: &str,
    secrets: &str,
) -> String {
    let mut parts: Vec<String> = Vec::new();
    parts.push(format!("# {}", name));
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
        parts.push(format!("语气：{}", speech.trim()));
    }
    if !status.trim().is_empty() {
        parts.push(format!("当前状态：{}", status.trim()));
    }
    if !secrets.trim().is_empty() {
        parts.push(format!("秘密（GM 私有）：{}", secrets.trim()));
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

/// 简单 NPC 卡选:先挑 player_card(按 player_name 匹配),再按 scan_text 命中数挑前 6 个 NPC。
fn pick_active_cards(
    cards: &[Value],
    scan_text: &str,
    player_name: &str,
) -> (Option<Value>, Vec<Value>) {
    let player_card = cards
        .iter()
        .find(|c| {
            c.get("name")
                .and_then(|v| v.as_str())
                .map(|n| !player_name.is_empty() && n == player_name)
                .unwrap_or(false)
        })
        .cloned();

    let mut scored: Vec<(i32, Value)> = Vec::new();
    for card in cards {
        let name = card.get("name").and_then(|v| v.as_str()).unwrap_or("");
        if name.is_empty() || (!player_name.is_empty() && name == player_name) {
            continue;
        }
        let mut score: i32 = 0;
        if scan_text.contains(name) {
            score += 10;
        }
        if let Some(aliases) = card.get("aliases").and_then(|v| v.as_array()) {
            for a in aliases {
                if let Some(s) = a.as_str() {
                    if !s.is_empty() && scan_text.contains(s) {
                        score += 5;
                    }
                }
            }
        }
        if score > 0 {
            scored.push((score, card.clone()));
        }
    }
    scored.sort_by_key(|b| std::cmp::Reverse(b.0));
    let npc_cards: Vec<Value> = scored.into_iter().take(6).map(|(_, c)| c).collect();
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
        let book_id = match services.book_id {
            Some(b) => b,
            None => return Ok(ContextContribution::skipped(self.id(), "no book_id")),
        };

        let entries = match load_worldbook_entries(pool, book_id).await {
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
                "book_id": book_id,
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

async fn load_worldbook_entries(pool: &sqlx::PgPool, book_id: i64) -> anyhow::Result<Vec<Value>> {
    let rows = sqlx::query(
        "select title, content, keys, regex_keys, priority, token_budget, enabled \
         from worldbook_entries \
         where book_id = $1 and enabled = true \
         order by priority desc",
    )
    .bind(book_id)
    .fetch_all(pool)
    .await?;
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
    let mut scored: Vec<(i32, Value)> = Vec::new();
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
            scored.push((hits * 100 + pri, e.clone()));
        }
    }
    scored.sort_by_key(|b| std::cmp::Reverse(b.0));
    scored.into_iter().take(8).map(|(_, e)| e).collect()
}

fn strip_worldbook(e: &Value) -> Value {
    json!({
        "title": e.get("title").cloned().unwrap_or(Value::Null),
        "keys": e.get("keys").cloned().unwrap_or(Value::Null),
        "priority": e.get("priority").cloned().unwrap_or(Value::Null),
    })
}
