//! seed —— `seed_tree(save_id, state_path)` 骨架。
//!
//! 对应 Python `branches/seed.py`。新存档建 root commit + 默认 ref;老数据迁移
//! 留 TODO(`migrate_legacy_nodes` 需要把 `branch_nodes` 老表读出来重建)。

use sqlx::PgPool;

use crate::error::PlatformResult;

use super::commits::insert_commit;
use super::helpers::{load_state, snapshot_for_history, write_snapshot, MAIN_REF};
use super::maintenance::{ensure_state_snapshots, ensure_summaries};
use super::refs::{set_save_active, upsert_ref};

/// Python `seed_tree(save_id, state_path)`。
///
/// 行为(对应 Python 三分支判断):
/// 1. 已有 commit → 跑 ensure_*,返回
/// 2. 仅有 branch_nodes 老数据 → migrate(TODO)
/// 3. 全新 → 建 root commit + main ref
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
        // TODO[Sonnet]: refs::ensure_active_ref(pool, save_id)
        return Ok(());
    }

    // 2) 老表 branch_nodes —— TODO
    let has_legacy: Option<(i64,)> =
        sqlx::query_as("select 1::bigint from branch_nodes where save_id = $1 limit 1")
            .bind(save_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);
    if has_legacy.is_some() {
        // TODO[Sonnet]: migrate_legacy_nodes(pool, save_id)
        return Ok(());
    }

    // 3) 全新存档,落 root
    // 读 save_row.state_snapshot;失败 fallback 读盘
    let snap_opt: Option<(serde_json::Value,)> =
        sqlx::query_as("select state_snapshot from game_saves where id = $1")
            .bind(save_id)
            .fetch_optional(pool)
            .await?;
    let data = snap_opt
        .map(|(v,)| v)
        .filter(|v| v.is_object() && !v.as_object().map(|o| o.is_empty()).unwrap_or(true))
        .unwrap_or_else(|| load_state(std::path::Path::new(state_path)));

    let root_snapshot = snapshot_for_history(&data, 0);
    let root_state = write_snapshot(save_id, 0, &root_snapshot)?;
    let metadata = serde_json::json!({});
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
        &root_snapshot,
        "",
        "",
        &metadata,
    )
    .await?;
    let main = upsert_ref(pool, save_id, MAIN_REF, root.id, true, "head").await?;
    set_save_active(pool, save_id, root.id, Some(main.id)).await?;
    Ok(())
}

// TODO[Sonnet]: migrate_legacy_nodes(db, save_id) —— 老 branch_nodes 表迁到 branch_commits
// TODO[Sonnet]: seed_and_bootstrap(owner_id, save_id, state_path, user_id) —
//               seed_tree + bootstrap_runtime_binding 一把梭
