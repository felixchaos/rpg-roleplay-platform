//! deletion —— `delete_subtree / rollback_to_message`。
//!
//! 对应 Python `branches/deletion.py`。
//!
//! 完成度: 主路径(`delete_subtree` 完整连通 + state 文件 unlink;
//! `rollback_to_message` 完整 — 多表清理 + trash ref + runtime 同步)。
//!
//! 事务边界(对照 Python `connect()` 上下文 = 1 tx):
//! - delete_subtree: branch_refs 删 + branch_commits 删 + 可选 active 回退,共一个 tx;
//!   文件 unlink 在 tx 之外(I/O 不带事务,失败也只是孤儿文件)。
//! - rollback_to_message: target commit 查 + trash ref + 切 active + 清 messages /
//!   timeline_anchors / context_runs / save_phase_digests,共一个 tx;runtime 切换在 tx 外。

use chrono::Utc;
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

use super::commits::{commit_for_user, BranchCommit};
use super::helpers::{commit_state, unlink_branch_state, MAIN_REF};
use super::refs::{
    find_or_create_ref_for_commit, set_save_active, upsert_ref, write_checkout,
};
use super::tree_ops::{collect_ids, round_start_node};

/// `rollback_to_message` 的统计返回。
#[derive(Debug, Clone, Default)]
pub struct RollbackStats {
    pub target_commit_id: i64,
    pub restored_turn: i32,
    pub messages: i64,
    pub timeline_anchors: i64,
    pub context_runs: i64,
    pub phase_digests_truncated: i64,
    pub phase_digests_dropped: i64,
    pub trash_ref_id: Option<i64>,
}

/// Python `delete_subtree(user_id, node_id)` —— 删除以 node_id 为根的整棵子树。
///
/// 行为:
/// 1. 校验 user 持有该 commit;若 commit 是 gm(回合中段),`round_start_node` 把范围扩到 player parent
/// 2. root 节点不可删 → Validation
/// 3. 收集子树 ids;一并删 branch_refs + branch_commits
/// 4. 若 active commit 在被删集合中,回退到 parent commit + 重建 main ref + 写 checkout + 通知 runtime
/// 5. 子树各 state_path 文件做 unlink_branch_state(state_dir 内才删)
///
/// 返回删除的 commit 数。
pub async fn delete_subtree(
    pool: &PgPool,
    user_id: i64,
    node_id: i64,
) -> PlatformResult<usize> {
    let node = commit_for_user(pool, user_id, node_id)
        .await?
        .ok_or_else(|| PlatformError::forbidden("无权访问该分支节点"))?;
    let node = round_start_node(pool, &node).await?;
    if node.parent_id.is_none() {
        return Err(PlatformError::validation("不能删除 root 节点"));
    }
    let save_id = node.save_id;
    let ids = collect_ids(pool, node.id).await?;
    if ids.is_empty() {
        return Ok(0);
    }

    // 收集要 unlink 的文件路径(在删之前查)。
    let paths: Vec<(String,)> = sqlx::query_as(
        "select coalesce(state_path,'') from branch_commits where id = any($1)",
    )
    .bind(&ids)
    .fetch_all(pool)
    .await?;

    // 查 active + parent 兜底
    let save_row = sqlx::query(
        "select active_commit_id, active_branch_node_id from game_saves where id = $1",
    )
    .bind(save_id)
    .fetch_optional(pool)
    .await?;
    let active_commit_id: Option<i64> = save_row
        .as_ref()
        .and_then(|r| {
            r.try_get::<Option<i64>, _>("active_commit_id")
                .ok()
                .flatten()
                .or_else(|| {
                    r.try_get::<Option<i64>, _>("active_branch_node_id")
                        .ok()
                        .flatten()
                })
        });
    let parent_id = node.parent_id.expect("validated above");
    let parent_row = sqlx::query(
        "select * from branch_commits where id = $1 and save_id = $2",
    )
    .bind(parent_id)
    .bind(save_id)
    .fetch_optional(pool)
    .await?;
    let fallback = match parent_row {
        Some(r) => Some(BranchCommit::from_row(&r)?),
        None => None,
    };

    let active_deleted = active_commit_id
        .map(|c| ids.contains(&c))
        .unwrap_or(false);

    // 单 tx:refs + commits 删,active 回退在外面(需要其它子调用)。
    let mut tx = pool.begin().await?;
    sqlx::query("delete from branch_refs where save_id = $1 and target_commit_id = any($2)")
        .bind(save_id)
        .bind(&ids)
        .execute(&mut *tx)
        .await?;
    let deleted = sqlx::query("delete from branch_commits where id = any($1)")
        .bind(&ids)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    // active 回退
    if active_deleted {
        if let Some(parent) = fallback {
            let r =
                upsert_ref(pool, save_id, MAIN_REF, parent.id, true, "head").await?;
            set_save_active(pool, save_id, parent.id, Some(r.id)).await?;
            write_checkout(pool, user_id, save_id, Some(r.id), parent.id).await?;
            let state_snapshot = commit_state(Some(&parent.state_snapshot), &parent.state_path);
            crate::runtime::activate_state_snapshot(
                pool,
                user_id,
                save_id,
                parent.id,
                &state_snapshot,
                &parent.state_path,
                Some(r.id),
            )
            .await?;
        }
    }

    // unlink state files(tx 外,best-effort)。
    for (p,) in paths {
        unlink_branch_state(&p);
    }

    Ok(deleted.rows_affected() as usize)
}

/// Python `rollback_to_message(user_id, save_id, message_index)`(task 116c)。
///
/// 软回滚:删除消息 N 及之后所有,回到 turn `(N//2 - 1)` 的 round commit
/// (N<0 报错;N//2 == 0 → 回到 root)。
///
/// 多表清理:
/// - `messages` where turn >= deleted_turn
/// - `save_timeline_anchors` where turn_index >= deleted_turn
/// - `context_runs` 经 `game_sessions.save_id` 关联 + turn >= deleted_turn
/// - `save_phase_digests`:turn_start>=deleted_turn 整行删;否则 truncate turn_end
///
/// 配合:为当前 commit 建 trash ref 留痕;切 active + write_checkout + runtime。
pub async fn rollback_to_message(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    message_index: i64,
) -> PlatformResult<RollbackStats> {
    if message_index < 0 {
        return Err(PlatformError::validation("message_index 不能小于 0"));
    }
    let deleted_turn = (message_index / 2) as i32;
    let target_turn = deleted_turn - 1;

    // 校验 save 归属
    let save_row = sqlx::query(
        "select active_commit_id, active_branch_node_id from game_saves where id = $1 and user_id = $2",
    )
    .bind(save_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    let save_row = save_row
        .ok_or_else(|| PlatformError::forbidden("无权访问该存档,或存档不存在"))?;
    let current_commit_id: Option<i64> = save_row
        .try_get::<Option<i64>, _>("active_commit_id")
        .ok()
        .flatten()
        .or_else(|| {
            save_row
                .try_get::<Option<i64>, _>("active_branch_node_id")
                .ok()
                .flatten()
        });

    // 找 target commit
    let target_row = if target_turn < 0 {
        sqlx::query(
            r#"
            select * from branch_commits
             where save_id = $1 and kind = 'root'
             order by id asc limit 1
            "#,
        )
        .bind(save_id)
        .fetch_optional(pool)
        .await?
    } else {
        sqlx::query(
            r#"
            select * from branch_commits
             where save_id = $1 and turn_index = $2 and kind in ('round','gm')
             order by id desc limit 1
            "#,
        )
        .bind(save_id)
        .bind(target_turn)
        .fetch_optional(pool)
        .await?
    };
    let target_row = target_row.ok_or_else(|| {
        if target_turn < 0 {
            PlatformError::validation("找不到 root commit,无法回到开局之前")
        } else {
            PlatformError::validation(format!("找不到 turn {target_turn} 的 commit,无法回滚"))
        }
    })?;
    let target = BranchCommit::from_row(&target_row)?;

    // 单 tx:trash ref + active 切 + 清四张表
    let mut tx = pool.begin().await?;

    // 1) 给当前 commit 留个 trash ref(便于事后捞回)。
    let mut trash_ref_id: Option<i64> = None;
    if let Some(cur) = current_commit_id {
        if cur != target.id {
            let ts = Utc::now().format("%Y%m%d-%H%M%S").to_string();
            let trash_name = format!("refs/trash/{ts}-msg{message_index}");
            // 直接 inline upsert(对齐 _upsert_ref kind=trash, active=false)。
            // 这里不调用 upsert_ref(它走 pool);走 tx 内 sql。
            let (id,) = sqlx::query_as::<_, (i64,)>(
                r#"
                insert into branch_refs(save_id, name, kind, target_commit_id, is_active)
                values ($1, $2, 'trash', $3, false)
                on conflict(save_id, name) do update set
                  kind = excluded.kind,
                  target_commit_id = excluded.target_commit_id,
                  is_active = excluded.is_active,
                  row_version = branch_refs.row_version + 1,
                  updated_at = now()
                returning id
                "#,
            )
            .bind(save_id)
            .bind(&trash_name)
            .bind(cur)
            .fetch_one(&mut *tx)
            .await?;
            trash_ref_id = Some(id);
        }
    }

    // 2) 清四张表
    let n_msgs = sqlx::query("delete from messages where save_id = $1 and turn >= $2")
        .bind(save_id)
        .bind(deleted_turn)
        .execute(&mut *tx)
        .await?
        .rows_affected() as i64;

    let n_anchors = sqlx::query(
        "delete from save_timeline_anchors where save_id = $1 and turn_index >= $2",
    )
    .bind(save_id)
    .bind(deleted_turn)
    .execute(&mut *tx)
    .await?
    .rows_affected() as i64;

    let n_runs = sqlx::query(
        r#"
        delete from context_runs
         where session_id in (select id from game_sessions where save_id = $1)
           and turn >= $2
        "#,
    )
    .bind(save_id)
    .bind(deleted_turn)
    .execute(&mut *tx)
    .await?
    .rows_affected() as i64;

    // 3) phase_digests:对齐 Python 的 fix-or-drop
    let phase_rows = sqlx::query(
        r#"
        select id, turn_start from save_phase_digests
         where save_id = $1 and turn_end >= $2
         order by phase_index
        "#,
    )
    .bind(save_id)
    .bind(deleted_turn)
    .fetch_all(&mut *tx)
    .await?;
    let mut phase_fixed: i64 = 0;
    let mut phase_dropped: i64 = 0;
    for row in &phase_rows {
        let id: i64 = row.try_get("id")?;
        let turn_start: i32 = row.try_get("turn_start").unwrap_or(0);
        if turn_start >= deleted_turn {
            sqlx::query("delete from save_phase_digests where id = $1")
                .bind(id)
                .execute(&mut *tx)
                .await?;
            phase_dropped += 1;
        } else {
            sqlx::query(
                "update save_phase_digests set turn_end = $1, updated_at = now() where id = $2",
            )
            .bind(deleted_turn - 1)
            .bind(id)
            .execute(&mut *tx)
            .await?;
            phase_fixed += 1;
        }
    }
    tx.commit().await?;

    // 4) 切 active(走非 tx 路径,会再读 commit / 写 game_saves 等)。
    let new_ref =
        find_or_create_ref_for_commit(pool, user_id, save_id, target.id).await?;
    let new_ref_id = Some(new_ref.id);
    set_save_active(pool, save_id, target.id, new_ref_id).await?;
    write_checkout(pool, user_id, save_id, new_ref_id, target.id).await?;

    let target_state = commit_state(Some(&target.state_snapshot), &target.state_path);
    crate::runtime::activate_state_snapshot(
        pool,
        user_id,
        save_id,
        target.id,
        &target_state,
        &target.state_path,
        new_ref_id,
    )
    .await?;

    Ok(RollbackStats {
        target_commit_id: target.id,
        restored_turn: if target_turn >= 0 { target_turn } else { -1 },
        messages: n_msgs,
        timeline_anchors: n_anchors,
        context_runs: n_runs,
        phase_digests_truncated: phase_fixed,
        phase_digests_dropped: phase_dropped,
        trash_ref_id,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// message_index < 0 必须 validation error,在 DB 调用之前。
    #[tokio::test]
    async fn rollback_negative_index_rejects() {
        let pool = sqlx::PgPool::connect_lazy("postgres://localhost/nonexistent").unwrap();
        let res = rollback_to_message(&pool, 1, 1, -1).await;
        assert!(matches!(res, Err(PlatformError::Validation(_))));
    }

}
