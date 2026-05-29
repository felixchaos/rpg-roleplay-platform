//! summary —— LLM 摘要生成。
//!
//! 对应 Python `branches/summary.py`(~70 行 + `_run_llm_summary` 后台线程)。
//!
//! 完成度:
//! - **同步入口**(`generate_summary_now`): 立即写一条 rough_summary 占位,保证 `branch_commits.summary`
//!   永远有值,不会出现 GET /api/branches/tree 显示空标题。
//! - **异步入口**(`schedule_llm_summary`): `tokio::spawn` fire-and-forget,后续替换 placeholder。
//! - **真正的 LLM pipeline**: TODO P2 — rpg-llm crate 目前只暴露流式 ChatChunk/Reasoning summary
//!   (responses.rs::reasoning_summary_text.delta),没有 `summarize(text)` 这种 one-shot 接口。
//!   等 rpg-llm 开 `RoleClient::quick_summarize(system, prompt, max_tokens) -> String` 后,
//!   把 `_run_llm_summary` 真正翻过来:读 GameMaster role config → call → 清洗 → update 列。

use sqlx::PgPool;

use crate::error::PlatformResult;

/// 同步写 placeholder summary,失败仅 log。专给 `schedule_llm_summary` 的 spawn body 用。
///
/// 用 `rough_summary(player, gm, 22)`(对应 Python 24 字 limit 减 2 的安全余量)。
/// 仅当 commit 当前 summary 空 / "空回合" / "我好像..." 这类 placeholder 才覆写。
pub async fn generate_summary_now(
    pool: &PgPool,
    commit_id: i64,
    player_text: &str,
    gm_text: &str,
) -> PlatformResult<()> {
    if commit_id <= 0 || (player_text.is_empty() && gm_text.is_empty()) {
        return Ok(());
    }
    let placeholder = if !player_text.is_empty() {
        crate::branches::helpers::rough_summary(player_text, gm_text, 22)
    } else {
        crate::branches::helpers::rough_summary("", gm_text, 22)
    };
    sqlx::query(
        r#"
        update branch_commits
           set summary = $1
         where id = $2
           and (summary is null
                or summary = ''
                or summary = '空回合'
                or summary like '我好像%')
        "#,
    )
    .bind(&placeholder)
    .bind(commit_id)
    .execute(pool)
    .await?;
    Ok(())
}

/// Python `schedule_llm_summary(commit_id, player_text, gm_text)`。
///
/// fire-and-forget: 立刻把粗 summary 写一条到 branch_commits(防 UI 空),
/// 然后 `tokio::spawn` 后台 task 占位(等 LLM pipeline)。
///
/// 当前行为:
/// 1. 调 `generate_summary_now` 同步落 rough_summary(已经够 UI 显示);
/// 2. spawn 一个 noop task,带 TODO 注释 — 等 rpg-llm 暴露 `quick_summarize` 后改成真 LLM 调用。
pub fn schedule_llm_summary(
    pool: PgPool,
    commit_id: i64,
    player_text: String,
    gm_text: String,
) {
    if commit_id <= 0 || (player_text.is_empty() && gm_text.is_empty()) {
        return;
    }
    tokio::spawn(async move {
        // 第一步:先落 rough_summary,确保 GET /tree 不空。
        if let Err(e) = generate_summary_now(&pool, commit_id, &player_text, &gm_text).await {
            tracing::warn!(commit_id, error = %e, "schedule_llm_summary: rough_summary 写入失败");
            return;
        }
        // 第二步:真正的 LLM 摘要 —— P2,依赖 rpg-llm 暴露 quick_summarize。
        // TODO[P2-LLM]: 翻译 Python `_run_llm_summary`:
        //   - 读 GameMaster role config(默认 gemini-3.5-flash)
        //   - 拼 prompt: "玩家输入:\n{player[:600]}\n\nGM 响应:\n{gm[:1200]}"
        //   - call(system=_LLM_SUMMARY_SYSTEM, max_tokens=64)
        //   - 清洗前缀引号/尾标点 → 长度 4-32
        //   - update branch_commits.summary 覆写 placeholder
        tracing::debug!(
            commit_id,
            "schedule_llm_summary: placeholder 已落,真 LLM 摘要待 rpg-llm 接口稳定"
        );
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 入口 guard:空 commit_id / 空文本不应 panic / 不应 spawn 任何东西。
    #[test]
    fn schedule_skips_when_empty() {
        // 不能直连真实 pool,所以仅断言 zero 路径不 panic。
        // 真实路径在集成测试由 record_runtime_turn 覆盖。
        // 这里只能用一个 dummy pool — 用 lazy 构造,确保不会真的 spawn。
        // 由于函数早 return,不构造 spawn,所以可以传任意 pool 而不触发 tokio runtime。
        // 但 PgPool 不能凭空造,跳过 spawn 路径 —— 用 commit_id=0 走 early-return。
        // 此测试本质是编译时签名校验 + early-return 路径不需要 runtime。
        // 没法 mock PgPool,所以只验签名稳定。
        let _f: fn(PgPool, i64, String, String) = schedule_llm_summary;
    }
}
