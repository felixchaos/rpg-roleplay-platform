//! phase_digest_agent — save 级阶段摘要 LLM 子代理。
//!
//! 对应 Python: `rpg/agents/phase_digest_agent.py`
//!
//! GM 在长游戏(100+ turn)中 context_engine 只能塞下 6 轮 recent_chat,
//! 对 100 turn 之前彻底失忆。本 agent 把每段 phase 的对话喂给轻量 LLM,
//! 产出结构化摘要(summary / key_events / key_npcs / key_locations /
//! key_decisions / emotion_arc),回写 save_phase_digests。

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::PgPool;
use std::sync::Arc;

use crate::common::{
    call_structured, extract_json_block, AgentError, AgentResult, ChatMessage, SharedLlm,
};

const SYSTEM_PROMPT: &str = include_str!("prompts/phase_digest.txt");

const DEFAULT_MAX_TOKENS: usize = 2400;
const MAX_RETRIES: u32 = 1;

#[derive(Debug, Clone)]
pub struct PhaseDigestInput {
    pub save_id: i64,
    pub phase_index: i32,
    pub user_id: Option<i64>,
    pub force: bool,
    pub model_override: Option<String>,
    pub api_id_override: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PhaseDigest {
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub key_events: Vec<Value>,
    #[serde(default)]
    pub key_npcs: Vec<Value>,
    #[serde(default)]
    pub key_locations: Vec<String>,
    #[serde(default)]
    pub key_decisions: Vec<Value>,
    #[serde(default)]
    pub emotion_arc: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PhaseDigestOutput {
    pub digest: PhaseDigest,
    pub error: Option<String>,
}

/// 加载到的 phase 上下文(从 DB 拉)。
#[derive(Debug, Clone, Default)]
struct PhaseContext {
    phase_label: String,
    /// 上一个 phase 的 digest 摘要(用作衔接)。
    prev_summary: String,
    /// 该 phase 内的对话原文(已拼好)。
    dialog_text: String,
    /// 剧本端 chapter_facts 摘要(衔接段:GM 应该把 phase 推到哪里)。
    script_chapter_facts: String,
    /// 是否已有摘要(status=closed)。
    already_closed: bool,
    /// 已存在的 summary(用于 force=false 直接返回)。
    existing_digest: Option<PhaseDigest>,
}

pub struct PhaseDigestAgent {
    llm: SharedLlm,
    max_tokens: usize,
    /// 可选 DB pool。注入后启用 DB 加载 + persist;为 None 时回退到骨架行为。
    db: Option<Arc<PgPool>>,
}

impl PhaseDigestAgent {
    pub fn new(llm: SharedLlm) -> Self {
        Self {
            llm,
            max_tokens: DEFAULT_MAX_TOKENS,
            db: None,
        }
    }

    /// 注入 PgPool。注入后 compact_phase 会从 platform DB 读历史并 persist 新摘要。
    pub fn with_db(mut self, pool: Arc<PgPool>) -> Self {
        self.db = Some(pool);
        self
    }

    /// 主入口。返回结构化 digest 或 error。
    pub async fn compact_phase(
        &self,
        input: PhaseDigestInput,
    ) -> AgentResult<PhaseDigestOutput> {
        // 1) DB 加载历史上下文。
        let ctx = self.load_phase_context(&input).await;

        // 2) force=false 且现存 closed digest → 直接返回。
        if !input.force {
            if let Some(existing) = ctx.existing_digest.clone() {
                if ctx.already_closed && !existing.summary.is_empty() {
                    return Ok(PhaseDigestOutput {
                        digest: existing,
                        error: None,
                    });
                }
            }
        }

        let user_prompt = self.build_user_prompt(&input, &ctx)?;
        let messages = vec![ChatMessage::user(user_prompt)];

        let digest = match self.call_with_retry(&messages).await {
            Ok(d) => d,
            Err(e) => {
                return Ok(PhaseDigestOutput {
                    digest: PhaseDigest::default(),
                    error: Some(e.to_string()),
                });
            }
        };

        // 3) persist。失败仅警告,不破坏主流程。
        if let Err(e) = self.persist_digest(&input, &ctx, &digest).await {
            tracing::warn!("[phase_digest] persist 失败: {e}");
        }

        Ok(PhaseDigestOutput {
            digest,
            error: None,
        })
    }

    /// 加载 phase 上下文。无 DB 时返回 default。
    async fn load_phase_context(&self, input: &PhaseDigestInput) -> PhaseContext {
        let Some(pool) = self.db.as_ref() else {
            return PhaseContext::default();
        };

        let mut ctx = PhaseContext::default();

        // (a) save_phase_digests 行 — 拿 phase_label / 现存 summary / status。
        //     表中没有 status 列;status=closed 用 summary 非空 + key_events 非空启发判定。
        if let Ok(Some(row)) =
            rpg_db::repos::save_phase_digests::get(pool, input.save_id, input.phase_index).await
        {
            ctx.phase_label = row.phase_label.clone();
            let dig = PhaseDigest {
                summary: row.summary.clone(),
                key_events: row
                    .key_events
                    .as_array()
                    .cloned()
                    .unwrap_or_default(),
                key_npcs: row
                    .characters_state
                    .as_array()
                    .cloned()
                    .unwrap_or_default(),
                key_locations: row
                    .world_state
                    .get("locations")
                    .and_then(|v| v.as_array())
                    .map(|a| {
                        a.iter()
                            .filter_map(|x| x.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default(),
                key_decisions: row
                    .metadata
                    .get("key_decisions")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default(),
                emotion_arc: row
                    .metadata
                    .get("emotion_arc")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            };
            ctx.already_closed = !dig.summary.trim().is_empty()
                && row
                    .metadata
                    .get("status")
                    .and_then(|v| v.as_str())
                    .map(|s| s == "closed")
                    .unwrap_or(!dig.summary.is_empty());
            ctx.existing_digest = Some(dig);
        }

        // (b) 上一 phase 的 summary。
        if input.phase_index > 0 {
            if let Ok(Some(prev)) = rpg_db::repos::save_phase_digests::get(
                pool,
                input.save_id,
                input.phase_index - 1,
            )
            .await
            {
                ctx.prev_summary = prev.summary;
            }
        }

        // (c) branch_commits 对话原文(可能表名不同,用 raw SQL 兜底,失败不中断)。
        //     使用 sqlx::query 拉 turn / role / content。
        let dialog_q = sqlx::query_as::<_, (Option<i32>, Option<String>, Option<String>)>(
            r#"SELECT turn, role, content
               FROM branch_commits
               WHERE save_id = $1
               ORDER BY turn ASC, id ASC
               LIMIT 200"#,
        )
        .bind(input.save_id)
        .fetch_all(pool.as_ref())
        .await;
        if let Ok(rows) = dialog_q {
            let mut lines: Vec<String> = Vec::with_capacity(rows.len());
            for (turn, role, content) in rows {
                let t = turn.map(|x| x.to_string()).unwrap_or_default();
                let r = role.unwrap_or_default();
                let c = content.unwrap_or_default();
                let c_trunc: String = c.chars().take(600).collect();
                if !c_trunc.trim().is_empty() {
                    lines.push(format!("[t={t} {r}] {c_trunc}"));
                }
            }
            if !lines.is_empty() {
                ctx.dialog_text = lines.join("\n");
            }
        }

        // (d) script_chapter_facts — 衔接段:GM 应该把当前 phase 推到哪里。
        //     从 game_saves → script_id → chapter_facts(取 phase_index 附近 ±2 章)。
        ctx.script_chapter_facts = load_script_chapter_facts_for_phase(
            pool.as_ref(),
            input.save_id,
            input.phase_index,
        )
        .await
        .unwrap_or_default();

        ctx
    }

    /// 把 digest 写回 save_phase_digests。
    async fn persist_digest(
        &self,
        input: &PhaseDigestInput,
        ctx: &PhaseContext,
        digest: &PhaseDigest,
    ) -> Result<(), sqlx::Error> {
        let Some(pool) = self.db.as_ref() else {
            return Ok(());
        };

        // 构造 row,沿用现有 phase_label;无则给 placeholder。
        let phase_label = if ctx.phase_label.is_empty() {
            format!("phase_{}", input.phase_index)
        } else {
            ctx.phase_label.clone()
        };

        let mut metadata = serde_json::json!({
            "status": "closed",
            "emotion_arc": digest.emotion_arc,
            "key_decisions": digest.key_decisions,
        });
        if let Some(uid) = input.user_id {
            metadata
                .as_object_mut()
                .unwrap()
                .insert("user_id".to_string(), Value::from(uid));
        }
        let world_state = serde_json::json!({
            "locations": digest.key_locations,
        });

        let row = rpg_db::repos::save_phase_digests::SavePhaseDigest {
            id: 0, // upsert 时不读
            save_id: input.save_id,
            phase_index: input.phase_index,
            phase_label,
            summary: digest.summary.clone(),
            key_events: Value::Array(digest.key_events.clone()),
            characters_state: Value::Array(digest.key_npcs.clone()),
            world_state,
            metadata,
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        };
        rpg_db::repos::save_phase_digests::upsert(pool.as_ref(), &row).await?;
        Ok(())
    }

    /// 构造 user prompt。注入 dialog / prev summary / script chapter_facts 衔接段。
    fn build_user_prompt(
        &self,
        input: &PhaseDigestInput,
        ctx: &PhaseContext,
    ) -> AgentResult<String> {
        let dialog = if ctx.dialog_text.is_empty() {
            "(无 branch_commits 历史或表未就绪)".to_string()
        } else {
            ctx.dialog_text.clone()
        };
        let prev = if ctx.prev_summary.is_empty() {
            "(无上一段摘要)".to_string()
        } else {
            ctx.prev_summary.clone()
        };
        let script_facts = if ctx.script_chapter_facts.is_empty() {
            "(无剧本端 chapter_facts)".to_string()
        } else {
            ctx.script_chapter_facts.clone()
        };
        Ok(format!(
            "## 阶段元数据\n- save_id: {}\n- phase_index: {}\n- phase_label: {}\n\n\
             ## 玩家与 GM 对话原文\n{}\n\n\
             ## 上一段摘要(衔接参考)\n{}\n\n\
             ## 剧本预期段落(chapter_facts 衔接段)\n{}\n",
            input.save_id,
            input.phase_index,
            if ctx.phase_label.is_empty() {
                "(未命名)"
            } else {
                ctx.phase_label.as_str()
            },
            dialog,
            prev,
            script_facts,
        ))
    }

    async fn call_with_retry(&self, messages: &[ChatMessage]) -> AgentResult<PhaseDigest> {
        let mut last_err: Option<AgentError> = None;
        for attempt in 0..=MAX_RETRIES {
            let raw = call_structured(
                self.llm.as_ref(),
                SYSTEM_PROMPT,
                messages,
                self.max_tokens,
            )
            .await
            .map_err(|e| AgentError::Llm(format!("attempt {attempt}: {e}")))?;
            match parse_digest(&raw) {
                Some(d) => return Ok(normalize_digest(d)),
                None => {
                    last_err = Some(AgentError::JsonParse(format!(
                        "attempt {attempt}: digest JSON 解析失败"
                    )));
                }
            }
        }
        Err(last_err.unwrap_or_else(|| AgentError::JsonParse("无法解析 digest".to_string())))
    }
}

fn parse_digest(text: &str) -> Option<PhaseDigest> {
    let blk = extract_json_block(text).ok()?;
    serde_json::from_str::<PhaseDigest>(blk).ok()
}

/// 规范化:截字数 / 限上限 / 去重(简化版)。
fn normalize_digest(mut d: PhaseDigest) -> PhaseDigest {
    if d.key_events.len() > 5 {
        d.key_events.truncate(5);
    }
    if d.key_npcs.len() > 8 {
        d.key_npcs.truncate(8);
    }
    if d.key_locations.len() > 6 {
        d.key_locations.truncate(6);
    }
    if d.key_decisions.len() > 5 {
        d.key_decisions.truncate(5);
    }
    d
}

/// 拉 script 端 chapter_facts 衔接段。
///
/// 流程:
///   1. game_saves(id=save_id) → script_id
///   2. chapter_facts WHERE script_id=$1 — 取窗口 [phase_index-2, phase_index+2]
///      (chapter 字段近似当作 phase_index 用,精度有限但够 prompt 拼接)。
///   3. 拼成 "第N章《title》(time_label): summary" 多行。
///
/// 全部失败/空表 → 返回空 String,由调用方兜底显示 "(无...)"。
async fn load_script_chapter_facts_for_phase(
    pool: &sqlx::PgPool,
    save_id: i64,
    phase_index: i32,
) -> Option<String> {
    let script_row: Option<(Option<i64>,)> =
        sqlx::query_as("SELECT script_id FROM game_saves WHERE id = $1")
            .bind(save_id)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten();
    let script_id = script_row.and_then(|(s,)| s)?;
    let lo = (phase_index - 2).max(0);
    let hi = phase_index + 2;
    let rows = sqlx::query_as::<
        _,
        (i32, Option<String>, Option<String>, Option<String>),
    >(
        r#"SELECT chapter, title, story_time_label, summary
           FROM chapter_facts
           WHERE script_id = $1 AND chapter BETWEEN $2 AND $3
           ORDER BY chapter ASC
           LIMIT 8"#,
    )
    .bind(script_id)
    .bind(lo)
    .bind(hi)
    .fetch_all(pool)
    .await
    .ok()?;
    if rows.is_empty() {
        return None;
    }
    let mut lines: Vec<String> = Vec::with_capacity(rows.len());
    for (chapter, title, time_label, summary) in rows {
        let title_s = title.unwrap_or_default();
        let time_s = time_label.unwrap_or_default();
        let sum: String = summary
            .unwrap_or_default()
            .chars()
            .take(400)
            .collect();
        if sum.trim().is_empty() {
            continue;
        }
        lines.push(format!(
            "- 第{chapter}章《{title_s}》({time_s}): {sum}"
        ));
    }
    if lines.is_empty() {
        None
    } else {
        Some(lines.join("\n"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── parse_digest ────────────────────────────────────────────────

    #[test]
    fn test_parse_digest_valid_json() {
        let json_str = r#"{
            "summary": "这是一段摘要",
            "key_events": [{"turn": 1, "summary": "重要事件"}],
            "key_npcs": [],
            "key_locations": ["大殿"],
            "key_decisions": [],
            "emotion_arc": "好奇 → 紧张"
        }"#;
        let d = parse_digest(json_str).unwrap();
        assert_eq!(d.summary, "这是一段摘要");
        assert_eq!(d.key_locations, vec!["大殿"]);
        assert_eq!(d.emotion_arc, "好奇 → 紧张");
    }

    #[test]
    fn test_parse_digest_invalid_returns_none() {
        assert!(parse_digest("not json at all").is_none());
        assert!(parse_digest("").is_none());
    }

    #[test]
    fn test_parse_digest_with_fence() {
        let fenced = "```json\n{\"summary\":\"摘要\",\"key_events\":[],\"key_npcs\":[],\"key_locations\":[],\"key_decisions\":[],\"emotion_arc\":\"\"}\n```";
        let d = parse_digest(fenced).unwrap();
        assert_eq!(d.summary, "摘要");
    }

    // ── normalize_digest ────────────────────────────────────────────

    #[test]
    fn test_normalize_truncates_lists() {
        let d = PhaseDigest {
            summary: "s".to_string(),
            key_events: (0..10).map(|i| json!({"turn": i})).collect(),
            key_npcs: (0..12).map(|i| json!({"name": i})).collect(),
            key_locations: (0..9).map(|i| i.to_string()).collect(),
            key_decisions: (0..7).map(|i| json!({"turn": i})).collect(),
            emotion_arc: "".to_string(),
        };
        let n = normalize_digest(d);
        assert_eq!(n.key_events.len(), 5);
        assert_eq!(n.key_npcs.len(), 8);
        assert_eq!(n.key_locations.len(), 6);
        assert_eq!(n.key_decisions.len(), 5);
    }

    #[test]
    fn test_normalize_keeps_short_lists() {
        let d = PhaseDigest {
            summary: "短摘要".to_string(),
            key_events: vec![json!({"turn": 1})],
            key_npcs: vec![],
            key_locations: vec!["城门".to_string()],
            key_decisions: vec![],
            emotion_arc: "紧张".to_string(),
        };
        let n = normalize_digest(d);
        assert_eq!(n.key_events.len(), 1);
        assert_eq!(n.key_locations.len(), 1);
    }

    // ── PhaseDigestAgent build_user_prompt ─────────────────────────

    #[test]
    fn test_build_user_prompt_contains_metadata() {
        // 无 DB 时 load_phase_context 返回 default PhaseContext,
        // build_user_prompt 应能正常构造 prompt 字符串。
        use rpg_llm::AnyBackend;
        use rpg_llm::anthropic::AnthropicBackend;
        use std::sync::Arc;

        // 构造一个 stub Anthropic backend(不会真正调用)
        let backend = AnthropicBackend::new("stub-key").expect("build stub backend");
        let agent = PhaseDigestAgent::new(Arc::new(AnyBackend::Anthropic(backend)));
        let input = PhaseDigestInput {
            save_id: 42,
            phase_index: 3,
            user_id: None,
            force: false,
            model_override: None,
            api_id_override: None,
        };
        let ctx = PhaseContext {
            phase_label: "第三阶段".to_string(),
            dialog_text: "玩家说:我要去城门".to_string(),
            ..Default::default()
        };
        let prompt = agent.build_user_prompt(&input, &ctx).unwrap();
        assert!(prompt.contains("42"));
        assert!(prompt.contains("3"));
        assert!(prompt.contains("第三阶段"));
        assert!(prompt.contains("城门"));
    }
}
