//! skills.py → skills.rs — Skill 导入与运行路由
//! POST /api/skills/import          — 导入 skill bundle(admin only)
//! POST /api/skills/{skill_id}/run  — 在沙箱里运行 skill(admin only)

use std::path::PathBuf;

use axum::{
    extract::{Multipart, Path, State},
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::json;

use rpg_tools_dsl::skill_executor::{import_skill_bundle, run_skill_command};

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/skills/import", post(api_skills_import))
        .route("/api/skills/{skill_id}/run", post(api_skill_run))
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct SkillRunRequest {
    pub cmd: Option<Vec<String>>,
    pub command: Option<Vec<String>>,
    pub stdin: Option<String>,
    pub timeout_sec: Option<u64>,
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// POST /api/skills/import  — multipart/form-data
///
/// 字段约定(对应 Python `skill_importer.py`):
///   - `file`  : zip 文件内容
///   - `name`  : skill slug(可选,默认取文件名去 .zip)
///
/// 返回 `{ok, skill_id, name, version}`.
async fn api_skills_import(
    State(s): State<AppState>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Result<Response, ResponseError> {
    let u = require_user(&s, &headers).await?;
    if u.role != "admin" {
        return Err(ResponseError::forbidden("仅管理员"));
    }

    let mut zip_bytes: Option<bytes::Bytes> = None;
    let mut file_name: Option<String> = None;
    let mut skill_name: Option<String> = None;

    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|e| ResponseError::bad_request(format!("multipart error: {e}")))?
    {
        let field_name = field.name().unwrap_or("").to_string();
        match field_name.as_str() {
            "file" => {
                file_name = field.file_name().map(|s| s.to_string());
                let data = field
                    .bytes()
                    .await
                    .map_err(|e| ResponseError::bad_request(format!("read file: {e}")))?;
                zip_bytes = Some(data);
            }
            "name" => {
                let text = field
                    .text()
                    .await
                    .map_err(|e| ResponseError::bad_request(format!("read name: {e}")))?;
                if !text.trim().is_empty() {
                    skill_name = Some(text.trim().to_string());
                }
            }
            _ => {
                // 忽略未知字段
                let _ = field.bytes().await;
            }
        }
    }

    let zip_bytes = zip_bytes.ok_or_else(|| ResponseError::bad_request("missing file field"))?;

    // skill_name 优先级: 显式 name 字段 > 文件名去 .zip > "unnamed"
    let name = skill_name.unwrap_or_else(|| {
        file_name
            .as_deref()
            .unwrap_or("unnamed")
            .trim_end_matches(".zip")
            .to_string()
    });

    // skill_dir: 翻译期用 env SKILL_DIR 或 ./skills 目录
    let skill_dir: PathBuf = std::env::var("SKILL_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join("skills")
        });

    let imported = import_skill_bundle(&zip_bytes, &name, &skill_dir)
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    Ok(Json(json!({
        "ok": true,
        "skill_id": imported.id,
        "name": imported.name,
        "version": "1.0.0",
        "path": imported.path,
    }))
    .into_response())
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
