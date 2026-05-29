//! activation —— `continue_from / activate_node / activate_save` 骨架。
//!
//! 对应 Python `branches/activation.py`。
//! 完成度: **骨架** — 函数签名 + 顺序 + DB 操作占位,具体的 runtime 同步留 TODO。

use sqlx::PgPool;

use crate::error::{PlatformError, PlatformResult};
use crate::runtime;

use super::commits::commit_for_user;
use super::helpers::commit_state;
use super::refs::{set_save_active, upsert_ref, write_checkout};
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

    // 通知 runtime 切换
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
    set_save_active(pool, save_id, node.id, None).await?;
    write_checkout(pool, user_id, save_id, None, node.id).await?;
    runtime::activate_state_snapshot(pool, user_id, save_id, node.id, &state_snapshot, &state_path, None).await?;
    tree(pool, user_id, save_id).await
}

/// Python `activate_save(user_id, save_id)` —— 切换到 save 的活跃分支。
pub async fn activate_save(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
) -> PlatformResult<TreeResult> {
    // 当前实现只是返回 tree(用户用 save_id 后,UI 刷新)
    // TODO[Sonnet]: 读 game_saves.active_commit_id,做 runtime 同步
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
