//! character_cards —— 剧本角色卡 CRUD + Tavern V1/V2 兼容。
//!
//! 完成度: **主路径完整**
//! - `CharacterCard` struct(对应 `character_cards` 表)
//! - `list_character_cards` / `get_character_card` / `upsert_character_card`
//! - `delete_character_card` / `set_character_card_enabled`
//! - `import_tavern_v2` —— SillyTavern V1/V2 JSON 解析 + 字段映射
//!
//! 对应 Python:
//!   - `rpg/platform_app/knowledge/character_cards.py`
//!   - `rpg/platform_app/knowledge/_character_cards_repo.py`
//!   - `rpg/platform_app/tavern_cards.py` (parse_card + tavern_to_user_card)
//!
//! TODO:
//!   - chapter_facts 列表(`list_chapter_facts`)— 见 retrieval 模块
//!   - PNG tEXt chunk 解析(`parse_png_card`)— 等 image crate 接入

use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// `character_cards` 表行(剧本作用域)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CharacterCard {
    pub id: i64,
    pub script_id: i64,
    pub book_id: i64,
    pub name: String,
    #[serde(default)]
    pub aliases: serde_json::Value,
    #[serde(default)]
    pub identity: String,
    #[serde(default)]
    pub appearance: String,
    #[serde(default)]
    pub personality: String,
    #[serde(default)]
    pub speech_style: String,
    #[serde(default)]
    pub current_status: String,
    #[serde(default)]
    pub secrets: String,
    #[serde(default)]
    pub sample_dialogue: serde_json::Value,
    pub token_budget: i32,
    pub priority: i32,
    pub enabled: bool,
    #[serde(default)]
    pub metadata: serde_json::Value,
    #[serde(default)]
    pub first_chapter: Option<i32>,
    #[serde(default)]
    pub last_seen_chapter: Option<i32>,
}

/// Python: `upsert_character_card` 的入参 dict。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CharacterCardPayload {
    #[serde(default)]
    pub id: Option<i64>,
    pub name: String,
    #[serde(default)]
    pub aliases: Vec<String>,
    #[serde(default)]
    pub identity: String,
    #[serde(default)]
    pub appearance: String,
    #[serde(default)]
    pub personality: String,
    #[serde(default)]
    pub speech_style: String,
    #[serde(default)]
    pub current_status: String,
    #[serde(default)]
    pub secrets: String,
    #[serde(default)]
    pub sample_dialogue: Vec<String>,
    #[serde(default = "default_token_budget")]
    pub token_budget: i32,
    #[serde(default = "default_priority")]
    pub priority: i32,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

fn default_token_budget() -> i32 {
    450
}
fn default_priority() -> i32 {
    100
}
fn default_enabled() -> bool {
    true
}

// ─── helpers ───────────────────────────────────────────────────────────────

fn row_to_card(r: &sqlx::postgres::PgRow) -> Result<CharacterCard, sqlx::Error> {
    Ok(CharacterCard {
        id: r.try_get("id")?,
        script_id: r.try_get("script_id")?,
        book_id: r.try_get::<i64, _>("book_id").unwrap_or(0),
        name: r.try_get("name")?,
        aliases: r
            .try_get::<Option<serde_json::Value>, _>("aliases")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([])),
        identity: r.try_get::<Option<String>, _>("identity").ok().flatten().unwrap_or_default(),
        appearance: r
            .try_get::<Option<String>, _>("appearance")
            .ok()
            .flatten()
            .unwrap_or_default(),
        personality: r
            .try_get::<Option<String>, _>("personality")
            .ok()
            .flatten()
            .unwrap_or_default(),
        speech_style: r
            .try_get::<Option<String>, _>("speech_style")
            .ok()
            .flatten()
            .unwrap_or_default(),
        current_status: r
            .try_get::<Option<String>, _>("current_status")
            .ok()
            .flatten()
            .unwrap_or_default(),
        secrets: r.try_get::<Option<String>, _>("secrets").ok().flatten().unwrap_or_default(),
        sample_dialogue: r
            .try_get::<Option<serde_json::Value>, _>("sample_dialogue")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!([])),
        token_budget: r.try_get::<i32, _>("token_budget").unwrap_or(450),
        priority: r.try_get::<i32, _>("priority").unwrap_or(100),
        enabled: r.try_get::<bool, _>("enabled").unwrap_or(true),
        metadata: r
            .try_get::<Option<serde_json::Value>, _>("metadata")
            .ok()
            .flatten()
            .unwrap_or(serde_json::json!({})),
        first_chapter: r.try_get::<Option<i32>, _>("first_chapter").ok().flatten(),
        last_seen_chapter: r.try_get::<Option<i32>, _>("last_seen_chapter").ok().flatten(),
    })
}

/// 校验 script 属于 user。对应 Python `_require_script`。
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

/// Python: `list_character_cards(user_id, script_id, limit, cursor)`。
///
/// cursor 为 `before_id`(基于 priority,id desc 游标分页)。
/// 返回 `(items, has_more)`,调用方负责拼 `page_payload`。
pub async fn list_character_cards(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    limit: i64,
    before_id: Option<i64>,
) -> PlatformResult<(Vec<CharacterCard>, bool)> {
    require_script(pool, user_id, script_id).await?;
    let page_limit = limit.clamp(1, 200);
    let rows = sqlx::query(
        r#"
        select * from character_cards
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
    let items: Result<Vec<_>, sqlx::Error> = rows.iter().take(take).map(row_to_card).collect();
    Ok((items?, has_more))
}

/// Python: `get_character_card(user_id, script_id, card_id)`。
pub async fn get_character_card(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    card_id: i64,
) -> PlatformResult<Option<CharacterCard>> {
    require_script(pool, user_id, script_id).await?;
    let row = sqlx::query("select * from character_cards where id = $1 and script_id = $2")
        .bind(card_id)
        .bind(script_id)
        .fetch_optional(pool)
        .await?;
    Ok(match row {
        Some(r) => Some(row_to_card(&r)?),
        None => None,
    })
}

/// Python: `upsert_character_card(user_id, script_id, payload)`。
///
/// payload.id 给定就 update,否则 insert。
pub async fn upsert_character_card(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    payload: CharacterCardPayload,
) -> PlatformResult<CharacterCard> {
    let name = payload.name.trim().to_string();
    if name.is_empty() {
        return Err(PlatformError::validation("character.name 不能为空"));
    }
    require_script(pool, user_id, script_id).await?;
    let book_id = fetch_book_id(pool, script_id).await?;

    let aliases_json = serde_json::to_value(&payload.aliases)?;
    let sample_dialogue_json = serde_json::to_value(&payload.sample_dialogue)?;
    let metadata_json = if payload.metadata.is_null() {
        serde_json::json!({})
    } else {
        payload.metadata
    };
    let identity = payload.identity.trim().to_string();
    let appearance = payload.appearance.trim().to_string();
    let personality = payload.personality.trim().to_string();
    let speech_style = payload.speech_style.trim().to_string();
    let current_status = payload.current_status.trim().to_string();
    let secrets = payload.secrets.trim().to_string();
    let token_budget = if payload.token_budget <= 0 { 450 } else { payload.token_budget };
    let priority = if payload.priority <= 0 { 100 } else { payload.priority };

    let row = if let Some(card_id) = payload.id {
        let owned = sqlx::query("select 1 from character_cards where id = $1 and script_id = $2")
            .bind(card_id)
            .bind(script_id)
            .fetch_optional(pool)
            .await?;
        if owned.is_none() {
            return Err(PlatformError::not_found("character_card 不存在或不属于该剧本"));
        }
        sqlx::query(
            r#"
            update character_cards set
              name = $1, aliases = $2,
              identity = $3, appearance = $4,
              personality = $5, speech_style = $6,
              current_status = $7, secrets = $8,
              sample_dialogue = $9, token_budget = $10,
              priority = $11, enabled = $12, metadata = $13,
              row_version = row_version + 1, updated_at = now()
             where id = $14 and script_id = $15
            returning *
            "#,
        )
        .bind(&name)
        .bind(&aliases_json)
        .bind(&identity)
        .bind(&appearance)
        .bind(&personality)
        .bind(&speech_style)
        .bind(&current_status)
        .bind(&secrets)
        .bind(&sample_dialogue_json)
        .bind(token_budget)
        .bind(priority)
        .bind(payload.enabled)
        .bind(&metadata_json)
        .bind(card_id)
        .bind(script_id)
        .fetch_one(pool)
        .await?
    } else {
        sqlx::query(
            r#"
            insert into character_cards (
              book_id, script_id, name, aliases, identity, appearance, personality,
              speech_style, current_status, secrets, sample_dialogue,
              token_budget, priority, enabled, metadata
            ) values (
              $1, $2, $3, $4, $5, $6, $7,
              $8, $9, $10, $11,
              $12, $13, $14, $15
            )
            returning *
            "#,
        )
        .bind(book_id)
        .bind(script_id)
        .bind(&name)
        .bind(&aliases_json)
        .bind(&identity)
        .bind(&appearance)
        .bind(&personality)
        .bind(&speech_style)
        .bind(&current_status)
        .bind(&secrets)
        .bind(&sample_dialogue_json)
        .bind(token_budget)
        .bind(priority)
        .bind(payload.enabled)
        .bind(&metadata_json)
        .fetch_one(pool)
        .await?
    };
    Ok(row_to_card(&row)?)
}

/// Python: `delete_character_card(user_id, script_id, card_id)`。
pub async fn delete_character_card(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    card_id: i64,
) -> PlatformResult<bool> {
    require_script(pool, user_id, script_id).await?;
    let res = sqlx::query("delete from character_cards where id = $1 and script_id = $2")
        .bind(card_id)
        .bind(script_id)
        .execute(pool)
        .await?;
    Ok(res.rows_affected() > 0)
}

/// Python: `set_character_card_enabled(user_id, script_id, card_id, enabled)`。
pub async fn set_character_card_enabled(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    card_id: i64,
    enabled: bool,
) -> PlatformResult<CharacterCard> {
    require_script(pool, user_id, script_id).await?;
    let row = sqlx::query(
        r#"
        update character_cards
           set enabled = $1, row_version = row_version + 1, updated_at = now()
         where id = $2 and script_id = $3
        returning *
        "#,
    )
    .bind(enabled)
    .bind(card_id)
    .bind(script_id)
    .fetch_optional(pool)
    .await?;
    let row = row.ok_or_else(|| PlatformError::not_found("character_card 不存在"))?;
    Ok(row_to_card(&row)?)
}

// ─── Tavern V1/V2 import ────────────────────────────────────────────────────

/// Python: `tavern_cards.parse_card` —— V1 扁平 / V2 spec+data 双格式吃入。
///
/// 输入是 raw JSON value(已经解析了 base64/PNG 外壳),
/// 返回标准化的 V2 dict(`spec` / `spec_version` / `data` 三层)。
pub fn parse_tavern_card(raw: serde_json::Value) -> PlatformResult<serde_json::Value> {
    let obj = raw
        .as_object()
        .ok_or_else(|| PlatformError::validation("角色卡必须是 JSON object"))?;
    let spec = obj.get("spec").and_then(|v| v.as_str()).unwrap_or("");
    if spec == "chara_card_v2" || spec == "chara_card_v3" {
        normalize_v2(&raw)
    } else {
        v1_to_v2(&raw)
    }
}

fn normalize_v2(card: &serde_json::Value) -> PlatformResult<serde_json::Value> {
    let d = card.get("data").cloned().unwrap_or(serde_json::json!({}));
    let g = |k: &str| d.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let arr = |k: &str| d.get(k).cloned().unwrap_or(serde_json::json!([]));
    let obj = |k: &str| d.get(k).cloned().unwrap_or(serde_json::json!({}));
    let name = g("name");
    let name = name.trim();
    if name.is_empty() {
        return Err(PlatformError::validation("角色卡缺少 name"));
    }
    Ok(serde_json::json!({
        "spec": card.get("spec").and_then(|v| v.as_str()).unwrap_or("chara_card_v2"),
        "spec_version": card.get("spec_version").and_then(|v| v.as_str()).unwrap_or("2.0"),
        "data": {
            "name": name,
            "description": g("description"),
            "personality": g("personality"),
            "scenario": g("scenario"),
            "first_mes": g("first_mes"),
            "mes_example": g("mes_example"),
            "creator_notes": g("creator_notes"),
            "system_prompt": g("system_prompt"),
            "post_history_instructions": g("post_history_instructions"),
            "alternate_greetings": arr("alternate_greetings"),
            "tags": arr("tags"),
            "creator": g("creator"),
            "character_version": g("character_version"),
            "extensions": obj("extensions"),
            "character_book": d.get("character_book").cloned().unwrap_or(serde_json::Value::Null),
        }
    }))
}

fn v1_to_v2(card: &serde_json::Value) -> PlatformResult<serde_json::Value> {
    let g = |k: &str| card.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let pick = |k1: &str, k2: &str| {
        let v = g(k1);
        if v.is_empty() {
            g(k2)
        } else {
            v
        }
    };
    let name = pick("name", "char_name");
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err(PlatformError::validation("V1 角色卡缺少 name"));
    }
    let wrapped = serde_json::json!({
        "spec": "chara_card_v1",
        "spec_version": "1.0",
        "data": {
            "name": name,
            "description": pick("description", "char_persona"),
            "personality": g("personality"),
            "scenario": pick("scenario", "world_scenario"),
            "first_mes": pick("first_mes", "char_greeting"),
            "mes_example": pick("mes_example", "example_dialogue"),
            "creator": g("creator"),
            "character_version": if g("character_version").is_empty() {
                "1.0".to_string()
            } else {
                g("character_version")
            },
            "tags": card.get("tags").cloned().unwrap_or(serde_json::json!([])),
        }
    });
    normalize_v2(&wrapped)
}

/// 把标准化 V2 卡映射到 `CharacterCardPayload`(对应 Python `tavern_to_user_card`)。
pub fn tavern_v2_to_payload(card_v2: &serde_json::Value) -> PlatformResult<CharacterCardPayload> {
    let d = card_v2
        .get("data")
        .ok_or_else(|| PlatformError::validation("V2 角色卡缺少 data 字段"))?;
    let g = |k: &str| d.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();

    // mes_example -> 切 <START>/---,提取 {{char}}: 后段(最多 4 条)
    let mes_example = g("mes_example");
    let mut samples: Vec<String> = Vec::new();
    let blocks: Vec<&str> = mes_example
        .split(['<', '-'])
        .filter(|s| !s.trim().is_empty())
        .collect();
    'outer: for chunk in blocks {
        for line in chunk.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            // 提取 {{char}}: 后内容
            if let Some(rest) = line.split_once("{{char}}:") {
                let val = rest.1.trim().to_string();
                if !val.is_empty() {
                    samples.push(val);
                    if samples.len() >= 4 {
                        break 'outer;
                    }
                }
            }
        }
        if !samples.is_empty() {
            break;
        }
    }

    let metadata = serde_json::json!({
        "tavern_imported": true,
        "scenario": g("scenario"),
        "first_mes": g("first_mes"),
        "alternate_greetings": d.get("alternate_greetings").cloned().unwrap_or(serde_json::json!([])),
        "creator_notes": g("creator_notes"),
        "system_prompt": g("system_prompt"),
        "post_history_instructions": g("post_history_instructions"),
        "creator": g("creator"),
        "character_version": g("character_version"),
        "extensions": d.get("extensions").cloned().unwrap_or(serde_json::json!({})),
        "character_book": d.get("character_book").cloned().unwrap_or(serde_json::Value::Null),
        "spec": card_v2.get("spec").cloned().unwrap_or(serde_json::Value::Null),
        "spec_version": card_v2.get("spec_version").cloned().unwrap_or(serde_json::Value::Null),
    });

    let description = g("description");
    let description = if description.len() > 2000 {
        description.chars().take(2000).collect()
    } else {
        description
    };
    let personality = g("personality");
    let personality = if personality.len() > 1500 {
        personality.chars().take(1500).collect()
    } else {
        personality
    };

    Ok(CharacterCardPayload {
        id: None,
        name: g("name"),
        aliases: Vec::new(),
        identity: description,
        appearance: String::new(),
        personality,
        speech_style: String::new(),
        current_status: String::new(),
        secrets: String::new(),
        sample_dialogue: samples,
        token_budget: 450,
        priority: 100,
        enabled: true,
        metadata,
    })
}

/// 顶层入口:吃 raw JSON 解析为 payload 后落库到指定剧本。
///
/// 对应 Python:`parse_card -> tavern_to_user_card -> upsert_character_card`,
/// 但目标表是剧本作用域的 `character_cards`(不是 `user_character_cards`)。
pub async fn import_tavern_v2(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    raw: serde_json::Value,
) -> PlatformResult<CharacterCard> {
    let v2 = parse_tavern_card(raw)?;
    let payload = tavern_v2_to_payload(&v2)?;
    upsert_character_card(pool, user_id, script_id, payload).await
}
