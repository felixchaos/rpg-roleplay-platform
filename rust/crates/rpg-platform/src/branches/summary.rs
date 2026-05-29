//! summary —— LLM 摘要生成。
//!
//! 对应 Python `branches/summary.py`。
//! 完成度: **TODO 占位**(依赖 rpg-llm pipeline 稳定)。

use sqlx::PgPool;

use crate::error::PlatformResult;

/// Python `schedule_llm_summary(commit_id, player_text, gm_text)`。
///
/// 当前实现: 立即写一条 placeholder summary 到 commit。
/// TODO[Sonnet]: 改为 spawn tokio::task,跑 rpg-llm summary GM 流水线。
pub async fn schedule_llm_summary(
    pool: &PgPool,
    commit_id: i64,
    player_text: &str,
    gm_text: &str,
) -> PlatformResult<()> {
    let placeholder = if !player_text.is_empty() {
        crate::branches::helpers::rough_summary(player_text, gm_text, 22)
    } else {
        crate::branches::helpers::rough_summary("", gm_text, 22)
    };
    sqlx::query("update branch_commits set summary = $1 where id = $2 and (summary is null or summary = '')")
        .bind(&placeholder)
        .bind(commit_id)
        .execute(pool)
        .await?;
    Ok(())
}
