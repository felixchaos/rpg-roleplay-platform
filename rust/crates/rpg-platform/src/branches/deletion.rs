//! deletion —— `delete_subtree / rollback_to_message` 骨架。
//!
//! 对应 Python `branches/deletion.py`。

use sqlx::PgPool;

use crate::error::{PlatformError, PlatformResult};

use super::commits::commit_for_user;
use super::tree_ops::collect_ids;

/// Python `delete_subtree(user_id, node_id)` —— 删除以 node_id 为根的整棵子树。
pub async fn delete_subtree(
    pool: &PgPool,
    user_id: i64,
    node_id: i64,
) -> PlatformResult<usize> {
    let node = commit_for_user(pool, user_id, node_id)
        .await?
        .ok_or_else(|| PlatformError::forbidden("无权访问该分支节点"))?;
    if node.parent_id.is_none() {
        return Err(PlatformError::validation("不能删除 root 节点"));
    }
    let ids = collect_ids(pool, node_id).await?;
    if ids.is_empty() {
        return Ok(0);
    }
    // 删除指向这些 commit 的 ref,再删 commit
    sqlx::query("delete from branch_refs where target_commit_id = any($1)")
        .bind(&ids)
        .execute(pool)
        .await?;
    let deleted = sqlx::query("delete from branch_commits where id = any($1)")
        .bind(&ids)
        .execute(pool)
        .await?;
    // TODO[Sonnet]: unlink branch state files (helpers::unlink_branch_state)
    Ok(deleted.rows_affected() as usize)
}

// TODO[Sonnet]: rollback_to_message(user_id, save_id, message_index) —
//               用 tree_ops::resolve_commit_id_by_message 找到 commit,删除其后子树。
