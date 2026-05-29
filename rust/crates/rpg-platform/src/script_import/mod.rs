//! script_import —— 拆书导入流水线。
//!
//! 对应 Python `rpg/platform_app/script_import.py` (1006 行)。
//! 实现:
//! - `splitter`  —— 章节切分(decode_bytes / clean_text / split_chapters_with_report)
//! - `upload`    —— init_upload / put_chunk / finish_upload / cancel_upload / consume
//! - `import_script` —— 端到端流水线:bytes → 切章 → 落 scripts + script_chapters → 触发 embedding
//! - `schedule_knowledge_sync` —— 用 import_jobs 表去重 + 限流 + spawn embedding
//! - `get_sync_status` —— 查最新 sync job 状态
//!
//! ## 与 Python 的差异
//! - DB:写 `scripts` 用 v021 migration 建的表;同 Python schema。
//! - 知识库同步:Python 是 ThreadPoolExecutor + DB 持久化 import_jobs;Rust 端用
//!   `knowledge::embedding::spawn_embed_script`(tokio::spawn,进程内 fire-and-forget)+
//!   import_jobs 行做持久化。心跳 / stale 回收暂不翻 —— Rust 单进程下用 tokio supervision
//!   足够;真上水平扩展时再补 (TODO[P2-SYNC])。
//! - LLM summary:Python 走 sync_script_knowledge 顺带 summarize;Rust 端 embedding 是 only
//!   embed,summary 留 TODO (TODO[P2-LLM])。
//! - heartbeat / recover_pending_sync_jobs:同上,P2 再补 (TODO[P2-SYNC])。

pub mod splitter;
pub mod upload;

use std::path::PathBuf;
use std::sync::Arc;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};
use crate::knowledge::embedding::{EmbeddingClient, spawn_embed_script};
use crate::library::safe_filename;

pub use splitter::{Chapter, SplitReport};
pub use upload::{
    cancel_upload, cleanup_stale_upload_chunks, consume_upload_chunks, finish_upload, init_upload,
    put_chunk, UploadMeta, MAX_CHUNKS, MAX_SCRIPT_UPLOAD_BYTES, MAX_UPLOAD_CHUNK_BYTES,
    max_chunks, max_script_upload_bytes, max_upload_chunk_bytes,
};

/// 同用户跨剧本最多 1 个活跃 knowledge_sync 任务(对应 Python 限流)。
pub const MAX_ACTIVE_JOBS_PER_USER: i64 = 1;

/// 剧本根目录;读 `RPG_SCRIPT_DIR`,否则 `platform_data/scripts`。
pub fn script_root() -> PathBuf {
    if let Ok(p) = std::env::var("RPG_SCRIPT_DIR") {
        return PathBuf::from(p);
    }
    PathBuf::from("platform_data/scripts")
}

fn unique_path(target: &std::path::Path) -> PathBuf {
    if !target.exists() {
        return target.to_path_buf();
    }
    let stem = target.file_stem().and_then(|s| s.to_str()).unwrap_or("file");
    let ext = target.extension().and_then(|s| s.to_str()).unwrap_or("");
    let parent = target.parent().unwrap_or(std::path::Path::new("."));
    for i in 2..1000 {
        let name = if ext.is_empty() {
            format!("{}-{}", stem, i)
        } else {
            format!("{}-{}.{}", stem, i, ext)
        };
        let cand = parent.join(name);
        if !cand.exists() {
            return cand;
        }
    }
    target.to_path_buf()
}

// ─────────────────────────── ImportJob 旧 API(保留)─────────────────────────
// 老接口 start_job/transition/fail/get 走 `script_import_jobs` —— 但那张表
// 从来没在 Rust 端 migration 里建过。新流水线统一用 `import_jobs`(v009/012/013)。
// 旧 API 删掉,reroute 到 import_jobs。
//
// 调用方 routes/server 都没用过它(grep 过),改名不会破坏现有调用。

/// Job 状态枚举,对应 import_jobs.status。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Pending,
    Running,
    Done,
    Failed,
    Cancelled,
}

impl JobStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            JobStatus::Pending => "pending",
            JobStatus::Running => "running",
            JobStatus::Done => "done",
            JobStatus::Failed => "failed",
            JobStatus::Cancelled => "cancelled",
        }
    }
}

/// import_jobs 行视图。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ImportJob {
    pub id: i64,
    pub job_id: String,
    pub user_id: i64,
    pub script_id: Option<i64>,
    pub kind: String,
    pub status: String,
    pub stage: String,
    pub stage_progress: i32,
    pub stage_total: i32,
    pub overall_progress: i32,
    pub overall_total: i32,
    pub error: String,
    pub created_at: Option<DateTime<Utc>>,
    pub started_at: Option<DateTime<Utc>>,
    pub finished_at: Option<DateTime<Utc>>,
}

fn row_to_job(row: &sqlx::postgres::PgRow) -> PlatformResult<ImportJob> {
    Ok(ImportJob {
        id: row.try_get("id")?,
        job_id: row.try_get("job_id")?,
        user_id: row.try_get("user_id")?,
        script_id: row.try_get::<Option<i64>, _>("script_id").ok().flatten(),
        kind: row.try_get::<String, _>("kind").unwrap_or_default(),
        status: row.try_get::<String, _>("status")?,
        stage: row.try_get::<String, _>("stage").unwrap_or_default(),
        stage_progress: row.try_get::<i32, _>("stage_progress").unwrap_or(0),
        stage_total: row.try_get::<i32, _>("stage_total").unwrap_or(0),
        overall_progress: row.try_get::<i32, _>("overall_progress").unwrap_or(0),
        overall_total: row.try_get::<i32, _>("overall_total").unwrap_or(0),
        error: row.try_get::<String, _>("error").unwrap_or_default(),
        created_at: row.try_get("created_at").ok(),
        started_at: row.try_get("started_at").ok(),
        finished_at: row.try_get("finished_at").ok(),
    })
}

// ─────────────────────────── 端到端 import_script ───────────────────────────

/// `import_script` 的输入。
#[derive(Debug, Clone)]
pub enum ImportSource<'a> {
    /// 单次 POST 的原始 bytes(对应 Python file_item base64 解码后)。
    Bytes { name: &'a str, raw: Vec<u8> },
    /// 已通过 init_upload + put_chunk + finish_upload 完成的分片。
    Upload { upload_id: &'a str, name: Option<&'a str> },
}

/// import_script 输出。
#[derive(Debug, Clone, Serialize)]
pub struct ImportResult {
    pub script_id: i64,
    pub title: String,
    pub chapter_count: i32,
    pub word_count: i64,
    pub report: SplitReport,
    pub encoding: String,
    pub source_path: String,
    pub preview: Vec<ChapterPreview>,
    pub knowledge_job_id: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ChapterPreview {
    pub chapter_index: i32,
    pub title: String,
    pub volume_title: String,
    pub word_count: i32,
    pub content_preview: String,
}

fn chapter_preview(chapters: &[Chapter], limit: usize) -> Vec<ChapterPreview> {
    chapters
        .iter()
        .take(limit)
        .map(|c| {
            let preview_text: String = c
                .content
                .replace('\n', " ")
                .chars()
                .take(120)
                .collect();
            ChapterPreview {
                chapter_index: c.chapter_number,
                title: c.title.clone(),
                volume_title: c.volume_title.clone(),
                word_count: c.content.chars().count() as i32,
                content_preview: preview_text,
            }
        })
        .collect()
}

/// Python `import_script` 的端到端流水线。
///
/// 流程:
/// 1. 拿到 raw bytes(直传或拼分片)
/// 2. decode_bytes → clean_text → split_chapters_with_report
/// 3. 写原文件到 `scripts/user_<id>/<name>`
/// 4. INSERT scripts(...) + 批量 INSERT script_chapters(...)
/// 5. schedule_knowledge_sync —— spawn embedding 任务
///
/// embedding_client 可选(None 时不触发后台 embed —— 测试 / 离线导入用)。
pub async fn import_script(
    pool: &PgPool,
    user_id: i64,
    source: ImportSource<'_>,
    split_rule: &str,
    custom_pattern: &str,
    title: &str,
    embedding_client: Option<Arc<dyn EmbeddingClient>>,
) -> PlatformResult<ImportResult> {
    // 1) 取 bytes + 文件名
    let (raw, original_name) = match source {
        ImportSource::Bytes { name, raw } => {
            let n = if name.is_empty() { "script.txt" } else { name };
            (raw, safe_filename(n))
        }
        ImportSource::Upload { upload_id, name } => {
            let bytes = consume_upload_chunks(user_id, upload_id, false)?;
            let n = name.map(safe_filename).unwrap_or_else(|| {
                // upload_id 用作 fallback name
                format!("{}.txt", upload_id)
            });
            (bytes, n)
        }
    };
    if raw.len() > max_script_upload_bytes() {
        return Err(PlatformError::validation(format!(
            "剧本文件过大:{}",
            original_name
        )));
    }

    // 2) decode + clean + split
    let (text, encoding) = splitter::decode_bytes(&raw);
    let cleaned_check = splitter::clean_text(&text);
    if cleaned_check.is_empty() {
        return Err(PlatformError::validation("剧本文本为空"));
    }

    if split_rule == "custom" {
        if custom_pattern.trim().is_empty() {
            return Err(PlatformError::validation(
                "split_rule=custom 时必须提供 custom_pattern",
            ));
        }
        if splitter::build_custom_pattern(custom_pattern).is_none() {
            return Err(PlatformError::validation("custom_pattern 不是合法/安全正则"));
        }
    }

    let (chapters, report) =
        splitter::split_chapters_with_report(&text, split_rule, custom_pattern);

    // 用户明确选了某种模式但实际走了别的 —— 拒绝静默回退,对齐 Python
    let rule = split_rule.trim();
    if !rule.is_empty() && rule != "auto" {
        let mode_matches = report.mode == rule
            || report.mode == format!("rule_{}", rule)
            || report.mode == "custom_pattern"
            || report.mode == "auto";
        if !mode_matches {
            return Err(PlatformError::validation(format!(
                "无法用 {} 规则切分该文本:实际只能用 {}",
                rule, report.mode
            )));
        }
    }
    if chapters.is_empty() {
        return Err(PlatformError::validation("没有识别到可导入章节"));
    }

    // 3) 写原文件到磁盘
    let script_title = {
        let t = title.trim();
        let chosen = if !t.is_empty() {
            t.to_string()
        } else {
            std::path::Path::new(&original_name)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("未命名剧本")
                .to_string()
        };
        chosen.chars().take(160).collect::<String>()
    };

    let user_dir = script_root().join(format!("user_{}", user_id));
    std::fs::create_dir_all(&user_dir)?;
    let target_path = unique_path(&user_dir.join(&original_name));
    std::fs::write(&target_path, &raw)?;
    let storage_rel = target_path
        .strip_prefix(script_root().parent().unwrap_or(std::path::Path::new(".")))
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| target_path.to_string_lossy().into_owned());

    // 4) 落库
    let total_words: i64 = chapters
        .iter()
        .map(|c| c.content.chars().count() as i64)
        .sum();
    let description = format!(
        "导入剧本 · {}章 · {} · 置信 {:.2}",
        chapters.len(),
        report.mode_label,
        report.confidence
    );

    let report_with_meta = serde_json::json!({
        "mode": report.mode,
        "mode_label": report.mode_label,
        "confidence": report.confidence,
        "chapter_count": report.chapter_count,
        "total_words": report.total_words,
        "average_words": report.average_words,
        "min_words": report.min_words,
        "max_words": report.max_words,
        "split_rule": report.split_rule,
        "reasons": report.reasons,
        "encoding": encoding,
        "source_name": original_name,
        "storage_path": storage_rel,
    });

    let script_row = sqlx::query(
        r#"
        insert into scripts(owner_id, title, description, source_path,
                            chapter_count, word_count, import_report)
        values ($1, $2, $3, $4, $5, $6, $7)
        returning id, title
        "#,
    )
    .bind(user_id)
    .bind(&script_title)
    .bind(&description)
    .bind(&storage_rel)
    .bind(chapters.len() as i32)
    .bind(total_words)
    .bind(&report_with_meta)
    .fetch_one(pool)
    .await?;

    let script_id: i64 = script_row.try_get("id")?;
    let final_title: String = script_row.try_get("title")?;

    // 批量插 script_chapters
    for (idx, ch) in chapters.iter().enumerate() {
        let chapter_index = (idx + 1) as i32;
        let title_trunc: String = ch.title.chars().take(200).collect();
        let vol_trunc: String = ch.volume_title.chars().take(200).collect();
        let content_len = ch.content.chars().count() as i32;
        sqlx::query(
            r#"
            insert into script_chapters(
                script_id, chapter_index, title, content, word_count,
                volume_title, source_marker, confidence
            )
            values ($1, $2, $3, $4, $5, $6, $7, $8)
            "#,
        )
        .bind(script_id)
        .bind(chapter_index)
        .bind(if title_trunc.is_empty() {
            format!("第{}章", chapter_index)
        } else {
            title_trunc
        })
        .bind(&ch.content)
        .bind(content_len)
        .bind(vol_trunc)
        .bind(&ch.source_marker)
        .bind(report.confidence)
        .execute(pool)
        .await?;
    }

    // 5) 触发 knowledge sync(异步,fire-and-forget)
    let knowledge_job_id = match embedding_client {
        Some(client) => Some(schedule_knowledge_sync(pool, user_id, script_id, client).await?),
        None => None,
    };

    Ok(ImportResult {
        script_id,
        title: final_title,
        chapter_count: chapters.len() as i32,
        word_count: total_words,
        report,
        encoding: encoding.to_string(),
        source_path: storage_rel,
        preview: chapter_preview(&chapters, 8),
        knowledge_job_id,
    })
}

// ─────────────────────────── schedule_knowledge_sync ──────────────────────

/// Python `_schedule_knowledge_sync` 的 Rust 版。
///
/// - 同 user 跨 script 活跃 sync 任务数 >= MAX_ACTIVE_JOBS_PER_USER → 拒绝
/// - 同 (user, script) 已有 pending/running → 直接返回老 job_id(走 unique index ON CONFLICT)
/// - 否则插一行新 job,然后 spawn embedding(进程内 tokio task)
///
/// 与 Python 不同:Rust 端 embedding 用 `spawn_embed_script(pool, client, script_id)` —— 它内部
/// 完整处理 chunk embed / character_cards / worldbook,不再走 sync_script_knowledge wrapper。
/// 任务进度回写 import_jobs 留到 P2(spawn_embed_script 自己也不更新 import_jobs)。
pub async fn schedule_knowledge_sync(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
    client: Arc<dyn EmbeddingClient>,
) -> PlatformResult<String> {
    // 限流:同 user 跨 script 已有几个 active sync
    let active_count: i64 = sqlx::query_scalar(
        r#"
        select count(*)::bigint from import_jobs
        where user_id = $1
          and kind = 'knowledge_sync'
          and status in ('pending', 'running')
          and (user_id, script_id) is distinct from ($1, $2)
        "#,
    )
    .bind(user_id)
    .bind(script_id)
    .fetch_one(pool)
    .await?;
    if active_count >= MAX_ACTIVE_JOBS_PER_USER {
        return Err(PlatformError::validation(format!(
            "已有 {} 个同步任务在跑,请等已有任务完成(每用户最多 {} 个并发)",
            active_count, MAX_ACTIVE_JOBS_PER_USER
        )));
    }

    let mut buf = [0u8; 6];
    rand::Rng::fill(&mut rand::thread_rng(), &mut buf);
    let token: String = buf.iter().map(|b| format!("{:02x}", b)).collect();
    let job_id = format!("ks_{}_{}", script_id, token);

    // 原子插入 + ON CONFLICT(走 uq_import_jobs_active_per_script partial unique)
    let inserted = sqlx::query(
        r#"
        insert into import_jobs(job_id, user_id, script_id, kind, status, stage,
                                stage_progress, stage_total, overall_progress, overall_total)
        values ($1, $2, $3, 'knowledge_sync', 'pending', 'pending', 0, 1, 0, 1)
        on conflict (user_id, script_id, kind)
            where status in ('pending', 'running')
            do nothing
        returning job_id
        "#,
    )
    .bind(&job_id)
    .bind(user_id)
    .bind(script_id)
    .fetch_optional(pool)
    .await?;

    let actual_job_id = match inserted {
        Some(row) => row.try_get::<String, _>("job_id")?,
        None => {
            // 撞了去查现有 active job
            let existing = sqlx::query_scalar::<_, String>(
                r#"
                select job_id from import_jobs
                where user_id = $1 and script_id = $2 and kind = 'knowledge_sync'
                  and status in ('pending', 'running')
                order by created_at desc limit 1
                "#,
            )
            .bind(user_id)
            .bind(script_id)
            .fetch_optional(pool)
            .await?;
            existing.ok_or_else(|| PlatformError::Other(anyhow::anyhow!(
                "无法插入 sync job 也无法读取已存在 job_id(请重试)"
            )))?
        }
    };

    // fire-and-forget:把这个 job 直接标 running 并 spawn embedding
    // 不走 Python 的 _claim_pending_job —— Rust 单进程,刚插入的 pending 立即转 running
    sqlx::query(
        r#"
        update import_jobs
        set status = 'running', started_at = coalesce(started_at, now()), updated_at = now()
        where job_id = $1 and status = 'pending'
        "#,
    )
    .bind(&actual_job_id)
    .execute(pool)
    .await?;

    // spawn embed_script;后台 task 在自己完成后用 RUNNING_MAP flag 标记结束。
    // 这里没把 job_id 串进去 —— import_jobs.status 留 'running' 永久(知识库不会失败式中断)
    // (TODO[P2-SYNC]:让 spawn_embed_script 完成时回写 import_jobs.status='done'。)
    let _flag = spawn_embed_script(pool.clone(), client, script_id);

    Ok(actual_job_id)
}

// ─────────────────────────── get_sync_status ──────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct SyncStatus {
    pub job_id: Option<String>,
    pub status: String,
    pub progress: i32,
    pub error: Option<String>,
    pub started_at: Option<DateTime<Utc>>,
    pub finished_at: Option<DateTime<Utc>>,
}

/// Python `get_sync_status`:返回该剧本最近一次 sync 任务状态。
pub async fn get_sync_status(
    pool: &PgPool,
    user_id: i64,
    script_id: i64,
) -> PlatformResult<SyncStatus> {
    let row = sqlx::query(
        r#"
        select job_id, status, stage_progress, stage_total,
               overall_progress, overall_total,
               started_at, finished_at, error
        from import_jobs
        where user_id = $1 and script_id = $2 and kind = 'knowledge_sync'
        order by created_at desc limit 1
        "#,
    )
    .bind(user_id)
    .bind(script_id)
    .fetch_optional(pool)
    .await?;

    match row {
        None => Ok(SyncStatus {
            job_id: None,
            status: "none".into(),
            progress: 0,
            error: None,
            started_at: None,
            finished_at: None,
        }),
        Some(r) => {
            let job_id: String = r.try_get("job_id")?;
            let status: String = r.try_get("status")?;
            let overall: i32 = r.try_get::<i32, _>("overall_progress").unwrap_or(0);
            let total: i32 = r.try_get::<i32, _>("overall_total").unwrap_or(1).max(1);
            let progress = ((overall as f32 / total as f32) * 100.0).round() as i32;
            let err: String = r.try_get::<String, _>("error").unwrap_or_default();
            Ok(SyncStatus {
                job_id: Some(job_id),
                status,
                progress: progress.clamp(0, 100),
                error: if err.is_empty() { None } else { Some(err) },
                started_at: r.try_get("started_at").ok(),
                finished_at: r.try_get("finished_at").ok(),
            })
        }
    }
}

/// 取一个 import_jobs 行(给 routes / 测试用)。
pub async fn get_job(pool: &PgPool, job_id: &str) -> PlatformResult<ImportJob> {
    let row = sqlx::query("select * from import_jobs where job_id = $1")
        .bind(job_id)
        .fetch_optional(pool)
        .await?;
    match row {
        Some(r) => row_to_job(&r),
        None => Err(PlatformError::not_found("import job not found")),
    }
}

// ─────────────────────────── tests ────────────────────────────────────────
#[cfg(test)]
mod tests {
    use super::*;
    use crate::script_import::splitter::{split_chapters_with_report, Chapter};

    #[test]
    fn test_chapter_preview_truncates_and_replaces_newlines() {
        let chapters = vec![Chapter {
            title: "第一章".into(),
            content: "第一行\n第二行非常长".repeat(20),
            chapter_number: 1,
            volume_title: "正卷".into(),
            source_marker: String::new(),
        }];
        let preview = chapter_preview(&chapters, 8);
        assert_eq!(preview.len(), 1);
        assert!(!preview[0].content_preview.contains('\n'));
        assert!(preview[0].content_preview.chars().count() <= 120);
        assert_eq!(preview[0].volume_title, "正卷");
    }

    #[test]
    fn test_job_status_strings_match_python() {
        // Python script_import.py 用这些字符串
        assert_eq!(JobStatus::Pending.as_str(), "pending");
        assert_eq!(JobStatus::Running.as_str(), "running");
        assert_eq!(JobStatus::Done.as_str(), "done");
        assert_eq!(JobStatus::Failed.as_str(), "failed");
        assert_eq!(JobStatus::Cancelled.as_str(), "cancelled");
    }

    #[test]
    fn test_unique_path_falls_back_on_existing() {
        let tmp = std::env::temp_dir().join(format!(
            "rpg_sip_unique_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let p = tmp.join("a.txt");
        std::fs::write(&p, b"x").unwrap();
        let next = unique_path(&p);
        assert_ne!(next, p);
        assert!(next.to_string_lossy().contains("a-2"));
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_import_source_bytes_constructs() {
        // 单纯压制 enum 构造不被未来 refactor 破坏
        let src = ImportSource::Bytes {
            name: "x.txt",
            raw: vec![1, 2, 3],
        };
        match src {
            ImportSource::Bytes { name, raw } => {
                assert_eq!(name, "x.txt");
                assert_eq!(raw, vec![1, 2, 3]);
            }
            _ => panic!("expected Bytes"),
        }
    }

    #[test]
    fn test_split_then_preview_full_text() {
        // 端到端(不入库):decode_bytes → clean_text → split → preview
        let text = "第一章 启程\n他出门了。这是一段比较长的正文,至少够 strong heading 触发。\n\n第二章 抵达\n他到了目的地。说了几句话。又走了一段路。";
        let raw = text.as_bytes();
        let (decoded, enc) = splitter::decode_bytes(raw);
        assert_eq!(enc, "utf-8");
        let (chapters, report) =
            split_chapters_with_report(&decoded, "auto", "");
        assert_eq!(chapters.len(), 2);
        let preview = chapter_preview(&chapters, 8);
        assert_eq!(preview.len(), 2);
        assert!(preview[0].title.contains("第一章"));
        assert!(report.chapter_count == 2);
    }
}
