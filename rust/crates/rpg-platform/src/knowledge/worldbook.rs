//! worldbook —— 世界书条目 CRUD + `consult` 检索。
//!
//! 完成度: **主路径完整**
//! - `WorldbookEntry` 行结构
//! - `list_worldbook_entries` / `get_worldbook_entry`
//! - `upsert_worldbook_entry` / `delete_worldbook_entry`
//! - `consult(state, query)` —— 按 keys / 文本关键词命中召回条目
//!
//! 对应 Python:
//!   - `rpg/platform_app/knowledge/worldbook.py`
//!   - `rpg/platform_app/knowledge/_worldbook_repo.py`
//!   - Python 现行 consult 散落在 `context_engine.loaders._load_world` 与 `_search.py`,
//!     这里抽出一个 Rust 端可直接 GM 用的最小 consult。

use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// `worldbook_entries` 行(剧本作用域)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorldbookEntry {
    pub id: i64,
    pub script_id: i64,
    pub book_id: i64,
    pub title: String,
    #[serde(default)]
    pub keys: serde_json::Value,
    #[serde(default)]
    pub regex_keys: serde_json::Value,
    pub priority: i32,
    pub token_budget: i32,
    #[serde(default)]
    pub insertion_position: String,
    #[serde(default)]
    pub sticky_turns: i32,
    #[serde(default)]
    pub cooldown_turns: i32,
    #[serde(default)]
    pub probability: f64,
    #[serde(default)]
    pub character_filter: serde_json::Value,
    #[serde(default)]
    pub scene_filter: serde_json::Value,
    pub content: String,
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub first_chapter: Option<i32>,
    #[serde(default)]
    pub last_seen_chapter: Option<i32>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct WorldbookEntryPayload {
    #[serde(default)]
    pub id: Option<i64>,
    pub title: String,
    #[serde(default)]
    pub keys: Vec<String>,
    #[serde(default)]
    pub regex_keys: Vec<String>,
    #[serde(default = "default_priority")]
    pub priority: i32,
    #[serde(default = "default_token_budget")]
    pub token_budget: i32,
    #[serde(default = "default_insertion")]
    pub insertion_position: String,
    #[serde(default)]
    pub sticky_turns: i32,
    #[serde(default)]
    pub cooldown_turns: i32,
    #[serde(default = "default_probability")]
    pub probability: f64,
    #[serde(default)]
    pub character_filter: Vec<String>,
    #[serde(default)]
    pub scene_filter: Vec<String>,
    pub content: String,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
}

fn default_priority() -> i32 {
    90
}
fn default_token_budget() -> i32 {
    600
}
fn default_insertion() -> String {
    "worldbook".to_string()
}
fn default_probability() -> f64 {
    100.0
}
fn default_enabled() -> bool {
    true
}

/// `consult` 单条命中(向上给 GM 的最小载荷)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EntryHit {
    pub id: i64,
    pub title: String,
    pub content: String,
    pub priority: i32,
    pub score: f64,
}

fn row_to_entry(r: &sqlx::postgres::PgRow) -> Result<WorldbookEntry, sqlx::Error> {
    Ok(WorldbookEntry {
        id: r.try_get("id")?,
        script_id: r.try_get("script_id")?,
        book_id: r.try_get::<i64, _>("book_id").unwrap_or(0),
        title: r.try_get("title")?,
        keys: r
            .try_get::<Option<serde_json::Value>, _>("keys")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([])),
        regex_keys: r
            .try_get::<Option<serde_json::Value>, _>("regex_keys")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([])),
        priority: r.try_get::<i32, _>("priority").unwrap_or(0),
        token_budget: r.try_get::<i32, _>("token_budget").unwrap_or(600),
        insertion_position: r
            .try_get::<Option<String>, _>("insertion_position")
            .ok()
            .flatten()
            .unwrap_or_default(),
        sticky_turns: r.try_get::<i32, _>("sticky_turns").unwrap_or(0),
        cooldown_turns: r.try_get::<i32, _>("cooldown_turns").unwrap_or(0),
        probability: r.try_get::<f64, _>("probability").unwrap_or(100.0),
        character_filter: r
            .try_get::<Option<serde_json::Value>, _>("character_filter")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([])),
        scene_filter: r
            .try_get::<Option<serde_json::Value>, _>("scene_filter")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([])),
        content: r.try_get::<Option<String>, _>("content").ok().flatten().unwrap_or_default(),
        enabled: r.try_get::<bool, _>("enabled").unwrap_or(true),
        first_chapter: r.try_get::<Option<i32>, _>("first_chapter").ok().flatten(),
        last_seen_chapter: r.try_get::<Option<i32>, _>("last_seen_chapter").ok().flatten(),
    })
}

/// Python `_require_script` 的本地副本(避免跨模块循环)。
async fn require_script(pool: &PgPool, user_id: i64, script_id: i64) -> PlatformResult<()> {
    let row = sqlx::query("select 1 from scripts where id = $1 and owner_id = $2")
        .bind(script_id)
        .bind(user_id)
        .fetch_optional(pool)
        .await?;
    if row.is_none() {
        return Err(PlatformError::forbidden("无权访问该剧本"));
    }
    Ok(())
}

async fn fetch_book_id(pool: &PgPool, script_id: i64) -> PlatformResult<i64> {
    let row = sqlx::query("select id from books where script_id = $1")
        .bind(script_id)
        .fetch_optional(pool)
        .await?;
    match row {
        Some(r) => Ok(r.try_get::<i64, _>("id").unwrap_or(0)),
        None => Err(PlatformError::validation(
            "剧本 book 未初始化，先调一次 /api/scripts/{id}/knowledge/sync",
        )),
    }
}

// ─── CRUD ──────────────────────────────────────────────────────────────────

/// Python: `list_worldbook_entries(user_id, script_id, limit, cursor)`。
pub async fn list_worldbook_entries(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    limit: i64,
    before_id: Option<i64>,
) -> PlatformResult<(Vec<WorldbookEntry>, bool)> {
    require_script(pool, user_id, script_id).await?;
    let page_limit = limit.max(1).min(200);
    let rows = sqlx::query(
        r#"
        select * from worldbook_entries
         where script_id = $1
           and ($2::bigint is null or id < $2)
         order by priority desc, id desc
         limit $3
        "#,
    )
    .bind(script_id)
    .bind(before_id)
    .bind(page_limit + 1)
    .fetch_all(pool)
    .await?;
    let has_more = rows.len() as i64 > page_limit;
    let take = (rows.len()).min(page_limit as usize);
    let items: Result<Vec<_>, sqlx::Error> = rows.iter().take(take).map(row_to_entry).collect();
    Ok((items?, has_more))
}

/// 取单条。Python 无对应函数,这里补一条 GM 直查用。
pub async fn get_worldbook_entry(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    entry_id: i64,
) -> PlatformResult<Option<WorldbookEntry>> {
    require_script(pool, user_id, script_id).await?;
    let row = sqlx::query("select * from worldbook_entries where id = $1 and script_id = $2")
        .bind(entry_id)
        .bind(script_id)
        .fetch_optional(pool)
        .await?;
    Ok(match row {
        Some(r) => Some(row_to_entry(&r)?),
        None => None,
    })
}

/// 新增/更新。payload.id 给定就 update,否则 insert。
pub async fn upsert_worldbook_entry(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    payload: WorldbookEntryPayload,
) -> PlatformResult<WorldbookEntry> {
    let title = payload.title.trim().to_string();
    if title.is_empty() {
        return Err(PlatformError::validation("worldbook.title 不能为空"));
    }
    require_script(pool, user_id, script_id).await?;
    let book_id = fetch_book_id(pool, script_id).await?;

    let keys_json = serde_json::to_value(&payload.keys)?;
    let regex_json = serde_json::to_value(&payload.regex_keys)?;
    let char_filter_json = serde_json::to_value(&payload.character_filter)?;
    let scene_filter_json = serde_json::to_value(&payload.scene_filter)?;
    let priority = if payload.priority <= 0 { 90 } else { payload.priority };
    let token_budget = if payload.token_budget <= 0 {
        600
    } else {
        payload.token_budget
    };
    let insertion = if payload.insertion_position.is_empty() {
        "worldbook".to_string()
    } else {
        payload.insertion_position.clone()
    };

    let row = if let Some(entry_id) = payload.id {
        let owned = sqlx::query("select 1 from worldbook_entries where id = $1 and script_id = $2")
            .bind(entry_id)
            .bind(script_id)
            .fetch_optional(pool)
            .await?;
        if owned.is_none() {
            return Err(PlatformError::not_found(
                "worldbook_entry 不存在或不属于该剧本",
            ));
        }
        sqlx::query(
            r#"
            update worldbook_entries set
              title = $1, keys = $2, regex_keys = $3,
              priority = $4, token_budget = $5, insertion_position = $6,
              sticky_turns = $7, cooldown_turns = $8, probability = $9,
              character_filter = $10, scene_filter = $11, content = $12,
              enabled = $13, row_version = row_version + 1, updated_at = now()
             where id = $14 and script_id = $15
            returning *
            "#,
        )
        .bind(&title)
        .bind(&keys_json)
        .bind(&regex_json)
        .bind(priority)
        .bind(token_budget)
        .bind(&insertion)
        .bind(payload.sticky_turns)
        .bind(payload.cooldown_turns)
        .bind(payload.probability)
        .bind(&char_filter_json)
        .bind(&scene_filter_json)
        .bind(&payload.content)
        .bind(payload.enabled)
        .bind(entry_id)
        .bind(script_id)
        .fetch_one(pool)
        .await?
    } else {
        sqlx::query(
            r#"
            insert into worldbook_entries (
              book_id, script_id, title, keys, regex_keys,
              priority, token_budget, insertion_position,
              sticky_turns, cooldown_turns, probability,
              character_filter, scene_filter, content, enabled
            ) values (
              $1, $2, $3, $4, $5,
              $6, $7, $8,
              $9, $10, $11,
              $12, $13, $14, $15
            )
            returning *
            "#,
        )
        .bind(book_id)
        .bind(script_id)
        .bind(&title)
        .bind(&keys_json)
        .bind(&regex_json)
        .bind(priority)
        .bind(token_budget)
        .bind(&insertion)
        .bind(payload.sticky_turns)
        .bind(payload.cooldown_turns)
        .bind(payload.probability)
        .bind(&char_filter_json)
        .bind(&scene_filter_json)
        .bind(&payload.content)
        .bind(payload.enabled)
        .fetch_one(pool)
        .await?
    };
    Ok(row_to_entry(&row)?)
}

/// 删除条目。
pub async fn delete_worldbook_entry(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    entry_id: i64,
) -> PlatformResult<bool> {
    require_script(pool, user_id, script_id).await?;
    let res = sqlx::query("delete from worldbook_entries where id = $1 and script_id = $2")
        .bind(entry_id)
        .bind(script_id)
        .execute(pool)
        .await?;
    Ok(res.rows_affected() > 0)
}

// ─── consult ───────────────────────────────────────────────────────────────

/// State context — `consult` 在调用方提供的最小上下文。
///
/// 不绑死到任何 state 类型,GM 端可以从 `rpg_state::State` 直接组装。
#[derive(Debug, Clone, Default)]
pub struct ConsultState {
    pub script_id: i64,
    pub chapter_min: Option<i32>,
    pub chapter_max: Option<i32>,
    pub current_character: Option<String>,
    pub current_scene: Option<String>,
}

/// 把世界书 keys (JSON array of string) 摊平为 Vec<String>。
fn json_keys_to_vec(v: &serde_json::Value) -> Vec<String> {
    v.as_array()
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

/// `consult(state, query)` —— 检索世界书条目。
///
/// 实现策略(rpg-retrieval crate 的轻量版):
/// 1. 从 query 切 2-gram + 词;同时把 entry.keys / title 当字典
/// 2. 命中数 + priority 加权打分
/// 3. 按 chapter_min/max 硬过滤(防剧透,task 52 关键)
/// 4. 按 character/scene filter 过滤(空则默认全通过)
pub async fn consult(
    pool: &PgPool,
    state: &ConsultState,
    query: &str,
    top_k: usize,
) -> PlatformResult<Vec<EntryHit>> {
    if query.trim().is_empty() {
        return Ok(Vec::new());
    }

    // 拉所有 enabled + 时间线过滤后的条目(剧本通常 ~几十条,全捞内存里筛性价比高)
    let rows = sqlx::query(
        r#"
        select id, title, content, priority, keys, character_filter, scene_filter,
               first_chapter
          from worldbook_entries
         where script_id = $1
           and enabled = true
           and ($2::integer is null or first_chapter is null or first_chapter <= $2)
        "#,
    )
    .bind(state.script_id)
    .bind(state.chapter_max)
    .fetch_all(pool)
    .await?;

    // 2-gram tokens(rpg-retrieval 风格)
    let tokens = rpg_retrieval::bm25_tokens(query);
    let query_lower = query.to_lowercase();

    let mut hits: Vec<EntryHit> = Vec::with_capacity(rows.len());
    for row in &rows {
        let id: i64 = row.try_get("id")?;
        let title: String = row.try_get::<Option<String>, _>("title").ok().flatten().unwrap_or_default();
        let content: String =
            row.try_get::<Option<String>, _>("content").ok().flatten().unwrap_or_default();
        let priority: i32 = row.try_get::<i32, _>("priority").unwrap_or(0);
        let keys_v = row
            .try_get::<Option<serde_json::Value>, _>("keys")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([]));
        let keys = json_keys_to_vec(&keys_v);
        let char_filter_v = row
            .try_get::<Option<serde_json::Value>, _>("character_filter")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([]));
        let scene_filter_v = row
            .try_get::<Option<serde_json::Value>, _>("scene_filter")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([]));

        // character_filter 非空 → 必须命中当前角色
        let char_filter = json_keys_to_vec(&char_filter_v);
        if !char_filter.is_empty() {
            match &state.current_character {
                Some(c) => {
                    if !char_filter.iter().any(|x| x == c) {
                        continue;
                    }
                }
                None => continue,
            }
        }
        // scene_filter 同理
        let scene_filter = json_keys_to_vec(&scene_filter_v);
        if !scene_filter.is_empty() {
            match &state.current_scene {
                Some(s) => {
                    if !scene_filter.iter().any(|x| x == s) {
                        continue;
                    }
                }
                None => continue,
            }
        }

        // 评分:keys / title 命中 + bm25 tokens 命中
        let mut score: f64 = 0.0;
        for k in &keys {
            if !k.is_empty() && query_lower.contains(&k.to_lowercase()) {
                score += 3.0; // key 命中权重最高
            }
        }
        if !title.is_empty() && query_lower.contains(&title.to_lowercase()) {
            score += 5.0;
        }
        for t in &tokens {
            if title.contains(t.as_str()) {
                score += 1.5;
            }
            if content.contains(t.as_str()) {
                score += 0.6;
            }
        }
        if score <= 0.0 {
            continue;
        }
        // priority 微调(0~100 范围)
        score += priority as f64 * 0.01;

        hits.push(EntryHit {
            id,
            title,
            content,
            priority,
            score,
        });
    }
    hits.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    hits.truncate(top_k.max(1));
    Ok(hits)
}
