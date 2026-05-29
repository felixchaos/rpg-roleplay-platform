//! crypto —— 用户级 API key AES-256-GCM 加解密。
//!
//! 对应 Python: `rpg/utils/crypto.py`。
//!
//! 流程:
//! - 主密钥来自 `RPG_MASTER_KEY` env (64 位 hex);若不是 32 字节用 HKDF 拉伸。
//! - W4-2 fail-fast:**服务端/云部署模式** (server/production/cloud) 下未设此 env 直接报错,
//!   绝不静默生成落盘 —— 避免 cwd 变更后历史密文全废。
//! - 本地 / 自托管模式 (local/desktop/self_hosted) 才允许回退到 `platform_data/master.key`
//!   并首次自动生成,生成时打印明显 WARN。
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
///
/// **panics**:在 server/production/cloud 等部署模式下若未设 `RPG_MASTER_KEY`
/// 会 panic —— 这是有意为之的 fail-fast,生产部署绝不应该使用临时密钥。
/// 业务路径建议改走 [`master_key_checked`] 拿到 `Result` 显式处理。
pub fn master_key() -> [u8; 32] {
    *MASTER_KEY.get_or_init(|| {
        load_master_key().unwrap_or_else(|e| {
            panic!(
                "rpg_platform::crypto::master_key 初始化失败 — 请检查部署 {} 设置: {}",
                MASTER_KEY_ENV, e
            )
        })
    })
}

/// `master_key` 的可错版本 —— 业务热路径建议用这个。
pub fn master_key_checked() -> PlatformResult<[u8; 32]> {
    if let Some(k) = MASTER_KEY.get() {
        return Ok(*k);
    }
    let k = load_master_key()?;
    // 抢占式写入 —— 失败说明已被别人初始化,以那份为准。
    let stored = *MASTER_KEY.get_or_init(|| k);
    Ok(stored)
}

/// 部署模式枚举 —— 用来判断是否允许 fallback 生成 master_key。
fn deployment_mode_lower() -> String {
    std::env::var("RPG_DEPLOYMENT_MODE")
        .unwrap_or_else(|_| "local".to_string())
        .trim()
        .to_lowercase()
}

fn is_server_mode(mode: &str) -> bool {
    matches!(mode, "server" | "production" | "prod" | "cloud")
}

fn is_local_mode(mode: &str) -> bool {
    matches!(mode, "local" | "desktop" | "self_hosted" | "self-hosted")
}

fn load_master_key() -> PlatformResult<[u8; 32]> {
    if let Ok(raw) = std::env::var(MASTER_KEY_ENV) {
        let raw = raw.trim();
        if !raw.is_empty() {
            // 优先按 hex 解析(等价 Python `bytes.fromhex`)。
            if let Ok(bytes) = hex::decode(raw) {
                if bytes.len() == 32 {
                    let mut out = [0u8; 32];
                    out.copy_from_slice(&bytes);
                    return Ok(out);
                }
                return Ok(stretch_to_32(&bytes));
            }
            return Ok(stretch_to_32(raw.as_bytes()));
        }
    }

    // 走到这里说明 env 没设。按部署模式决定 fail-fast 还是 fallback。
    let mode = deployment_mode_lower();
    if is_server_mode(&mode) {
        return Err(PlatformError::validation(format!(
            "{} 未设置 — 部署模式 '{}' 必须显式提供主密钥(64 位 hex)。\
             绝不允许使用自动生成的临时密钥:进程一旦轮换 master_key,历史 \
             API key 密文全部失效。请在部署环境变量里设置 {}=<64-hex> 后重试。",
            MASTER_KEY_ENV, mode, MASTER_KEY_ENV
        )));
    }
    if !is_local_mode(&mode) {
        // 未知模式按 server 处理(更安全) —— 但留有出路:在错误提示里建议显式切到 local。
        return Err(PlatformError::validation(format!(
            "{} 未设置,且部署模式 '{}' 不在白名单 (local/desktop/self_hosted)。\
             若是本地开发,请显式 RPG_DEPLOYMENT_MODE=local;若是生产,请设 {}。",
            MASTER_KEY_ENV, mode, MASTER_KEY_ENV
        )));
    }

    let fallback = fallback_key_path();
    if let Ok(text) = std::fs::read_to_string(&fallback) {
        if let Ok(bytes) = hex::decode(text.trim()) {
            if bytes.len() == 32 {
                let mut out = [0u8; 32];
                out.copy_from_slice(&bytes);
                return Ok(out);
            }
        }
    }
    // 首次:本地模式才生成并落盘,且 WARN 大字提示。
    let mut key = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut key);
    if let Some(parent) = fallback.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&fallback, hex::encode(key))?;
    tracing::warn!(
        target: "rpg_platform::crypto",
        "⚠️ 生成新 master_key → {} | 部署模式 '{}'. \
         生产部署必须改设 {} env,否则密钥落盘到 cwd,部署目录一变历史密文全废。",
        fallback.display(),
        mode,
        MASTER_KEY_ENV
    );
    Ok(key)
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

    /// 注:所有用 `encrypt_api_key/decrypt_api_key` 的测试共享同一个 `OnceLock<MASTER_KEY>`,
    /// 必须先设置好 env 再进入 init。为 robust,我们用 `Once` 同步首次初始化。
    fn ensure_test_master_key() {
        use std::sync::Once;
        static INIT: Once = Once::new();
        INIT.call_once(|| {
            std::env::set_var(MASTER_KEY_ENV, "0".repeat(64));
        });
    }

    /// 老测试保留 —— API key 加解密 round-trip,跨 user 隔离已隐含。
    #[test]
    fn roundtrip_api_key() {
        ensure_test_master_key();
        let blob = encrypt_api_key("sk-test-12345", 42, "openai").unwrap();
        assert!(blob.len() > NONCE_LEN + TAG_LEN);
        let plain = decrypt_api_key(&blob, 42, "openai");
        assert_eq!(plain, "sk-test-12345");
        // Wrong user → empty.
        let bad = decrypt_api_key(&blob, 43, "openai");
        assert_eq!(bad, "");
    }

    /// 通用 credential round-trip:直接传 key,完全绕开 OnceLock。
    #[test]
    fn test_encrypt_decrypt_roundtrip() {
        let key = [7u8; 32];
        let aad = b"unit-test-aad";
        let blob = encrypt_credential(b"hello secret", &key, aad).unwrap();
        let plain = decrypt_credential(&blob, &key, aad).unwrap();
        assert_eq!(plain, b"hello secret");
    }

    /// 错误 key 必须解密失败 —— 不能被静默返回空串。
    #[test]
    fn test_decrypt_wrong_key_fails() {
        let key_a = [1u8; 32];
        let key_b = [2u8; 32];
        let aad = b"aad";
        let blob = encrypt_credential(b"sensitive", &key_a, aad).unwrap();
        // 同 key + 同 aad 才能解。
        assert_eq!(decrypt_credential(&blob, &key_a, aad).unwrap(), b"sensitive");
        // 换 key:必须 Err,不允许返回成功。
        assert!(decrypt_credential(&blob, &key_b, aad).is_err());
        // 换 aad:也必须 Err(AES-GCM 鉴权失败)。
        assert!(decrypt_credential(&blob, &key_a, b"different-aad").is_err());
        // blob 长度不足:Validation err。
        assert!(decrypt_credential(&[0u8; 10], &key_a, aad).is_err());
    }

    /// 跨用户隔离:salt = user_id,两个 user 派生 key 不同,密文不能互通。
    /// 用 `encrypt_api_key/decrypt_api_key` 直接验证 derive_user_key 的 salt 效果。
    #[test]
    fn test_cross_user_isolation() {
        ensure_test_master_key();
        let user_a: i64 = 100;
        let user_b: i64 = 200;
        let api = "openai";

        let blob_a = encrypt_api_key("token-of-A", user_a, api).unwrap();
        let blob_b = encrypt_api_key("token-of-B", user_b, api).unwrap();

        // 自己解自己 → 原文
        assert_eq!(decrypt_api_key(&blob_a, user_a, api), "token-of-A");
        assert_eq!(decrypt_api_key(&blob_b, user_b, api), "token-of-B");

        // user B 用自己 key 解 user A 的密文 → 失败(空串)。
        assert_eq!(decrypt_api_key(&blob_a, user_b, api), "");
        assert_eq!(decrypt_api_key(&blob_b, user_a, api), "");

        // 进一步验证 derive_user_key 的 raw 比特层面也不同。
        let k_a = derive_user_key(user_a, api);
        let k_b = derive_user_key(user_b, api);
        assert_ne!(k_a, k_b);
        // 同 user 同 api 派生稳定。
        assert_eq!(derive_user_key(user_a, api), k_a);
    }
}
