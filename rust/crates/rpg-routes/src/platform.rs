//! `/api/platform`、`/api/platform/commands`、`/api/plugins`、`/api/profile`
//!
//! Python 源: `rpg/platform_app/api/platform.py` (42 行) + `routes/core.py`(部分)
//! 端点:
//!   GET /api/platform          — 平台总览(require_user)
//!   GET /api/platform/commands — commands 清单(require_user)
//!   GET /api/plugins           — 已安装插件清单(公开)
//!   GET /api/profile           — 公开 profile(无需登录)

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use http::HeaderMap;
use serde_json::json;

use crate::{require_user, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/platform", get(api_platform))
        .route("/api/platform/commands", get(api_platform_commands))
        .route("/api/plugins", get(api_plugins))
        .route("/api/profile", get(api_profile))
}

// ── hardcoded command list ────────────────────────────────────────────────────

/// 核心 command 清单(对应 Python `platform.py` 的 `COMMANDS` 常量列表)。
/// 翻译期 hardcoded;后续可从 DB / 配置文件动态加载。
fn core_commands() -> serde_json::Value {
    json!([
        {"id": "new_game",         "label": "新游戏",           "category": "game"},
        {"id": "load_game",        "label": "读取存档",         "category": "game"},
        {"id": "save_game",        "label": "保存存档",         "category": "game"},
        {"id": "quick_save",       "label": "快速保存",         "category": "game"},
        {"id": "quick_load",       "label": "快速读取",         "category": "game"},
        {"id": "export_save",      "label": "导出存档",         "category": "game"},
        {"id": "import_save",      "label": "导入存档",         "category": "game"},
        {"id": "delete_save",      "label": "删除存档",         "category": "game"},
        {"id": "view_timeline",    "label": "查看时间线",       "category": "navigation"},
        {"id": "worldline_jump",   "label": "世界线跳转",       "category": "navigation"},
        {"id": "open_library",     "label": "角色库",           "category": "library"},
        {"id": "open_rules",       "label": "规则设置",         "category": "settings"},
        {"id": "open_permissions", "label": "权限管理",         "category": "settings"},
        {"id": "open_memory",      "label": "记忆管理",         "category": "memory"},
        {"id": "clear_memory",     "label": "清空记忆",         "category": "memory"},
        {"id": "open_scripts",     "label": "脚本管理",         "category": "scripts"},
        {"id": "run_script",       "label": "运行脚本",         "category": "scripts"},
        {"id": "open_branches",    "label": "分支管理",         "category": "branches"},
        {"id": "create_branch",    "label": "创建分支",         "category": "branches"},
        {"id": "merge_branch",     "label": "合并分支",         "category": "branches"},
        {"id": "open_metrics",     "label": "指标面板",         "category": "admin"},
        {"id": "admin_config",     "label": "部署配置",         "category": "admin"},
        {"id": "admin_smtp_test",  "label": "测试 SMTP",        "category": "admin"},
        {"id": "open_mcp",         "label": "MCP 工具管理",     "category": "tools"},
        {"id": "open_plugins",     "label": "插件管理",         "category": "tools"},
        {"id": "open_profile",     "label": "个人设置",         "category": "user"},
        {"id": "logout",           "label": "退出登录",         "category": "user"},
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

    // TODO: 等 rpg_platform::workspace::overview(user, &s.db) 实装后替换此 stub
    let workspace = json!({
        "saves": [],
        "scripts": [],
        "library": {},
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

    Ok(Json(json!({
        "ok": true,
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
