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
    // 列名:game_saves 用 `active_branch_ref_id`(V001 schema 对齐 Python),
    // 别写成 `active_ref_id`(那是 user_runtime 表的列,W5 排查多次踩过)。
    let active: Option<(Option<i64>, Option<i64>)> = sqlx::query_as(
        "select active_commit_id, active_branch_ref_id from game_saves where id = $1 and user_id = $2",
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

/// Python `resolve_commit_id_by_message(user_id, save_id, message_index)`。
///
/// 把前端 chat history 的 message index 映射到 `branch_commits.id`:
/// - `turn_index = message_index // 2`
/// - `is_player = message_index % 2 == 0` → 优先匹配同 turn 的 `player`/`gm` kind
/// - 找不到 preferred kind 时 fallback 同 turn 任意 kind
/// - save 不属于 user → 返回 None
/// - message_index < 0 → 返回 None(对齐 Python 边界)
pub async fn resolve_commit_id_by_message(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    message_index: i64,
) -> PlatformResult<Option<i64>> {
    if message_index < 0 {
        return Ok(None);
    }
    let turn_index = (message_index / 2) as i32;
    let is_player = message_index % 2 == 0;

    let owned: Option<(i64,)> = sqlx::query_as(
        "select 1::bigint from game_saves where id = $1 and user_id = $2",
    )
    .bind(save_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    if owned.is_none() {
        return Ok(None);
    }

    let preferred_kind = if is_player { "player" } else { "gm" };
    let preferred: Option<(i64,)> = sqlx::query_as(
        r#"
        select id from branch_commits
         where save_id = $1 and turn_index = $2 and kind = $3
         order by id desc limit 1
        "#,
    )
    .bind(save_id)
    .bind(turn_index)
    .bind(preferred_kind)
    .fetch_optional(pool)
    .await?;
    if let Some((id,)) = preferred {
        return Ok(Some(id));
    }

    let fallback: Option<(i64,)> = sqlx::query_as(
        r#"
        select id from branch_commits
         where save_id = $1 and turn_index = $2
         order by id desc limit 1
        "#,
    )
    .bind(save_id)
    .bind(turn_index)
    .fetch_optional(pool)
    .await?;
    Ok(fallback.map(|(id,)| id))
}

/// Python `round_start_node(db, node)` —— 若 `node` 是 gm,且其 parent 是同 save/同 turn 的
/// player,则返回 parent(回合开头);否则原样返回。
///
/// 用于 delete_subtree 把 gm 节点的删除范围扩到 player parent,避免半残回合。
pub async fn round_start_node(
    pool: &PgPool,
    node: &BranchCommit,
) -> PlatformResult<BranchCommit> {
    if node.kind != "gm" {
        return Ok(node.clone());
    }
    let parent_id = match node.parent_id {
        Some(p) => p,
        None => return Ok(node.clone()),
    };
    let parent_row = sqlx::query("select * from branch_commits where id = $1")
        .bind(parent_id)
        .fetch_optional(pool)
        .await?;
    if let Some(row) = parent_row {
        let parent = BranchCommit::from_row(&row)?;
        if parent.kind == "player"
            && parent.save_id == node.save_id
            && parent.turn_index == node.turn_index
        {
            return Ok(parent);
        }
    }
    Ok(node.clone())
}
