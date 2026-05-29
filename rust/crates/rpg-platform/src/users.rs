//! users —— 用户基础 CRUD + persona + credential 顶层 re-export。
//!
//! 完成度: **骨架 + 主路径**
//!
//! 拆分:
//! - 用户 CRUD 本身: 全部在 `auth::sessions::AuthService`,这里只做 re-export
//! - personas: 骨架(`list_personas`,字段对齐 Python `user_personas` 表)
//! - credentials: 骨架(`set_credential` / `resolve_api_key`,加解密留 TODO 等 utils/crypto)
//!
//! 详细 CRUD 路由后续由 rpg-routes 接管。

use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

pub use crate::auth::sessions::{
    get_user, login, logout, register, update_profile, user_from_token, AuthService, User,
};
pub use crate::auth::password::{public_user, PublicUser};

// ─── personas ──────────────────────────────────────────────────────────────

/// `user_personas` 行(玩家自创身份卡)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserPersona {
    pub id: i64,
    pub user_id: i64,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub is_default: bool,
    pub metadata: serde_json::Value,
}

/// Python: `list_personas(user_id)` —— 返回玩家所有 persona。
pub async fn list_personas(pool: &PgPool, user_id: i64) -> PlatformResult<Vec<UserPersona>> {
    let rows = sqlx::query(
        r#"
        select id, user_id, name, coalesce(description,'') as description,
               coalesce(is_default, false) as is_default,
               coalesce(metadata, '{}'::jsonb) as metadata
          from user_personas
         where user_id = $1
         order by is_default desc, updated_at desc, id desc
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(|r| {
            Ok(UserPersona {
                id: r.try_get("id")?,
                user_id: r.try_get("user_id")?,
                name: r.try_get("name")?,
                description: r.try_get("description")?,
                is_default: r.try_get("is_default")?,
                metadata: r.try_get("metadata")?,
            })
        })
        .collect::<Result<Vec<_>, sqlx::Error>>()
        .map_err(Into::into)
}

/// Python: `get_persona(user_id, persona_id)` —— 未找到返回 None。
pub async fn get_persona(
    pool: &PgPool,
    user_id: i64,
    persona_id: i64,
) -> PlatformResult<Option<UserPersona>> {
    let row = sqlx::query(
        "select id, user_id, name, coalesce(description,'') as description, \
         coalesce(is_default, false) as is_default, coalesce(metadata,'{}'::jsonb) as metadata \
         from user_personas where id = $1 and user_id = $2",
    )
    .bind(persona_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(match row {
        Some(r) => Some(UserPersona {
            id: r.try_get("id")?,
            user_id: r.try_get("user_id")?,
            name: r.try_get("name")?,
            description: r.try_get("description")?,
            is_default: r.try_get("is_default")?,
            metadata: r.try_get("metadata")?,
        }),
        None => None,
    })
}

// TODO[Sonnet]: create_persona / update_persona / delete_persona / set_default — 翻译 Python user_cards.py
// TODO[Sonnet]: 用户角色卡(user_character_cards)CRUD — 翻译 Python user_cards.py
// TODO[Sonnet]: tavern V1/V2 卡解析与导入 — 翻译 Python tavern_cards.py

// ─── credentials ───────────────────────────────────────────────────────────

/// `user_api_credentials` 行(去 raw key)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CredentialMeta {
    pub api_id: String,
    pub has_credential: bool,
    pub base_url_override: String,
    pub enabled: bool,
}

/// API key 解析结果(对应 Python `resolve_api_key` 返回的 dict)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResolvedApiKey {
    pub key: String,
    /// `user_db` | `env` | `none`
    pub source: &'static str,
    pub base_url_override: String,
}

impl ResolvedApiKey {
    pub fn none() -> Self {
        Self {
            key: String::new(),
            source: "none",
            base_url_override: String::new(),
        }
    }
}

/// Python: `list_credentials(user_id)` — 不返回 raw key。
pub async fn list_credentials(
    pool: &PgPool,
    user_id: i64,
) -> PlatformResult<Vec<CredentialMeta>> {
    let rows = sqlx::query(
        r#"
        select api_id, base_url_override, enabled, length(encrypted_key) as cipher_len
          from user_api_credentials
         where user_id = $1
         order by api_id
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in rows {
        let cipher_len: i32 = r.try_get("cipher_len").unwrap_or(0);
        out.push(CredentialMeta {
            api_id: r.try_get("api_id")?,
            has_credential: cipher_len > 0,
            base_url_override: r.try_get::<String, _>("base_url_override").unwrap_or_default(),
            enabled: r.try_get::<bool, _>("enabled").unwrap_or(false),
        });
    }
    Ok(out)
}

/// Python: `delete_credential(user_id, api_id)`
pub async fn delete_credential(
    pool: &PgPool,
    user_id: i64,
    api_id: &str,
) -> PlatformResult<()> {
    sqlx::query("delete from user_api_credentials where user_id = $1 and api_id = $2")
        .bind(user_id)
        .bind(api_id)
        .execute(pool)
        .await?;
    Ok(())
}

/// Python: `set_credential(user_id, api_id, plaintext_key, base_url_override, ...)`
///
/// 加密留 TODO,先以明文写入(框架可调)。
pub async fn set_credential(
    pool: &PgPool,
    user_id: i64,
    api_id: &str,
    plaintext_key: &str,
    base_url_override: &str,
    enabled: bool,
    allow_base_url: bool,
) -> PlatformResult<()> {
    let api_id = api_id.trim();
    if api_id.is_empty() {
        return Err(PlatformError::validation("api_id 不能为空"));
    }
    if plaintext_key.is_empty() {
        return delete_credential(pool, user_id, api_id).await;
    }
    if !base_url_override.is_empty() && !allow_base_url {
        return Err(PlatformError::forbidden(
            "base_url_override 仅管理员可设置",
        ));
    }
    let base_url = if allow_base_url {
        validate_base_url(base_url_override)?;
        base_url_override.to_string()
    } else {
        String::new()
    };

    // TODO[Sonnet]: 调用 utils::crypto::encrypt_api_key (master_key + user_id + api_id → AEAD)
    let encrypted = plaintext_key.as_bytes().to_vec();
    sqlx::query(
        r#"
        insert into user_api_credentials(user_id, api_id, encrypted_key, base_url_override, enabled, metadata)
        values ($1, $2, $3, $4, $5, '{}'::jsonb)
        on conflict(user_id, api_id) do update set
          encrypted_key = excluded.encrypted_key,
          base_url_override = excluded.base_url_override,
          enabled = excluded.enabled,
          metadata = excluded.metadata,
          updated_at = now()
        "#,
    )
    .bind(user_id)
    .bind(api_id)
    .bind(encrypted)
    .bind(base_url)
    .bind(enabled)
    .execute(pool)
    .await?;
    Ok(())
}

/// Python: `resolve_api_key(user_id, api_id, env_fallback)`
///
/// 顺序:
/// 1. `user_api_credentials` 表(明文解密 — TODO)
/// 2. 未强制鉴权时,环境变量 fallback
pub async fn resolve_api_key(
    pool: &PgPool,
    user_id: Option<i64>,
    api_id: &str,
    env_fallback: &str,
) -> PlatformResult<ResolvedApiKey> {
    if let Some(uid) = user_id {
        let row = sqlx::query(
            "select encrypted_key, base_url_override, enabled \
             from user_api_credentials where user_id = $1 and api_id = $2",
        )
        .bind(uid)
        .bind(api_id)
        .fetch_optional(pool)
        .await?;
        if let Some(r) = row {
            let enabled: bool = r.try_get("enabled").unwrap_or(false);
            if enabled {
                let encrypted: Vec<u8> = r.try_get("encrypted_key").unwrap_or_default();
                // TODO[Sonnet]: utils::crypto::decrypt_api_key(encrypted, user_id, api_id)
                let plaintext = String::from_utf8(encrypted).unwrap_or_default();
                if !plaintext.is_empty() {
                    return Ok(ResolvedApiKey {
                        key: plaintext,
                        source: "user_db",
                        base_url_override: r
                            .try_get::<String, _>("base_url_override")
                            .unwrap_or_default(),
                    });
                }
            }
        }
    }
    if rpg_core::config::require_auth() {
        return Ok(ResolvedApiKey::none());
    }
    if !env_fallback.is_empty() {
        if let Ok(v) = std::env::var(env_fallback) {
            if !v.is_empty() {
                return Ok(ResolvedApiKey {
                    key: v,
                    source: "env",
                    base_url_override: String::new(),
                });
            }
        }
    }
    Ok(ResolvedApiKey::none())
}

// ─── helpers ───────────────────────────────────────────────────────────────

/// 对应 Python `_validate_base_url`,禁止指向私网。
fn validate_base_url(url: &str) -> PlatformResult<()> {
    let parsed = url::Url::parse(url)
        .map_err(|_| PlatformError::validation("base_url 必须是合法 URL"))?;
    let scheme = parsed.scheme();
    if scheme != "http" && scheme != "https" {
        return Err(PlatformError::validation("base_url 必须是 http/https"));
    }
    if scheme == "http" && rpg_core::config::require_auth() {
        return Err(PlatformError::validation("服务器模式下 base_url 必须是 https"));
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| PlatformError::validation("base_url 缺少 host"))?
        .to_lowercase();
    const PRIVATE_PREFIXES: &[&str] = &[
        "127.", "10.", "192.168.", "169.254.", "172.16.", "172.17.", "172.18.", "172.19.",
        "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
        "172.28.", "172.29.", "172.30.", "172.31.", "0.", "localhost", "::1", "fc", "fd", "fe80",
    ];
    for prefix in PRIVATE_PREFIXES {
        let cmp = prefix.trim_end_matches('.');
        if host == cmp || host.starts_with(prefix) {
            return Err(PlatformError::validation(format!(
                "base_url 不允许指向私有/本地地址:{host}"
            )));
        }
    }
    Ok(())
}
