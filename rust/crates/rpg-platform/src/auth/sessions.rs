//! Session / User CRUD —— 对应 Python `auth.py` 的 `register / login / logout /
//! user_from_token / get_user / update_profile`。
//!
//! Python 用同步 psycopg + global `connect()`;Rust 这里改为
//! `AuthService { pool, limiter }` 的形态,可在 axum extractor 里注入。

use chrono::{DateTime, Utc};
use rpg_core::UserId;
use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

use super::password::{
    hash_password, normalize_username, verify_and_maybe_rehash, AuthVerifyError,
};
use super::rate_limit::{RateLimited, RateLimiter, GLOBAL_LIMITER};

/// 等价于 Python `SESSION_DAYS = 14`。
pub const SESSION_DAYS: i64 = 14;

/// `users` 表行(对应 Python `dict(row)`)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct User {
    pub id: UserId,
    /// 部分老库可能没有 `public_id`;留 Option。
    #[serde(default)]
    pub public_id: Option<uuid::Uuid>,
    pub username: String,
    pub password_hash: String,
    pub display_name: String,
    #[serde(default)]
    pub bio: String,
    pub role: String,
    pub row_version: i64,
    pub created_at: Option<DateTime<Utc>>,
    pub updated_at: Option<DateTime<Utc>>,
}

impl User {
    fn from_row(row: &sqlx::postgres::PgRow) -> sqlx::Result<Self> {
        Ok(User {
            id: row.try_get::<UserId, _>("id")?,
            public_id: row.try_get::<Option<uuid::Uuid>, _>("public_id").unwrap_or(None),
            username: row.try_get::<String, _>("username")?,
            password_hash: row.try_get::<String, _>("password_hash")?,
            display_name: row.try_get::<String, _>("display_name").unwrap_or_default(),
            bio: row.try_get::<String, _>("bio").unwrap_or_default(),
            role: row.try_get::<String, _>("role").unwrap_or_else(|_| "user".into()),
            row_version: row.try_get::<i64, _>("row_version").unwrap_or(0),
            created_at: row.try_get("created_at").ok(),
            updated_at: row.try_get("updated_at").ok(),
        })
    }
}

/// 鉴权服务,持有连接池 + 速率限制器。
pub struct AuthService {
    pub pool: PgPool,
    pub limiter: &'static RateLimiter,
}

impl AuthService {
    pub fn new(pool: PgPool) -> Self {
        Self {
            pool,
            limiter: &GLOBAL_LIMITER,
        }
    }
}

// ─── 顶层函数(对应 Python 顶层 def) — 包装 AuthService ─────────────────────

/// Python: `register(username, password, display_name)`
#[tracing::instrument(skip(pool, password), fields(username = %username))]
pub async fn register(
    pool: &PgPool,
    username: &str,
    password: &str,
    display_name: &str,
) -> PlatformResult<User> {
    let svc = AuthService::new(pool.clone());
    svc.register(username, password, display_name).await
}

/// Python: `login(username, password, ip="")` → `(user, token)`
#[tracing::instrument(skip(pool, password), fields(username = %username, ip = %ip))]
pub async fn login(
    pool: &PgPool,
    username: &str,
    password: &str,
    ip: &str,
) -> PlatformResult<(User, String)> {
    let svc = AuthService::new(pool.clone());
    svc.login(username, password, ip).await
}

/// Python: `logout(token)`
#[tracing::instrument(skip(pool, token))]
pub async fn logout(pool: &PgPool, token: Option<&str>) -> PlatformResult<()> {
    let svc = AuthService::new(pool.clone());
    svc.logout(token).await
}

/// Python: `user_from_token(token)` —— axum extractor 的核心。
#[tracing::instrument(skip(pool, token))]
pub async fn user_from_token(pool: &PgPool, token: Option<&str>) -> PlatformResult<Option<User>> {
    let svc = AuthService::new(pool.clone());
    svc.user_from_token(token).await
}

/// Python: `get_user(user_id)` — 不存在抛 `ValueError("用户不存在")`。
#[tracing::instrument(skip(pool), fields(user_id = %user_id))]
pub async fn get_user(pool: &PgPool, user_id: UserId) -> PlatformResult<User> {
    let svc = AuthService::new(pool.clone());
    svc.get_user(user_id).await
}

/// Python: `update_profile(user_id, display_name, bio)`
#[tracing::instrument(skip(pool), fields(user_id = %user_id))]
pub async fn update_profile(
    pool: &PgPool,
    user_id: UserId,
    display_name: &str,
    bio: &str,
) -> PlatformResult<User> {
    let svc = AuthService::new(pool.clone());
    svc.update_profile(user_id, display_name, bio).await
}

// ─── AuthService impl(主体)───────────────────────────────────────────────

impl AuthService {
    #[tracing::instrument(skip(self, password), fields(username = %username))]
    pub async fn register(
        &self,
        username: &str,
        password: &str,
        display_name: &str,
    ) -> PlatformResult<User> {
        let normalized = normalize_username(username);
        if normalized.is_empty() {
            return Err(PlatformError::validation("用户名不能为空"));
        }
        let min_len = rpg_core::config::min_password_length();
        if password.chars().count() < min_len {
            return Err(PlatformError::validation(format!("密码至少 {min_len} 位")));
        }

        // 首位注册的人成为 admin。
        let count: i64 = sqlx::query("select count(*)::bigint as count from users")
            .fetch_one(&self.pool)
            .await?
            .try_get::<i64, _>("count")?;
        let role = if count == 0 { "admin" } else { "user" };

        let display = if display_name.trim().is_empty() {
            normalized.clone()
        } else {
            display_name.trim().to_string()
        };

        // Python 捕获 UniqueViolation → "用户名已存在"。
        let row_res = sqlx::query(
            r#"
            insert into users(username, password_hash, display_name, role)
            values ($1, $2, $3, $4)
            returning *
            "#,
        )
        .bind(&normalized)
        .bind(hash_password(password))
        .bind(&display)
        .bind(role)
        .fetch_one(&self.pool)
        .await;
        let row = match row_res {
            Ok(r) => r,
            Err(sqlx::Error::Database(db_err)) if db_err.is_unique_violation() => {
                return Err(PlatformError::conflict("用户名已存在"));
            }
            Err(e) => return Err(e.into()),
        };
        User::from_row(&row).map_err(Into::into)
    }

    #[tracing::instrument(skip(self, password), fields(username = %username, ip = %ip))]
    pub async fn login(
        &self,
        username: &str,
        password: &str,
        ip: &str,
    ) -> PlatformResult<(User, String)> {
        let normalized = normalize_username(username);
        if let Err(RateLimited {
            retry_after_sec,
            key,
        }) = self.limiter.check(ip, &normalized).await
        {
            return Err(PlatformError::RateLimited {
                retry_after_sec,
                key,
            });
        }

        let row_opt = sqlx::query("select * from users where username = $1")
            .bind(&normalized)
            .fetch_optional(&self.pool)
            .await?;
        let Some(row) = row_opt else {
            self.limiter.record_fail(ip, &normalized).await;
            return Err(PlatformError::validation("用户名或密码错误"));
        };
        let user = User::from_row(&row)?;
        // W4-2:verify_and_maybe_rehash — 老 PBKDF2 命中后 silent rehash 到 Argon2id 写回 DB。
        match verify_and_maybe_rehash(password, &user.password_hash) {
            Ok(Some(new_hash)) => {
                // 老 hash 验证通过,silent upgrade。失败不阻断登录,只记 warn。
                if let Err(e) = sqlx::query(
                    "update users set password_hash = $1, row_version = row_version + 1 where id = $2",
                )
                .bind(&new_hash)
                .bind(user.id)
                .execute(&self.pool)
                .await
                {
                    tracing::warn!(
                        target: "rpg_platform::auth",
                        user_id = %user.id,
                        error = %e,
                        "silent rehash 写回失败,登录继续",
                    );
                }
            }
            Ok(None) => {} // 已是新 hash
            Err(AuthVerifyError::WrongPassword) | Err(AuthVerifyError::Malformed) => {
                self.limiter.record_fail(ip, &normalized).await;
                return Err(PlatformError::validation("用户名或密码错误"));
            }
        }
        // 生成 32 字节 token,url-safe base64 编码(对应 Python `secrets.token_urlsafe(32)`).
        // DB 只存 SHA-256 hex(token_hash),明文只返回给客户端。
        let token = url_safe_token(32);
        let token_hash = sha256_hex(&token);
        let expires_at = Utc::now() + chrono::Duration::days(SESSION_DAYS);
        // 轮换:先删该 user 的旧 session(旧 token 立即失效),再插入新 session。
        sqlx::query("delete from sessions where user_id = $1")
            .bind(user.id)
            .execute(&self.pool)
            .await?;
        sqlx::query(
            "insert into sessions(token_hash, user_id, expires_at) values ($1, $2, $3)",
        )
        .bind(&token_hash)
        .bind(user.id)
        .bind(expires_at)
        .execute(&self.pool)
        .await?;
        self.limiter.record_success(ip, &normalized).await;
        Ok((user, token))
    }

    #[tracing::instrument(skip(self, token))]
    pub async fn logout(&self, token: Option<&str>) -> PlatformResult<()> {
        let Some(token) = token.filter(|t| !t.is_empty()) else {
            return Ok(());
        };
        let token_hash = sha256_hex(token);
        sqlx::query("delete from sessions where token_hash = $1")
            .bind(&token_hash)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    #[tracing::instrument(skip(self, token))]
    pub async fn user_from_token(&self, token: Option<&str>) -> PlatformResult<Option<User>> {
        let Some(token) = token.filter(|t| !t.is_empty()) else {
            return Ok(None);
        };
        let token_hash = sha256_hex(token);
        let row = sqlx::query(
            r#"
            select users.* from sessions
            join users on users.id = sessions.user_id
            where sessions.token_hash = $1 and sessions.expires_at > now()
            "#,
        )
        .bind(&token_hash)
        .fetch_optional(&self.pool)
        .await?;
        match row {
            Some(r) => Ok(Some(User::from_row(&r)?)),
            None => Ok(None),
        }
    }

    #[tracing::instrument(skip(self), fields(user_id = %user_id))]
    pub async fn get_user(&self, user_id: UserId) -> PlatformResult<User> {
        let row = sqlx::query("select * from users where id = $1")
            .bind(user_id)
            .fetch_optional(&self.pool)
            .await?;
        match row {
            Some(r) => User::from_row(&r).map_err(Into::into),
            None => Err(PlatformError::not_found("用户不存在")),
        }
    }

    #[tracing::instrument(skip(self), fields(user_id = %user_id))]
    pub async fn update_profile(
        &self,
        user_id: UserId,
        display_name: &str,
        bio: &str,
    ) -> PlatformResult<User> {
        let row = sqlx::query(
            r#"
            update users
               set display_name = $1,
                   bio = $2,
                   row_version = row_version + 1,
                   updated_at = now()
             where id = $3
            returning *
            "#,
        )
        .bind(display_name.trim())
        .bind(bio.trim())
        .bind(user_id)
        .fetch_one(&self.pool)
        .await?;
        User::from_row(&row).map_err(Into::into)
    }
}

// URL-safe base64,等价 Python `secrets.token_urlsafe(n_bytes)`(返回长度 ≈ n*4/3)。
fn url_safe_token(n_bytes: usize) -> String {
    use base64::Engine;
    let mut buf = vec![0u8; n_bytes];
    rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut buf);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&buf)
}

/// 对明文 token 计算 SHA-256,返回小写 hex(64 字符)。
/// DB 只存此 hash,明文只返回给客户端。
fn sha256_hex(token: &str) -> String {
    use sha2::{Digest, Sha256};
    let hash = Sha256::digest(token.as_bytes());
    hex::encode(hash)
}

/// 撤销 user 的全部 session(改密时调用)。
pub async fn revoke_all_user_sessions(pool: &PgPool, user_id: UserId) -> PlatformResult<u64> {
    let result = sqlx::query("delete from sessions where user_id = $1")
        .bind(user_id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected())
}
