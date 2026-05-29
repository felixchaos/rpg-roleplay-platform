//! tree_ops —— GET /api/branches/tree 的核心。
//!
//! 对应 Python `branches/tree_ops.py`。
//! 完成度: `tree` / `collect_ids` 主路径,`resolve_commit_id_by_message` / `round_start_node` TODO。

use serde::{Deserialize, Serialize};
use sqlx::PgPool;

use crate::error::PlatformResult;

use super::commits::BranchCommit;

/// GET /api/branches/tree 的回包结构。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TreeResult {
    pub ok: bool,
    pub save_id: i64,
    pub nodes: Vec<BranchCommit>,
    pub active_commit_id: Option<i64>,
    pub active_ref_id: Option<i64>,
    pub total: usize,
}

/// Python `tree(user_id, save_id, limit, cursor)` 的主路径(不分页,后续可加 cursor)。
pub async fn tree(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
) -> PlatformResult<TreeResult> {
    // 校验 save 归属
    let active: Option<(Option<i64>, Option<i64>)> = sqlx::query_as(
        "select active_commit_id, active_ref_id from game_saves where id = $1 and user_id = $2",
    )
    .bind(save_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;

    let (active_commit_id, active_ref_id) = match active {
        Some((c, r)) => (c, r),
        None => {
            return Ok(TreeResult {
                ok: false,
                save_id,
                nodes: Vec::new(),
                active_commit_id: None,
                active_ref_id: None,
                total: 0,
            });
        }
    };

    let rows = sqlx::query(
        r#"
        select * from branch_commits
         where save_id = $1
         order by turn_index asc, id asc
        "#,
    )
    .bind(save_id)
    .fetch_all(pool)
    .await?;
    let nodes: Vec<BranchCommit> = rows.iter().filter_map(|r| BranchCommit::from_row(r).ok()).collect();
    let total = nodes.len();
    Ok(TreeResult {
        ok: true,
        save_id,
        nodes,
        active_commit_id,
        active_ref_id,
        total,
    })
}

/// Python `collect_ids(db, node_id)` —— 收集 node_id 子树所有 id(用于 delete_subtree)。
pub async fn collect_ids(pool: &PgPool, node_id: i64) -> PlatformResult<Vec<i64>> {
    let rows: Vec<(i64,)> = sqlx::query_as(
        r#"
        with recursive subtree as (
            select id from branch_commits where id = $1
            union all
            select c.id from branch_commits c join subtree s on c.parent_id = s.id
        )
        select id from subtree
        "#,
    )
    .bind(node_id)
    .fetch_all(pool)
    .await?;
    Ok(rows.into_iter().map(|(id,)| id).collect())
}

// TODO[Sonnet]: resolve_commit_id_by_message(user_id, save_id, message_index) —
//               按 message index 找对应 commit_id(用于 rollback_to_message)
// TODO[Sonnet]: round_start_node(db, node) — 找到回合开头(player kind)
