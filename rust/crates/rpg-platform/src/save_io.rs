//! save_io —— 存档 (`game_saves`) 读写 + 导入/导出。
//!
//! 对应 Python: `rpg/platform_app/save_io.py`。
//!
//! 提供:
//! - `Save` struct + `read_save / list_saves_for_user / delete_save / create_save`
//! - `export_save / import_save`:跨用户搬运,按当前 user 重映射 owner、commit_id。
//!
//! 不导入 sessions / context_runs / token_usage 这些跨用户敏感数据。

use chrono::{DateTime, Utc};
use rand::RngCore;
use rpg_core::UserId;
use rpg_schemas::GameStateData;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::{Column, PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

pub const EXPORT_VERSION: i32 = 1;

/// `game_saves` 行。state_snapshot/active_commit_id 是 v5 migration 加列。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Save {
    pub id: i64,
    pub user_id: UserId,
    pub script_id: i64,
    pub title: String,
    #[serde(default)]
    pub state_path: String,
    #[serde(default)]
    pub active_commit_id: Option<i64>,
    #[serde(default)]
    pub state_snapshot: Value,
    /// UUID public_id — 对应 Python 端 `public_id`,前端 normalize 为 'uid'。
    /// 旧存档可能无此列,用 Option 兜底。
    #[serde(default)]
    pub public_id: Option<uuid::Uuid>,
    pub created_at: Option<DateTime<Utc>>,
    pub updated_at: Option<DateTime<Utc>>,
}

fn save_from_row(row: &sqlx::postgres::PgRow) -> sqlx::Result<Save> {
    Ok(Save {
        id: row.try_get("id")?,
        user_id: row.try_get("user_id")?,
        script_id: row.try_get("script_id")?,
        title: row.try_get("title")?,
        state_path: row.try_get::<String, _>("state_path").unwrap_or_default(),
        active_commit_id: row.try_get::<Option<i64>, _>("active_commit_id").unwrap_or(None),
        state_snapshot: row
            .try_get::<Value, _>("state_snapshot")
            .unwrap_or(Value::Object(Default::default())),
        public_id: row.try_get::<Option<uuid::Uuid>, _>("public_id").unwrap_or(None),
        created_at: row.try_get("created_at").ok(),
        updated_at: row.try_get("updated_at").ok(),
    })
}

/// 列出当前用户的所有 save (按更新时间倒序)。
pub async fn list_saves_for_user(pool: &PgPool, user_id: UserId) -> PlatformResult<Vec<Save>> {
    let rows = sqlx::query(
        "select id, user_id, script_id, title, state_path, \
                active_commit_id, \
                coalesce(state_snapshot, '{}'::jsonb) as state_snapshot, \
                public_id, created_at, updated_at \
         from game_saves where user_id = $1 order by updated_at desc, id desc",
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(save_from_row)
        .collect::<Result<Vec<_>, _>>()
        .map_err(Into::into)
}

/// 读取单条 save。鉴权通过 user_id。
pub async fn read_save(
    pool: &PgPool,
    user_id: UserId,
    save_id: i64,
) -> PlatformResult<Option<Save>> {
    let row = sqlx::query(
        "select id, user_id, script_id, title, state_path, \
                active_commit_id, \
                coalesce(state_snapshot, '{}'::jsonb) as state_snapshot, \
                public_id, created_at, updated_at \
         from game_saves where id = $1 and user_id = $2",
    )
    .bind(save_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|r| save_from_row(&r)).transpose()?)
}

/// 新建 save。
pub async fn create_save(
    pool: &PgPool,
    user_id: UserId,
    script_id: i64,
    title: &str,
    state_snapshot: &Value,
) -> PlatformResult<Save> {
    let title = if title.trim().is_empty() {
        "新存档"
    } else {
        title
    };
    let row = sqlx::query(
        "insert into game_saves(user_id, script_id, title, state_path, state_snapshot) \
         values ($1, $2, $3, '', $4) \
         returning id, user_id, script_id, title, state_path, \
                   active_commit_id, \
                   coalesce(state_snapshot, '{}'::jsonb) as state_snapshot, \
                   public_id, created_at, updated_at",
    )
    .bind(user_id)
    .bind(script_id)
    .bind(title)
    .bind(state_snapshot)
    .fetch_one(pool)
    .await?;
    Ok(save_from_row(&row)?)
}

/// 删除 save (级联删 commits/refs/checkouts)。
pub async fn delete_save(pool: &PgPool, user_id: UserId, save_id: i64) -> PlatformResult<bool> {
    let res = sqlx::query("delete from game_saves where id = $1 and user_id = $2")
        .bind(save_id)
        .bind(user_id)
        .execute(pool)
        .await?;
    Ok(res.rows_affected() > 0)
}

// ─── export / import ───────────────────────────────────────────────────

/// 完整导出 payload (跨用户搬运用)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaveExport {
    pub export_version: i32,
    pub exported_at: f64,
    pub save: Value,
    pub commits: Vec<Value>,
    pub refs: Vec<Value>,
    pub messages: Vec<Value>,
    pub memories: Vec<Value>,
}

fn row_to_json(row: &sqlx::postgres::PgRow) -> Value {
    let mut map = serde_json::Map::new();
    for col in row.columns() {
        let name = col.name();
        // 简化:尝试 Value(jsonb)→string→i64→f64→bool。
        if let Ok(v) = row.try_get::<Value, _>(name) {
            map.insert(name.to_string(), v);
            continue;
        }
        if let Ok(v) = row.try_get::<Option<String>, _>(name) {
            map.insert(
                name.to_string(),
                v.map(Value::String).unwrap_or(Value::Null),
            );
            continue;
        }
        if let Ok(v) = row.try_get::<Option<i64>, _>(name) {
            map.insert(
                name.to_string(),
                v.map(|n| json!(n)).unwrap_or(Value::Null),
            );
            continue;
        }
        if let Ok(v) = row.try_get::<Option<i32>, _>(name) {
            map.insert(
                name.to_string(),
                v.map(|n| json!(n)).unwrap_or(Value::Null),
            );
            continue;
        }
        if let Ok(v) = row.try_get::<Option<bool>, _>(name) {
            map.insert(
                name.to_string(),
                v.map(Value::Bool).unwrap_or(Value::Null),
            );
            continue;
        }
        if let Ok(v) = row.try_get::<Option<DateTime<Utc>>, _>(name) {
            map.insert(
                name.to_string(),
                v.map(|t| json!(t.to_rfc3339())).unwrap_or(Value::Null),
            );
            continue;
        }
        map.insert(name.to_string(), Value::Null);
    }
    Value::Object(map)
}

/// Python `export_save`:把整份 save 打包成 JSON。
pub async fn export_save(
    pool: &PgPool,
    user_id: UserId,
    save_id: i64,
) -> PlatformResult<SaveExport> {
    let save = sqlx::query("select * from game_saves where id = $1 and user_id = $2")
        .bind(save_id)
        .bind(user_id)
        .fetch_optional(pool)
        .await?
        .ok_or_else(|| PlatformError::forbidden("无权访问该存档"))?;
    let commits = sqlx::query("select * from branch_commits where save_id = $1 order by id")
        .bind(save_id)
        .fetch_all(pool)
        .await?;
    let refs = sqlx::query("select * from branch_refs where save_id = $1 order by id")
        .bind(save_id)
        .fetch_all(pool)
        .await?;
    let session_rows = sqlx::query("select id from game_sessions where save_id = $1")
        .bind(save_id)
        .fetch_all(pool)
        .await
        .unwrap_or_default();
    let session_ids: Vec<i64> = session_rows
        .iter()
        .filter_map(|r| r.try_get::<i64, _>("id").ok())
        .collect();
    let (messages, memories) = if session_ids.is_empty() {
        (Vec::new(), Vec::new())
    } else {
        let m = sqlx::query("select * from messages where session_id = ANY($1) order by id")
            .bind(&session_ids[..])
            .fetch_all(pool)
            .await
            .unwrap_or_default();
        let me = sqlx::query("select * from memories where session_id = ANY($1) order by id")
            .bind(&session_ids[..])
            .fetch_all(pool)
            .await
            .unwrap_or_default();
        (m, me)
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    Ok(SaveExport {
        export_version: EXPORT_VERSION,
        exported_at: now,
        save: row_to_json(&save),
        commits: commits.iter().map(row_to_json).collect(),
        refs: refs.iter().map(row_to_json).collect(),
        messages: messages.iter().map(row_to_json).collect(),
        memories: memories.iter().map(row_to_json).collect(),
    })
}

fn random_hex(n_bytes: usize) -> String {
    let mut buf = vec![0u8; n_bytes];
    rand::thread_rng().fill_bytes(&mut buf);
    let mut out = String::with_capacity(n_bytes * 2);
    for b in buf {
        out.push_str(&format!("{:02x}", b));
    }
    out
}

fn s(v: Option<&Value>) -> String {
    v.and_then(|x| x.as_str())
        .map(|s| s.to_string())
        .unwrap_or_default()
}

fn i(v: Option<&Value>) -> i64 {
    v.and_then(|x| x.as_i64()).unwrap_or(0)
}

/// 导入结果。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ImportResult {
    pub ok: bool,
    pub save_id: i64,
    pub commits_imported: usize,
    pub script_id: i64,
}

/// Python `import_save`:按当前 user 重建存档。返回新 save_id。
pub async fn import_save(
    pool: &PgPool,
    user_id: UserId,
    payload: &Value,
) -> PlatformResult<ImportResult> {
    let obj = payload
        .as_object()
        .ok_or_else(|| PlatformError::validation("payload 必须是对象"))?;
    let ver = obj
        .get("export_version")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    if ver != EXPORT_VERSION as i64 {
        return Err(PlatformError::validation(format!(
            "export_version 不匹配(期望 {})",
            EXPORT_VERSION
        )));
    }
    let save_data = obj.get("save").cloned().unwrap_or(Value::Null);
    if !save_data.is_object() {
        return Err(PlatformError::validation("payload.save 缺失"));
    }
    let save_obj = save_data.as_object().unwrap();
    let title_raw = s(save_obj.get("title"));
    let new_title = if title_raw.is_empty() {
        "导入存档".to_string()
    } else {
        title_raw
    };
    let script_id_raw = save_obj.get("script_id").and_then(|v| v.as_i64());
    let state_snapshot = save_obj
        .get("state_snapshot")
        .cloned()
        .unwrap_or_else(|| Value::Object(Default::default()));

    let mut tx = pool.begin().await?;

    // 校验 script_id 归属;否则用当前 user 第一个剧本兜底。
    let mut script_id: Option<i64> = None;
    if let Some(sid) = script_id_raw {
        let owned = sqlx::query("select 1 as ok from scripts where id = $1 and owner_id = $2")
            .bind(sid)
            .bind(user_id)
            .fetch_optional(&mut *tx)
            .await?;
        if owned.is_some() {
            script_id = Some(sid);
        }
    }
    let script_id = match script_id {
        Some(s) => s,
        None => {
            let row =
                sqlx::query("select id from scripts where owner_id = $1 order by id limit 1")
                    .bind(user_id)
                    .fetch_optional(&mut *tx)
                    .await?;
            row.ok_or_else(|| PlatformError::validation("当前用户没有剧本,无法导入存档"))?
                .try_get::<i64, _>("id")?
        }
    };

    // 1. 新建 save
    let new_save = sqlx::query(
        "insert into game_saves(user_id, script_id, title, state_path, state_snapshot) \
         values ($1, $2, $3, '', $4) returning id",
    )
    .bind(user_id)
    .bind(script_id)
    .bind(&new_title)
    .bind(&state_snapshot)
    .fetch_one(&mut *tx)
    .await?;
    let new_save_id: i64 = new_save.try_get("id")?;

    // 2. 重建 commits
    let empty: Vec<Value> = Vec::new();
    let commits = obj
        .get("commits")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty);
    let mut old_to_new: Vec<(i64, i64)> = Vec::with_capacity(commits.len());
    for c in commits {
        let co = match c.as_object() {
            Some(o) => o,
            None => continue,
        };
        let old_id = i(co.get("id"));
        let old_parent = co.get("parent_id").and_then(|v| v.as_i64());
        let new_parent =
            old_parent.and_then(|p| old_to_new.iter().find(|(o, _)| *o == p).map(|(_, n)| *n));
        let obj_hash = {
            let h = s(co.get("object_hash"));
            if h.is_empty() {
                random_hex(20)
            } else {
                h
            }
        };
        let metadata = co
            .get("metadata")
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        let snap = co
            .get("state_snapshot")
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        let new_row = sqlx::query(
            "insert into branch_commits(\
               save_id, parent_id, object_hash, tree_hash, turn_index, \
               kind, title, message, summary, content_preview, \
               state_path, player_input, gm_output, metadata, state_snapshot\
             ) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) returning id",
        )
        .bind(new_save_id)
        .bind(new_parent)
        .bind(obj_hash)
        .bind(s(co.get("tree_hash")))
        .bind(i(co.get("turn_index")) as i32)
        .bind({
            let k = s(co.get("kind"));
            if k.is_empty() { "round".to_string() } else { k }
        })
        .bind(s(co.get("title")))
        .bind(s(co.get("message")))
        .bind(s(co.get("summary")))
        .bind(s(co.get("content_preview")))
        .bind("")
        .bind(s(co.get("player_input")))
        .bind(s(co.get("gm_output")))
        .bind(&metadata)
        .bind(&snap)
        .fetch_one(&mut *tx)
        .await?;
        let new_id: i64 = new_row.try_get("id")?;
        old_to_new.push((old_id, new_id));
    }

    // 3. 创建 active ref 指向最后一个 commit
    if let Some(&(_, last)) = old_to_new.last() {
        sqlx::query(
            "insert into branch_refs(save_id, name, kind, target_commit_id, is_active) \
             values ($1, 'refs/heads/main', 'head', $2, true)",
        )
        .bind(new_save_id)
        .bind(last)
        .execute(&mut *tx)
        .await?;
        // active_commit_id 列可能不存在,失败不阻塞导入。
        let _ = sqlx::query("update game_saves set active_commit_id = $1 where id = $2")
            .bind(last)
            .bind(new_save_id)
            .execute(&mut *tx)
            .await;
    }

    tx.commit().await?;

    Ok(ImportResult {
        ok: true,
        save_id: new_save_id,
        commits_imported: old_to_new.len(),
        script_id,
    })
}

// ─── 状态外置(6C-1):active save 解析 + 读写 state_snapshot ───────────────
//
// rpg-state 的 StateStore read-through 需要"按 user 加载/落库存档",但 rpg-state
// 不能依赖本 crate(循环)。所以这里只提供**面向 `Value` 快照**的纯持久化原语,
// 由 rpg-server 装配层包成闭包注入 StateStore(在那里完成 Value↔GameState 转换)。

/// 解析某 user 当前「活跃」存档 id。
///
/// 优先取 `user_runtime.save_id`(玩家正在玩的存档);没有则回落该 user 最近更新的
/// `game_saves` 行。两者都没有(新用户 / 无存档)返回 `None`。
pub async fn resolve_active_save_id(pool: &PgPool, user_id: UserId) -> Option<i64> {
    // 1. user_runtime.save_id
    if let Ok(Some(row)) =
        sqlx::query("select save_id from user_runtime where user_id = $1 and save_id is not null")
            .bind(user_id)
            .fetch_optional(pool)
            .await
    {
        if let Ok(sid) = row.try_get::<i64, _>("save_id") {
            return Some(sid);
        }
    }
    // 2. 回落最近更新的 save
    sqlx::query(
        "select id from game_saves where user_id = $1 order by updated_at desc, id desc limit 1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
    .and_then(|r| r.try_get::<i64, _>("id").ok())
}

/// 加载某 user 活跃存档的 `state_snapshot`。返回 `(save_id, snapshot)`。
///
/// 无活跃存档时返回 `None`(StateStore 退化为创建空白存档)。
pub async fn load_active_state_snapshot(
    pool: &PgPool,
    user_id: UserId,
) -> Option<(i64, Value)> {
    let save_id = resolve_active_save_id(pool, user_id).await?;
    match read_save(pool, user_id, save_id).await {
        Ok(Some(save)) => {
            let snap = save.state_snapshot;
            // 空对象视为"无有效快照",仍返回 save_id 以便落库时复用。
            Some((save_id, snap))
        }
        _ => None,
    }
}

/// 把 `snapshot` 写回某 save 的 `state_snapshot`,并刷新 `runtime_checkouts.updated_at`
/// (供 `cluster::is_state_stale` 跨 pod 缓存失效判断)。鉴权通过 user_id。
pub async fn write_state_snapshot(
    pool: &PgPool,
    user_id: UserId,
    save_id: i64,
    snapshot: &Value,
) -> PlatformResult<()> {
    let mut tx = pool.begin().await?;
    let res = sqlx::query(
        "update game_saves set state_snapshot = $1, updated_at = now() \
         where id = $2 and user_id = $3",
    )
    .bind(snapshot)
    .bind(save_id)
    .bind(user_id)
    .execute(&mut *tx)
    .await?;
    if res.rows_affected() == 0 {
        // save 不存在或不归属该 user:不写,回滚。
        tx.rollback().await?;
        return Err(PlatformError::forbidden("无权写入该存档"));
    }
    // upsert runtime_checkouts 时间戳。
    // 注:runtime_checkouts 没有 worker_id 列,ON CONFLICT 用 (user_id, save_id)。
    let _ = sqlx::query(
        "insert into runtime_checkouts(user_id, save_id, updated_at) \
         values ($1, $2, now()) \
         on conflict(user_id, save_id) do update set updated_at = now()",
    )
    .bind(user_id)
    .bind(save_id)
    .execute(&mut *tx)
    .await;
    tx.commit().await?;
    Ok(())
}

/// 便捷封装:解析活跃 save 后写回 snapshot。新用户无 save 时返回 `Ok(false)`(不报错,
/// 因为 read-through 阶段尚无存档时落库无目标 —— 由 `/api/new` 等显式建档路径负责创建)。
pub async fn write_active_state_snapshot(
    pool: &PgPool,
    user_id: UserId,
    data: &GameStateData,
) -> PlatformResult<bool> {
    // DB 列仍是 jsonb — 在 IO 边界序列化一次
    let snapshot = serde_json::to_value(data)?;
    match resolve_active_save_id(pool, user_id).await {
        Some(save_id) => {
            write_state_snapshot(pool, user_id, save_id, &snapshot).await?;
            Ok(true)
        }
        None => Ok(false),
    }
}

// ─── build_initial_snapshot ────────────────────────────────────────────────
//
// 对应 Python: `rpg/platform_app/workspace.py::_build_initial_snapshot` + `_apply_script_opening`。
//
// 从 UI 传入的角色卡/persona/身份/出生点/故事意图构造新存档的初始 GameState。
// 任何 DB 读取失败都不应炸 create_save,退化到空白快照。

use once_cell::sync::Lazy;
use regex::Regex;

/// 从首章 content 提取 inline 元数据的正则(当前地点/当前目标/时间锚点)。
/// 对应 Python `_OPENING_LOCATION_RE` / `_OPENING_OBJECTIVE_RE` / `_OPENING_TIME_RE`。
static OPENING_LOCATION_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?:当前地点|地点)\s*[:：]\s*([^。\n；;]+)").unwrap());
static OPENING_OBJECTIVE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?:当前目标|主线目标|目标)\s*[:：]\s*([^。\n；;]+)").unwrap());
static OPENING_TIME_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?:时间锚点|时刻|时间)\s*[:：]\s*([^。\n；;]+)").unwrap());

/// 匹配 markdown 标题前缀 `## ` / `### ` 等。
static MD_HEADING_PREFIX: Lazy<Regex> = Lazy::new(|| Regex::new(r"^#+\s*").unwrap());

/// 秘密段标题匹配(## 秘密 / ## 隐藏 / ## 元知识 / ## meta)。
/// Rust regex crate 不支持 look-ahead,用手动 split-by-heading 实现。
static SECRET_HEADING_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?mi)^#{1,6}\s*(?:秘密|隐藏|元知识|meta)\b").unwrap()
});
static HEADING_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)^#{1,6}\s").unwrap()
});

fn find_secret_ranges(text: &str) -> Vec<(usize, usize)> {
    let mut ranges = Vec::new();
    for m in SECRET_HEADING_RE.find_iter(text) {
        let start = m.start();
        let after_heading = m.end();
        let end = HEADING_RE.find_at(text, after_heading)
            .map(|next| next.start())
            .unwrap_or(text.len());
        ranges.push((start, end));
    }
    ranges
}

fn strip_secret_sections(text: &str) -> String {
    if text.is_empty() { return String::new(); }
    let ranges = find_secret_ranges(text);
    if ranges.is_empty() { return text.to_string(); }
    let mut result = String::with_capacity(text.len());
    let mut cursor = 0;
    for (start, end) in &ranges {
        if *start > cursor { result.push_str(&text[cursor..*start]); }
        cursor = *end;
    }
    if cursor < text.len() { result.push_str(&text[cursor..]); }
    let re_blanks = Regex::new(r"\n{3,}").unwrap();
    re_blanks.replace_all(result.trim(), "\n\n").to_string()
}

fn extract_secret_sections(text: &str) -> Vec<String> {
    if text.is_empty() { return Vec::new(); }
    find_secret_ranges(text)
        .iter()
        .map(|(s, e)| text[*s..*e].trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// 判断一章是不是"纯文档总标题 / 空内容 / 只复述标题"形态。
fn is_doc_title_only(content: &str, title: &str) -> bool {
    let c = content.trim();
    if c.is_empty() || c.chars().count() < 4 {
        return true;
    }
    let t = MD_HEADING_PREFIX.replace(title.trim(), "").trim().to_string();
    let bare = MD_HEADING_PREFIX.replace(c, "").trim().to_string();
    if !t.is_empty() && bare == t {
        return true;
    }
    false
}

/// 是否含至少一项 inline 元数据。
fn has_opening_meta(content: &str) -> bool {
    if content.is_empty() {
        return false;
    }
    OPENING_LOCATION_RE.is_match(content)
        || OPENING_OBJECTIVE_RE.is_match(content)
        || OPENING_TIME_RE.is_match(content)
}

/// 从 inline 元数据正则过滤后的句子列表中提取句子(排除元数据句)。
static META_SENTENCE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^(?:当前地点|地点|当前目标|主线目标|目标|时间锚点|时刻|时间)\s*[:：]").unwrap()
});

/// 构造新存档的初始 GameState(对应 Python `_build_initial_snapshot`)。
///
/// 逻辑:
/// 1. 空白 state(`GameState::new`)
/// 2. 从 new_card / character(persona/user_card/script_card) / 默认 persona 取 name/role/background
/// 3. 秘密段抽取 → player_private.secrets,原字段 strip 后保留
/// 4. `_apply_script_opening` 从首章设置初始世界状态
/// 5. birthpoint → world.timeline
/// 6. identity → 覆盖 player(逐字段,非空才覆盖)
/// 7. story_intent → player_private.story_intent + worldline.user_variables.story_intent
/// 8. user_preferences → permissions.mode
pub async fn build_initial_snapshot(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    new_card: Option<&Value>,
    character: Option<&Value>,
    birthpoint: Option<&Value>,
    identity: Option<&Value>,
    story_intent: Option<&str>,
) -> Value {
    use rpg_state::GameState;

    let mut state = GameState::new(user_id.to_string());

    let mut name = String::new();
    let mut role = String::new();
    let mut background = String::new();
    let mut extra_card_fields: Vec<(String, String)> = Vec::new();
    let mut extra_private_secrets: Vec<String> = Vec::new();

    // Helper closure: absorb card secrets from a card-like JSON object
    let absorb_card_secrets =
        |card: &Value,
         extra_fields: &mut Vec<(String, String)>,
         extra_secrets: &mut Vec<String>| {
            // 1. direct secrets field → player_private
            let sec_raw = card
                .get("secrets")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            if !sec_raw.is_empty() && !extra_secrets.contains(&sec_raw) {
                extra_secrets.push(sec_raw);
            }
            // 2. personality / appearance / background 里的 ## 秘密 段
            for f in &["appearance", "personality", "background"] {
                let v = card
                    .get(*f)
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if v.is_empty() {
                    continue;
                }
                for h in extract_secret_sections(&v) {
                    if !h.is_empty() && !extra_secrets.contains(&h) {
                        extra_secrets.push(h);
                    }
                }
                let stripped = strip_secret_sections(&v);
                if !stripped.is_empty() {
                    extra_fields.push((f.to_string(), stripped));
                }
            }
            // 3. speech_style / aliases → NPC 可观察,直接保留
            for f in &["speech_style", "aliases"] {
                let v = card
                    .get(*f)
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if !v.is_empty() {
                    extra_fields.push((f.to_string(), v));
                }
            }
        };

    // Determine character source: new_card > character > default persona > first user_card
    let is_new_card = new_card.map(|v| v.is_object()).unwrap_or(false);
    let is_character = character.map(|v| v.is_object()).unwrap_or(false);

    // Build effective character reference (may need default lookup)
    let mut effective_character: Option<Value> = character.cloned();
    if !is_new_card && !is_character {
        // Fallback: default persona, then first user_card
        if let Ok(personas) = crate::user_cards::list_personas(pool, user_id).await {
            let default_p = personas
                .iter()
                .find(|p| p.is_default)
                .or_else(|| personas.first());
            if let Some(p) = default_p {
                effective_character = Some(json!({"kind": "persona", "id": p.id}));
            } else {
                // No persona — try first user_card
                if let Ok(cards) =
                    crate::user_cards::list_user_cards(pool, user_id, None, false).await
                {
                    if let Some(c) = cards.first() {
                        effective_character = Some(json!({"kind": "user_card", "id": c.id}));
                    }
                }
            }
        }
    }

    if is_new_card {
        let card = new_card.unwrap();
        name = card
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        role = card
            .get("role")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let new_bg = card
            .get("background")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if !new_bg.is_empty() {
            for h in extract_secret_sections(&new_bg) {
                if !h.is_empty() && !extra_private_secrets.contains(&h) {
                    extra_private_secrets.push(h);
                }
            }
            background = strip_secret_sections(&new_bg);
        }
    } else if let Some(char_ref) = &effective_character {
        if char_ref.is_object() {
            let kind = char_ref
                .get("kind")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            let cid = char_ref.get("id").and_then(|v| v.as_i64());
            if let Some(cid) = cid {
                match kind {
                    "persona" => {
                        if let Ok(Some(p)) =
                            crate::user_cards::get_persona(pool, user_id, cid).await
                        {
                            name = p.name.trim().to_string();
                            role = p.role.trim().to_string();
                            let p_bg = p.background.trim().to_string();
                            for h in extract_secret_sections(&p_bg) {
                                if !h.is_empty() && !extra_private_secrets.contains(&h) {
                                    extra_private_secrets.push(h);
                                }
                            }
                            background =
                                if p_bg.is_empty() { String::new() } else { strip_secret_sections(&p_bg) };
                        }
                    }
                    "user_card" => {
                        if let Ok(Some(c)) =
                            crate::user_cards::get_user_card(pool, user_id, cid).await
                        {
                            name = c.name.trim().to_string();
                            role = c.identity.trim().to_string();
                            let bg_src = if !c.personality.trim().is_empty() {
                                c.personality.trim().to_string()
                            } else {
                                c.appearance.trim().to_string()
                            };
                            background = if bg_src.is_empty() {
                                String::new()
                            } else {
                                strip_secret_sections(&bg_src)
                            };
                            let card_val = serde_json::to_value(&c).unwrap_or(Value::Null);
                            absorb_card_secrets(
                                &card_val,
                                &mut extra_card_fields,
                                &mut extra_private_secrets,
                            );
                        }
                    }
                    "script_card" => {
                        let mut found = false;
                        if let Ok(Some(c)) = crate::knowledge::get_character_card(
                            pool, user_id, script_id, cid,
                        )
                        .await
                        {
                            name = c.name.trim().to_string();
                            role = c.identity.trim().to_string();
                            let bg_src = if !c.personality.trim().is_empty() {
                                c.personality.trim().to_string()
                            } else {
                                c.appearance.trim().to_string()
                            };
                            background = if bg_src.is_empty() {
                                String::new()
                            } else {
                                strip_secret_sections(&bg_src)
                            };
                            let card_val = serde_json::to_value(&c).unwrap_or(Value::Null);
                            absorb_card_secrets(
                                &card_val,
                                &mut extra_card_fields,
                                &mut extra_private_secrets,
                            );
                            found = !name.is_empty();
                        }
                        // task 114: fallback to user_card if script_card not found
                        if !found {
                            if let Ok(Some(uc)) =
                                crate::user_cards::get_user_card(pool, user_id, cid).await
                            {
                                name = uc.name.trim().to_string();
                                role = uc.identity.trim().to_string();
                                let bg_src = if !uc.personality.trim().is_empty() {
                                    uc.personality.trim().to_string()
                                } else {
                                    uc.appearance.trim().to_string()
                                };
                                background = if bg_src.is_empty() {
                                    String::new()
                                } else {
                                    strip_secret_sections(&bg_src)
                                };
                                let card_val =
                                    serde_json::to_value(&uc).unwrap_or(Value::Null);
                                absorb_card_secrets(
                                    &card_val,
                                    &mut extra_card_fields,
                                    &mut extra_private_secrets,
                                );
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    // setup_player
    if !name.is_empty() || !role.is_empty() || !background.is_empty() {
        state.data.player.name = if name.is_empty() {
            "无名者".into()
        } else {
            name
        };
        state.data.player.role = if role.is_empty() {
            "未指定".into()
        } else {
            role
        };
        state.data.player.background = if background.is_empty() {
            "（无背景）".into()
        } else {
            background
        };
    }

    // task 137: extra card fields → player (NPC-visible portion after secret stripping)
    for (field, value) in &extra_card_fields {
        state
            .data
            .player
            .extra
            .insert(field.clone(), Value::String(value.clone()));
    }

    // task 138: secrets → player_private.secrets
    for sec in &extra_private_secrets {
        let sec_val = Value::String(sec.clone());
        if !state.data.player_private.secrets.contains(&sec_val) {
            state.data.player_private.secrets.push(sec_val);
        }
    }

    // apply_script_opening (reads first chapter, sets location/objective/time/events/retrieval)
    apply_script_opening(pool, script_id, &mut state.data).await;

    // birthpoint → world.timeline (priority > _apply_script_opening)
    if let Some(bp) = birthpoint {
        if bp.is_object() {
            let phase_label = bp
                .get("phase_label")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            let story_time_label = bp
                .get("story_time_label")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            let chapter_min = bp.get("chapter_min").and_then(|v| v.as_i64());
            let chapter_max = bp.get("chapter_max").and_then(|v| v.as_i64());

            if !phase_label.is_empty() {
                state.data.world.timeline.current_phase = phase_label.to_string();
            }
            if !story_time_label.is_empty() {
                state.data.world.time = story_time_label.to_string();
                state.data.world.timeline.current_label = story_time_label.to_string();
            }
            if let (Some(cmin), Some(cmax)) = (chapter_min, chapter_max) {
                state.data.world.timeline.extra.insert(
                    "anchor_chapter_range".to_string(),
                    json!([cmin, cmax]),
                );
            }
        }
    }

    // identity → 逐字段 merge player(只覆盖非空字段)
    if let Some(id) = identity {
        if id.is_object() {
            let id_name = id
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            let id_role = id
                .get("role")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            let id_background = id
                .get("background")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();

            if !id_name.is_empty() {
                state.data.player.name = id_name.to_string();
            }
            if !id_role.is_empty() {
                state.data.player.role = id_role.to_string();
            }
            // identity.background → identity_role_desc (不覆盖角色卡的 background)
            if !id_background.is_empty() {
                state.data.player.extra.insert(
                    "identity_role_desc".to_string(),
                    Value::String(id_background.to_string()),
                );
            }
            // 兜底
            if state.data.player.name.is_empty() {
                state.data.player.name = "无名者".into();
            }
            if state.data.player.role.is_empty() {
                state.data.player.role = "未指定".into();
            }
            if state.data.player.background.is_empty() {
                state.data.player.background = "（无背景）".into();
            }
        }
    }

    // story_intent → player_private + worldline.user_variables (dual-write for compat)
    if let Some(si) = story_intent {
        let si = si.trim();
        if !si.is_empty() {
            state.data.player_private.story_intent = si.to_string();
            state.data.worldline.user_variables.insert(
                "story_intent".to_string(),
                json!({
                    "value": si,
                    "source": "user:new_game_wizard",
                    "locked": false,
                    "turn": 0,
                    "updated_at": chrono::Utc::now().to_rfc3339(),
                }),
            );
        }
    }

    // Bug 5 fix: user_preferences → permissions.mode
    if let Ok(pref_row) = sqlx::query(
        "select preferences from user_preferences where user_id = $1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    {
        if let Some(row) = pref_row {
            if let Ok(prefs) = row.try_get::<Value, _>("preferences") {
                let default_mode = prefs
                    .get("perm.default_mode")
                    .or_else(|| prefs.get("default_perm_mode"))
                    .and_then(|v| v.as_str());
                if let Some(mode) = default_mode {
                    match mode {
                        "default" | "review" | "full_access" => {
                            state.data.permissions.mode = mode.to_string();
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    serde_json::to_value(&state.data).unwrap_or(Value::Object(Default::default()))
}

/// 从 script_chapters 找"真实首章"(不是文档总标题/空前言),把 inline 元数据填到 state。
///
/// 对应 Python `_apply_script_opening`:
/// - 当前地点 → player.current_location
/// - 当前目标 → memory.current_objective
/// - 时间锚点 → world.time + world.timeline.current_label
/// - known_events → 首章 title + 前两段非元数据正文摘要
/// - last_retrieval → 首章正文前 ~400 字
///
/// 无论是否找到有效首章,都清掉 DEFAULT_STATE 的柏林硬编码。
async fn apply_script_opening(pool: &PgPool, script_id: i64, data: &mut GameStateData) {
    // 1. scrub DEFAULT_STATE 柏林硬编码(不论 script 有无章节)
    scrub_berlin_default(data);

    // 2. 查前 10 章
    let rows = match sqlx::query(
        "select chapter_index, title, content \
         from script_chapters \
         where script_id = $1 \
         order by chapter_index asc \
         limit 10",
    )
    .bind(script_id)
    .fetch_all(pool)
    .await
    {
        Ok(rows) => rows,
        Err(_) => return,
    };
    if rows.is_empty() {
        return;
    }

    // 3. 选第一个有 inline meta 的章节;没有就选第一个有显著正文的章节
    let mut chosen_idx: Option<usize> = None;
    for (i, row) in rows.iter().enumerate() {
        let content: String = row.try_get::<String, _>("content").unwrap_or_default();
        let title: String = row.try_get::<String, _>("title").unwrap_or_default();
        if is_doc_title_only(&content, &title) {
            continue;
        }
        if has_opening_meta(&content) {
            chosen_idx = Some(i);
            break;
        }
    }
    if chosen_idx.is_none() {
        for (i, row) in rows.iter().enumerate() {
            let content: String = row.try_get::<String, _>("content").unwrap_or_default();
            let title: String = row.try_get::<String, _>("title").unwrap_or_default();
            if is_doc_title_only(&content, &title) {
                continue;
            }
            if content.trim().chars().count() >= 40 {
                chosen_idx = Some(i);
                break;
            }
        }
    }

    if chosen_idx.is_none() {
        // 全部章节都是空/总标题:用第一条 title 作为 opening 事件
        let first_title: String = rows[0]
            .try_get::<String, _>("title")
            .unwrap_or_default()
            .trim()
            .to_string();
        if !first_title.is_empty() {
            let ev_title = MD_HEADING_PREFIX
                .replace(&first_title, "")
                .trim()
                .to_string();
            if !ev_title.is_empty() {
                data.world.known_events =
                    vec![Value::String(format!("开场：{}", ev_title))];
            }
        }
        return;
    }

    let chosen = &rows[chosen_idx.unwrap()];
    let title: String = chosen
        .try_get::<String, _>("title")
        .unwrap_or_default()
        .trim()
        .to_string();
    let content: String = chosen
        .try_get::<String, _>("content")
        .unwrap_or_default();
    let title_clean = MD_HEADING_PREFIX
        .replace(&title, "")
        .trim()
        .to_string();

    // Parse inline metadata
    let loc = OPENING_LOCATION_RE
        .captures(&content)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().trim().to_string())
        .unwrap_or_default();
    let obj = OPENING_OBJECTIVE_RE
        .captures(&content)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().trim().to_string())
        .unwrap_or_default();
    let tm = OPENING_TIME_RE
        .captures(&content)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().trim().to_string())
        .unwrap_or_default();

    // Write back to state
    if !loc.is_empty() {
        data.player.current_location = loc;
    }
    if !tm.is_empty() {
        data.world.time = tm.clone();
        data.world.timeline.current_label = tm;
        data.world.timeline.last_transition = None;
    }
    if !obj.is_empty() {
        data.memory.current_objective = obj;
    }

    // known_events: "开场：<标题>" + 前两段去元数据后的正文摘要
    let sentences: Vec<&str> = content
        .split(|c: char| c == '。' || c == '\n')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();
    let body_sents: Vec<&str> = sentences
        .iter()
        .filter(|s| !META_SENTENCE_RE.is_match(s))
        .copied()
        .collect();
    let mut events: Vec<Value> = Vec::new();
    if !title_clean.is_empty() {
        events.push(Value::String(format!("开场：{}", title_clean)));
    }
    for s in body_sents.iter().take(2) {
        let truncated = if s.chars().count() > 80 {
            let mut t: String = s.chars().take(77).collect();
            t.push_str("…");
            t
        } else {
            s.to_string()
        };
        events.push(Value::String(truncated));
    }
    if !events.is_empty() {
        data.world.known_events = events;
    }

    // last_retrieval: 首章前 ~400 字
    let snippet = content.trim();
    if !snippet.is_empty() {
        let truncated = if snippet.chars().count() > 400 {
            let mut t: String = snippet.chars().take(400).collect();
            t = t.trim_end().to_string();
            t.push_str("…");
            t
        } else {
            snippet.to_string()
        };
        let label = if title_clean.is_empty() {
            "第1章".to_string()
        } else {
            title_clean
        };
        data.memory.last_retrieval = format!("=== 剧本开场 · {} ===\n{}", label, truncated);
    }
}

/// 清掉 DEFAULT_STATE 的柏林硬编码,避免跨剧本污染。
fn scrub_berlin_default(data: &mut GameStateData) {
    const BERLIN_LOC: &str = "柏林，哈布斯堡庄园附近";
    const BERLIN_TIME: &str = "图卢兹失守后翌日，柏林";
    const BERLIN_PHASE: &str = "柏林暗流篇";
    const BERLIN_OBJECTIVE_FRAG: &str = "柏林局势";

    if data.player.current_location == BERLIN_LOC {
        data.player.current_location = String::new();
    }
    if data.world.time == BERLIN_TIME {
        data.world.time = String::new();
    }

    // known_events: remove default Berlin events
    let default_events: &[&str] = &[
        "宴会上调令伪造事件已曝光",
        "图卢兹战役：薇瑟帝国八位渊戮大胜，地联溃败",
        "娅赛兰决定暂留柏林",
        "蛇信在外围全程监视",
    ];
    data.world.known_events.retain(|e| {
        let s = e.as_str().unwrap_or("");
        !default_events.contains(&s)
    });

    if data.world.timeline.current_label == BERLIN_TIME {
        data.world.timeline.current_label = String::new();
    }
    if data.world.timeline.current_phase == BERLIN_PHASE {
        data.world.timeline.current_phase = String::new();
    }

    if data.memory.current_objective.contains(BERLIN_OBJECTIVE_FRAG) {
        data.memory.current_objective = String::new();
    }
}

// ─── 压缩存档 (flate2 gzip) ────────────────────────────────────────────────
//
// 对应 Python save_io 的扩展需求:"compress_save / version_migration"。
// Python 端目前只做 JSON 导出,这里提供 gzip 压缩/解压 + 格式版本迁移工具。

use flate2::read::GzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use std::io::{Read as IoRead, Write as IoWrite};

/// 把 `SaveExport` 序列化后 gzip 压缩,返回字节数组。
///
/// 格式: gzip(UTF-8 JSON)。解压后即可 `serde_json::from_slice::<SaveExport>`。
pub fn compress_save(export: &SaveExport) -> PlatformResult<Vec<u8>> {
    let json = serde_json::to_vec(export)?;
    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder
        .write_all(&json)
        .map_err(|e| PlatformError::validation(format!("gzip 压缩失败: {e}")))?;
    encoder
        .finish()
        .map_err(|e| PlatformError::validation(format!("gzip finish 失败: {e}")))
}

/// 解压 `compress_save` 产生的字节数组,还原为 `SaveExport`。
pub fn decompress_save(data: &[u8]) -> PlatformResult<SaveExport> {
    let mut decoder = GzDecoder::new(data);
    let mut buf = Vec::new();
    decoder
        .read_to_end(&mut buf)
        .map_err(|e| PlatformError::validation(format!("gzip 解压失败: {e}")))?;
    serde_json::from_slice(&buf)
        .map_err(|e| PlatformError::validation(format!("存档 JSON 解析失败: {e}")))
}

// ─── 版本迁移 ──────────────────────────────────────────────────────────────
//
// 当 export_version 落后于当前 EXPORT_VERSION 时,`migrate_save_format` 逐步升级。
// 目前只有 v1,但框架结构完整,之后每加一版 push 一段 migrate_v1_to_v2 即可。

/// 把任意历史版本 `SaveExport` 升级到当前 `EXPORT_VERSION`。
///
/// - 若版本已是最新,直接返回原值(零拷贝 clone)。
/// - 逐步升级:v0→v1→…→CURRENT。
/// - 不支持降级(export_version > EXPORT_VERSION 时报 validation 错)。
pub fn migrate_save_format(mut export: SaveExport) -> PlatformResult<SaveExport> {
    if export.export_version > EXPORT_VERSION {
        return Err(PlatformError::validation(format!(
            "存档版本 {} 高于本程序支持的 {}",
            export.export_version, EXPORT_VERSION
        )));
    }
    // v0 → v1:补全缺失字段默认值(v0 是早期无版本号存档)。
    if export.export_version < 1 {
        export.export_version = 1;
        // v0 存档 refs/messages/memories 可能缺失:已在 SaveExport 各字段 `#[serde(default)]` 兜底,
        // 这里只需刷 version。
    }
    // 此处未来可继续: if export.export_version < 2 { migrate_v1_to_v2(...) }
    Ok(export)
}

// ─── tests ─────────────────────────────────────────────────────────────────
#[cfg(test)]
mod save_io_tests {
    use super::*;
    use serde_json::json;

    fn dummy_export(ver: i32) -> SaveExport {
        SaveExport {
            export_version: ver,
            exported_at: 1_700_000_000.0,
            save: json!({"id": 1, "title": "test"}),
            commits: vec![],
            refs: vec![],
            messages: vec![],
            memories: vec![],
        }
    }

    #[test]
    fn compress_decompress_roundtrip() {
        let original = dummy_export(1);
        let compressed = compress_save(&original).unwrap();
        assert!(!compressed.is_empty(), "压缩结果不应为空");
        // 通常 gzip 结果比原始 JSON 小或相当
        let restored = decompress_save(&compressed).unwrap();
        assert_eq!(restored.export_version, original.export_version);
        assert_eq!(restored.exported_at, original.exported_at);
    }

    #[test]
    fn decompress_invalid_data_errors() {
        let bad = b"not gzip data at all";
        let result = decompress_save(bad);
        assert!(result.is_err(), "非法 gzip 数据应返回 Err");
    }

    #[test]
    fn migrate_v0_to_v1() {
        let v0 = dummy_export(0);
        let migrated = migrate_save_format(v0).unwrap();
        assert_eq!(migrated.export_version, 1);
    }

    #[test]
    fn migrate_already_current_is_noop() {
        let current = dummy_export(EXPORT_VERSION);
        let migrated = migrate_save_format(current).unwrap();
        assert_eq!(migrated.export_version, EXPORT_VERSION);
    }

    #[test]
    fn migrate_future_version_errors() {
        let future = dummy_export(EXPORT_VERSION + 1);
        let result = migrate_save_format(future);
        assert!(result.is_err(), "未来版本应返回 Err");
    }
}
