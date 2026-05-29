//! refs —— `branch_refs` 表 + 当前 active commit 标记。
//!
//! 对应 Python `branches/refs.py`,这里给出 `BranchRef` struct + 主路径(`upsert_ref` /
//! `set_save_active` / `write_checkout`),其余 TODO。

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::PlatformResult;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BranchRef {
    pub id: i64,
    pub save_id: i64,
    pub name: String,
    pub target_commit_id: i64,
    pub kind: String,
    pub active: bool,
    pub created_at: Option<DateTime<Utc>>,
    pub updated_at: Option<DateTime<Utc>>,
}

impl BranchRef {
    fn from_row(row: &sqlx::postgres::PgRow) -> sqlx::Result<Self> {
        Ok(BranchRef {
            id: row.try_get("id")?,
            save_id: row.try_get("save_id")?,
            name: row.try_get("name")?,
            target_commit_id: row.try_get("target_commit_id")?,
            kind: row.try_get::<String, _>("kind").unwrap_or_else(|_| "head".into()),
            active: row.try_get::<bool, _>("active").unwrap_or(false),
            created_at: row.try_get("created_at").ok(),
            updated_at: row.try_get("updated_at").ok(),
        })
    }
}

/// Python `_upsert_ref(db, save_id, name, target, *, active, kind)`。
pub async fn upsert_ref(
    pool: &PgPool,
    save_id: i64,
    name: &str,
    target_commit_id: i64,
    active: bool,
    kind: &str,
) -> PlatformResult<BranchRef> {
    let row = sqlx::query(
        r#"
        insert into branch_refs(save_id, name, target_commit_id, kind, active, updated_at)
        values ($1, $2, $3, $4, $5, now())
        on conflict(save_id, name) do update set
          target_commit_id = excluded.target_commit_id,
          kind = excluded.kind,
          active = excluded.active,
          updated_at = now()
        returning *
        "#,
    )
    .bind(save_id)
    .bind(name)
    .bind(target_commit_id)
    .bind(kind)
    .bind(active)
    .fetch_one(pool)
    .await?;
    BranchRef::from_row(&row).map_err(Into::into)
}

/// Python `_set_save_active(db, save_id, commit_id, ref_id)`。
pub async fn set_save_active(
    pool: &PgPool,
    save_id: i64,
    commit_id: i64,
    ref_id: Option<i64>,
) -> PlatformResult<()> {
    sqlx::query(
        r#"
        update game_saves
           set active_commit_id = $1,
               active_ref_id = $2,
               updated_at = now()
         where id = $3
        "#,
    )
    .bind(commit_id)
    .bind(ref_id)
    .bind(save_id)
    .execute(pool)
    .await?;
    Ok(())
}

/// Python `_write_checkout(db, user_id, save_id, ref_id, commit_id)` —
/// 更新或插入 `runtime_checkouts`,用作 dirty 标记。
pub async fn write_checkout(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    ref_id: Option<i64>,
    commit_id: i64,
) -> PlatformResult<()> {
    sqlx::query(
        r#"
        insert into runtime_checkouts(user_id, save_id, ref_id, commit_id, dirty, updated_at)
        values ($1, $2, $3, $4, false, now())
        on conflict(user_id, save_id) do update set
          ref_id = excluded.ref_id,
          commit_id = excluded.commit_id,
          dirty = false,
          updated_at = now()
        "#,
    )
    .bind(user_id)
    .bind(save_id)
    .bind(ref_id)
    .bind(commit_id)
    .execute(pool)
    .await?;
    Ok(())
}

// TODO[Sonnet]: _upsert_ref_by_id — 按 ref_id 更新 target_commit_id 的快捷路径
// TODO[Sonnet]: _ensure_active_ref(save_id) — 检查至少一个 ref active,否则把 main 设为 active
// TODO[Sonnet]: _find_or_create_ref_for_commit(user_id, commit) — 找到指向 commit 的 ref 或建一个
