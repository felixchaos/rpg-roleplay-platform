//! merge —— `merge_branch` 分支合并。
//!
//! 支持两种模式:
//! - **fast-forward**: source commit 是 target 的后代 → 直接移动 target ref
//! - **merge commit**: 否则 → 创建 merge commit(parent=target, 合并 state_snapshot)

use serde_json::{json, Value};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

use super::commits::insert_commit_with_tx;
use super::helpers::{commit_state, write_snapshot};
use super::refs::{set_save_active, upsert_ref};
use super::tree_ops::{tree, TreeResult};

/// 判断 `ancestor_id` 是否是 `descendant_id` 的祖先(含自身)。
///
/// 从 descendant 沿 parent_id 链向上走,若命中 ancestor_id 则返回 true。
async fn is_ancestor(pool: &PgPool, ancestor_id: i64, descendant_id: i64) -> PlatformResult<bool> {
    if ancestor_id == descendant_id {
        return Ok(true);
    }
    // CTE 递归向上查 parent 链,深度限制 10000 防止无限环
    let found: Option<(i64,)> = sqlx::query_as(
        r#"
        with recursive ancestors as (
            select parent_id, 1 as depth from branch_commits where id = $1
            union all
            select c.parent_id, a.depth + 1
              from branch_commits c
              join ancestors a on c.id = a.parent_id
             where a.depth < 10000
        )
        select 1::bigint from ancestors where parent_id = $2
        "#,
    )
    .bind(descendant_id)
    .bind(ancestor_id)
    .fetch_optional(pool)
    .await?;
    // 上面查的是 parent_id chain,再补查 descendant 本身的 parent
    // 更直接的写法:ancestors 从 id 出发而非 parent_id
    let found2: Option<(i64,)> = sqlx::query_as(
        r#"
        with recursive chain as (
            select id, parent_id from branch_commits where id = $1
            union all
            select c.id, c.parent_id
              from branch_commits c
              join chain ch on c.id = ch.parent_id
        )
        select 1::bigint from chain where id = $2
        "#,
    )
    .bind(descendant_id)
    .bind(ancestor_id)
    .fetch_optional(pool)
    .await?;
    Ok(found.is_some() || found2.is_some())
}

/// 合并两个 branch ref。
///
/// - `source_ref_id`: 要合并进来的分支 ref
/// - `target_ref_id`: 被合并到的目标分支 ref
///
/// 如果 source commit 是 target commit 的后代(target 是 source 的祖先),
/// 则 fast-forward:直接把 target ref 指向 source commit。
/// 否则创建 merge commit(parent = target commit, state = source 的 state_snapshot)
/// 并把 target ref 指向新 merge commit。
///
/// 合并后更新 active ref。
pub async fn merge_branch(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    source_ref_id: i64,
    target_ref_id: i64,
) -> PlatformResult<TreeResult> {
    // 1. 找 source ref 和 target ref
    let source_ref_row = sqlx::query(
        "select * from branch_refs where id = $1 and save_id = $2",
    )
    .bind(source_ref_id)
    .bind(save_id)
    .fetch_optional(pool)
    .await?
    .ok_or_else(|| PlatformError::not_found("source ref 不存在"))?;
    let source_commit_id: i64 = source_ref_row.try_get("target_commit_id")?;

    let target_ref_row = sqlx::query(
        "select * from branch_refs where id = $1 and save_id = $2",
    )
    .bind(target_ref_id)
    .bind(save_id)
    .fetch_optional(pool)
    .await?
    .ok_or_else(|| PlatformError::not_found("target ref 不存在"))?;
    let target_commit_id: i64 = target_ref_row.try_get("target_commit_id")?;
    let target_ref_name: String = target_ref_row.try_get("name")?;

    if source_commit_id == target_commit_id {
        // 已经在同一个 commit,无需合并
        return tree(pool, user_id, save_id).await;
    }

    // 2. 检查是否可以 fast-forward
    //    fast-forward 条件:target commit 是 source commit 的祖先
    let can_ff = is_ancestor(pool, target_commit_id, source_commit_id).await?;

    let new_commit_id = if can_ff {
        // fast-forward:直接把 target ref 移到 source commit
        source_commit_id
    } else {
        // 创建 merge commit
        // 取 source commit 的 state_snapshot 作为合并后的 state
        let source_row = sqlx::query(
            "select state_snapshot, state_path, turn_index from branch_commits where id = $1",
        )
        .bind(source_commit_id)
        .fetch_optional(pool)
        .await?
        .ok_or_else(|| PlatformError::not_found("source commit 不存在"))?;

        let source_snapshot: Value = source_row
            .try_get::<Value, _>("state_snapshot")
            .unwrap_or(Value::Null);
        let source_state_path: String = source_row
            .try_get::<String, _>("state_path")
            .unwrap_or_default();
        let source_turn: i32 = source_row.try_get::<i32, _>("turn_index").unwrap_or(0);

        let merged_state = commit_state(Some(&source_snapshot), &source_state_path);
        let merged_typed: rpg_schemas::GameStateData =
            serde_json::from_value(merged_state.clone()).unwrap_or_default();
        let snap_path = write_snapshot(save_id, (source_turn + 1) as usize, &merged_typed)
            .map_err(PlatformError::from)?;

        let metadata = json!({
            "source": "merge",
            "source_ref_id": source_ref_id,
            "target_ref_id": target_ref_id,
            "source_commit_id": source_commit_id,
            "target_commit_id": target_commit_id,
        });

        let mut tx = pool.begin().await?;
        let commit = insert_commit_with_tx(
            &mut tx,
            save_id,
            Some(target_commit_id), // parent = target
            source_turn + 1,
            "merge",
            "合并分支",
            &format!("合并 ref#{source_ref_id} → ref#{target_ref_id}"),
            "合并分支",
            "合并分支",
            &snap_path,
            &merged_state,
            "",
            "",
            &metadata,
        )
        .await?;
        tx.commit().await?;
        commit.id
    };

    // 3. 更新 target ref 指向新的 commit
    let updated_ref = upsert_ref(
        pool,
        save_id,
        &target_ref_name,
        new_commit_id,
        true,
        "head",
    )
    .await?;

    // 4. 更新 active
    set_save_active(pool, save_id, new_commit_id, Some(updated_ref.id)).await?;

    tree(pool, user_id, save_id).await
}
