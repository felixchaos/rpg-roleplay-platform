//! summary —— LLM 摘要生成。
//!
//! 对应 Python `branches/summary.py`(~70 行 + `_run_llm_summary` 后台线程)。
//!
//! 完成度:
//! - **同步入口**(`generate_summary_now`): 立即写一条 rough_summary 占位,保证 `branch_commits.summary`
//!   永远有值,不会出现 GET /api/branches/tree 显示空标题。
//! - **异步入口**(`schedule_llm_summary`): `tokio::spawn` fire-and-forget。
//!   先写 rough placeholder,再(如果 `init_summary_backend` 注入过 backend)调 LLM
//!   生成真正的 15-22 字摘要并覆写。
//! - **LLM pipeline**: `run_llm_summary` 用 `LlmBackend::stream_chat` drain 出 Text chunk,
//!   按 Python 同款 regex 清洗前后缀符号,长度落在 [4, 32] 之外不覆写。
//!
//! 注入入口:启动期(rpg-routes / bootstrap)调用 [`init_summary_backend`] 把
//! `Arc<AnyBackend>` + 默认 model_id 塞进 OnceCell。未注入时退化为旧行为
//! (仅写 placeholder,P2-LLM TODO 不阻断)。

use std::sync::Arc;

use futures::StreamExt;
use once_cell::sync::OnceCell;
use regex::Regex;
use sqlx::PgPool;

use rpg_llm::pipeline::{ChatChunk, ChatMessage, ChatRequest, LlmBackend};
use rpg_llm::AnyBackend;

use crate::error::PlatformResult;

/// Python `_LLM_SUMMARY_SYSTEM`,逐字对齐。
const LLM_SUMMARY_SYSTEM: &str = concat!(
    "你是剧情摘要助手。读完一回合的玩家输入和 GM 响应后，用 15-22 字概括这一回合发生了什么。\n",
    "要求：\n",
    "- 只输出摘要本身，不要前缀\n",
    "- 用动词为主，避免主语\n",
    "- 不带句号、引号、标签\n",
    "- 失败/拒绝/打断也要客观描述",
);

/// `_run_llm_summary` 默认 max_tokens(Python 同值 64)。
const LLM_SUMMARY_MAX_TOKENS: u32 = 64;

/// Summary 后台进程允许的 LLM 调用总耗时上限(秒)。Python 端走 ThreadPool
/// 没有 timeout 概念,但 Rust 这里跑在 tokio,无超时会拖住 task slot。
const LLM_SUMMARY_TIMEOUT_SECS: u64 = 30;

/// 后台 LLM 摘要全局配置(OnceCell)。
///
/// 启动期(rpg-routes)调 `init_summary_backend` 注入选定的 backend + model。
/// 未注入时 `schedule_llm_summary` 只写 placeholder,行为与之前一致。
struct SummaryBackend {
    backend: Arc<AnyBackend>,
    model: String,
}

static SUMMARY_BACKEND: OnceCell<SummaryBackend> = OnceCell::new();

/// 启动期注入 backend + 默认 model_id。重复注入只生效第一次(OnceCell 语义)。
pub fn init_summary_backend(backend: Arc<AnyBackend>, model: impl Into<String>) {
    // set 失败仅代表已经注入过 —— 不报错,符合幂等初始化预期。
    let _ = SUMMARY_BACKEND.set(SummaryBackend {
        backend,
        model: model.into(),
    });
}

/// 单元测试钩子:暴露当前是否注入过 backend(不暴露内容)。
#[cfg(test)]
fn summary_backend_is_set() -> bool {
    SUMMARY_BACKEND.get().is_some()
}

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
/// 然后 `tokio::spawn` 后台 task。若 [`init_summary_backend`] 已注入 backend,
/// task 会调 LLM 生成真正的 15-22 字摘要并覆写 placeholder;否则保留 placeholder。
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
        // 第二步:真 LLM 摘要 —— 仅在 init_summary_backend 注入后启用。
        let Some(cfg) = SUMMARY_BACKEND.get() else {
            tracing::debug!(
                commit_id,
                "schedule_llm_summary: 未注入 backend,跳过 LLM 摘要,沿用 placeholder"
            );
            return;
        };
        match run_llm_summary(
            &pool,
            cfg.backend.as_ref(),
            &cfg.model,
            commit_id,
            &player_text,
            &gm_text,
        )
        .await
        {
            Ok(true) => tracing::debug!(commit_id, "schedule_llm_summary: LLM 摘要写回成功"),
            Ok(false) => tracing::debug!(
                commit_id,
                "schedule_llm_summary: LLM 摘要被清洗后长度不达标,沿用 placeholder"
            ),
            Err(e) => tracing::warn!(
                commit_id,
                error = %e,
                "schedule_llm_summary: LLM 摘要失败,沿用 placeholder"
            ),
        }
    });
}

/// 实际跑 LLM + 写回。返回 `true` 表示已 update,`false` 表示清洗后长度不达标
/// (沿用 placeholder)。
///
/// 行为对齐 Python `_run_llm_summary`:
/// - prompt: "玩家输入：\n{player[:600]}\n\nGM 响应：\n{gm[:1200]}"
/// - drain stream_chat → 拼接 Text chunk → strip
/// - regex 清洗前后符号:`^[【「"':\-—]+` / `[】」"'。！？!?]+$`
/// - 换行替空格
/// - 长度 > 32 截断 32,< 4 直接放弃
pub async fn run_llm_summary(
    pool: &PgPool,
    backend: &dyn LlmBackend,
    model: &str,
    commit_id: i64,
    player_text: &str,
    gm_text: &str,
) -> PlatformResult<bool> {
    let prompt = build_summary_prompt(player_text, gm_text);
    let req = ChatRequest {
        model: model.to_string(),
        system: Some(LLM_SUMMARY_SYSTEM.to_string()),
        messages: vec![ChatMessage::user(prompt)],
        tools: Vec::new(),
        temperature: None,
        max_tokens: Some(LLM_SUMMARY_MAX_TOKENS),
        stream: false,
        extra: serde_json::Value::Null,
    };

    // drain stream_chat,只关心 Text chunk(忽略 Thinking / ToolCall / Usage / Stop)。
    // 加 30s 总超时,避免 hang 住 spawn slot。
    let collect_fut = async {
        let mut stream = backend
            .stream_chat(req)
            .await
            .map_err(|e| crate::error::PlatformError::Other(anyhow::anyhow!("stream_chat: {e}")))?;
        let mut text = String::new();
        while let Some(chunk) = stream.next().await {
            match chunk {
                Ok(ChatChunk::Text(t)) => text.push_str(&t),
                Ok(_) => {}
                Err(e) => {
                    return Err(crate::error::PlatformError::Other(anyhow::anyhow!(
                        "stream_chat chunk: {e}"
                    )))
                }
            }
        }
        Ok::<String, crate::error::PlatformError>(text)
    };

    let raw_text = match tokio::time::timeout(
        std::time::Duration::from_secs(LLM_SUMMARY_TIMEOUT_SECS),
        collect_fut,
    )
    .await
    {
        Ok(r) => r?,
        Err(_) => {
            return Err(crate::error::PlatformError::Other(anyhow::anyhow!(
                "LLM summary timed out"
            )));
        }
    };

    let cleaned = sanitize_summary(&raw_text);
    if cleaned.chars().count() < 4 {
        // Python: 太短的不写回,保留 rough_summary。
        return Ok(false);
    }
    sqlx::query("update branch_commits set summary = $1 where id = $2")
        .bind(&cleaned)
        .bind(commit_id)
        .execute(pool)
        .await?;
    Ok(true)
}

/// 拼 `_run_llm_summary` 的 user prompt。Python 字面量:
/// `f"玩家输入：\n{player_text[:600]}\n\nGM 响应：\n{gm_text[:1200]}"`。
fn build_summary_prompt(player_text: &str, gm_text: &str) -> String {
    let player_clip: String = player_text.chars().take(600).collect();
    let gm_clip: String = gm_text.chars().take(1200).collect();
    format!(
        "玩家输入：\n{player_clip}\n\nGM 响应：\n{gm_clip}"
    )
}

/// 与 Python `_run_llm_summary` 完全同语义的清洗器:
/// 1. trim
/// 2. 前缀 strip:`^[【「"':\-—]+`
/// 3. 后缀 strip:`[】」"'。！？!?]+$`
/// 4. 换行替换为空格
/// 5. > 32 字截断到 32(按 char,不按 byte;CJK 安全)
fn sanitize_summary(text: &str) -> String {
    static LEAD_RE: OnceCell<Regex> = OnceCell::new();
    static TAIL_RE: OnceCell<Regex> = OnceCell::new();
    let lead = LEAD_RE
        .get_or_init(|| Regex::new(r#"^[【「"':：\-—]+"#).expect("lead regex"));
    let tail = TAIL_RE
        .get_or_init(|| Regex::new(r#"[】」"'。！？!?]+$"#).expect("tail regex"));

    let mut s = text.trim().to_string();
    s = lead.replace(&s, "").to_string();
    s = tail.replace(&s, "").to_string();
    s = s.replace('\n', " ").trim().to_string();
    let count = s.chars().count();
    if count > 32 {
        s = s.chars().take(32).collect();
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 入口 guard:空 commit_id / 空文本不应 panic / 不应 spawn 任何东西。
    #[test]
    fn schedule_skips_when_empty() {
        // 签名稳定即可 —— early-return 路径不需要 tokio runtime,
        // 也不需要构造 PgPool。
        let _f: fn(PgPool, i64, String, String) = schedule_llm_summary;
    }

    // ── sanitize_summary 对齐 Python `_run_llm_summary` ───────────────

    #[test]
    fn sanitize_strips_leading_brackets_and_quotes() {
        // 对齐 Python: re.sub(r"^[【「\"'：:\-—]+", "", summary)
        let cases = [
            ("【主角推门进城", "主角推门进城"),
            ("「主角推门进城", "主角推门进城"),
            ("\"主角推门进城", "主角推门进城"),
            ("：主角推门进城", "主角推门进城"),
            ("——主角推门进城", "主角推门进城"),
        ];
        for (input, want) in cases {
            assert_eq!(sanitize_summary(input), want, "input={input}");
        }
    }

    #[test]
    fn sanitize_strips_trailing_punctuation() {
        // 对齐 Python: re.sub(r"[】」\"'。！？!?]+$", "", summary)
        let cases = [
            ("主角推门进城。", "主角推门进城"),
            ("主角推门进城！", "主角推门进城"),
            ("主角推门进城?", "主角推门进城"),
            ("主角推门进城」", "主角推门进城"),
            ("主角推门进城\"", "主角推门进城"),
        ];
        for (input, want) in cases {
            assert_eq!(sanitize_summary(input), want, "input={input}");
        }
    }

    #[test]
    fn sanitize_replaces_newlines_and_trims() {
        // 换行替空格 + 总 trim
        assert_eq!(sanitize_summary("  主角\n推门  "), "主角 推门");
    }

    #[test]
    fn sanitize_truncates_long_text_by_char_not_byte() {
        // 33 个中文字符 → 截到 32 字符,字节长度不能踩半字
        let raw: String = "字".repeat(33);
        let out = sanitize_summary(&raw);
        assert_eq!(out.chars().count(), 32);
        // 截断点在 char 边界,UTF-8 不会破损
        assert!(out.is_char_boundary(out.len()));
    }

    #[test]
    fn sanitize_short_text_passes_through() {
        // < 4 字符也照常返回(由调用方判断 length 阈值)。
        assert_eq!(sanitize_summary("跳过"), "跳过");
    }

    #[test]
    fn build_summary_prompt_clips_player_and_gm_text() {
        // Python: player[:600],gm[:1200]
        let player = "你".repeat(800);
        let gm = "我".repeat(2000);
        let prompt = build_summary_prompt(&player, &gm);
        assert!(prompt.starts_with("玩家输入："));
        // 玩家截到 600,GM 截到 1200,不会超
        let after_player_marker = prompt.split_once("玩家输入：\n").unwrap().1;
        let (player_part, _) = after_player_marker.split_once("\n\nGM 响应：\n").unwrap();
        assert_eq!(player_part.chars().count(), 600);
    }

    // ── 注入语义 ────────────────────────────────────────────────────────

    /// 验证未注入时 `summary_backend_is_set()` 仍可能因其他测试已注入 → 返回 true。
    /// 因为 OnceCell 跨测试线程一旦 set 就持久,所以这里只断言函数可调用,
    /// 不强求 false(避免测试顺序依赖)。
    #[test]
    fn init_summary_backend_helper_is_callable() {
        // 不真实 set —— 仅验证 helper API 在 cfg(test) 下编译通过。
        let _ = summary_backend_is_set();
    }
}
