//! user_cards —— 用户级 persona / character card CRUD。
//!
//! 对应 Python: `rpg/platform_app/user_cards.py`。
//!
//! 两个独立资源:
//! - `user_personas`        玩家身份卡
//! - `user_character_cards` 用户自创 NPC 卡
//!
//! 所有接口严格按 `user_id` 隔离。

use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

static SLUG_NON_ALNUM: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[^0-9A-Za-z_\u{4e00}-\u{9fff}]+").unwrap());
static LIST_SPLIT: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[,，;;、]").unwrap());

fn slugify(text: &str) -> String {
    let cleaned = SLUG_NON_ALNUM.replace_all(text.trim(), "-");
    let cleaned = cleaned.trim_matches('-');
    let s: String = cleaned.chars().take(80).collect();
    if s.is_empty() {
        "untitled".to_string()
    } else {
        s
    }
}

fn normalize_list(value: &Value) -> Vec<Value> {
    match value {
        Value::Array(a) => a.clone(),
        Value::Null => vec![],
        Value::String(s) => {
            if s.is_empty() {
                vec![]
            } else {
                LIST_SPLIT
                    .split(s)
                    .map(|p| p.trim())
                    .filter(|p| !p.is_empty())
                    .map(|p| Value::String(p.to_string()))
                    .collect()
            }
        }
        other => vec![other.clone()],
    }
}

fn pick_str(payload: &Value, key: &str) -> String {
    payload
        .get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

fn pick_i64(payload: &Value, key: &str, default: i64) -> i64 {
    payload.get(key).and_then(|v| v.as_i64()).unwrap_or(default)
}

fn pick_bool(payload: &Value, key: &str, default: bool) -> bool {
    payload
        .get(key)
        .and_then(|v| v.as_bool())
        .unwrap_or(default)
}

// ─── PERSONAS ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PersonaRow {
    pub id: i64,
    pub user_id: i64,
    pub slug: String,
    pub name: String,
    pub role: String,
    pub background: String,
    pub appearance: String,
    pub personality: String,
    pub avatar_path: String,
    pub tags: Value,
    pub metadata: Value,
    pub is_default: bool,
    pub row_version: i64,
}

fn persona_from_row(row: &sqlx::postgres::PgRow) -> sqlx::Result<PersonaRow> {
    Ok(PersonaRow {
        id: row.try_get("id")?,
        user_id: row.try_get("user_id")?,
        slug: row.try_get::<String, _>("slug").unwrap_or_default(),
        name: row.try_get::<String, _>("name").unwrap_or_default(),
        role: row.try_get::<String, _>("role").unwrap_or_default(),
        background: row.try_get::<String, _>("background").unwrap_or_default(),
        appearance: row.try_get::<String, _>("appearance").unwrap_or_default(),
        personality: row.try_get::<String, _>("personality").unwrap_or_default(),
        avatar_path: row.try_get::<String, _>("avatar_path").unwrap_or_default(),
        tags: row.try_get::<Value, _>("tags").unwrap_or(Value::Array(vec![])),
        metadata: row
            .try_get::<Value, _>("metadata")
            .unwrap_or(Value::Object(Default::default())),
        is_default: row.try_get::<bool, _>("is_default").unwrap_or(false),
        row_version: row.try_get::<i64, _>("row_version").unwrap_or(1),
    })
}

/// 列出 user 所有 persona。
pub async fn list_personas(pool: &PgPool, user_id: i64) -> PlatformResult<Vec<PersonaRow>> {
    let rows = sqlx::query(
        "select * from user_personas where user_id = $1 \
         order by is_default desc, updated_at desc, id desc",
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(persona_from_row)
        .collect::<Result<Vec<_>, _>>()
        .map_err(Into::into)
}

pub async fn get_persona(
    pool: &PgPool,
    user_id: i64,
    persona_id: i64,
) -> PlatformResult<Option<PersonaRow>> {
    let row = sqlx::query("select * from user_personas where id = $1 and user_id = $2")
        .bind(persona_id)
        .bind(user_id)
        .fetch_optional(pool)
        .await?;
    Ok(row.map(|r| persona_from_row(&r)).transpose()?)
}

pub async fn upsert_persona(
    pool: &PgPool,
    user_id: i64,
    payload: &Value,
) -> PlatformResult<PersonaRow> {
    let name = pick_str(payload, "name");
    if name.is_empty() {
        return Err(PlatformError::validation("persona.name 不能为空"));
    }
    let persona_id = payload.get("id").and_then(|v| v.as_i64());
    let slug = {
        let s = pick_str(payload, "slug");
        if s.is_empty() { slugify(&name) } else { s }
    };
    let is_default = pick_bool(payload, "is_default", false);
    let role = pick_str(payload, "role");
    let background = pick_str(payload, "background");
    let appearance = pick_str(payload, "appearance");
    let personality = pick_str(payload, "personality");
    let avatar_path = pick_str(payload, "avatar_path");
    let tags = Value::Array(normalize_list(payload.get("tags").unwrap_or(&Value::Null)));
    let metadata = payload
        .get("metadata")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));

    let row = if let Some(pid) = persona_id {
        let owned = sqlx::query("select 1 as ok from user_personas where id = $1 and user_id = $2")
            .bind(pid)
            .bind(user_id)
            .fetch_optional(pool)
            .await?;
        if owned.is_none() {
            return Err(PlatformError::not_found("persona 不存在或无权访问"));
        }
        sqlx::query(
            "update user_personas set \
                name = $1, slug = $2, role = $3, background = $4, appearance = $5, \
                personality = $6, avatar_path = $7, tags = $8, metadata = $9, \
                is_default = $10, row_version = row_version + 1, updated_at = now() \
             where id = $11 and user_id = $12 \
             returning *",
        )
        .bind(&name)
        .bind(&slug)
        .bind(&role)
        .bind(&background)
        .bind(&appearance)
        .bind(&personality)
        .bind(&avatar_path)
        .bind(&tags)
        .bind(&metadata)
        .bind(is_default)
        .bind(pid)
        .bind(user_id)
        .fetch_one(pool)
        .await?
    } else {
        sqlx::query(
            "insert into user_personas(\
                user_id, slug, name, role, background, appearance, personality, \
                avatar_path, tags, metadata, is_default \
             ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) \
             on conflict(user_id, slug) do update set \
                name = excluded.name, role = excluded.role, \
                background = excluded.background, appearance = excluded.appearance, \
                personality = excluded.personality, avatar_path = excluded.avatar_path, \
                tags = excluded.tags, metadata = excluded.metadata, \
                is_default = excluded.is_default, \
                row_version = user_personas.row_version + 1, updated_at = now() \
             returning *",
        )
        .bind(user_id)
        .bind(&slug)
        .bind(&name)
        .bind(&role)
        .bind(&background)
        .bind(&appearance)
        .bind(&personality)
        .bind(&avatar_path)
        .bind(&tags)
        .bind(&metadata)
        .bind(is_default)
        .fetch_one(pool)
        .await?
    };
    let persona = persona_from_row(&row)?;
    if is_default {
        sqlx::query(
            "update user_personas set is_default = false where user_id = $1 and id <> $2",
        )
        .bind(user_id)
        .bind(persona.id)
        .execute(pool)
        .await?;
    }
    Ok(persona)
}

pub async fn delete_persona(
    pool: &PgPool,
    user_id: i64,
    persona_id: i64,
) -> PlatformResult<bool> {
    let res = sqlx::query("delete from user_personas where id = $1 and user_id = $2")
        .bind(persona_id)
        .bind(user_id)
        .execute(pool)
        .await?;
    Ok(res.rows_affected() > 0)
}

// ─── CHARACTER CARDS ───────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserCardRow {
    pub id: i64,
    pub user_id: i64,
    pub slug: String,
    pub name: String,
    pub aliases: Value,
    pub identity: String,
    pub appearance: String,
    pub personality: String,
    pub speech_style: String,
    pub current_status: String,
    pub secrets: String,
    pub sample_dialogue: Value,
    pub tags: Value,
    pub metadata: Value,
    pub token_budget: i32,
    pub priority: i32,
    pub enabled: bool,
    pub scope: String,
    pub row_version: i64,
    /// 迁移 020 新增:scope='public' 卡被 retrieval 命中的累计次数。
    #[serde(default)]
    pub public_retrieval_count: i64,
}

fn card_from_row(row: &sqlx::postgres::PgRow) -> sqlx::Result<UserCardRow> {
    Ok(UserCardRow {
        id: row.try_get("id")?,
        user_id: row.try_get("user_id")?,
        slug: row.try_get::<String, _>("slug").unwrap_or_default(),
        name: row.try_get::<String, _>("name").unwrap_or_default(),
        aliases: row.try_get::<Value, _>("aliases").unwrap_or(Value::Array(vec![])),
        identity: row.try_get::<String, _>("identity").unwrap_or_default(),
        appearance: row.try_get::<String, _>("appearance").unwrap_or_default(),
        personality: row.try_get::<String, _>("personality").unwrap_or_default(),
        speech_style: row.try_get::<String, _>("speech_style").unwrap_or_default(),
        current_status: row.try_get::<String, _>("current_status").unwrap_or_default(),
        secrets: row.try_get::<String, _>("secrets").unwrap_or_default(),
        sample_dialogue: row
            .try_get::<Value, _>("sample_dialogue")
            .unwrap_or(Value::Array(vec![])),
        tags: row.try_get::<Value, _>("tags").unwrap_or(Value::Array(vec![])),
        metadata: row
            .try_get::<Value, _>("metadata")
            .unwrap_or(Value::Object(Default::default())),
        token_budget: row.try_get::<i32, _>("token_budget").unwrap_or(450),
        priority: row.try_get::<i32, _>("priority").unwrap_or(100),
        enabled: row.try_get::<bool, _>("enabled").unwrap_or(true),
        scope: row.try_get::<String, _>("scope").unwrap_or_else(|_| "private".to_string()),
        row_version: row.try_get::<i64, _>("row_version").unwrap_or(1),
        public_retrieval_count: row
            .try_get::<i64, _>("public_retrieval_count")
            .unwrap_or(0),
    })
}

pub async fn list_user_cards(
    pool: &PgPool,
    user_id: i64,
    q: Option<&str>,
    enabled_only: bool,
) -> PlatformResult<Vec<UserCardRow>> {
    // scope=public 跨用户共享:owner 看到自己的全部卡 + 所有 public 卡。
    let mut sql = String::from(
        "select * from user_character_cards where (user_id = $1 or scope = 'public')",
    );
    if enabled_only {
        sql.push_str(" and enabled = true");
    }
    let mut bind_q: Option<String> = None;
    if let Some(qstr) = q {
        if !qstr.is_empty() {
            sql.push_str(" and (lower(name) like $2 or lower(identity) like $2)");
            bind_q = Some(format!("%{}%", qstr.to_lowercase()));
        }
    }
    sql.push_str(" order by (user_id = $1) desc, priority desc, updated_at desc, id desc");
    let mut query = sqlx::query(&sql).bind(user_id);
    if let Some(like) = bind_q {
        query = query.bind(like);
    }
    let rows = query.fetch_all(pool).await?;
    rows.iter()
        .map(card_from_row)
        .collect::<Result<Vec<_>, _>>()
        .map_err(Into::into)
}

/// 仅列出 scope='public' 的卡(跨用户共享市场页)。
pub async fn list_public_user_cards(
    pool: &PgPool,
    limit: i64,
) -> PlatformResult<Vec<UserCardRow>> {
    let limit = limit.clamp(1, 500);
    let rows = sqlx::query(
        "select * from user_character_cards \
          where scope = 'public' and enabled = true \
          order by priority desc, updated_at desc, id desc \
          limit $1",
    )
    .bind(limit)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(card_from_row)
        .collect::<Result<Vec<_>, _>>()
        .map_err(Into::into)
}

pub async fn get_user_card(
    pool: &PgPool,
    user_id: i64,
    card_id: i64,
) -> PlatformResult<Option<UserCardRow>> {
    // scope=public 跨用户共享:任何 user 都能读他人公开卡(但不能改)。
    let row = sqlx::query(
        "select * from user_character_cards \
          where id = $1 and (user_id = $2 or scope = 'public')",
    )
    .bind(card_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|r| card_from_row(&r)).transpose()?)
}

pub async fn upsert_user_card(
    pool: &PgPool,
    user_id: i64,
    payload: &Value,
) -> PlatformResult<UserCardRow> {
    let name = pick_str(payload, "name");
    if name.is_empty() {
        return Err(PlatformError::validation("character.name 不能为空"));
    }
    let card_id = payload.get("id").and_then(|v| v.as_i64());
    let slug = {
        let s = pick_str(payload, "slug");
        if s.is_empty() { slugify(&name) } else { s }
    };
    let aliases = Value::Array(normalize_list(payload.get("aliases").unwrap_or(&Value::Null)));
    let identity = pick_str(payload, "identity");
    let appearance = pick_str(payload, "appearance");
    let personality = pick_str(payload, "personality");
    let speech_style = pick_str(payload, "speech_style");
    let current_status = pick_str(payload, "current_status");
    let secrets = pick_str(payload, "secrets");
    let sample_dialogue = Value::Array(normalize_list(
        payload.get("sample_dialogue").unwrap_or(&Value::Null),
    ));
    let tags = Value::Array(normalize_list(payload.get("tags").unwrap_or(&Value::Null)));
    let metadata = payload
        .get("metadata")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));
    let token_budget = pick_i64(payload, "token_budget", 450) as i32;
    let priority = pick_i64(payload, "priority", 100) as i32;
    let enabled = pick_bool(payload, "enabled", true);
    let scope_raw = pick_str(payload, "scope");
    let scope = if scope_raw.is_empty() {
        "private".to_string()
    } else {
        scope_raw
    };

    let row = if let Some(cid) = card_id {
        let owned = sqlx::query(
            "select 1 as ok from user_character_cards where id = $1 and user_id = $2",
        )
        .bind(cid)
        .bind(user_id)
        .fetch_optional(pool)
        .await?;
        if owned.is_none() {
            return Err(PlatformError::not_found("card 不存在或无权访问"));
        }
        sqlx::query(
            "update user_character_cards set \
                name = $1, slug = $2, aliases = $3, identity = $4, appearance = $5, \
                personality = $6, speech_style = $7, current_status = $8, secrets = $9, \
                sample_dialogue = $10, tags = $11, metadata = $12, \
                token_budget = $13, priority = $14, enabled = $15, scope = $16, \
                row_version = row_version + 1, updated_at = now() \
             where id = $17 and user_id = $18 returning *",
        )
        .bind(&name)
        .bind(&slug)
        .bind(&aliases)
        .bind(&identity)
        .bind(&appearance)
        .bind(&personality)
        .bind(&speech_style)
        .bind(&current_status)
        .bind(&secrets)
        .bind(&sample_dialogue)
        .bind(&tags)
        .bind(&metadata)
        .bind(token_budget)
        .bind(priority)
        .bind(enabled)
        .bind(&scope)
        .bind(cid)
        .bind(user_id)
        .fetch_one(pool)
        .await?
    } else {
        sqlx::query(
            "insert into user_character_cards(\
                user_id, slug, name, aliases, identity, appearance, personality, \
                speech_style, current_status, secrets, sample_dialogue, \
                tags, metadata, token_budget, priority, enabled, scope \
             ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17) \
             on conflict(user_id, slug) do update set \
                name = excluded.name, aliases = excluded.aliases, \
                identity = excluded.identity, appearance = excluded.appearance, \
                personality = excluded.personality, speech_style = excluded.speech_style, \
                current_status = excluded.current_status, secrets = excluded.secrets, \
                sample_dialogue = excluded.sample_dialogue, \
                tags = excluded.tags, metadata = excluded.metadata, \
                token_budget = excluded.token_budget, priority = excluded.priority, \
                enabled = excluded.enabled, scope = excluded.scope, \
                row_version = user_character_cards.row_version + 1, updated_at = now() \
             returning *",
        )
        .bind(user_id)
        .bind(&slug)
        .bind(&name)
        .bind(&aliases)
        .bind(&identity)
        .bind(&appearance)
        .bind(&personality)
        .bind(&speech_style)
        .bind(&current_status)
        .bind(&secrets)
        .bind(&sample_dialogue)
        .bind(&tags)
        .bind(&metadata)
        .bind(token_budget)
        .bind(priority)
        .bind(enabled)
        .bind(&scope)
        .fetch_one(pool)
        .await?
    };
    Ok(card_from_row(&row)?)
}

pub async fn delete_user_card(
    pool: &PgPool,
    user_id: i64,
    card_id: i64,
) -> PlatformResult<bool> {
    let res = sqlx::query("delete from user_character_cards where id = $1 and user_id = $2")
        .bind(card_id)
        .bind(user_id)
        .execute(pool)
        .await?;
    Ok(res.rows_affected() > 0)
}

/// Python `user_cards_for_retrieval`:按角色名(+ aliases)匹配,给 context_engine 用。
pub async fn user_cards_for_retrieval(
    pool: &PgPool,
    user_id: i64,
    names: &[String],
) -> PlatformResult<Vec<Value>> {
    if user_id == 0 || names.is_empty() {
        return Ok(Vec::new());
    }
    let lc: Vec<String> = names
        .iter()
        .filter(|n| !n.is_empty())
        .map(|n| n.to_lowercase())
        .collect();
    // scope=public 跨用户共享:retrieval 也吃公开卡。
    let rows = sqlx::query(
        "select * from user_character_cards \
          where (user_id = $1 or scope = 'public') and enabled = true",
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::new();
    for row in rows {
        let card = card_from_row(&row)?;
        let mut candidates: Vec<String> = vec![card.name.to_lowercase()];
        if let Value::Array(a) = &card.aliases {
            for v in a {
                if let Some(s) = v.as_str() {
                    candidates.push(s.to_lowercase());
                }
            }
        }
        let matched = lc.iter().any(|n| {
            candidates.iter().any(|c| c == n || c.contains(n) || n.contains(c))
        });
        if matched {
            out.push(serde_json::to_value(&card).unwrap_or(json!({})));
        }
    }
    Ok(out)
}

// ─── CREATE / UPDATE / SET_DEFAULT helpers ────────────────────────────
// Python upsert_persona 同时处理 create 和 update;Rust 为路由层提供具名 wrapper。

/// 创建新 persona(payload 不含 id)。
pub async fn create_persona(
    pool: &PgPool,
    user_id: i64,
    payload: &Value,
) -> PlatformResult<PersonaRow> {
    // 剥离 id,确保走 insert 路径。
    let mut p = payload.clone();
    if let Some(obj) = p.as_object_mut() {
        obj.remove("id");
    }
    upsert_persona(pool, user_id, &p).await
}

/// 更新已有 persona(payload 必须含 id)。
pub async fn update_persona(
    pool: &PgPool,
    user_id: i64,
    persona_id: i64,
    payload: &Value,
) -> PlatformResult<PersonaRow> {
    use serde_json::json;
    let mut p = payload.clone();
    if let Some(obj) = p.as_object_mut() {
        obj.insert("id".to_string(), json!(persona_id));
    }
    upsert_persona(pool, user_id, &p).await
}

/// 把指定 persona 设为 default;其余全部清零。
/// Python: `upsert_persona(user_id, {id, is_default: true})` 里的后半段。
pub async fn set_default_persona(
    pool: &PgPool,
    user_id: i64,
    persona_id: i64,
) -> PlatformResult<()> {
    // 先确认归属
    let owned = sqlx::query("select 1 as ok from user_personas where id = $1 and user_id = $2")
        .bind(persona_id)
        .bind(user_id)
        .fetch_optional(pool)
        .await?;
    if owned.is_none() {
        return Err(PlatformError::not_found("persona 不存在或无权访问"));
    }
    // 本条设 true,其余清零
    sqlx::query("update user_personas set is_default = true where id = $1 and user_id = $2")
        .bind(persona_id)
        .bind(user_id)
        .execute(pool)
        .await?;
    sqlx::query(
        "update user_personas set is_default = false where user_id = $1 and id <> $2",
    )
    .bind(user_id)
    .bind(persona_id)
    .execute(pool)
    .await?;
    Ok(())
}

// ─── PUBLIC CARD AUDIT ────────────────────────────────────────────────

/// 公开审计记录行。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CardPublicAuditRow {
    pub id: i64,
    pub card_id: i64,
    pub card_owner_id: i64,
    pub set_by_user_id: i64,
    pub scope_before: String,
    pub scope_after: String,
    pub created_at: String,
}

/// 当卡 scope 变为 public 时写一条审计日志。
/// 调用方(upsert_user_card 中 scope 从 non-public 变为 public 时)负责触发。
pub async fn record_card_public_audit(
    pool: &PgPool,
    card_id: i64,
    card_owner_id: i64,
    set_by_user_id: i64,
    scope_before: &str,
    scope_after: &str,
) -> PlatformResult<()> {
    sqlx::query(
        "insert into user_card_public_audit \
         (card_id, card_owner_id, set_by_user_id, scope_before, scope_after) \
         values ($1, $2, $3, $4, $5)",
    )
    .bind(card_id)
    .bind(card_owner_id)
    .bind(set_by_user_id)
    .bind(scope_before)
    .bind(scope_after)
    .execute(pool)
    .await?;
    Ok(())
}

/// 查询某卡的全部公开审计记录(最新在前)。
pub async fn get_card_public_audit(
    pool: &PgPool,
    card_id: i64,
) -> PlatformResult<Vec<CardPublicAuditRow>> {
    let rows = sqlx::query(
        "select id, card_id, card_owner_id, set_by_user_id, \
                scope_before, scope_after, \
                to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') as created_at \
           from user_card_public_audit \
          where card_id = $1 \
          order by created_at desc",
    )
    .bind(card_id)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(|r| {
            Ok(CardPublicAuditRow {
                id: r.try_get("id")?,
                card_id: r.try_get("card_id")?,
                card_owner_id: r.try_get("card_owner_id")?,
                set_by_user_id: r.try_get("set_by_user_id")?,
                scope_before: r.try_get::<String, _>("scope_before").unwrap_or_default(),
                scope_after: r.try_get::<String, _>("scope_after").unwrap_or_default(),
                created_at: r.try_get::<String, _>("created_at").unwrap_or_default(),
            })
        })
        .collect::<Result<Vec<_>, sqlx::Error>>()
        .map_err(Into::into)
}

/// 公开卡被 retrieval 命中一次时递增计数。
pub async fn increment_public_retrieval_count(
    pool: &PgPool,
    card_id: i64,
) -> PlatformResult<()> {
    sqlx::query(
        "update user_character_cards \
          set public_retrieval_count = public_retrieval_count + 1 \
          where id = $1 and scope = 'public'",
    )
    .bind(card_id)
    .execute(pool)
    .await?;
    Ok(())
}

/// 查询公开卡的检索命中统计(card_id → count)。
pub async fn get_public_retrieval_stats(
    pool: &PgPool,
    limit: i64,
) -> PlatformResult<Vec<(i64, String, i64)>> {
    let limit = limit.clamp(1, 200);
    let rows = sqlx::query(
        "select id, name, public_retrieval_count \
           from user_character_cards \
          where scope = 'public' \
          order by public_retrieval_count desc, updated_at desc \
          limit $1",
    )
    .bind(limit)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(|r| {
            Ok((
                r.try_get::<i64, _>("id")?,
                r.try_get::<String, _>("name").unwrap_or_default(),
                r.try_get::<i64, _>("public_retrieval_count").unwrap_or(0),
            ))
        })
        .collect::<Result<Vec<_>, sqlx::Error>>()
        .map_err(Into::into)
}

// ─── UNIT TESTS ───────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── slugify ──────────────────────────────────────────────────────────

    #[test]
    fn slugify_ascii() {
        assert_eq!(slugify("Hello World"), "Hello-World");
    }

    #[test]
    fn slugify_chinese_preserved() {
        // CJK 字符属于 \u{4e00}-\u{9fff},不被替换。
        let s = slugify("林知意");
        assert_eq!(s, "林知意");
    }

    #[test]
    fn slugify_truncates_at_80() {
        let long = "a".repeat(200);
        assert_eq!(slugify(&long).len(), 80);
    }

    #[test]
    fn slugify_empty_becomes_untitled() {
        assert_eq!(slugify(""), "untitled");
        assert_eq!(slugify("   "), "untitled");
    }

    // ── normalize_list ────────────────────────────────────────────────────

    #[test]
    fn normalize_list_from_comma_str() {
        let v = json!("foo,bar, baz");
        let out = normalize_list(&v);
        assert_eq!(out, vec![json!("foo"), json!("bar"), json!("baz")]);
    }

    #[test]
    fn normalize_list_null_empty() {
        assert!(normalize_list(&Value::Null).is_empty());
        assert!(normalize_list(&json!("")).is_empty());
    }

    #[test]
    fn normalize_list_array_passthrough() {
        let v = json!(["x", "y"]);
        let out = normalize_list(&v);
        assert_eq!(out, vec![json!("x"), json!("y")]);
    }

    // ── persona payload helpers ────────────────────────────────────────────

    #[test]
    fn persona_payload_name_required() {
        // upsert_persona 是 async + PgPool,这里只测同步字段检查路径:
        // pick_str 返回 ""  → 应触发 validation 错误。
        let payload = json!({"role": "hero"});
        let name = pick_str(&payload, "name");
        assert!(name.is_empty(), "空 name 应触发 validation");
    }

    // ── Tavern card integration (pure logic, no DB) ────────────────────────

    #[test]
    fn tavern_v1_to_user_card_maps_all_fields() {
        use crate::tavern_cards::{parse_card_value, tavern_to_user_card};

        let v1 = json!({
            "name": "林知意",
            "description": "信使",
            "personality": "沉静",
            "scenario": "现代都市",
            "first_mes": "你好,我是林知意。",
            "mes_example": "<START>\n{{char}}: 我记得你说过的每一句话。",
            "creator": "felix",
            "character_version": "1.0",
            "tags": ["信使", "现代"]
        });
        let card = parse_card_value(&v1).unwrap();
        assert_eq!(card.data.name, "林知意");
        assert_eq!(card.spec, "chara_card_v1");

        let payload = tavern_to_user_card(&card);
        assert_eq!(payload["name"], "林知意");
        assert_eq!(payload["identity"], "信使");
        assert_eq!(payload["personality"], "沉静");

        // sample_dialogue 从 mes_example 提取
        let samples = payload["sample_dialogue"].as_array().unwrap();
        assert!(!samples.is_empty(), "应从 mes_example 提取 sample_dialogue");
        assert_eq!(samples[0], "我记得你说过的每一句话。");

        // metadata 带 tavern_imported 标记
        assert_eq!(payload["metadata"]["tavern_imported"], true);
        assert_eq!(payload["metadata"]["scenario"], "现代都市");
        assert_eq!(payload["metadata"]["first_mes"], "你好,我是林知意。");
        assert_eq!(payload["metadata"]["creator"], "felix");
        assert_eq!(payload["metadata"]["spec"], "chara_card_v1");
    }

    #[test]
    fn tavern_v2_roundtrip() {
        use crate::tavern_cards::{parse_card_value, user_card_to_tavern_v2};

        let v2 = json!({
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": "杭雁菱",
                "description": "穿越者",
                "personality": "坚毅",
                "scenario": "异世界",
                "first_mes": "我来自未来。",
                "mes_example": "",
                "creator_notes": "主角",
                "system_prompt": "",
                "post_history_instructions": "",
                "alternate_greetings": ["你好啊", "嗨"],
                "tags": ["穿越", "主角"],
                "creator": "felix",
                "character_version": "2.0",
                "extensions": {},
                "character_book": null
            }
        });
        let card = parse_card_value(&v2).unwrap();
        assert_eq!(card.data.name, "杭雁菱");
        assert_eq!(card.spec, "chara_card_v2");
        assert_eq!(card.data.alternate_greetings, vec!["你好啊", "嗨"]);
        assert_eq!(card.data.tags, vec!["穿越", "主角"]);
        assert_eq!(card.data.post_history_instructions, "");

        // 反向:user_card → V2
        let user_payload = json!({
            "name": card.data.name,
            "identity": card.data.description,
            "personality": card.data.personality,
            "sample_dialogue": ["我来自未来"],
            "tags": card.data.tags,
            "metadata": {
                "scenario": card.data.scenario,
                "first_mes": card.data.first_mes,
                "alternate_greetings": card.data.alternate_greetings,
                "creator_notes": card.data.creator_notes,
                "system_prompt": card.data.system_prompt,
                "post_history_instructions": card.data.post_history_instructions,
                "creator": card.data.creator,
                "character_version": card.data.character_version,
                "extensions": card.data.extensions,
                "character_book": null
            }
        });
        let back = user_card_to_tavern_v2(&user_payload);
        assert_eq!(back.data.name, "杭雁菱");
        assert_eq!(back.data.description, "穿越者");
        assert_eq!(back.data.alternate_greetings, vec!["你好啊", "嗨"]);
        assert!(!back.data.mes_example.is_empty(), "sample_dialogue 应被合成进 mes_example");
    }

    #[test]
    fn tavern_v1_missing_name_errors() {
        use crate::tavern_cards::parse_card_value;
        let v = json!({"description": "no name"});
        assert!(parse_card_value(&v).is_err());
    }

    #[test]
    fn tavern_v2_missing_name_errors() {
        use crate::tavern_cards::parse_card_value;
        let v = json!({"spec": "chara_card_v2", "spec_version": "2.0", "data": {"description": "no name"}});
        assert!(parse_card_value(&v).is_err());
    }

    #[test]
    fn tavern_description_truncated_to_2000() {
        use crate::tavern_cards::{parse_card_value, tavern_to_user_card};
        let long_desc: String = "x".repeat(3000);
        let v1 = json!({"name": "Test", "description": long_desc});
        let card = parse_card_value(&v1).unwrap();
        let payload = tavern_to_user_card(&card);
        let identity = payload["identity"].as_str().unwrap();
        assert!(identity.len() <= 2000, "identity 应被截断到 2000 字符");
    }

    #[test]
    fn tavern_base64_parse() {
        use crate::tavern_cards::parse_card_str;
        use base64::{engine::general_purpose, Engine};

        // 先生成一张 V2 卡的 JSON,再 base64 编码。
        let v2 = json!({
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": "Base64Test",
                "description": "desc",
                "personality": "",
                "scenario": "",
                "first_mes": "",
                "mes_example": "",
                "creator_notes": "",
                "system_prompt": "",
                "post_history_instructions": "",
                "alternate_greetings": [],
                "tags": [],
                "creator": "",
                "character_version": "",
                "extensions": {},
                "character_book": null
            }
        });
        let json_bytes = serde_json::to_vec(&v2).unwrap();
        let encoded = general_purpose::STANDARD.encode(&json_bytes);
        let card = parse_card_str(&encoded).unwrap();
        assert_eq!(card.data.name, "Base64Test");
    }
}
