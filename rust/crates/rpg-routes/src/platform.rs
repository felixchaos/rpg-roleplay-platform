//! `/api/platform`、`/api/platform/commands`、`/api/plugins`、`/api/profile`
//!
//! Python 源: `rpg/platform_app/api/platform.py` (42 行) + `routes/core.py`(部分)
//! 端点:
//!   GET  /api/platform          — 平台总览(require_user)
//!   GET  /api/platform/commands — commands 清单(require_user)
//!   GET  /api/plugins           — 已安装插件清单(公开)
//!   GET  /api/profile           — 公开 profile(无需登录)
//!   POST /api/profile           — 保存 display_name/bio(require_user)

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::json;

use rpg_platform::{library, users as users_svc};

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/platform", get(api_platform))
        .route("/api/platform/commands", get(api_platform_commands))
        .route("/api/plugins", get(api_plugins))
        .route("/api/profile", get(api_profile).post(api_save_profile))
}

// ── hardcoded command list ────────────────────────────────────────────────────

/// 核心 command 清单 — 与 Python `platform_app.api._deps.COMMANDS` + `command_payload()` 对齐。
/// 格式: [{method, path, name, desc}]，其中 name = path 末段。
fn core_commands() -> serde_json::Value {
    json!([
        {"method": "GET",  "path": "/",                                         "name": "",                     "desc": "Backend root (service info JSON)"},
        {"method": "GET",  "path": "/api/state",                                "name": "state",                "desc": "读取当前可玩存档状态"},
        {"method": "POST", "path": "/api/new",                                  "name": "new",                  "desc": "创建新游戏并保留旧档备份"},
        {"method": "POST", "path": "/api/opening",                              "name": "opening",              "desc": "生成开场"},
        {"method": "POST", "path": "/api/chat",                                 "name": "chat",                 "desc": "发送玩家行动/对话，支持流式 GM 输出与结构化状态写回"},
        {"method": "POST", "path": "/api/stop",                                 "name": "stop",                 "desc": "打断当前生成"},
        {"method": "POST", "path": "/api/save",                                 "name": "save",                 "desc": "手动保存当前游戏"},
        {"method": "POST", "path": "/api/memory/mode",                          "name": "mode",                 "desc": "设置记忆模式"},
        {"method": "POST", "path": "/api/memory/add",                           "name": "add",                  "desc": "添加长期记忆"},
        {"method": "POST", "path": "/api/memory/remove",                        "name": "remove",               "desc": "删除长期记忆"},
        {"method": "POST", "path": "/api/permissions",                          "name": "permissions",          "desc": "设置 LLM 状态写入权限"},
        {"method": "GET",  "path": "/api/models",                               "name": "models",               "desc": "读取 API/模型树与前端显示模型"},
        {"method": "POST", "path": "/api/models/select",                        "name": "select",               "desc": "选择当前前端模型"},
        {"method": "POST", "path": "/api/models/api",                           "name": "api",                  "desc": "新增或更新 API 供应商"},
        {"method": "POST", "path": "/api/models/model",                         "name": "model",                "desc": "新增或更新 API 下属模型"},
        {"method": "GET",  "path": "/api/tools",                                "name": "tools",                "desc": "插件/MCP/Skill 能力状态"},
        {"method": "POST", "path": "/api/mcp/server",                           "name": "server",               "desc": "新增或更新 MCP 服务器配置"},
        {"method": "POST", "path": "/api/mcp/server/enabled",                   "name": "enabled",              "desc": "启用或禁用 MCP 服务器"},
        {"method": "POST", "path": "/api/mcp/server/delete",                    "name": "delete",               "desc": "删除 MCP 服务器配置"},
        {"method": "POST", "path": "/api/mcp/server/validate",                  "name": "validate",             "desc": "校验 MCP stdio 命令可用性"},
        {"method": "POST", "path": "/api/skills/import",                        "name": "import",               "desc": "本地部署导入 Skill 包"},
        {"method": "POST", "path": "/api/worldline/variable",                   "name": "variable",             "desc": "新增或锁定用户世界线变量"},
        {"method": "POST", "path": "/api/worldline/variable/remove",            "name": "remove",               "desc": "移除用户世界线变量"},
        {"method": "POST", "path": "/api/auth/register",                        "name": "register",             "desc": "注册账号"},
        {"method": "POST", "path": "/api/auth/login",                           "name": "login",                "desc": "登录并写入会话 cookie"},
        {"method": "POST", "path": "/api/auth/logout",                          "name": "logout",               "desc": "退出登录"},
        {"method": "GET",  "path": "/api/platform",                             "name": "platform",             "desc": "平台总览：主页、剧本、存档、库、工具"},
        {"method": "GET",  "path": "/api/scripts",                              "name": "scripts",              "desc": "剧本列表"},
        {"method": "POST", "path": "/api/scripts/import",                       "name": "import",               "desc": "导入 TXT/MD 剧本并自动识别章节"},
        {"method": "GET",  "path": "/api/scripts/{script_id}/chapters",         "name": "chapters",             "desc": "读取剧本章节目录与预览"},
        {"method": "POST", "path": "/api/scripts/{script_id}/knowledge/sync",   "name": "sync",                 "desc": "重建剧本 ChapterFact、世界书、人设卡和检索块"},
        {"method": "GET",  "path": "/api/scripts/{script_id}/chapter-facts",    "name": "chapter-facts",        "desc": "读取剧本 ChapterFact 时间线"},
        {"method": "GET",  "path": "/api/scripts/{script_id}/birthpoints",      "name": "birthpoints",          "desc": "入场选出生点：按 phase 聚合 + 每 phase 均匀采样 anchor"},
        {"method": "GET",  "path": "/api/scripts/{script_id}/character-cards",  "name": "character-cards",      "desc": "读取剧本人设卡"},
        {"method": "GET",  "path": "/api/scripts/{script_id}/worldbook",        "name": "worldbook",            "desc": "读取剧本世界书条目"},
        {"method": "GET",  "path": "/api/saves",                                "name": "saves",                "desc": "游戏存档目录"},
        {"method": "POST", "path": "/api/saves",                                "name": "saves",                "desc": "基于剧本创建新存档"},
        {"method": "GET",  "path": "/api/branches/{save_id}",                   "name": "{save_id}",            "desc": "读取某个存档的分支树"},
        {"method": "POST", "path": "/api/branches/continue",                    "name": "continue",             "desc": "从任意对话节点派生/激活当前游戏 runtime"},
        {"method": "POST", "path": "/api/branches/activate",                    "name": "activate",             "desc": "直接激活某个分支节点为当前游戏 runtime"},
        {"method": "POST", "path": "/api/branches/delete",                      "name": "delete",               "desc": "删除某条连线下的整条分支"},
        {"method": "GET",  "path": "/api/saves/{save_id}/context-runs",         "name": "context-runs",         "desc": "读取某个存档的上下文子代理运行记录"},
        {"method": "GET",  "path": "/api/saves/{save_id}/anchors",              "name": "anchors",              "desc": "task 136: 读取存档世界线收束锚点状态"},
        {"method": "POST", "path": "/api/saves/{save_id}/anchors/reseed",       "name": "reseed",               "desc": "task 136: 重 seed 锚点 (调试用)"},
        {"method": "GET",  "path": "/api/settings",                             "name": "settings",             "desc": "读取设置"},
        {"method": "POST", "path": "/api/settings",                             "name": "settings",             "desc": "写入设置"},
        {"method": "GET",  "path": "/api/library",                              "name": "library",              "desc": "文件库列表"},
        {"method": "POST", "path": "/api/library/upload",                       "name": "upload",               "desc": "文件库上传"},
        {"method": "POST", "path": "/api/library/mkdir",                        "name": "mkdir",                "desc": "文件库创建文件夹"},
        {"method": "POST", "path": "/api/library/delete",                       "name": "delete",               "desc": "文件库删除"},
        {"method": "GET",  "path": "/api/library/download",                     "name": "download",             "desc": "文件库下载"},
        {"method": "GET",  "path": "/api/platform/commands",                    "name": "commands",             "desc": "读取全部功能指令清单"},
    ])
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// GET /api/platform — 平台总览
///
/// Python 端合并 `workspace.overview(user) + tools + commands + library`。
/// Rust 翻译期:`rpg_platform::workspace` 不存在,用占位 stub + TODO。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_platform(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));

    // PLATFORM_WORKSPACE_STUB: 已用真实数据填充(非 stub),等价于 Python workspace.overview()。
    // API-001: 用真实数据填充 workspace(saves / scripts / library)。
    // 对应 Python platform_for(user) → workspace.overview(user)。
    let saves = rpg_platform::save_io::list_saves_for_user(&s.db, user.id)
        .await
        .unwrap_or_default();
    let saves_json: Vec<serde_json::Value> = saves
        .iter()
        .take(50)
        .map(|sv| {
            serde_json::json!({
                "id": sv.id,
                "script_id": sv.script_id,
                "title": sv.title,
                "updated_at": sv.updated_at,
            })
        })
        .collect();

    let scripts = rpg_platform::library::list_scripts(&s.db, user.id.into())
        .await
        .unwrap_or_default();
    let scripts_json: Vec<serde_json::Value> = scripts
        .iter()
        .take(50)
        .map(|sc| serde_json::to_value(sc).unwrap_or_default())
        .collect();

    let library_entries = library::list_dir(&s.db, user.id.into(), "", None, None)
        .await
        .map(|l| l.entries)
        .unwrap_or_default();

    let workspace = serde_json::json!({
        "saves": saves_json,
        "scripts": scripts_json,
        "library": {
            "entries": library_entries,
        },
    });

    // 从 tool_registry 取已注册工具列表
    let tools: Vec<serde_json::Value> = {
        let reg = s.tool_registry.read();
        reg.list()
            .into_iter()
            .map(|t| {
                json!({
                    "id": t.id,
                    "name": t.name,
                    "kind": t.kind,
                    "enabled": t.enabled,
                })
            })
            .collect()
    };

    let commands = core_commands();

    // user 字段:与 Python platform_for() 中 payload["user"] = public_user(user) 对齐
    let user_field = users_svc::public_user(&user);

    Ok(Json(json!({
        "ok": true,
        "user": user_field,
        "workspace": workspace,
        "tools": tools,
        "commands": commands,
        "library": {},
    }))
    .into_response())
}

/// GET /api/platform/commands — commands 清单
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_platform_commands(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let _user = require_user(&s, &headers).await?;
    Ok(Json(json!({
        "ok": true,
        "commands": core_commands(),
    }))
    .into_response())
}

/// GET /api/plugins — 已安装插件清单(公开端点,不要求登录)
///
/// Python `tool_payload()` 返回 plugins 段;Rust 从 tool_registry 读 kind==Plugin 的条目。
#[tracing::instrument(skip(s))]
async fn api_plugins(State(s): State<AppState>) -> Result<Response, ResponseError> {
    let plugins: Vec<serde_json::Value> = {
        let reg = s.tool_registry.read();
        reg.list()
            .into_iter()
            .filter(|t| t.kind == rpg_tools_dsl::tool_registry::ToolKind::Plugin)
            .map(|t| {
                json!({
                    "id": t.id,
                    "name": t.name,
                    "enabled": t.enabled,
                    "meta": t.meta,
                })
            })
            .collect()
    };

    Ok(Json(json!({
        "ok": true,
        "plugins": plugins,
    }))
    .into_response())
}

/// GET /api/profile — 公开 profile(无需登录)
///
/// 返回应用级公开 profile(title、deployment_mode、版本等),
/// 与 `/api/me/profile` 不同:后者是用户个人信息,需要登录。
#[tracing::instrument(skip(s))]
async fn api_profile(State(s): State<AppState>) -> Result<Response, ResponseError> {
    Ok(Json(json!({
        "ok": true,
        "app_title": s.config.app_title,
        "deployment_mode": s.config.deployment_mode,
        "require_auth": s.config.require_auth,
        "version": env!("CARGO_PKG_VERSION"),
    }))
    .into_response())
}

// ── POST /api/profile — save profile ─────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct SaveProfileBody {
    #[serde(default)]
    display_name: Option<String>,
    #[serde(default)]
    bio: Option<String>,
}

/// POST /api/profile — 保存用户 display_name / bio
///
/// 对应 Python `platform.py` 的 `POST /api/profile` → `_auth.update_profile()`。
/// 前端调用: `api.account.saveProfile(body) => POST /api/v1/profile`
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_save_profile(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<SaveProfileBody>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));

    let display_name = body.display_name.as_deref().unwrap_or(&user.display_name);
    let bio = body.bio.as_deref().unwrap_or(&user.bio);

    let updated = users_svc::update_profile(&s.db, user.id, display_name, bio).await?;

    Ok(Json(json!({
        "ok": true,
        "user": users_svc::public_user(&updated),
    }))
    .into_response())
}
