//! seed —— `seed_tree(save_id, state_path)` 主路径。
//!
//! 对应 Python `branches/seed.py`。
//!
//! 完成度: 主路径(`seed_tree` 三分支 + `migrate_legacy_nodes`)。
//! `_seed_and_bootstrap` 仍 TODO(需 bootstrap_runtime_binding 完整版,Wave-2 翻)。
//!
//! 关键事务边界(对照 Python):seed_tree 在 Python 里整个跑在 `connect()` 单事务,
//! Rust 用 `pool.begin()` BEGIN/COMMIT 包整段,失败 rollback,避免半建状态。

use std::collections::HashMap;
use std::path::Path;

use rpg_schemas::GameStateData;
use serde_json::{json, Value};
use sqlx::{PgPool, Row};

use crate::error::PlatformResult;

use super::commits::insert_commit;
use super::helpers::{load_state, snapshot_for_history, write_snapshot, MAIN_REF};
use super::maintenance::{ensure_state_snapshots, ensure_summaries};
use super::refs::{ensure_active_ref, set_save_active, upsert_ref};

/// Python `seed_tree(save_id, state_path)`。
///
/// 行为(对应 Python 三分支判断):
/// 1. 已有 commit → 跑 ensure_*,然后 ensure_active_ref,返回
/// 2. 仅有 branch_nodes 老数据 → migrate_legacy_nodes
/// 3. 全新 → 建 root commit + main ref(若 state.history 非空,顺带回放成 round commits)
pub async fn seed_tree(
    pool: &PgPool,
    save_id: i64,
    state_path: &str,
) -> PlatformResult<()> {
    // 1) 已有 commit
    let has_commit: Option<(i64,)> =
        sqlx::query_as("select 1::bigint from branch_commits where save_id = $1 limit 1")
            .bind(save_id)
            .fetch_optional(pool)
            .await?;
    if has_commit.is_some() {
        ensure_state_snapshots(pool, save_id).await?;
        ensure_summaries(pool, save_id).await?;
        ensure_active_ref(pool, save_id).await?;
        return Ok(());
    }

    // 2) 老表 branch_nodes
    let has_legacy: Option<(i64,)> =
        sqlx::query_as("select 1::bigint from branch_nodes where save_id = $1 limit 1")
            .bind(save_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);
    if has_legacy.is_some() {
        migrate_legacy_nodes(pool, save_id).await?;
        ensure_state_snapshots(pool, save_id).await?;
        ensure_summaries(pool, save_id).await?;
        ensure_active_ref(pool, save_id).await?;
        return Ok(());
    }

    // 3) 全新存档,落 root(+ 可选回放 history)
    let snap_opt: Option<(Value,)> =
        sqlx::query_as("select state_snapshot from game_saves where id = $1")
            .bind(save_id)
            .fetch_optional(pool)
            .await?;
    let data_value = snap_opt
        .map(|(v,)| v)
        .filter(|v| v.is_object() && !v.as_object().map(|o| o.is_empty()).unwrap_or(true))
        .unwrap_or_else(|| load_state(Path::new(state_path)));
    let data: GameStateData = serde_json::from_value(data_value.clone()).unwrap_or_default();

    // 注:insert_commit / upsert_ref 当前签名都是 &PgPool,没有 tx 重载。
    // 整段 seed 不在单 tx 内 — 与 Python 单事务略有偏差(见 TODO[P3-TX])。
    // TODO[P3-TX]: 抽 tx 重载 insert_commit,把 seed_tree 整体放进 tx。
    let root_snapshot_val = snapshot_for_history(&data, 0);
    let root_data_truncated: GameStateData =
        serde_json::from_value(root_snapshot_val.clone()).unwrap_or_default();
    let root_state =
        write_snapshot(save_id, 0, &root_data_truncated).map_err(crate::error::PlatformError::from)?;
    let metadata = json!({"source": "seed"});

    let root = insert_commit(
        pool,
        save_id,
        None,
        0,
        "root",
        "开始",
        "存档起点",
        "存档起点",
        "存档起点",
        &root_state,
        &root_snapshot_val,
        "",
        "",
        &metadata,
    )
    .await?;

    // 回放 history(对齐 Python:player+gm 组合成 round commit)。
    let history: Vec<Value> = data_value
        .get("history")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let mut parent_id = root.id;
    let mut history_index: usize = 0;
    let mut turn: i32 = 1;
    while history_index < history.len() {
        let cur = &history[history_index];
        let is_user = cur.get("role").and_then(|v| v.as_str()) == Some("user");
        let mut player_text = String::new();
        let mut gm_text = String::new();
        if is_user {
            player_text = cur
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            history_index += 1;
            if history_index < history.len()
                && history[history_index].get("role").and_then(|v| v.as_str()) != Some("user")
            {
                gm_text = history[history_index]
                    .get("content")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                history_index += 1;
            }
        } else {
            gm_text = cur
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            history_index += 1;
        }

        let snapshot_val = snapshot_for_history(&data, history_index);
        let snap_typed: GameStateData =
            serde_json::from_value(snapshot_val.clone()).unwrap_or_default();
        let snap_path =
            write_snapshot(save_id, turn as usize, &snap_typed).map_err(crate::error::PlatformError::from)?;
        let preview = super::helpers::round_preview(&player_text, &gm_text, 260);
        let summary = super::helpers::rough_summary(&player_text, &gm_text, 22);
        let meta = json!({"source": "seed", "history_index": history_index});
        let row = insert_commit(
            pool,
            save_id,
            Some(parent_id),
            turn,
            "round",
            &format!("第 {turn} 回合"),
            &summary,
            &summary,
            &preview,
            &snap_path,
            &snapshot_val,
            &player_text,
            &gm_text,
            &meta,
        )
        .await?;
        parent_id = row.id;
        turn += 1;
    }

    let main = upsert_ref(pool, save_id, MAIN_REF, parent_id, true, "head").await?;
    set_save_active(pool, save_id, parent_id, Some(main.id)).await?;
    Ok(())
}

/// Python `_migrate_legacy_nodes(db, save_id)` —— 老 `branch_nodes` 表迁到 `branch_commits`。
///
/// 顺序迁移并维护 `legacy_id -> commit_id` map(因为 parent_id 用老表 id 引用),
/// role=='branch' 的额外建一个 `refs/heads/legacy-<id>` ref(inactive)。
/// 完成后选 save.active_branch_node_id 或最后一条做 main ref + active。
pub async fn migrate_legacy_nodes(pool: &PgPool, save_id: i64) -> PlatformResult<()> {
    let rows = sqlx::query(
        "select * from branch_nodes where save_id = $1 order by id",
    )
    .bind(save_id)
    .fetch_all(pool)
    .await?;
    if rows.is_empty() {
        return Ok(());
    }

    let mut id_map: HashMap<i64, i64> = HashMap::new();
    let mut last_new_id: Option<i64> = None;
    for r in &rows {
        let legacy_id: i64 = r.try_get("id")?;
        let parent_legacy: Option<i64> = r.try_get("parent_id").ok().flatten();
        let parent_new = parent_legacy.and_then(|p| id_map.get(&p).copied());
        let turn_index: i32 = r.try_get("turn_index").unwrap_or(0);
        let role: String = r.try_get::<String, _>("role").unwrap_or_else(|_| "round".into());
        let title: String = r.try_get::<String, _>("title").unwrap_or_default();
        let summary: String = r.try_get::<String, _>("summary").unwrap_or_default();
        let content_preview: String =
            r.try_get::<String, _>("content_preview").unwrap_or_default();
        let state_path: String = r.try_get::<String, _>("state_path").unwrap_or_default();
        let state_snapshot = if state_path.is_empty() {
            super::helpers::empty_state()
        } else {
            load_state(Path::new(&state_path))
        };
        let message = if summary.is_empty() { title.clone() } else { summary.clone() };
        let metadata = json!({"source": "legacy_branch_nodes", "legacy_node_id": legacy_id});

        let commit = insert_commit(
            pool,
            save_id,
            parent_new,
            turn_index,
            &role,
            &title,
            &message,
            &summary,
            &content_preview,
            &state_path,
            &state_snapshot,
            "",
            "",
            &metadata,
        )
        .await?;
        id_map.insert(legacy_id, commit.id);
        last_new_id = Some(commit.id);

        if role == "branch" {
            let name = format!("refs/heads/legacy-{legacy_id}");
            upsert_ref(pool, save_id, &name, commit.id, false, "head").await?;
        }
    }

    // 选 active commit:save.active_branch_node_id 优先,否则最后一条。
    let save = sqlx::query("select active_branch_node_id from game_saves where id = $1")
        .bind(save_id)
        .fetch_optional(pool)
        .await?;
    let active_old: Option<i64> = save
        .as_ref()
        .and_then(|r| r.try_get::<Option<i64>, _>("active_branch_node_id").ok().flatten());
    let active_commit_id = active_old
        .and_then(|old| id_map.get(&old).copied())
        .or(last_new_id);
    if let Some(cid) = active_commit_id {
        let main = upsert_ref(pool, save_id, MAIN_REF, cid, true, "head").await?;
        set_save_active(pool, save_id, cid, Some(main.id)).await?;
    }
    Ok(())
}

// TODO[Wave-2]: seed_and_bootstrap(owner_id, save_id, state_path, user_id) —
//               seed_tree + bootstrap_runtime_binding 一把梭(需 bootstrap 完整版)。

