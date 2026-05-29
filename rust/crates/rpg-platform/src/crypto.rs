//! crypto —— 用户级 API key AES-256-GCM 加解密。
//!
//! 对应 Python: `rpg/utils/crypto.py`。
//!
//! 流程:
//! - 主密钥来自 `RPG_MASTER_KEY` env (64 位 hex);若不是 32 字节用 HKDF 拉伸。
//!   未设则回退到 `platform_data/master.key`,首次会自动生成并落盘 (仅本地模式合理)。
//! - 派生:HKDF(master, salt=user_id, info=`api:<api_id>`) → 32 byte key。
//! - 加密:AES-256-GCM,12 字节 nonce,AAD = `user=<id>&api=<api_id>`。
//! - 输出格式:`nonce(12) || ciphertext || tag(16)`,可直接 INSERT。

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Key, Nonce,
};
use hkdf::Hkdf;
use rand::RngCore;
use sha2::Sha256;
use std::path::PathBuf;
use std::sync::OnceLock;

use crate::error::{PlatformError, PlatformResult};

const MASTER_KEY_ENV: &str = "RPG_MASTER_KEY";
const NONCE_LEN: usize = 12;
const TAG_LEN: usize = 16;

static MASTER_KEY: OnceLock<[u8; 32]> = OnceLock::new();

/// 公开:确保 master key 已加载,返回 32 字节副本。
pub fn master_key() -> [u8; 32] {
    *MASTER_KEY.get_or_init(load_master_key)
}

fn load_master_key() -> [u8; 32] {
    if let Ok(raw) = std::env::var(MASTER_KEY_ENV) {
        let raw = raw.trim();
        if !raw.is_empty() {
            // 优先按 hex 解析(等价 Python `bytes.fromhex`)。
            if let Ok(bytes) = hex::decode(raw) {
                if bytes.len() == 32 {
                    let mut out = [0u8; 32];
                    out.copy_from_slice(&bytes);
                    return out;
                }
                return stretch_to_32(&bytes);
            }
            return stretch_to_32(raw.as_bytes());
        }
    }
    let fallback = fallback_key_path();
    if let Ok(text) = std::fs::read_to_string(&fallback) {
        if let Ok(bytes) = hex::decode(text.trim()) {
            if bytes.len() == 32 {
                let mut out = [0u8; 32];
                out.copy_from_slice(&bytes);
                return out;
            }
        }
    }
    // 首次:生成并落盘。
    let mut key = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut key);
    if let Some(parent) = fallback.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(&fallback, hex::encode(key));
    tracing::warn!(
        target: "rpg_platform::crypto",
        "未设置 {} 环境变量,已生成本地主密钥 → {}",
        MASTER_KEY_ENV,
        fallback.display()
    );
    key
}

fn fallback_key_path() -> PathBuf {
    // 等价 Python `rpg/platform_data/master.key`,以当前工作目录上溯。
    // 优先使用 `RPG_DATA_DIR`,否则 `./platform_data/master.key`。
    if let Ok(dir) = std::env::var("RPG_DATA_DIR") {
        return PathBuf::from(dir).join("master.key");
    }
    PathBuf::from("platform_data/master.key")
}

fn stretch_to_32(seed: &[u8]) -> [u8; 32] {
    let hk = Hkdf::<Sha256>::new(Some(b"rpg-master-stretch"), seed);
    let mut out = [0u8; 32];
    hk.expand(b"v1", &mut out).expect("HKDF 32 byte");
    out
}

fn derive_user_key(user_id: i64, api_id: &str) -> [u8; 32] {
    let master = master_key();
    let salt = user_id.to_string();
    let hk = Hkdf::<Sha256>::new(Some(salt.as_bytes()), &master);
    let info = format!("api:{}", api_id);
    let mut out = [0u8; 32];
    hk.expand(info.as_bytes(), &mut out).expect("HKDF 32 byte");
    out
}

fn aad_for(user_id: i64, api_id: &str) -> Vec<u8> {
    format!("user={}&api={}", user_id, api_id).into_bytes()
}

/// 加密一个 API key。空串返回空 `Vec`。
///
/// 对应 Python `encrypt_api_key(plaintext, user_id, api_id)`。
pub fn encrypt_api_key(plaintext: &str, user_id: i64, api_id: &str) -> PlatformResult<Vec<u8>> {
    if plaintext.is_empty() {
        return Ok(Vec::new());
    }
    let key_bytes = derive_user_key(user_id, api_id);
    let key = Key::<Aes256Gcm>::from_slice(&key_bytes);
    let cipher = Aes256Gcm::new(key);
    let mut nonce_bytes = [0u8; NONCE_LEN];
    rand::thread_rng().fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);
    let aad = aad_for(user_id, api_id);
    let ct = cipher
        .encrypt(
            nonce,
            Payload {
                msg: plaintext.as_bytes(),
                aad: &aad,
            },
        )
        .map_err(|e| PlatformError::Other(anyhow::anyhow!("AES-GCM encrypt 失败: {e}")))?;
    let mut out = Vec::with_capacity(NONCE_LEN + ct.len());
    out.extend_from_slice(&nonce_bytes);
    out.extend_from_slice(&ct);
    Ok(out)
}

/// 解密;任何失败都返回空串(与 Python 行为一致,让调用方走 fallback)。
pub fn decrypt_api_key(blob: &[u8], user_id: i64, api_id: &str) -> String {
    if blob.len() < NONCE_LEN + TAG_LEN {
        return String::new();
    }
    let key_bytes = derive_user_key(user_id, api_id);
    let key = Key::<Aes256Gcm>::from_slice(&key_bytes);
    let cipher = Aes256Gcm::new(key);
    let nonce = Nonce::from_slice(&blob[..NONCE_LEN]);
    let aad = aad_for(user_id, api_id);
    match cipher.decrypt(
        nonce,
        Payload {
            msg: &blob[NONCE_LEN..],
            aad: &aad,
        },
    ) {
        Ok(plain) => String::from_utf8(plain).unwrap_or_default(),
        Err(_) => String::new(),
    }
}

/// 通用版:任意 plaintext + 32 字节 key → ciphertext。
///
/// 用于不带 user_id/api_id 上下文的场景(如批量导出/导入)。
pub fn encrypt_credential(plaintext: &[u8], key: &[u8; 32], aad: &[u8]) -> PlatformResult<Vec<u8>> {
    let aes_key = Key::<Aes256Gcm>::from_slice(key);
    let cipher = Aes256Gcm::new(aes_key);
    let mut nonce_bytes = [0u8; NONCE_LEN];
    rand::thread_rng().fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);
    let ct = cipher
        .encrypt(
            nonce,
            Payload {
                msg: plaintext,
                aad,
            },
        )
        .map_err(|e| PlatformError::Other(anyhow::anyhow!("AES-GCM encrypt 失败: {e}")))?;
    let mut out = Vec::with_capacity(NONCE_LEN + ct.len());
    out.extend_from_slice(&nonce_bytes);
    out.extend_from_slice(&ct);
    Ok(out)
}

/// 通用版解密。
pub fn decrypt_credential(blob: &[u8], key: &[u8; 32], aad: &[u8]) -> PlatformResult<Vec<u8>> {
    if blob.len() < NONCE_LEN + TAG_LEN {
        return Err(PlatformError::validation("blob too short"));
    }
    let aes_key = Key::<Aes256Gcm>::from_slice(key);
    let cipher = Aes256Gcm::new(aes_key);
    let nonce = Nonce::from_slice(&blob[..NONCE_LEN]);
    cipher
        .decrypt(
            nonce,
            Payload {
                msg: &blob[NONCE_LEN..],
                aad,
            },
        )
        .map_err(|e| PlatformError::Other(anyhow::anyhow!("AES-GCM decrypt 失败: {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_api_key() {
        // Set a deterministic master key for testing.
        std::env::set_var(MASTER_KEY_ENV, "0".repeat(64));
        let blob = encrypt_api_key("sk-test-12345", 42, "openai").unwrap();
        assert!(blob.len() > NONCE_LEN + TAG_LEN);
        let plain = decrypt_api_key(&blob, 42, "openai");
        assert_eq!(plain, "sk-test-12345");
        // Wrong user → empty.
        let bad = decrypt_api_key(&blob, 43, "openai");
        assert_eq!(bad, "");
    }
}
