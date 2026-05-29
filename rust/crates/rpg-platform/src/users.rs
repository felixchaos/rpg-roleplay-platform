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

use rpg_core::UserId;
use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};
use zeroize::Zeroizing;

use crate::crypto;
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
    pub user_id: UserId,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub is_default: bool,
    pub metadata: serde_json::Value,
}

/// Python: `list_personas(user_id)` —— 返回玩家所有 persona。
pub async fn list_personas(pool: &PgPool, user_id: UserId) -> PlatformResult<Vec<UserPersona>> {
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
    user_id: UserId,
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

// persona 完整 CRUD(upsert / delete / list / get)已实现于 user_cards 模块。
// users.rs 此处仅做 re-export,路由层统一从 user_cards 取。
pub use crate::user_cards::{
    create_persona, update_persona, delete_persona, set_default_persona,
    list_personas as list_personas_full, get_persona as get_persona_full,
    upsert_persona,
};

// 用户角色卡(user_character_cards)CRUD + 检索辅助已实现于 user_cards 模块。
pub use crate::user_cards::{
    list_user_cards, get_user_card, upsert_user_card, delete_user_card,
    list_public_user_cards, user_cards_for_retrieval,
};

// Tavern V1/V2 解析 / 映射已实现于 tavern_cards 模块。
pub use crate::tavern_cards::{
    parse_card_value, parse_card_str, parse_png_card, write_png_card,
    tavern_to_user_card, user_card_to_tavern_v2,
};

// ─── credentials ───────────────────────────────────────────────────────────

/// `user_api_credentials` 行(去 raw key)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CredentialMeta {
    pub api_id: String,
    pub has_credential: bool,
    pub base_url_override: String,
    pub enabled: bool,
    pub updated_at: String,
}

/// API key 解析结果(对应 Python `resolve_api_key` 返回的 dict)。
///
/// 6A-1:`key` 改为 `Zeroizing<String>`,明文在 Drop 时擦除内存。
/// **刻意不再 derive `Serialize`/`Deserialize`** —— 解析后的明文 key 绝不应被
/// 整体序列化(那等于把密钥写进日志/响应体)。调用方需要时显式取 `&*resolved.key`。
#[derive(Debug, Clone)]
pub struct ResolvedApiKey {
    pub key: Zeroizing<String>,
    /// `user_db` | `env` | `none`
    pub source: &'static str,
    pub base_url_override: String,
}

impl ResolvedApiKey {
    pub fn none() -> Self {
        Self {
            key: Zeroizing::new(String::new()),
            source: "none",
            base_url_override: String::new(),
        }
    }

    /// 是否解析到可用 key。
    pub fn is_some(&self) -> bool {
        !self.key.is_empty()
    }

    /// 借出明文 key 的 `&str` —— 调用方用完即弃,勿长期持有 owned 拷贝。
    pub fn as_str(&self) -> &str {
        &self.key
    }
}

/// Python: `list_credentials(user_id)` — 不返回 raw key。
pub async fn list_credentials(
    pool: &PgPool,
    user_id: UserId,
) -> PlatformResult<Vec<CredentialMeta>> {
    let rows = sqlx::query(
        r#"
        select api_id, base_url_override, enabled, length(encrypted_key) as cipher_len,
               updated_at
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
        let updated_at = r
            .try_get::<chrono::DateTime<chrono::Utc>, _>("updated_at")
            .map(|t| t.to_rfc3339())
            .unwrap_or_default();
        out.push(CredentialMeta {
            api_id: r.try_get("api_id")?,
            has_credential: cipher_len > 0,
            base_url_override: r.try_get::<String, _>("base_url_override").unwrap_or_default(),
            enabled: r.try_get::<bool, _>("enabled").unwrap_or(false),
            updated_at,
        });
    }
    Ok(out)
}

/// Python: `delete_credential(user_id, api_id)`
pub async fn delete_credential(
    pool: &PgPool,
    user_id: UserId,
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
/// 6A-1:写入前用 `crypto::encrypt_api_key(plaintext, user_id, api_id)` 做
/// AES-256-GCM 加密,落库的是密文(`nonce||ct||tag`),**绝不明文落库**。
pub async fn set_credential(
    pool: &PgPool,
    user_id: UserId,
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

    // 6A-1:AES-256-GCM 加密后落库。HKDF 派生 key 以 user_id 为 salt、api_id 入 info,
    // AAD 绑定 user/api —— 跨用户/跨 api 的密文互不可解。
    // crypto 是安全原语层,内部仍用裸 i64(派生 salt/AAD 的字节表示),在此接缝转换。
    let encrypted = crypto::encrypt_api_key(plaintext_key, user_id.get(), api_id)?;
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
/// 1. `user_api_credentials` 表(6A-1:AES-256-GCM 解密)
/// 2. 未强制鉴权时,环境变量 fallback
///
/// **安全语义**:用户库里的密文解密失败时(`crypto::decrypt_api_key` 返回 `None`,
/// 比如 master_key 轮换、密文损坏),**绝不**把空 key 当作可用凭据下发,而是当作
/// 「该用户没有可用凭据」继续走后续 fallback —— 失败已在 crypto 层记审计。
pub async fn resolve_api_key(
    pool: &PgPool,
    user_id: Option<UserId>,
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
                // 6A-1:解密;失败返回 None(已在 crypto 层 tracing::error! 记审计),
                // 此处不降级为空 key,而是跳过 user_db 继续 fallback。
                // crypto 内部裸 i64,在此接缝 `.get()` 转换。
                if let Some(plaintext) = crypto::decrypt_api_key(&encrypted, uid.get(), api_id) {
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
    }
    if rpg_core::config::require_auth() {
        return Ok(ResolvedApiKey::none());
    }
    if !env_fallback.is_empty() {
        if let Ok(v) = std::env::var(env_fallback) {
            if !v.is_empty() {
                return Ok(ResolvedApiKey {
                    key: Zeroizing::new(v),
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

#[cfg(test)]
mod tests {
    use super::*;

    /// 与 crypto::tests 共用同一 `OnceLock<MASTER_KEY>`,首次进入前必须设好 env。
    fn ensure_test_master_key() {
        use std::sync::Once;
        static INIT: Once = Once::new();
        INIT.call_once(|| {
            std::env::set_var("RPG_MASTER_KEY", "0".repeat(64));
        });
    }

    /// 6A-1 核心回归:`set_credential` 写进 `encrypted_key` 列的那串字节,
    /// 必须是密文 —— 既不包含明文 key,也不等于明文字节 —— 且能被
    /// `resolve_api_key` 走的同一条 `decrypt_api_key` 路径还原。
    ///
    /// `set_credential` 绑定的就是 `crypto::encrypt_api_key(...)` 的返回值,
    /// 这里直接对该值做不变量断言,无需 DB 连接即可锁死「明文落库」回归。
    #[test]
    fn test_credential_encrypted_at_rest() {
        ensure_test_master_key();
        let user_id: i64 = 777;
        let api_id = "openai";
        let plaintext = "sk-super-secret-DO-NOT-LEAK-0123456789";

        // 这正是 set_credential 写入 encrypted_key 列的字节。
        let at_rest = crypto::encrypt_api_key(plaintext, user_id, api_id).unwrap();

        // 1) 落库字节绝不等于明文字节(旧 bug:plaintext.as_bytes().to_vec())。
        assert_ne!(
            at_rest.as_slice(),
            plaintext.as_bytes(),
            "encrypted_key 不能是明文字节"
        );
        // 2) 明文子串不得出现在密文里(防止部分泄漏)。
        assert!(
            at_rest
                .windows(plaintext.len())
                .all(|w| w != plaintext.as_bytes()),
            "密文中不应出现明文 key 子串"
        );
        // 3) 至少含 nonce + tag 的开销,确认走了 AEAD 而非裸存。
        assert!(at_rest.len() > plaintext.len(), "密文应比明文长(nonce+tag)");

        // 4) resolve 侧解密路径能还原,且返回 Zeroizing<String>。
        let recovered = crypto::decrypt_api_key(&at_rest, user_id, api_id);
        assert_eq!(recovered.as_ref().map(|z| z.as_str()), Some(plaintext));

        // 5) 换用户解密失败 → None(resolve 会据此拒绝下发,不降级空 key)。
        let other_user = crypto::decrypt_api_key(&at_rest, user_id + 1, api_id);
        assert!(other_user.is_none());
    }

    /// `ResolvedApiKey::none` 不含 key,`is_some()` 为假。
    #[test]
    fn test_resolved_none_is_empty() {
        let none = ResolvedApiKey::none();
        assert!(!none.is_some());
        assert!(none.as_str().is_empty());
        assert_eq!(none.source, "none");
    }
}
