//! gc —— 孤儿 commit 清理(garbage collection)。
//!
//! 找不被任何非 trash ref 引用且创建时间超过 `max_age_days` 的 commits,
//! 删除对应的 state 文件 + DB 行,返回删除数。

use sqlx::PgPool;

use crate::error::PlatformResult;

use super::helpers::unlink_branch_state;

/// 清理孤儿 commits:不被任何非 trash ref(直接或间接)引用,
/// 且 `created_at` 早于 `now() - max_age_days` 天。
///
/// 步骤:
/// 1. 找所有非 trash ref 的 target_commit_id,递归展开它们的祖先链 → reachable set
/// 2. 找 save_id 下所有 commit,排除 reachable set → orphan set
/// 3. 在 orphan set 中过滤 created_at > max_age_days 的
/// 4. 删除 orphan commits 的 state 文件 + DB 行
pub async fn gc_orphaned_commits(
    pool: &PgPool,
    save_id: i64,
    max_age_days: i64,
) -> PlatformResult<usize> {
    // 找孤儿 commit:不在任何非 trash ref 的祖先链上,且超龄
    let orphan_rows: Vec<(i64, String)> = sqlx::query_as(
        r#"
        with recursive
        -- 所有非 trash ref 指向的 commit
        ref_targets as (
            select target_commit_id as id
              from branch_refs
             where save_id = $1
               and kind != 'trash'
        ),
        -- 从 ref targets 递归找所有祖先(reachable set)
        reachable as (
            select id from ref_targets
            union
            select c.parent_id as id
              from branch_commits c
              join reachable r on c.id = r.id
             where c.parent_id is not null
               and c.save_id = $1
        )
        -- 不在 reachable 中的 commit = orphan
        select bc.id, coalesce(bc.state_path, '') as state_path
          from branch_commits bc
         where bc.save_id = $1
           and bc.id not in (select id from reachable where id is not null)
           and bc.created_at < now() - ($2 || ' days')::interval
        "#,
    )
    .bind(save_id)
    .bind(max_age_days.to_string())
    .fetch_all(pool)
    .await?;

    if orphan_rows.is_empty() {
        return Ok(0);
    }

    let orphan_ids: Vec<i64> = orphan_rows.iter().map(|(id, _)| *id).collect();

    // 先删 DB 行(含 refs 指向这些 orphan 的 trash refs)
    let mut tx = pool.begin().await?;
    sqlx::query("delete from branch_refs where save_id = $1 and target_commit_id = any($2)")
        .bind(save_id)
        .bind(&orphan_ids)
        .execute(&mut *tx)
        .await?;
    let deleted = sqlx::query("delete from branch_commits where id = any($1)")
        .bind(&orphan_ids)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    // 再 unlink state 文件(best-effort,tx 外)
    for (_, path) in &orphan_rows {
        if !path.is_empty() {
            unlink_branch_state(path);
        }
    }

    Ok(deleted.rows_affected() as usize)
}
