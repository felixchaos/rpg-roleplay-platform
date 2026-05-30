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
use base64::Engine as _;
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use rpg_tools_dsl::skill_executor::{import_skill_bundle, run_skill_command};

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/skills/import", post(api_skills_import))
        .route("/api/skills/:skill_id/run", post(api_skill_run))
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

/// POST /api/skills/import  — JSON body (SKILLS-IMPORT-WRONG-CONTENT-TYPE)
///
/// 对应 Python `SkillsImportRequest`:
///   body: `{"file": {"base64": "...", "name": "skill.zip"}}` 或
///          `{"file": {"data_url": "data:...,base64data", "name": "skill.zip"}}`
///
/// 返回 `{ok, skill: {...}, tools: [...]}` (SKILLS-IMPORT-RESPONSE-MISMATCH)
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_skills_import(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Response, ResponseError> {
    let u = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&u.id));
    if u.role != "admin" {
        return Err(ResponseError::forbidden("仅管理员"));
    }

    // 提取 file 对象(对应 Python body_dict.get("file", {}))
    let file_obj = body.get("file").cloned().unwrap_or(Value::Object(Default::default()));

    // 解码 base64 内容(对应 Python _decode_upload)
    let data_url = file_obj
        .get("data_url")
        .or_else(|| file_obj.get("dataUrl"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let mut encoded = file_obj
        .get("base64")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    // data URL 格式: "data:...;base64,<data>"
    let data_url_payload;
    if data_url.contains(',') {
        data_url_payload = data_url.splitn(2, ',').nth(1).unwrap_or("").to_string();
        encoded = &data_url_payload;
    }
    if encoded.is_empty() {
        return Err(ResponseError::bad_request("上传内容为空: file.base64 或 file.data_url 必须提供"));
    }
    let zip_bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .map_err(|_| ResponseError::bad_request("上传内容不是有效 base64"))?;

    // skill name: 优先 file.name, 去 .zip 后缀
    let raw_name = file_obj
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("unnamed");
    let name = std::path::Path::new(raw_name)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unnamed")
        .to_string();

    // skill_dir: 环境变量 SKILL_DIR 或 ./skills
    let skill_dir: PathBuf = std::env::var("SKILL_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join("skills")
        });

    let imported = import_skill_bundle(&zip_bytes, &name, &skill_dir)
        .map_err(|e| ResponseError::bad_request(e.to_string()))?;

    // SKILLS-IMPORT-RESPONSE-MISMATCH: 已验证 — 返回 {ok, skill: {...}, tools: [...]}
    // 注册到 tool_registry 并返回真实 tools 列表(对应 Python tool_payload())
    {
        let mut reg = s.tool_registry.write();
        reg.register(rpg_tools_dsl::tool_registry::ToolDefinition {
            id: imported.id.clone(),
            name: imported.name.clone(),
            kind: rpg_tools_dsl::tool_registry::ToolKind::Skill,
            enabled: imported.enabled,
            meta: serde_json::Value::Null,
        });
    }
    let tools: Vec<serde_json::Value> = {
        let reg = s.tool_registry.read();
        reg.list()
            .into_iter()
            .map(|t| {
                serde_json::json!({
                    "id": t.id,
                    "name": t.name,
                    "kind": t.kind,
                    "enabled": t.enabled,
                })
            })
            .collect()
    };
    Ok(Json(json!({
        "ok": true,
        "skill": {
            "id": imported.id,
            "name": imported.name,
            "path": imported.path,
            "enabled": imported.enabled,
        },
        "tools": tools,
    }))
    .into_response())
}

/// POST /api/skills/{skill_id}/run
#[tracing::instrument(skip(s, headers, body), fields(user_id, skill_id = %skill_id))]
async fn api_skill_run(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(skill_id): Path<String>,
    Json(body): Json<SkillRunRequest>,
) -> Result<Response, ResponseError> {
    let u = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&u.id));
    // SKILLS-RUN-AUTH-LOGIC-WRONG: 已验证,与 Python 一致 —
    // Python: if is_server_deployment(): require_admin_or_403(user)
    // 仅 server/production/cloud 模式要求 admin；本地模式允许任意已登录用户运行 skill。
    let is_server_mode = matches!(
        s.config.deployment_mode.as_str(),
        "server" | "production" | "prod" | "cloud"
    );
    if is_server_mode && u.role != "admin" {
        return Err(ResponseError::forbidden("需要管理员权限"));
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
