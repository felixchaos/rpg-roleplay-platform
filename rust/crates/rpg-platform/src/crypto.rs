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
//!
//! ## master_key 取值现经 [`KeyProvider`](crate::infra::key_provider::KeyProvider)
//! 默认 `EnvKeyProvider`(就是本文件下方 `load_master_key_raw` 的 env/文件逻辑),
//! 行为与历史完全一致。设了 `RPG_KMS_ENDPOINT` 则切到 `KmsKeyProvider`(envelope/KMS-ready)。
//! 见 `infra::key_provider` 模块文档了解如何接 AWS KMS / Vault。

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Key, Nonce,
};
use hkdf::Hkdf;
use rand::RngCore;
use sha2::Sha256;
use std::path::PathBuf;
use std::sync::OnceLock;
use zeroize::Zeroizing;

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
        load_master_key_via_provider().unwrap_or_else(|e| {
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
    let k = load_master_key_via_provider()?;
    // 抢占式写入 —— 失败说明已被别人初始化,以那份为准。
    let stored = *MASTER_KEY.get_or_init(|| k);
    Ok(stored)
}

/// 经全局 [`KeyProvider`](crate::infra::key_provider) 取 master_key(KEK)。
///
/// 默认 `EnvKeyProvider` → 等价于直接调 [`load_master_key_raw`](原 env/文件逻辑),
/// 现有行为不回归;`RPG_KMS_ENDPOINT` 设了则走 KMS provider。
fn load_master_key_via_provider() -> PlatformResult<[u8; 32]> {
    use crate::infra::key_provider::GLOBAL_PROVIDER;
    let z = GLOBAL_PROVIDER.master_key()?;
    Ok(*z)
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

/// 原始 master_key 加载逻辑(env `RPG_MASTER_KEY` / 本地 fallback 文件 + fail-fast)。
///
/// `EnvKeyProvider` 直接调用本函数 —— 是「现有 env/文件行为」的唯一真源,
/// 既不缓存也不经 provider,供 [`KeyProvider`](crate::infra::key_provider) 默认实现复用。
pub(crate) fn load_master_key_raw() -> PlatformResult<[u8; 32]> {
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
    // 主密钥落盘必须收紧权限到仅属主可读写(0600),避免同机其他用户读取。
    harden_key_file_perms(&fallback);
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
    // 6A-1:fallback master_key 绝不再落到 cwd —— cwd 一变历史密文全废,
    // 且 cwd 常被多进程共享/打包,泄漏风险高。落盘点优先级:
    //   1. `$RPG_DATA_DIR/master.key`(显式覆盖,运维可控)
    //   2. `dirs::data_dir()/rpg/master.key`(平台标准数据目录,如
    //      Linux `~/.local/share`、macOS `~/Library/Application Support`)
    //   3. 退路:`~/.rpg/master.key`(data_dir 不可用的极端环境)
    if let Ok(dir) = std::env::var("RPG_DATA_DIR") {
        let dir = dir.trim();
        if !dir.is_empty() {
            return PathBuf::from(dir).join("master.key");
        }
    }
    if let Some(base) = dirs::data_dir() {
        return base.join("rpg").join("master.key");
    }
    if let Some(home) = dirs::home_dir() {
        return home.join(".rpg").join("master.key");
    }
    // 极端兜底:相对路径,但放进专用子目录而非裸 cwd。
    PathBuf::from(".rpg").join("master.key")
}

/// 把 master.key 文件权限收紧到 0600(仅属主可读写)。
///
/// 非 unix 平台无 POSIX 权限模型,跳过(NTFS ACL 默认即属主可控)。
/// 设权限失败只 WARN 不致命 —— 文件已写入,宁可降级也不要丢密钥。
fn harden_key_file_perms(path: &std::path::Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Err(e) = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)) {
            tracing::warn!(
                target: "rpg_platform::crypto",
                path = %path.display(),
                "无法将 master.key 权限收紧到 0600: {e} — 请手动 chmod 0600"
            );
        }
    }
    #[cfg(not(unix))]
    {
        let _ = path;
    }
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

/// 解密一个 API key 密文。
///
/// **安全行为变更(6A-1)**:不再静默返回空串。
/// - 成功:返回 `Some(Zeroizing<String>)`,明文在 Drop 时擦除内存。
/// - 失败(长度不足 / AEAD 鉴权失败 / 非法 UTF-8):返回 `None`,并以
///   `tracing::error!` 记审计。**绝不在日志里泄漏密文或明文内容**,只记
///   user_id / api_id / 失败原因,供运维定位「密钥轮换后历史密文全废」一类事故。
/// - 调用方拿到 `None` 必须拒绝调用(而非把空串当 key 发出去)。
pub fn decrypt_api_key(blob: &[u8], user_id: i64, api_id: &str) -> Option<Zeroizing<String>> {
    if blob.len() < NONCE_LEN + TAG_LEN {
        // 空 blob(未设凭据)是正常状态,不刷审计噪声;只有「有数据但太短」才告警。
        if !blob.is_empty() {
            tracing::error!(
                target: "rpg_platform::crypto",
                user_id, api_id, blob_len = blob.len(),
                "API key 密文长度不足(< nonce+tag),疑似损坏 — 拒绝使用"
            );
        }
        return None;
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
        Ok(plain) => {
            // 先把解密出的明文字节包进 Zeroizing,确保任何提前 return 路径都会擦除。
            let plain = Zeroizing::new(plain);
            match std::str::from_utf8(&plain) {
                Ok(s) => Some(Zeroizing::new(s.to_owned())),
                Err(_) => {
                    tracing::error!(
                        target: "rpg_platform::crypto",
                        user_id, api_id,
                        "API key 解密成功但非合法 UTF-8 — 拒绝使用"
                    );
                    None
                }
            }
        }
        Err(_) => {
            // AEAD 鉴权失败:master_key 轮换 / AAD 不匹配 / 密文被篡改。
            // 这是最关键的审计点 —— 历史密文集体失效会在这里大量出现。
            tracing::error!(
                target: "rpg_platform::crypto",
                user_id, api_id,
                "API key 解密失败(AEAD 鉴权未通过)— 可能 master_key 轮换或密文损坏,拒绝使用"
            );
            None
        }
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
    /// 6A-1:`decrypt_api_key` 现返回 `Option<Zeroizing<String>>`。
    #[test]
    fn roundtrip_api_key() {
        ensure_test_master_key();
        let blob = encrypt_api_key("sk-test-12345", 42, "openai").unwrap();
        assert!(blob.len() > NONCE_LEN + TAG_LEN);
        let plain = decrypt_api_key(&blob, 42, "openai");
        assert_eq!(plain.as_ref().map(|z| z.as_str()), Some("sk-test-12345"));
        // Wrong user → None(不再是空串)。
        let bad = decrypt_api_key(&blob, 43, "openai");
        assert!(bad.is_none());
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
        assert_eq!(
            decrypt_api_key(&blob_a, user_a, api).as_ref().map(|z| z.as_str()),
            Some("token-of-A")
        );
        assert_eq!(
            decrypt_api_key(&blob_b, user_b, api).as_ref().map(|z| z.as_str()),
            Some("token-of-B")
        );

        // user B 用自己 key 解 user A 的密文 → 失败(None,不再是空串)。
        assert!(decrypt_api_key(&blob_a, user_b, api).is_none());
        assert!(decrypt_api_key(&blob_b, user_a, api).is_none());

        // 进一步验证 derive_user_key 的 raw 比特层面也不同。
        let k_a = derive_user_key(user_a, api);
        let k_b = derive_user_key(user_b, api);
        assert_ne!(k_a, k_b);
        // 同 user 同 api 派生稳定。
        assert_eq!(derive_user_key(user_a, api), k_a);
    }
}
