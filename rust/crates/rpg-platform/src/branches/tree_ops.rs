//! tree_ops —— GET /api/branches/tree 的核心。
//!
//! 对应 Python `branches/tree_ops.py`。
//! 完成度: `tree` / `collect_ids` 主路径,`resolve_commit_id_by_message` / `round_start_node` TODO。

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{Column, PgPool, Row};

use crate::error::PlatformResult;
use crate::runtime::UserRuntime;

use super::commits::BranchCommit;
use super::refs::BranchRef;

/// 分页信息(对应 Python tree() 返回的 page 字段)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TreePage {
    pub limit: i64,
    pub next_cursor: Option<String>,
    pub has_more: bool,
}

/// GET /api/branches/tree 的回包结构 —— 完整对齐 Python tree() 返回。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TreeResult {
    pub ok: bool,
    pub save_id: i64,
    /// 完整 game_saves 行(对应 Python `result["save"]`)。
    pub save: Option<Value>,
    pub nodes: Vec<BranchCommit>,
    /// branch_refs 行列表(对应 Python `result["refs"]`)。
    pub refs: Vec<BranchRef>,
    pub active_commit_id: Option<i64>,
    /// 与 active_commit_id 同值(Python 兼容字段)。
    pub active_branch_node_id: Option<i64>,
    pub active_ref_id: Option<i64>,
    pub total: usize,
    pub page: TreePage,
    /// runtime 信息(activate_state_snapshot 返回值),部分端点填充。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime: Option<UserRuntime>,
    /// game_url 快捷字段(来自 runtime)。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub game_url: Option<String>,
    /// runtime_url 快捷字段(来自 runtime,Python continue_from/activate_node 用)。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime_url: Option<String>,
    /// active_ref 快捷字段(Python continue_from 用)。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub active_ref: Option<Value>,
    /// 回滚专属:restored_turn。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub restored_turn: Option<i32>,
    /// 回滚专属:deleted 统计。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub deleted: Option<Value>,
    /// 回滚专属:trash_ref。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trash_ref: Option<Value>,
}

/// Python `tree(user_id, save_id, limit, cursor)` 的主路径。
///
/// 返回完整格式,对齐 Python tree() 返回:save / nodes / refs / page / active_commit_id 等。
pub async fn tree(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
) -> PlatformResult<TreeResult> {
    // 校验 save 归属,同时取完整 save 行。
    // 列名:game_saves 用 `active_branch_ref_id`(V001 schema 对齐 Python),
    // 别写成 `active_ref_id`(那是 user_runtime 表的列,W5 排查多次踩过)。
    let save_row = sqlx::query(
        "select * from game_saves where id = $1 and user_id = $2",
    )
    .bind(save_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;

    let save_row = match save_row {
        Some(r) => r,
        None => {
            return Ok(TreeResult {
                ok: false,
                save_id,
                save: None,
                nodes: Vec::new(),
                refs: Vec::new(),
                active_commit_id: None,
                active_branch_node_id: None,
                active_ref_id: None,
                total: 0,
                page: TreePage { limit: 1000, next_cursor: None, has_more: false },
                runtime: None,
                game_url: None,
                runtime_url: None,
                active_ref: None,
                restored_turn: None,
                deleted: None,
                trash_ref: None,
            });
        }
    };

    // 把 save row 序列化为 serde_json::Value(对齐 Python expose(save))。
    let active_commit_id: Option<i64> = save_row
        .try_get::<Option<i64>, _>("active_commit_id")
        .unwrap_or(None)
        .or_else(|| save_row.try_get::<Option<i64>, _>("active_branch_node_id").ok().flatten());
    let active_ref_id: Option<i64> = save_row
        .try_get::<Option<i64>, _>("active_branch_ref_id")
        .ok()
        .flatten();

    let save_value = row_to_value(&save_row);

    const PAGE_LIMIT: i64 = 1000;
    let rows = sqlx::query(
        r#"
        select * from branch_commits
         where save_id = $1
         order by turn_index asc, id asc
         limit $2
        "#,
    )
    .bind(save_id)
    .bind(PAGE_LIMIT + 1)
    .fetch_all(pool)
    .await?;

    let has_more = rows.len() as i64 > PAGE_LIMIT;
    let visible_rows = if has_more { &rows[..PAGE_LIMIT as usize] } else { &rows[..] };
    let nodes: Vec<BranchCommit> = visible_rows.iter().filter_map(|r| BranchCommit::from_row(r).ok()).collect();
    let next_cursor = if has_more {
        visible_rows.last().and_then(|r| r.try_get::<i64, _>("id").ok()).map(|id| id.to_string())
    } else {
        None
    };
    let total = nodes.len();

    // 查 refs
    let ref_rows = sqlx::query(
        "select * from branch_refs where save_id = $1",
    )
    .bind(save_id)
    .fetch_all(pool)
    .await?;
    let refs: Vec<BranchRef> = ref_rows.iter().filter_map(|r| BranchRef::from_row(r).ok()).collect();

    Ok(TreeResult {
        ok: true,
        save_id,
        save: save_value,
        nodes,
        refs,
        active_commit_id,
        active_branch_node_id: active_commit_id,
        active_ref_id,
        total,
        page: TreePage {
            limit: PAGE_LIMIT,
            next_cursor,
            has_more,
        },
        runtime: None,
        game_url: None,
        runtime_url: None,
        active_ref: None,
        restored_turn: None,
        deleted: None,
        trash_ref: None,
    })
}

/// 把 PgRow 转为 serde_json::Value(列名→值的 map),用于 expose(save)。
fn row_to_value(row: &sqlx::postgres::PgRow) -> Option<Value> {
    let mut map = serde_json::Map::new();
    for col in row.columns() {
        let name = col.name();
        // 按常见类型顺序尝试
        if let Ok(v) = row.try_get::<Option<i64>, _>(name) {
            map.insert(name.to_string(), v.map(Value::from).unwrap_or(Value::Null));
        } else if let Ok(v) = row.try_get::<Option<i32>, _>(name) {
            map.insert(name.to_string(), v.map(Value::from).unwrap_or(Value::Null));
        } else if let Ok(v) = row.try_get::<Option<bool>, _>(name) {
            map.insert(name.to_string(), v.map(Value::from).unwrap_or(Value::Null));
        } else if let Ok(v) = row.try_get::<Option<String>, _>(name) {
            map.insert(name.to_string(), v.map(Value::from).unwrap_or(Value::Null));
        } else if let Ok(v) = row.try_get::<Option<Value>, _>(name) {
            map.insert(name.to_string(), v.unwrap_or(Value::Null));
        } else {
            map.insert(name.to_string(), Value::Null);
        }
    }
    Some(Value::Object(map))
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
