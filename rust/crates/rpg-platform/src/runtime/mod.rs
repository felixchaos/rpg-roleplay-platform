//! runtime —— per-user runtime 元数据 + state checkout。
//!
//! 对应 Python `rpg/platform_app/runtime.py` (346 行)。
//! 完成度: **完整**(file backend)+ **主路径**(db backend),legacy 迁移 TODO。

pub mod worldline;

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{PgPool, Row};

use crate::error::PlatformResult;

/// runtime 元数据(对应 Python `read_runtime` 返回的 dict)。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct UserRuntime {
    pub user_id: i64,
    pub save_id: i64,
    pub active_commit_id: i64,
    pub active_branch_node_id: i64,
    pub active_ref_id: Option<i64>,
    #[serde(default)]
    pub source_state_path: String,
    #[serde(default)]
    pub runtime_state_path: String,
    #[serde(default = "default_game_url")]
    pub game_url: String,

    // ── 附加(_attach_db_state)──
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dirty: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub snapshot_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub turn_at_commit: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub turn_runtime: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub turns_ahead: Option<i64>,
}

fn default_game_url() -> String {
    "/".to_string()
}

/// 选择当前 runtime backend(db | file),对应 Python `_runtime_backend()`。
pub fn backend() -> RuntimeBackend {
    let cfg = rpg_core::config::runtime_backend().to_lowercase();
    match cfg.as_str() {
        "db" => RuntimeBackend::Db,
        "file" => RuntimeBackend::File,
        _ => {
            if rpg_core::config::require_auth() {
                return RuntimeBackend::Db;
            }
            let mode = rpg_core::config::deployment_mode().to_lowercase();
            match mode.as_str() {
                "local" | "desktop" | "self_hosted" | "self-hosted" => RuntimeBackend::File,
                _ => RuntimeBackend::Db,
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeBackend {
    Db,
    File,
}

fn runtime_root() -> PathBuf {
    let base = std::env::var("RPG_PLATFORM_DATA_DIR")
        .ok()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("./platform_data"));
    base.join("runtime")
}

fn runtime_state_root() -> PathBuf {
    let base = std::env::var("RPG_PLATFORM_DATA_DIR")
        .ok()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("./platform_data"));
    base.join("runtime_states")
}

fn runtime_file(user_id: Option<i64>) -> PathBuf {
    match user_id {
        Some(uid) => runtime_root().join(format!("user_{uid}.json")),
        None => {
            let mut p = runtime_root();
            p.pop(); // platform_data
            p.join("runtime.json")
        }
    }
}

fn runtime_state_path(user_id: i64, save_id: i64) -> PathBuf {
    runtime_state_root()
        .join(format!("user_{user_id}"))
        .join(format!("save_{save_id}.json"))
}

// ─── 顶层 API ────────────────────────────────────────────────────────────

/// Python `read_runtime(user_id)`。
pub async fn read_runtime(pool: &PgPool, user_id: Option<i64>) -> PlatformResult<UserRuntime> {
    match (backend(), user_id) {
        (RuntimeBackend::Db, Some(uid)) => {
            let payload = db_read_runtime(pool, uid).await?;
            Ok(attach_db_state(pool, payload).await)
        }
        _ => {
            let payload = file_read_runtime(user_id).unwrap_or_default();
            Ok(attach_db_state(pool, payload).await)
        }
    }
}

/// Python `write_runtime(user_id, save_id, node_id, source_state_path, ref_id, runtime_state_path)`。
pub async fn write_runtime(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    node_id: i64,
    source_state_path: &str,
    ref_id: Option<i64>,
    runtime_state_path_override: Option<&str>,
) -> PlatformResult<UserRuntime> {
    let mut payload = UserRuntime {
        user_id,
        save_id,
        active_commit_id: node_id,
        active_branch_node_id: node_id,
        active_ref_id: ref_id,
        source_state_path: source_state_path.to_string(),
        runtime_state_path: runtime_state_path_override.unwrap_or("").to_string(),
        game_url: "/".to_string(),
        ..Default::default()
    };

    match backend() {
        RuntimeBackend::Db => {
            db_write_runtime(pool, &payload).await?;
            Ok(payload)
        }
        RuntimeBackend::File => {
            std::fs::create_dir_all(runtime_root())?;
            let state_path = if !payload.runtime_state_path.is_empty() {
                PathBuf::from(&payload.runtime_state_path)
            } else {
                runtime_state_path(user_id, save_id)
            };
            if let Some(parent) = state_path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            let source = std::path::Path::new(source_state_path);
            if source.exists() && source.canonicalize().ok() != state_path.canonicalize().ok() {
                let _ = std::fs::copy(source, &state_path);
            }
            payload.runtime_state_path = state_path.to_string_lossy().into_owned();
            let out_path = runtime_file(Some(user_id));
            if let Some(parent) = out_path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(&out_path, serde_json::to_string_pretty(&payload)?)?;
            Ok(payload)
        }
    }
}

/// Python `activate_state_file(...)`。
pub async fn activate_state_file(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    node_id: i64,
    source_state_path: &str,
    ref_id: Option<i64>,
) -> PlatformResult<UserRuntime> {
    if backend() == RuntimeBackend::Db {
        return write_runtime(pool, user_id, save_id, node_id, source_state_path, ref_id, None).await;
    }
    // file backend
    let runtime_state = runtime_state_path(user_id, save_id);
    if let Some(parent) = runtime_state.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let source = std::path::Path::new(source_state_path);
    if source.exists() {
        if source.canonicalize().ok() != runtime_state.canonicalize().ok() {
            let _ = std::fs::copy(source, &runtime_state);
        }
    } else {
        let fallback = serde_json::json!({"history": [], "turn": 0});
        std::fs::write(&runtime_state, serde_json::to_string_pretty(&fallback)?)?;
    }
    write_runtime(
        pool,
        user_id,
        save_id,
        node_id,
        source_state_path,
        ref_id,
        Some(runtime_state.to_string_lossy().as_ref()),
    )
    .await
}

/// Python `activate_state_snapshot(...)`。
pub async fn activate_state_snapshot(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    node_id: i64,
    state_data: &Value,
    source_state_path: &str,
    ref_id: Option<i64>,
) -> PlatformResult<UserRuntime> {
    if backend() == RuntimeBackend::Db {
        return write_runtime(pool, user_id, save_id, node_id, source_state_path, ref_id, None).await;
    }
    let runtime_state = runtime_state_path(user_id, save_id);
    if let Some(parent) = runtime_state.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let text = serde_json::to_string_pretty(state_data)?;
    std::fs::write(&runtime_state, text)?;
    write_runtime(
        pool,
        user_id,
        save_id,
        node_id,
        source_state_path,
        ref_id,
        Some(runtime_state.to_string_lossy().as_ref()),
    )
    .await
}

/// Python `update_active_node(...)`。
pub async fn update_active_node(
    pool: &PgPool,
    user_id: Option<i64>,
    node_id: i64,
    source_state_path: &str,
    ref_id: Option<i64>,
) -> PlatformResult<UserRuntime> {
    let mut payload = read_runtime(pool, user_id).await?;
    if payload.user_id == 0 {
        return Ok(payload);
    }
    payload.active_commit_id = node_id;
    payload.active_branch_node_id = node_id;
    if let Some(rid) = ref_id {
        payload.active_ref_id = Some(rid);
    }
    payload.source_state_path = source_state_path.to_string();
    payload.game_url = "/".to_string();

    if backend() == RuntimeBackend::Db {
        db_write_runtime(pool, &payload).await?;
        return Ok(payload);
    }
    // file backend(简化版)
    let runtime_state = if payload.runtime_state_path.is_empty() {
        runtime_state_path(payload.user_id, payload.save_id)
    } else {
        PathBuf::from(&payload.runtime_state_path)
    };
    if let Some(parent) = runtime_state.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let source = std::path::Path::new(source_state_path);
    if source.exists() && source.canonicalize().ok() != runtime_state.canonicalize().ok() {
        let _ = std::fs::copy(source, &runtime_state);
    }
    payload.runtime_state_path = runtime_state.to_string_lossy().into_owned();
    let out_path = runtime_file(Some(payload.user_id));
    if let Some(parent) = out_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&out_path, serde_json::to_string_pretty(&payload)?)?;
    Ok(payload)
}

// ─── backend 内部 ────────────────────────────────────────────────────────

async fn db_read_runtime(pool: &PgPool, user_id: i64) -> PlatformResult<UserRuntime> {
    let row = sqlx::query(
        r#"
        select user_id, save_id, active_commit_id, active_branch_node_id,
               active_ref_id, source_state_path, runtime_state_path, game_url, metadata
          from user_runtime
         where user_id = $1
        "#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    let Some(row) = row else {
        return Ok(UserRuntime::default());
    };
    let metadata: serde_json::Value = row.try_get("metadata").unwrap_or(serde_json::json!({}));
    let mut payload = UserRuntime {
        user_id: row.try_get::<i64, _>("user_id").unwrap_or(user_id),
        save_id: row.try_get::<i64, _>("save_id").unwrap_or(0),
        active_commit_id: row.try_get::<i64, _>("active_commit_id").unwrap_or(0),
        active_branch_node_id: row.try_get::<i64, _>("active_branch_node_id").unwrap_or(0),
        active_ref_id: row.try_get::<Option<i64>, _>("active_ref_id").unwrap_or(None),
        source_state_path: row.try_get::<String, _>("source_state_path").unwrap_or_default(),
        runtime_state_path: row.try_get::<String, _>("runtime_state_path").unwrap_or_default(),
        game_url: row.try_get::<String, _>("game_url").unwrap_or_else(|_| "/".into()),
        ..Default::default()
    };
    // 把 metadata 里的 dirty/turns 等字段拍平回 payload(简化:只关心几个常用字段)
    if let Some(obj) = metadata.as_object() {
        if let Some(v) = obj.get("dirty").and_then(|x| x.as_bool()) {
            payload.dirty = Some(v);
        }
        if let Some(v) = obj.get("snapshot_hash").and_then(|x| x.as_str()) {
            payload.snapshot_hash = Some(v.to_string());
        }
    }
    Ok(payload)
}

async fn db_write_runtime(pool: &PgPool, payload: &UserRuntime) -> PlatformResult<()> {
    if payload.user_id == 0 {
        return Ok(());
    }
    let metadata = serde_json::json!({}); // 主路径里不放额外字段
    sqlx::query(
        r#"
        insert into user_runtime(user_id, save_id, active_commit_id, active_branch_node_id,
                                 active_ref_id, source_state_path, runtime_state_path,
                                 game_url, metadata, updated_at)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
        on conflict(user_id) do update set
          save_id = excluded.save_id,
          active_commit_id = excluded.active_commit_id,
          active_branch_node_id = excluded.active_branch_node_id,
          active_ref_id = excluded.active_ref_id,
          source_state_path = excluded.source_state_path,
          runtime_state_path = excluded.runtime_state_path,
          game_url = excluded.game_url,
          metadata = excluded.metadata,
          updated_at = now()
        "#,
    )
    .bind(payload.user_id)
    .bind(if payload.save_id == 0 { None } else { Some(payload.save_id) })
    .bind(if payload.active_commit_id == 0 { None } else { Some(payload.active_commit_id) })
    .bind(if payload.active_branch_node_id == 0 { None } else { Some(payload.active_branch_node_id) })
    .bind(payload.active_ref_id)
    .bind(&payload.source_state_path)
    .bind(&payload.runtime_state_path)
    .bind(&payload.game_url)
    .bind(metadata)
    .execute(pool)
    .await?;
    Ok(())
}

fn file_read_runtime(user_id: Option<i64>) -> Option<UserRuntime> {
    let path = runtime_file(user_id);
    let text = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str::<UserRuntime>(&text).ok()
}

async fn attach_db_state(pool: &PgPool, mut payload: UserRuntime) -> UserRuntime {
    if payload.save_id == 0 || payload.user_id == 0 {
        return payload;
    }
    let row = sqlx::query(
        r#"
        select dirty, snapshot_hash, turn_at_commit, turn_runtime, commit_id, ref_id
          from runtime_checkouts
         where user_id = $1 and save_id = $2
        "#,
    )
    .bind(payload.user_id)
    .bind(payload.save_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();
    let Some(r) = row else { return payload };
    payload.dirty = Some(r.try_get::<bool, _>("dirty").unwrap_or(false));
    payload.snapshot_hash = Some(r.try_get::<String, _>("snapshot_hash").unwrap_or_default());
    let turn_at = r.try_get::<i64, _>("turn_at_commit").unwrap_or(0);
    let turn_runtime = r.try_get::<i64, _>("turn_runtime").unwrap_or(0);
    payload.turn_at_commit = Some(turn_at);
    payload.turn_runtime = Some(turn_runtime);
    payload.turns_ahead = Some(turn_runtime - turn_at);
    payload
}
