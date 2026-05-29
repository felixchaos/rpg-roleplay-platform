//! repos/save_worldbook_overlays.rs — save 级世界书 overlay 表 CRUD
//!
//! 字段对齐 001_init.sql save_worldbook_overlays 表定义:
//!   id, save_id, kind ('addition' | 'retirement'), title, content, keys,
//!   priority, retired_entry_id, retired_reason, introduced_turn, metadata,
//!   created_at, updated_at
//!
//! 用途:
//! - addition: 游戏中临时新增的世界书条目 (无 worldbook_entries.id)
//! - retirement: 屏蔽某条 script 级 worldbook_entries.id (通过 retired_entry_id)
//!
//! 由 `rpg-agents::worldbook_agent::load_effective_worldbook_for_save` 消费:
//! 把 script 级 entries 与 overlay 合并为"有效世界书"列表。

use serde::{Deserialize, Serialize};
use sqlx::PgPool;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct SaveWorldbookOverlay {
    pub id: i64,
    pub save_id: i64,
    /// 'addition' | 'retirement' (CHECK constraint 在 SQL 端)
    pub kind: String,
    pub title: String,
    pub content: String,
    pub keys: serde_json::Value,
    pub priority: i32,
    pub retired_entry_id: Option<i64>,
    pub retired_reason: String,
    pub introduced_turn: Option<i32>,
    pub metadata: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// 列出本 save 的所有 overlay 行,按 id ASC (对齐 Python `order by id asc`)。
#[tracing::instrument(skip(pool), fields(save_id = %save_id))]
pub async fn list_for_save(
    pool: &PgPool,
    save_id: i64,
) -> Result<Vec<SaveWorldbookOverlay>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, save_id, kind, title, content, keys, priority,
                retired_entry_id, retired_reason, introduced_turn, metadata,
                created_at, updated_at
         FROM save_worldbook_overlays
         WHERE save_id = $1
         ORDER BY id ASC",
    )
    .bind(save_id)
    .fetch_all(pool)
    .await
}
