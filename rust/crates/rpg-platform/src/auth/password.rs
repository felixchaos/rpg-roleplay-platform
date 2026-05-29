//! 密码哈希 + 用户名归一化 + 公开化。
//!
//! W4-2 安全升级:
//! - 新 hash:Argon2id PHC string `$argon2id$v=19$m=19456,t=2,p=1$...`
//!   参数取 `argon2` crate 默认(m=19 MiB, t=2, p=1),符合 OWASP 2026 推荐。
//! - 旧 PBKDF2-SHA256 180k 轮:**只读** verify,平滑迁移。
//! - `verify_and_maybe_rehash`:返回 `Some(new_hash)` 时调用方应回写 DB。
//!
//! 对应 Python: `rpg/platform_app/security.py`
//!   - `normalize_username(username)`
//!   - `hash_password(password)` → Argon2id PHC string
//!   - `verify_password(password, stored)` — 兼容新老格式
//!   - `verify_and_maybe_rehash(password, old)` — verify 同时给出 silent rehash
//!   - `public_user(user)`  — 抹掉 password_hash 等敏感字段

use argon2::password_hash::rand_core::OsRng;
use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::Argon2;
use base64::Engine;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::sessions::User;
use rpg_core::UserId;

/// 旧 PBKDF2 轮数,与 Python 历史值一致(只用于 verify 老 hash)。
const LEGACY_PBKDF2_ROUNDS: u32 = 180_000;
const LEGACY_PBKDF2_PREFIX: &str = "pbkdf2_sha256$";

/// Python: `normalize_username`
///
/// 规则:
/// - 去首尾空格 + 转小写
/// - 只保留 alphanumeric / `_` / `-` / `.`
/// - 截断到 48 字符
pub fn normalize_username(username: &str) -> String {
    let lower = username.trim().to_lowercase();
    let mut out = String::with_capacity(lower.len().min(48));
    for ch in lower.chars() {
        if ch.is_alphanumeric() || matches!(ch, '_' | '-' | '.') {
            out.push(ch);
            if out.chars().count() >= 48 {
                break;
            }
        }
    }
    out
}

/// 新 hash:Argon2id PHC string。
///
/// 输出形如:`$argon2id$v=19$m=19456,t=2,p=1$<salt-b64>$<hash-b64>`
pub fn hash_password(password: &str) -> String {
    // Argon2 默认 = Algorithm::Argon2id + Version::V0x13 + Params::DEFAULT
    // Params::DEFAULT_M_COST = 19 * 1024 = 19456 KiB
    // Params::DEFAULT_T_COST = 2
    // Params::DEFAULT_P_COST = 1
    let salt = SaltString::generate(&mut OsRng);
    let argon2 = Argon2::default();
    argon2
        .hash_password(password.as_bytes(), &salt)
        .expect("argon2 hash 不应失败(默认参数 + OsRng salt)")
        .to_string()
}

/// Python: `verify_password`(兼容新老两种格式)。
pub fn verify_password(password: &str, stored: &str) -> bool {
    if is_legacy_pbkdf2(stored) {
        return verify_legacy_pbkdf2(password, stored);
    }
    match PasswordHash::new(stored) {
        Ok(parsed) => Argon2::default()
            .verify_password(password.as_bytes(), &parsed)
            .is_ok(),
        Err(_) => false,
    }
}

/// 校验 + 如果是老 PBKDF2 格式则同时给出新 Argon2id hash 供 DB 回写。
///
/// 返回:
/// - `Ok(None)`:密码正确,已是新格式,不需要 rehash。
/// - `Ok(Some(new_hash))`:密码正确,且原 hash 是 legacy PBKDF2 —— 调用方应 update DB。
/// - `Err(_)`:密码不正确或 hash 格式损坏。
pub fn verify_and_maybe_rehash(
    password: &str,
    stored: &str,
) -> Result<Option<String>, AuthVerifyError> {
    if is_legacy_pbkdf2(stored) {
        if verify_legacy_pbkdf2(password, stored) {
            return Ok(Some(hash_password(password)));
        }
        return Err(AuthVerifyError::WrongPassword);
    }
    let parsed = PasswordHash::new(stored).map_err(|_| AuthVerifyError::Malformed)?;
    Argon2::default()
        .verify_password(password.as_bytes(), &parsed)
        .map_err(|_| AuthVerifyError::WrongPassword)?;
    Ok(None)
}

/// `verify_and_maybe_rehash` 的错误类型 —— 调用方只关心成功/失败。
#[derive(Debug, thiserror::Error)]
pub enum AuthVerifyError {
    #[error("wrong password")]
    WrongPassword,
    #[error("malformed password hash")]
    Malformed,
}

// ─── 老 PBKDF2 兼容路径(只读) ───────────────────────────────────────────────

fn is_legacy_pbkdf2(stored: &str) -> bool {
    stored.starts_with(LEGACY_PBKDF2_PREFIX)
}

fn verify_legacy_pbkdf2(password: &str, stored: &str) -> bool {
    let mut parts = stored.splitn(3, '$');
    let algo = match parts.next() {
        Some(v) => v,
        None => return false,
    };
    let salt = match parts.next() {
        Some(v) => v,
        None => return false,
    };
    let digest_hex = match parts.next() {
        Some(v) => v,
        None => return false,
    };
    if algo != "pbkdf2_sha256" {
        return false;
    }
    let candidate = pbkdf2_sha256(password.as_bytes(), salt.as_bytes(), LEGACY_PBKDF2_ROUNDS);
    let expected = match hex_decode(digest_hex) {
        Some(v) => v,
        None => return false,
    };
    constant_time_eq(&candidate, &expected)
}

/// 仅供测试 / 迁移工具:用老格式造 hash。生产路径不应再调用。
#[doc(hidden)]
pub fn legacy_hash_password_pbkdf2(password: &str) -> String {
    let mut salt_bytes = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut salt_bytes);
    let salt_hex = hex_encode(&salt_bytes);
    let digest = pbkdf2_sha256(password.as_bytes(), salt_hex.as_bytes(), LEGACY_PBKDF2_ROUNDS);
    format!("pbkdf2_sha256${}${}", salt_hex, hex_encode(&digest))
}

// ─── PublicUser ──────────────────────────────────────────────────────────────

/// Python: `public_user(user)` — 暴露给前端的公开形态。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PublicUser {
    pub id: UserId,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub public_id: Option<uuid::Uuid>,
    pub username: String,
    pub display_name: String,
    #[serde(default)]
    pub bio: String,
    pub role: String,
    #[serde(with = "chrono::serde::ts_seconds_option", default)]
    pub created_at: Option<chrono::DateTime<chrono::Utc>>,
    #[serde(with = "chrono::serde::ts_seconds_option", default)]
    pub updated_at: Option<chrono::DateTime<chrono::Utc>>,
    pub row_version: i64,
    /// Python 里的 `uid` 字段 = `str(public_id)`,前端按字符串展示。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uid: Option<String>,
}

/// User → PublicUser(对应 Python `public_user`)。
pub fn public_user(user: &User) -> PublicUser {
    PublicUser {
        id: user.id,
        public_id: user.public_id,
        username: user.username.clone(),
        display_name: user.display_name.clone(),
        bio: user.bio.clone(),
        role: user.role.clone(),
        created_at: user.created_at,
        updated_at: user.updated_at,
        row_version: user.row_version,
        uid: user.public_id.map(|u| u.to_string()),
    }
}

// ─── 工具函数 ────────────────────────────────────────────────────────────────

fn hex_encode(bytes: &[u8]) -> String {
    hex::encode(bytes)
}

fn hex_decode(s: &str) -> Option<Vec<u8>> {
    hex::decode(s).ok()
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff: u8 = 0;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// 简易 PBKDF2-HMAC-SHA256(HmacSha256 手写,只供老 hash verify)。
fn pbkdf2_sha256(password: &[u8], salt: &[u8], iterations: u32) -> [u8; 32] {
    // T_1 = U_1 XOR U_2 XOR ... XOR U_c
    // U_1 = HMAC(P, S || INT(1))
    let mut salt_block = Vec::with_capacity(salt.len() + 4);
    salt_block.extend_from_slice(salt);
    salt_block.extend_from_slice(&1u32.to_be_bytes());
    let mut u = hmac_sha256(password, &salt_block);
    let mut result = u;
    for _ in 1..iterations {
        u = hmac_sha256(password, &u);
        for (r, x) in result.iter_mut().zip(u.iter()) {
            *r ^= *x;
        }
    }
    result
}

/// 手写 HMAC-SHA256(只供老 hash verify)。32 字节 digest。
fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    const BLOCK: usize = 64;
    let mut key_block = [0u8; BLOCK];
    if key.len() > BLOCK {
        let mut h = Sha256::new();
        h.update(key);
        let d = h.finalize();
        key_block[..32].copy_from_slice(&d);
    } else {
        key_block[..key.len()].copy_from_slice(key);
    }
    let mut o_pad = [0x5cu8; BLOCK];
    let mut i_pad = [0x36u8; BLOCK];
    for i in 0..BLOCK {
        o_pad[i] ^= key_block[i];
        i_pad[i] ^= key_block[i];
    }
    let mut inner = Sha256::new();
    inner.update(i_pad);
    inner.update(msg);
    let inner_digest = inner.finalize();
    let mut outer = Sha256::new();
    outer.update(o_pad);
    outer.update(inner_digest);
    let out = outer.finalize();
    let mut arr = [0u8; 32];
    arr.copy_from_slice(&out);
    arr
}

/// 一个示例 base64 工具,使用 `base64::engine::general_purpose::STANDARD`。
/// 留作未来 session token 编码使用。
#[allow(dead_code)]
fn b64(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_username() {
        assert_eq!(normalize_username("  Felix_Chaos  "), "felix_chaos");
        assert_eq!(normalize_username("a!b@c#d"), "abcd");
    }

    /// 新 Argon2id round-trip:hash → verify。
    #[test]
    fn test_hash_verify_roundtrip() {
        let stored = hash_password("hunter2");
        // 必须是 Argon2id PHC 串
        assert!(stored.starts_with("$argon2id$"), "got: {stored}");
        assert!(verify_password("hunter2", &stored));
        assert!(!verify_password("hunter3", &stored));
    }

    /// 老 PBKDF2 hash 仍能 verify(平滑迁移)。
    #[test]
    fn test_verify_legacy_pbkdf2() {
        let stored = legacy_hash_password_pbkdf2("hunter2");
        assert!(stored.starts_with("pbkdf2_sha256$"));
        assert!(verify_password("hunter2", &stored));
        assert!(!verify_password("hunter3", &stored));
    }

    /// verify_and_maybe_rehash:legacy hash 命中时返回新 Argon2id。
    #[test]
    fn test_rehash_after_legacy_verify() {
        let legacy = legacy_hash_password_pbkdf2("hunter2");
        let out = verify_and_maybe_rehash("hunter2", &legacy).expect("verify ok");
        let new_hash = out.expect("应返回 Some(new_hash) 触发 silent rehash");
        assert!(new_hash.starts_with("$argon2id$"));
        // 新 hash 自身也得能 verify 通过
        assert!(verify_password("hunter2", &new_hash));

        // 已是新格式时返回 None
        let modern = hash_password("hunter2");
        assert!(verify_and_maybe_rehash("hunter2", &modern)
            .expect("verify ok")
            .is_none());

        // 错密码:legacy / modern 都返回 WrongPassword
        assert!(matches!(
            verify_and_maybe_rehash("wrong", &legacy),
            Err(AuthVerifyError::WrongPassword)
        ));
        assert!(matches!(
            verify_and_maybe_rehash("wrong", &modern),
            Err(AuthVerifyError::WrongPassword)
        ));
        // 损坏的 hash:返回 Malformed
        assert!(matches!(
            verify_and_maybe_rehash("hunter2", "garbage-not-phc"),
            Err(AuthVerifyError::Malformed)
        ));
    }
}
