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
                created_at, updated_at \
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
                created_at, updated_at \
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
                   created_at, updated_at",
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
    // upsert runtime_checkouts 时间戳(worker_id 记当前进程,便于排障)。
    let _ = sqlx::query(
        "insert into runtime_checkouts(save_id, user_id, worker_id, updated_at) \
         values ($1, $2, $3, now()) \
         on conflict(save_id) do update set updated_at = now(), \
           worker_id = excluded.worker_id, user_id = excluded.user_id",
    )
    .bind(save_id)
    .bind(user_id)
    .bind(crate::cluster::WORKER_ID.as_str())
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

// TODO[Sonnet]: 多 slot 文件状态落盘(`runtime_state_path`)、压缩存档、版本迁移工具。
