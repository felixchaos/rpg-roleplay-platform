//! refs —— `branch_refs` 表 + 当前 active commit 标记。
//!
//! 对应 Python `branches/refs.py`。
//!
//! 完成度: 主路径(`upsert_ref` / `upsert_ref_by_id` / `ensure_active_ref` /
//! `find_or_create_ref_for_commit` / `set_save_active` / `write_checkout`)。
//!
//! 关键 schema 对齐:`branch_refs` 实际列名是 `is_active`(对照
//! `rpg-db/migrations/022`),Wave 1A 的 `runtime.rs` 已经用 `is_active`;
//! 这里 Wave 1A 阶段的占位代码错写成了 `active`,本次一并修齐。

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

use super::helpers::{commit_state, MAIN_REF};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BranchRef {
    pub id: i64,
    pub save_id: i64,
    pub name: String,
    pub target_commit_id: i64,
    pub kind: String,
    pub is_active: bool,
    pub created_at: Option<DateTime<Utc>>,
    pub updated_at: Option<DateTime<Utc>>,
}

impl BranchRef {
    pub(crate) fn from_row(row: &sqlx::postgres::PgRow) -> sqlx::Result<Self> {
        Ok(BranchRef {
            id: row.try_get("id")?,
            save_id: row.try_get("save_id")?,
            name: row.try_get("name")?,
            target_commit_id: row.try_get("target_commit_id")?,
            kind: row.try_get::<String, _>("kind").unwrap_or_else(|_| "head".into()),
            is_active: row.try_get::<bool, _>("is_active").unwrap_or(false),
            created_at: row.try_get("created_at").ok(),
            updated_at: row.try_get("updated_at").ok(),
        })
    }
}

/// Python `_upsert_ref(db, save_id, name, target, *, active, kind)`。
///
/// `active=true` 时,先把同 save 下其它 ref 置 false(对齐 Python 行为)。
pub async fn upsert_ref(
    pool: &PgPool,
    save_id: i64,
    name: &str,
    target_commit_id: i64,
    active: bool,
    kind: &str,
) -> PlatformResult<BranchRef> {
    if active {
        sqlx::query("update branch_refs set is_active = false where save_id = $1")
            .bind(save_id)
            .execute(pool)
            .await?;
    }
    let row = sqlx::query(upsert_ref_sql())
        .bind(save_id)
        .bind(name)
        .bind(kind)
        .bind(target_commit_id)
        .bind(active)
        .fetch_one(pool)
        .await?;
    BranchRef::from_row(&row).map_err(Into::into)
}

/// 事务版 `upsert_ref` —— SQL/绑定与 [`upsert_ref`] 一致,走调用方持有的 tx。
pub async fn upsert_ref_with_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    save_id: i64,
    name: &str,
    target_commit_id: i64,
    active: bool,
    kind: &str,
) -> PlatformResult<BranchRef> {
    if active {
        sqlx::query("update branch_refs set is_active = false where save_id = $1")
            .bind(save_id)
            .execute(&mut **tx)
            .await?;
    }
    let row = sqlx::query(upsert_ref_sql())
        .bind(save_id)
        .bind(name)
        .bind(kind)
        .bind(target_commit_id)
        .bind(active)
        .fetch_one(&mut **tx)
        .await?;
    BranchRef::from_row(&row).map_err(Into::into)
}

fn upsert_ref_sql() -> &'static str {
    r#"
    insert into branch_refs(save_id, name, kind, target_commit_id, is_active)
    values ($1, $2, $3, $4, $5)
    on conflict(save_id, name) do update set
      kind = excluded.kind,
      target_commit_id = excluded.target_commit_id,
      is_active = excluded.is_active,
      row_version = branch_refs.row_version + 1,
      updated_at = now()
    returning *
    "#
}

/// Python `_upsert_ref_by_id(db, ref_id, target_commit_id, *, active)`。
///
/// 按 ref_id 更新 target_commit_id 的快捷路径;active=true 时清场同 save 下其它 ref。
/// ref_id 不存在 → `PlatformError::Validation`(对齐 Python 抛 ValueError)。
pub async fn upsert_ref_by_id(
    pool: &PgPool,
    ref_id: i64,
    target_commit_id: i64,
    active: bool,
) -> PlatformResult<BranchRef> {
    let row = sqlx::query("select save_id from branch_refs where id = $1")
        .bind(ref_id)
        .fetch_optional(pool)
        .await?;
    let save_id: i64 = match row {
        Some(r) => r.try_get("save_id").unwrap_or(0),
        None => return Err(PlatformError::validation("runtime 指向的分支引用不存在")),
    };
    if active {
        sqlx::query("update branch_refs set is_active = false where save_id = $1")
            .bind(save_id)
            .execute(pool)
            .await?;
    }
    let row = sqlx::query(
        r#"
        update branch_refs
           set target_commit_id = $1,
               is_active = $2,
               row_version = row_version + 1,
               updated_at = now()
         where id = $3
        returning *
        "#,
    )
    .bind(target_commit_id)
    .bind(active)
    .bind(ref_id)
    .fetch_one(pool)
    .await?;
    BranchRef::from_row(&row).map_err(Into::into)
}

/// Python `_find_or_create_ref_for_commit(db, user_id, commit)`。
///
/// 优先复用已指向 commit 的 head ref;否则建一个 `refs/runtime/user-<user_id>` kind=runtime。
pub async fn find_or_create_ref_for_commit(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    commit_id: i64,
) -> PlatformResult<BranchRef> {
    let existing = sqlx::query(
        r#"
        select name, kind from branch_refs
         where save_id = $1 and target_commit_id = $2
         order by case when kind = 'head' then 0 else 1 end, id desc
         limit 1
        "#,
    )
    .bind(save_id)
    .bind(commit_id)
    .fetch_optional(pool)
    .await?;
    if let Some(r) = existing {
        let name: String = r.try_get("name").unwrap_or_default();
        let kind: String = r.try_get("kind").unwrap_or_else(|_| "head".into());
        return upsert_ref(pool, save_id, &name, commit_id, true, &kind).await;
    }
    let name = format!("refs/runtime/user-{user_id}");
    upsert_ref(pool, save_id, &name, commit_id, true, "runtime").await
}

/// Python `_ensure_active_ref(db, save_id)`。
///
/// 检查 save 至少有一个 active ref 指向当前 active_commit_id;没有就把 main 设为 active
/// (commit 不存在时找最新 commit 兜底)。save 找不到 → 静默 return。
pub async fn ensure_active_ref(pool: &PgPool, save_id: i64) -> PlatformResult<()> {
    let save = sqlx::query(
        "select active_commit_id, active_branch_node_id from game_saves where id = $1",
    )
    .bind(save_id)
    .fetch_optional(pool)
    .await?;
    let save = match save {
        Some(s) => s,
        None => return Ok(()),
    };
    let active_commit_id: Option<i64> = save.try_get("active_commit_id").ok().flatten();
    let active_node_id: Option<i64> = save.try_get("active_branch_node_id").ok().flatten();
    let candidate = active_commit_id.or(active_node_id);

    // 找 commit;找不到就拿最新的 commit。
    let commit_id: Option<i64> = match candidate {
        Some(cid) => {
            let row: Option<(i64,)> = sqlx::query_as(
                "select id from branch_commits where id = $1 and save_id = $2",
            )
            .bind(cid)
            .bind(save_id)
            .fetch_optional(pool)
            .await?;
            match row {
                Some((id,)) => Some(id),
                None => {
                    let fallback: Option<(i64,)> = sqlx::query_as(
                        "select id from branch_commits where save_id = $1 order by id desc limit 1",
                    )
                    .bind(save_id)
                    .fetch_optional(pool)
                    .await?;
                    fallback.map(|(id,)| id)
                }
            }
        }
        None => {
            let fallback: Option<(i64,)> = sqlx::query_as(
                "select id from branch_commits where save_id = $1 order by id desc limit 1",
            )
            .bind(save_id)
            .fetch_optional(pool)
            .await?;
            fallback.map(|(id,)| id)
        }
    };
    let commit_id = match commit_id {
        Some(c) => c,
        None => return Ok(()),
    };

    // 已经有 active 指向当前 commit?
    let existing: Option<(i64,)> = sqlx::query_as(
        r#"
        select id from branch_refs
         where save_id = $1 and is_active = true and target_commit_id = $2
         order by id desc limit 1
        "#,
    )
    .bind(save_id)
    .bind(commit_id)
    .fetch_optional(pool)
    .await?;
    let ref_id = match existing {
        Some((id,)) => id,
        None => {
            let main = upsert_ref(pool, save_id, MAIN_REF, commit_id, true, "head").await?;
            main.id
        }
    };
    set_save_active(pool, save_id, commit_id, Some(ref_id)).await
}

/// Python `_set_save_active(db, save_id, commit_id, ref_id)`。
///
/// 同步 game_saves.active_branch_node_id / active_commit_id / active_branch_ref_id /
/// state_snapshot,并加 row_version。snapshot 从 branch_commits 行还原。
pub async fn set_save_active(
    pool: &PgPool,
    save_id: i64,
    commit_id: i64,
    ref_id: Option<i64>,
) -> PlatformResult<()> {
    // 读 commit 的 snapshot/state_path 还原 state。
    let row = sqlx::query(
        "select state_snapshot, state_path from branch_commits where id = $1 and save_id = $2",
    )
    .bind(commit_id)
    .bind(save_id)
    .fetch_optional(pool)
    .await?;
    let state_snapshot = match row {
        Some(r) => {
            let snap: serde_json::Value = r.try_get("state_snapshot").unwrap_or(serde_json::Value::Null);
            let path: String = r.try_get("state_path").unwrap_or_default();
            commit_state(Some(&snap), &path)
        }
        None => super::helpers::empty_state(),
    };
    sqlx::query(set_save_active_sql())
        .bind(commit_id)
        .bind(ref_id)
        .bind(&state_snapshot)
        .bind(save_id)
        .execute(pool)
        .await?;
    Ok(())
}

/// 事务版 `set_save_active` —— 与 [`set_save_active`] 同 SQL,走调用方持有的 tx。
pub async fn set_save_active_with_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    save_id: i64,
    commit_id: i64,
    ref_id: Option<i64>,
) -> PlatformResult<()> {
    let row = sqlx::query(
        "select state_snapshot, state_path from branch_commits where id = $1 and save_id = $2",
    )
    .bind(commit_id)
    .bind(save_id)
    .fetch_optional(&mut **tx)
    .await?;
    let state_snapshot = match row {
        Some(r) => {
            let snap: serde_json::Value = r.try_get("state_snapshot").unwrap_or(serde_json::Value::Null);
            let path: String = r.try_get("state_path").unwrap_or_default();
            commit_state(Some(&snap), &path)
        }
        None => super::helpers::empty_state(),
    };
    sqlx::query(set_save_active_sql())
        .bind(commit_id)
        .bind(ref_id)
        .bind(&state_snapshot)
        .bind(save_id)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

fn set_save_active_sql() -> &'static str {
    // 注:`active_ref_id` 在 `user_runtime` 表上才存在;`game_saves` 表的 ref 列
    // 叫 `active_branch_ref_id`(V001 schema)。早期把两者搞混会写挂。
    r#"
    update game_saves
       set active_branch_node_id = $1,
           active_commit_id = $1,
           active_branch_ref_id = $2,
           state_snapshot = $3,
           row_version = row_version + 1,
           updated_at = now()
     where id = $4
    "#
}

/// Python `_write_checkout(db, user_id, save_id, ref_id, commit_id)` —
/// 更新或插入 `runtime_checkouts`,dirty=false。
///
/// 写入完整列(对齐 Python):runtime_state_path / state_snapshot / snapshot_hash /
/// turn_at_commit / turn_runtime。
pub async fn write_checkout(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    ref_id: Option<i64>,
    commit_id: i64,
) -> PlatformResult<()> {
    let row = sqlx::query(
        "select state_snapshot, state_path from branch_commits where id = $1 and save_id = $2",
    )
    .bind(commit_id)
    .bind(save_id)
    .fetch_optional(pool)
    .await?;
    let (state_snapshot, runtime_state_path) = match row {
        Some(r) => {
            let snap: serde_json::Value = r.try_get("state_snapshot").unwrap_or(serde_json::Value::Null);
            let path: String = r.try_get("state_path").unwrap_or_default();
            let restored = commit_state(Some(&snap), &path);
            (restored, path)
        }
        None => (super::helpers::empty_state(), String::new()),
    };
    let snap_hash = super::commits::state_snapshot_hash(&state_snapshot);
    let turn_at_commit = state_snapshot
        .get("turn")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);

    sqlx::query(
        r#"
        insert into runtime_checkouts(user_id, save_id, ref_id, commit_id,
                                       runtime_state_path, state_snapshot,
                                       snapshot_hash, dirty,
                                       turn_at_commit, turn_runtime, updated_at)
        values ($1, $2, $3, $4, $5, $6, $7, false, $8, $8, now())
        on conflict(user_id, save_id) do update set
          ref_id = excluded.ref_id,
          commit_id = excluded.commit_id,
          runtime_state_path = excluded.runtime_state_path,
          state_snapshot = excluded.state_snapshot,
          snapshot_hash = excluded.snapshot_hash,
          dirty = false,
          turn_at_commit = excluded.turn_at_commit,
          turn_runtime = excluded.turn_runtime,
          updated_at = now()
        "#,
    )
    .bind(user_id)
    .bind(save_id)
    .bind(ref_id)
    .bind(commit_id)
    .bind(&runtime_state_path)
    .bind(&state_snapshot)
    .bind(&snap_hash)
    .bind(turn_at_commit)
    .execute(pool)
    .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 验证 `BranchRef` 字段访问 / serde round-trip(防回归 `is_active` 列名)。
    #[test]
    fn branch_ref_serde_roundtrip() {
        let r = BranchRef {
            id: 1,
            save_id: 2,
            name: "refs/heads/main".into(),
            target_commit_id: 3,
            kind: "head".into(),
            is_active: true,
            created_at: None,
            updated_at: None,
        };
        let j = serde_json::to_value(&r).unwrap();
        assert_eq!(j["is_active"], serde_json::Value::Bool(true));
        let back: BranchRef = serde_json::from_value(j).unwrap();
        assert_eq!(back.id, 1);
        assert!(back.is_active);
    }

}
