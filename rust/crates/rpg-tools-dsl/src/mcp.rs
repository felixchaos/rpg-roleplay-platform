//! mcp — MCP 服务器目录管理
//! 对应 Python: rpg/tools_dsl/tool_registry.py mcp_* 函数

use std::{
    collections::HashMap,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use tracing::warn;

use crate::DslError;

// ── 数据结构 ──────────────────────────────────────────────────────────────────

/// 单台 MCP 服务器配置（对应 Python _normalize_mcp_server）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpServer {
    pub id: String,
    pub display_name: String,
    pub transport: String,
    pub command: String,
    pub args: Vec<String>,
    pub env: HashMap<String, String>,
    pub enabled: bool,
    pub scope: String,
}

/// MCP 目录（schema_version + servers 列表）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpCatalog {
    pub schema_version: u32,
    pub servers: Vec<McpServer>,
}

impl Default for McpCatalog {
    fn default() -> Self {
        Self {
            schema_version: 1,
            servers: Vec::new(),
        }
    }
}

// ── McpCatalog impl ───────────────────────────────────────────────────────────

impl McpCatalog {
    /// 从 Postgres 加载 MCP 目录（对应 Python _load_mcp_catalog_from_db）。
    /// 若 DB 无数据则返回空 catalog（调用方可 fallback 读文件）。
    pub async fn load(pool: &PgPool) -> Result<McpCatalog, DslError> {
        // 使用动态查询避免 sqlx compile-time 宏要求 DATABASE_URL
        let rows: Vec<sqlx::postgres::PgRow> = sqlx::query(
            "SELECT server_id, display_name, transport, command, \
                    args, env, enabled, scope \
             FROM mcp_servers \
             ORDER BY server_id",
        )
        .fetch_all(pool)
        .await
        .map_err(|e| DslError::Other(format!("DB load mcp_servers: {e}")))?;

        use sqlx::Row as _;
        let servers = rows
            .into_iter()
            .map(|r| {
                let args: Vec<String> = r
                    .try_get::<serde_json::Value, _>("args")
                    .ok()
                    .and_then(|v| serde_json::from_value(v).ok())
                    .unwrap_or_default();
                let env: HashMap<String, String> = r
                    .try_get::<serde_json::Value, _>("env")
                    .ok()
                    .and_then(|v| serde_json::from_value(v).ok())
                    .unwrap_or_default();
                normalize_server(McpServer {
                    id: r.try_get("server_id").unwrap_or_default(),
                    display_name: r.try_get("display_name").unwrap_or_default(),
                    transport: r
                        .try_get::<Option<String>, _>("transport")
                        .ok()
                        .flatten()
                        .unwrap_or_else(|| "stdio".into()),
                    command: r
                        .try_get::<Option<String>, _>("command")
                        .ok()
                        .flatten()
                        .unwrap_or_default(),
                    args,
                    env,
                    enabled: r
                        .try_get::<Option<bool>, _>("enabled")
                        .ok()
                        .flatten()
                        .unwrap_or(false),
                    scope: r
                        .try_get::<Option<String>, _>("scope")
                        .ok()
                        .flatten()
                        .unwrap_or_else(|| "local".into()),
                })
            })
            .collect();

        Ok(McpCatalog {
            schema_version: 1,
            servers,
        })
    }

    /// 持久化到 Postgres（UPSERT 所有条目）（对应 Python _save_mcp_catalog_to_db）。
    pub async fn save(&self, pool: &PgPool) -> Result<(), DslError> {
        let mut tx = pool
            .begin()
            .await
            .map_err(|e| DslError::Other(format!("DB begin: {e}")))?;

        sqlx::query("DELETE FROM mcp_servers")
            .execute(&mut *tx)
            .await
            .map_err(|e| DslError::Other(format!("DB delete mcp_servers: {e}")))?;

        for s in &self.servers {
            let args_json = serde_json::to_value(&s.args)?;
            let env_json = serde_json::to_value(&s.env)?;
            sqlx::query(
                "INSERT INTO mcp_servers \
                   (server_id, display_name, transport, command, args, env, enabled, scope) \
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8) \
                 ON CONFLICT(server_id) DO UPDATE SET \
                   display_name = EXCLUDED.display_name, \
                   transport    = EXCLUDED.transport, \
                   command      = EXCLUDED.command, \
                   args         = EXCLUDED.args, \
                   env          = EXCLUDED.env, \
                   enabled      = EXCLUDED.enabled, \
                   scope        = EXCLUDED.scope, \
                   updated_at   = now()",
            )
            .bind(&s.id)
            .bind(&s.display_name)
            .bind(&s.transport)
            .bind(&s.command)
            .bind(args_json)
            .bind(env_json)
            .bind(s.enabled)
            .bind(&s.scope)
            .execute(&mut *tx)
            .await
            .map_err(|e| DslError::Other(format!("DB upsert {}: {e}", s.id)))?;
        }

        tx.commit()
            .await
            .map_err(|e| DslError::Other(format!("DB commit: {e}")))?;

        Ok(())
    }

    /// 新增或覆盖一台 MCP 服务器。
    pub fn upsert_server(&mut self, server: McpServer) {
        let server = normalize_server(server);
        if let Some(existing) = self.servers.iter_mut().find(|s| s.id == server.id) {
            *existing = server;
        } else {
            self.servers.push(server);
        }
    }

    /// 删除一台 MCP 服务器（若不存在则静默成功）。
    pub fn delete_server(&mut self, id: &str) {
        self.servers.retain(|s| s.id != id);
    }

    /// 启用或禁用一台 MCP 服务器。返回是否找到该 server。
    pub fn set_enabled(&mut self, id: &str, enabled: bool) -> bool {
        if let Some(s) = self.servers.iter_mut().find(|s| s.id == id) {
            s.enabled = enabled;
            true
        } else {
            false
        }
    }
}

// ── 字段校验 ──────────────────────────────────────────────────────────────────

/// 校验 MCP 服务器配置字段合法性（对应 Python validate_mcp_server）。
///
/// 检查：
/// - `id` 非空
/// - `transport` 为 "stdio"（暂只支持 stdio）
/// - `command` 非空且在 PATH 中可找到（`which` 检查）
pub fn validate_server(spec: &McpServer) -> Result<(), DslError> {
    if spec.id.trim().is_empty() {
        return Err(DslError::Other("MCP server id 不能为空".into()));
    }
    if spec.transport != "stdio" {
        return Err(DslError::Other(format!(
            "transport '{}' 暂不支持（仅支持 stdio）",
            spec.transport
        )));
    }
    if spec.command.trim().is_empty() {
        return Err(DslError::Other("MCP server command 不能为空".into()));
    }
    // which 检查：找不到命令时警告（不视为硬错误，允许路径稍后再安装）
    if which::which(&spec.command).is_err() {
        return Err(DslError::Other(format!(
            "command '{}' 在 PATH 中找不到",
            spec.command
        )));
    }
    Ok(())
}

// ── 文件镜像 ──────────────────────────────────────────────────────────────────

/// 将 catalog 原子写入到 `path`（对应 Python _mirror_mcp_catalog_file）。
///
/// 先写 `.tmp` 再 rename，保证文件不会处于半写状态。
pub fn mirror_to_filesystem(catalog: &McpCatalog, path: &Path) -> Result<(), DslError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = PathBuf::from(format!("{}.tmp", path.display()));
    let json = serde_json::to_string_pretty(catalog)?;
    std::fs::write(&tmp, json.as_bytes())?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

// ── 旧版顶层函数（向后兼容，内部委托给 McpCatalog impl）─────────────────────

/// 从文件加载 catalog（无 DB 时的 fallback，对应 _load_mcp_catalog_from_file）。
pub fn load_from_file(path: &Path) -> McpCatalog {
    match std::fs::read_to_string(path) {
        Ok(s) => serde_json::from_str::<McpCatalog>(&s).unwrap_or_default(),
        Err(e) => {
            warn!("load_from_file {}: {e}", path.display());
            McpCatalog::default()
        }
    }
}

// ── 内部：字段规范化 ──────────────────────────────────────────────────────────

fn normalize_server(mut s: McpServer) -> McpServer {
    s.id = slugify(&s.id);
    s.display_name = s.display_name.trim().to_owned();
    if s.display_name.is_empty() {
        s.display_name = s.id.clone();
    }
    s.transport = s.transport.trim().to_owned();
    if s.transport.is_empty() {
        s.transport = "stdio".into();
    }
    s.command = s.command.trim().to_owned();
    s.scope = s.scope.trim().to_owned();
    if s.scope.is_empty() {
        s.scope = "local".into();
    }
    s
}

fn slugify(s: &str) -> String {
    let s = s.trim().to_lowercase();
    s.chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect::<String>()
        .trim_matches('_')
        .to_owned()
}
