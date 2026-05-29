//! maintenance —— ensure_summaries / ensure_state_snapshots 占位。
//!
//! 对应 Python `branches/maintenance.py` —— 把缺失 summary/snapshot 的旧 commit
//! 补齐。Rust 侧暂时只暴露空 stub,等 rpg-llm summary 接口稳定后再翻。

use sqlx::PgPool;

use crate::error::PlatformResult;

/// Python `ensure_summaries(db, save_id)` — TODO。
pub async fn ensure_summaries(_pool: &PgPool, _save_id: i64) -> PlatformResult<()> {
    // TODO[Sonnet]: 遍历该 save 的所有 commit,缺 summary 的调用
    //               summary::schedule_llm_summary 异步补齐
    Ok(())
}

/// Python `ensure_state_snapshots(db, save_id)` — TODO。
pub async fn ensure_state_snapshots(_pool: &PgPool, _save_id: i64) -> PlatformResult<()> {
    // TODO[Sonnet]: 老数据可能只有 state_path 文件,没 state_snapshot 列,
    //               这里读盘补回 jsonb 列。
    Ok(())
}
