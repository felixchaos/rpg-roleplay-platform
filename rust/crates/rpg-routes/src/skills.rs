//! skills.py → skills.rs — Skill 导入与运行路由
//! POST /api/skills/import          — 导入 skill bundle(admin only)
//! POST /api/skills/{skill_id}/run  — 在沙箱里运行 skill(admin only)

use std::path::PathBuf;

use axum::{
    extract::{Path, State},
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use rpg_tools_dsl::skill_executor::run_skill_command;

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/skills/import", post(api_skills_import))
        .route("/api/skills/{skill_id}/run", post(api_skill_run))
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct SkillsImportRequest {
    pub file: Option<Value>,
}

#[derive(Debug, Deserialize, Default)]
pub struct SkillRunRequest {
    pub cmd: Option<Vec<String>>,
    pub command: Option<Vec<String>>,
    pub stdin: Option<String>,
    pub timeout_sec: Option<u64>,
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// POST /api/skills/import
///
/// TODO: 接 multipart upload + skill bundle 解析(Python `skill_importer.py`)。
/// 翻译期返回 not_implemented。
async fn api_skills_import(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(_body): Json<SkillsImportRequest>,
) -> Result<Response, ResponseError> {
    let u = require_user(&s, &headers).await?;
    if u.role != "admin" {
        return Err(ResponseError::forbidden("仅管理员"));
    }
    Err(ResponseError::not_implemented(
        "skill import: multipart upload TODO",
    ))
}

/// POST /api/skills/{skill_id}/run
async fn api_skill_run(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(skill_id): Path<String>,
    Json(body): Json<SkillRunRequest>,
) -> Result<Response, ResponseError> {
    let u = require_user(&s, &headers).await?;
    if u.role != "admin" {
        return Err(ResponseError::forbidden("仅管理员"));
    }
    let cmd = body
        .cmd
        .or(body.command)
        .ok_or_else(|| ResponseError::bad_request("cmd required"))?;
    let timeout_sec = body.timeout_sec.unwrap_or(30).clamp(1, 600);
    // skill_root 暂用 cwd 的 ./skills/{skill_id}(翻译期占位,真正路径由
    // skill_importer 解压后落到 rpg-platform 配置目录,等接好再换)。
    let skill_root: PathBuf = std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("skills")
        .join(&skill_id);
    let out = run_skill_command(&cmd, &skill_root, timeout_sec, body.stdin, None)
        .await
        .map_err(|e| ResponseError::internal(e.to_string()))?;
    Ok(Json(json!({
        "ok": true,
        "skill_id": skill_id,
        "stdout": out.stdout,
        "stderr": out.stderr,
        "exit_code": out.exit_code,
        "duration_ms": out.duration_ms,
        "timeout": out.timeout,
    }))
    .into_response())
}
