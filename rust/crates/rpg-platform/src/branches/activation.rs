//! activation —— `continue_from / activate_node / activate_save`。
//!
//! 对应 Python `branches/activation.py`。
//!
//! 完成度: 主路径(三个入口都连通 set_save_active + write_checkout + runtime 同步)。
//! `runtime_info` 详情:由 `runtime::activate_state_snapshot` 返回 `UserRuntime`,
//! caller 自行 expose 字段(对齐 Python 返回 dict 的形态)。

use sqlx::PgPool;

use crate::error::{PlatformError, PlatformResult};
use crate::runtime;

use super::commits::commit_for_user;
use super::helpers::commit_state;
use super::refs::{find_or_create_ref_for_commit, set_save_active, upsert_ref, write_checkout};
use super::seed::seed_tree;
use super::tree_ops::{tree, TreeResult};

/// Python `continue_from(user_id, node_id)` —— 从指定 commit 拉一条新分支。
pub async fn continue_from(
    pool: &PgPool,
    user_id: i64,
    node_id: i64,
) -> PlatformResult<TreeResult> {
    let node = commit_for_user(pool, user_id, node_id)
        .await?
        .ok_or_else(|| PlatformError::forbidden("无权访问该分支节点"))?;

    let save_id = node.save_id;
    let state_snapshot = commit_state(Some(&node.state_snapshot), &node.state_path);
    let state_path = node.state_path.clone();

    let new_ref_name = format!("refs/heads/from-{}-{}", node.id, random_hex(4));
    let r = upsert_ref(pool, save_id, &new_ref_name, node.id, true, "head").await?;
    let active_commit_id = node.id;
    let active_ref_id = Some(r.id);
    set_save_active(pool, save_id, active_commit_id, active_ref_id).await?;
    write_checkout(pool, user_id, save_id, active_ref_id, active_commit_id).await?;

    runtime::activate_state_snapshot(
        pool,
        user_id,
        save_id,
        active_commit_id,
        &state_snapshot,
        &state_path,
        active_ref_id,
    )
    .await?;

    tree(pool, user_id, save_id).await
}

/// Python `activate_node(user_id, node_id)` —— 把当前活跃分支移到 node_id。
///
/// 与 `continue_from` 区别:不新建 ref,优先复用已指向该 commit 的 ref(`find_or_create_ref_for_commit`)。
pub async fn activate_node(
    pool: &PgPool,
    user_id: i64,
    node_id: i64,
) -> PlatformResult<TreeResult> {
    let node = commit_for_user(pool, user_id, node_id)
        .await?
        .ok_or_else(|| PlatformError::forbidden("无权访问该分支节点"))?;
    let save_id = node.save_id;
    let state_snapshot = commit_state(Some(&node.state_snapshot), &node.state_path);
    let state_path = node.state_path.clone();

    let r = find_or_create_ref_for_commit(pool, user_id, save_id, node.id).await?;
    let active_ref_id = Some(r.id);
    set_save_active(pool, save_id, node.id, active_ref_id).await?;
    write_checkout(pool, user_id, save_id, active_ref_id, node.id).await?;
    runtime::activate_state_snapshot(
        pool,
        user_id,
        save_id,
        node.id,
        &state_snapshot,
        &state_path,
        active_ref_id,
    )
    .await?;
    tree(pool, user_id, save_id).await
}

/// Python `activate_save(user_id, save_id)` —— 切到 save 的当前活跃 commit。
///
/// 流程对齐 Python:
/// 1. 校验 save 归属
/// 2. 优先用 `active_branch_node_id` 找到 commit,失败则取最早 commit
/// 3. 没有任何 commit → 自动 `seed_tree`,再取最早
/// 4. 仍没有 → `PlatformError::Validation`(对齐 Python ValueError)
/// 5. `find_or_create_ref_for_commit` + `set_save_active` + `write_checkout` + runtime
pub async fn activate_save(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
) -> PlatformResult<TreeResult> {
    let save_row = sqlx::query_as::<_, (Option<i64>, String)>(
        "select active_branch_node_id, coalesce(state_path,'') from game_saves where id = $1 and user_id = $2",
    )
    .bind(save_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    let (active_node, fallback_state_path) = match save_row {
        Some((n, p)) => (n, p),
        None => return Err(PlatformError::forbidden("无权访问该存档")),
    };

    // 先按 active_branch_node_id 找
    let mut commit = match active_node {
        Some(nid) => {
            let row = sqlx::query(
                "select * from branch_commits where id = $1 and save_id = $2",
            )
            .bind(nid)
            .bind(save_id)
            .fetch_optional(pool)
            .await?;
            match row {
                Some(r) => Some(super::commits::BranchCommit::from_row(&r)?),
                None => None,
            }
        }
        None => None,
    };
    if commit.is_none() {
        let row = sqlx::query(
            "select * from branch_commits where save_id = $1 order by turn_index asc, id asc limit 1",
        )
        .bind(save_id)
        .fetch_optional(pool)
        .await?;
        commit = match row {
            Some(r) => Some(super::commits::BranchCommit::from_row(&r)?),
            None => None,
        };
    }
    // 还没有 → seed_tree 再试
    if commit.is_none() {
        seed_tree(pool, save_id, &fallback_state_path).await?;
        let row = sqlx::query(
            "select * from branch_commits where save_id = $1 order by turn_index asc, id asc limit 1",
        )
        .bind(save_id)
        .fetch_optional(pool)
        .await?;
        commit = match row {
            Some(r) => Some(super::commits::BranchCommit::from_row(&r)?),
            None => None,
        };
    }
    let commit = commit
        .ok_or_else(|| PlatformError::validation("save 没有任何 commit,无法激活"))?;

    let state_snapshot = commit_state(Some(&commit.state_snapshot), &commit.state_path);
    let state_path = if commit.state_path.is_empty() {
        fallback_state_path
    } else {
        commit.state_path.clone()
    };
    let r = find_or_create_ref_for_commit(pool, user_id, save_id, commit.id).await?;
    let active_ref_id = Some(r.id);
    set_save_active(pool, save_id, commit.id, active_ref_id).await?;
    write_checkout(pool, user_id, save_id, active_ref_id, commit.id).await?;
    runtime::activate_state_snapshot(
        pool,
        user_id,
        save_id,
        commit.id,
        &state_snapshot,
        &state_path,
        active_ref_id,
    )
    .await?;

    tree(pool, user_id, save_id).await
}

fn random_hex(n: usize) -> String {
    let mut buf = vec![0u8; n];
    rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut buf);
    let mut s = String::with_capacity(n * 2);
    for b in buf {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

