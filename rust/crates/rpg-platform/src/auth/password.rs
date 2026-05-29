//! 密码哈希 + 用户名归一化 + 公开化。
//!
//! 对应 Python: `rpg/platform_app/security.py`
//!   - `normalize_username(username)`
//!   - `hash_password(password)` → `pbkdf2_sha256$<salt>$<hex_digest>` (180000 轮 PBKDF2-HMAC-SHA256)
//!   - `verify_password(password, stored)` — 常数时间比较
//!   - `public_user(user)`  — 抹掉 password_hash 等敏感字段

use base64::Engine;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::sessions::User;

/// PBKDF2 轮数,与 Python 保持一致。
const PBKDF2_ROUNDS: u32 = 180_000;

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

/// Python: `hash_password`
///
/// 输出格式: `pbkdf2_sha256$<hex16-byte salt>$<hex32-byte digest>`
pub fn hash_password(password: &str) -> String {
    let mut salt_bytes = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut salt_bytes);
    let salt_hex = hex_encode(&salt_bytes);
    let digest = pbkdf2_sha256(password.as_bytes(), salt_hex.as_bytes(), PBKDF2_ROUNDS);
    format!("pbkdf2_sha256${}${}", salt_hex, hex_encode(&digest))
}

/// Python: `verify_password`
pub fn verify_password(password: &str, stored: &str) -> bool {
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
    let candidate = pbkdf2_sha256(password.as_bytes(), salt.as_bytes(), PBKDF2_ROUNDS);
    let expected = match hex_decode(digest_hex) {
        Some(v) => v,
        None => return false,
    };
    constant_time_eq(&candidate, &expected)
}

/// Python: `public_user(user)` — 暴露给前端的公开形态。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PublicUser {
    pub id: i64,
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
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

fn hex_decode(s: &str) -> Option<Vec<u8>> {
    if !s.len().is_multiple_of(2) {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok())
        .collect()
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

/// 一个简易 PBKDF2-HMAC-SHA256(HmacSha256 手写,避免引新 dep)。
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

/// 手写 HMAC-SHA256(避免额外 hmac crate)。32 字节 digest。
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
    fn roundtrip_pwd() {
        let stored = hash_password("hunter2");
        assert!(verify_password("hunter2", &stored));
        assert!(!verify_password("hunter3", &stored));
    }

    #[test]
    fn normalize() {
        assert_eq!(normalize_username("  Felix_Chaos  "), "felix_chaos");
        assert_eq!(normalize_username("a!b@c#d"), "abcd");
    }
}
