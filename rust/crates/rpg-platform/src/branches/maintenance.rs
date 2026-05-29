//! maintenance —— `ensure_summaries` / `ensure_state_snapshots`。
//!
//! 对应 Python `branches/maintenance.py`(`branches/_maintenance_repo.py`)。
//!
//! 行为:
//! - `ensure_summaries`: 遍历 save 下所有 commit,凡是 summary 为空 / "空回合" / "我好像*"
//!   的旧 placeholder,用 `helpers::rough_summary` 重算并 update。
//! - `ensure_state_snapshots`: 遍历 state_snapshot 为 `{}` / null 的 commit,从 `state_path` 文件
//!   读 JSON 回填 `state_snapshot` jsonb 列(并刷新 `tree_hash`/`row_version`)。
//!
//! Python 把这俩函数当成 boot-time 一次性补齐;这里同步走单线程跑完(每个 save 不会太多 commit)。

use std::path::Path;

use sqlx::{PgPool, Row};

use crate::error::PlatformResult;

use super::commits::state_snapshot_hash;
use super::helpers::{load_state, rough_summary};

/// Python `ensure_summaries(db, save_id)`。
///
/// 逻辑(对应 Python):
/// 1. 拉所有 commit(按 id 升序);
/// 2. 当前 summary 非空且非 placeholder("空回合"/"我好像*") → 跳过;
/// 3. 取 player_input / gm_output;若都空,根据 kind 用 content_preview / 父节点 preview
///    回填;
/// 4. 用 `helpers::rough_summary(player, gm, 22)` 重算,update。
pub async fn ensure_summaries(pool: &PgPool, save_id: i64) -> PlatformResult<()> {
    let rows = sqlx::query(
        r#"
        select id, parent_id, turn_index, kind,
               coalesce(summary, '') as summary,
               coalesce(player_input, '') as player_input,
               coalesce(gm_output, '') as gm_output,
               coalesce(content_preview, '') as content_preview,
               coalesce(title, '') as title
          from branch_commits
         where save_id = $1
         order by id
        "#,
    )
    .bind(save_id)
    .fetch_all(pool)
    .await?;

    // 先收集成 owned Vec,方便按 id 查父节点。
    #[derive(Clone)]
    struct Row {
        id: i64,
        parent_id: Option<i64>,
        turn_index: i32,
        kind: String,
        summary: String,
        player_input: String,
        gm_output: String,
        content_preview: String,
        title: String,
    }
    let mut all: Vec<Row> = Vec::with_capacity(rows.len());
    for r in &rows {
        all.push(Row {
            id: r.try_get("id").unwrap_or(0),
            parent_id: r.try_get::<Option<i64>, _>("parent_id").ok().flatten(),
            turn_index: r.try_get("turn_index").unwrap_or(0),
            kind: r.try_get::<String, _>("kind").unwrap_or_default(),
            summary: r.try_get::<String, _>("summary").unwrap_or_default(),
            player_input: r.try_get::<String, _>("player_input").unwrap_or_default(),
            gm_output: r.try_get::<String, _>("gm_output").unwrap_or_default(),
            content_preview: r.try_get::<String, _>("content_preview").unwrap_or_default(),
            title: r.try_get::<String, _>("title").unwrap_or_default(),
        });
    }
    let by_id: std::collections::HashMap<i64, Row> =
        all.iter().map(|r| (r.id, r.clone())).collect();

    for row in &all {
        let s = row.summary.trim();
        // 已有真实 summary —— 跳过。
        if !s.is_empty() && s != "空回合" && !s.starts_with("我好像") {
            continue;
        }
        let mut player_text = row.player_input.clone();
        let mut gm_text = row.gm_output.clone();
        if player_text.is_empty() && gm_text.is_empty() {
            match row.kind.as_str() {
                "gm" => {
                    if let Some(pid) = row.parent_id {
                        if let Some(parent) = by_id.get(&pid) {
                            if parent.kind == "player" && parent.turn_index == row.turn_index {
                                player_text = parent.content_preview.clone();
                            }
                        }
                    }
                    gm_text = row.content_preview.clone();
                }
                "player" => {
                    player_text = row.content_preview.clone();
                }
                "round" => {
                    gm_text = row.content_preview.clone();
                }
                _ => {
                    gm_text = if !row.content_preview.is_empty() {
                        row.content_preview.clone()
                    } else {
                        row.title.clone()
                    };
                }
            }
        }
        let new_summary = rough_summary(&player_text, &gm_text, 22);
        if new_summary == row.summary {
            continue;
        }
        sqlx::query("update branch_commits set summary = $1 where id = $2")
            .bind(&new_summary)
            .bind(row.id)
            .execute(pool)
            .await?;
    }
    tracing::debug!(save_id, rows = all.len(), "ensure_summaries 完成");
    Ok(())
}

/// Python `ensure_state_snapshots(db, save_id)`。
///
/// 找 `state_snapshot is null` 或 `{}` 的 commit,从 `state_path` 读盘回填。
/// 对应 Python `_db_update_commit_snapshot`:同时刷新 tree_hash、row_version。
pub async fn ensure_state_snapshots(pool: &PgPool, save_id: i64) -> PlatformResult<()> {
    let rows = sqlx::query(
        r#"
        select id, coalesce(state_path, '') as state_path
          from branch_commits
         where save_id = $1
           and (state_snapshot is null or state_snapshot = '{}'::jsonb)
         order by id
        "#,
    )
    .bind(save_id)
    .fetch_all(pool)
    .await?;

    let mut updated = 0u32;
    for row in rows {
        let id: i64 = row.try_get("id").unwrap_or(0);
        let state_path: String = row.try_get("state_path").unwrap_or_default();
        if state_path.is_empty() {
            continue;
        }
        let snapshot = load_state(Path::new(&state_path));
        let hash = state_snapshot_hash(&snapshot);
        // 兼容 schema:tree_hash 列在 Python migration v? 加,Rust 还没有 alter,
        // 所以用 dynamic SQL 探活;这里直接尝试写包含 tree_hash 的版本,失败回落到
        // 只写 state_snapshot 的简化版。
        let primary = sqlx::query(
            r#"
            update branch_commits
               set state_snapshot = $1,
                   tree_hash = $2,
                   row_version = row_version + 1
             where id = $3
            "#,
        )
        .bind(&snapshot)
        .bind(&hash)
        .bind(id)
        .execute(pool)
        .await;
        if primary.is_err() {
            sqlx::query("update branch_commits set state_snapshot = $1 where id = $2")
                .bind(&snapshot)
                .bind(id)
                .execute(pool)
                .await?;
        }
        updated += 1;
    }
    tracing::debug!(save_id, updated, "ensure_state_snapshots 完成");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 签名稳定:只查 fn pointer 强转,不实际 await(避免空连接等 60s)。
    #[test]
    fn signatures_stable() {
        fn _es<'a>(p: &'a PgPool, s: i64) -> impl std::future::Future<Output = PlatformResult<()>> + 'a {
            ensure_summaries(p, s)
        }
        fn _ess<'a>(p: &'a PgPool, s: i64) -> impl std::future::Future<Output = PlatformResult<()>> + 'a {
            ensure_state_snapshots(p, s)
        }
    }
}
