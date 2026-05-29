//! branches/runtime —— 把回合落地到 commit / 标 dirty。
//!
//! 对应 Python `branches/runtime.py`。
//! 完成度: **骨架**。

use serde_json::Value;
use sqlx::PgPool;

use crate::error::PlatformResult;

/// Python `record_runtime_turn(...)` —— 一次玩家回合 +1。
pub async fn record_runtime_turn(
    _pool: &PgPool,
    user_id: i64,
    save_id: i64,
    _player_input: &str,
    _gm_output: &str,
    _state: &Value,
) -> PlatformResult<()> {
    // TODO[Sonnet]: 主路径 — insert 两条 commit(player + gm),写新 ref,
    //               同步 runtime_checkouts.turn_runtime + 1,标 dirty=false。
    tracing::warn!(
        user_id = ?user_id,
        save_id = ?save_id,
        "record_runtime_turn 是 stub,Python 源未补完 (TODO[Sonnet]); 生产用前需补完"
    );
    Ok(())
}

/// Python `persist_runtime_state(...)` —— 把当前 runtime state 镜像到 commit/snapshot。
pub async fn persist_runtime_state(
    _pool: &PgPool,
    user_id: i64,
    save_id: i64,
    _state: &Value,
) -> PlatformResult<()> {
    tracing::warn!(
        user_id = ?user_id,
        save_id = ?save_id,
        "persist_runtime_state 是 stub,Python 源未补完 (TODO[Sonnet]); 生产用前需补完"
    );
    Ok(())
}

/// Python `bootstrap_runtime_binding(user_id)` —— 启动时把 user_runtime 关联到合适的 ref。
pub async fn bootstrap_runtime_binding(
    _pool: &PgPool,
    _user_id: Option<i64>,
) -> PlatformResult<Value> {
    Ok(serde_json::json!({}))
}

/// Python `mark_runtime_dirty(save_id, runtime_state)` —— 标 dirty bit。
pub async fn mark_runtime_dirty(
    pool: &PgPool,
    save_id: i64,
    _runtime_state: &Value,
) -> PlatformResult<()> {
    sqlx::query("update runtime_checkouts set dirty = true, updated_at = now() where save_id = $1")
        .bind(save_id)
        .execute(pool)
        .await?;
    Ok(())
}
