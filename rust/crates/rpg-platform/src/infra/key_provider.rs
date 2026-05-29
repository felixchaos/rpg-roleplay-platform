//! KeyProvider —— master_key 来源抽象 + envelope encryption(KMS-ready)。
//!
//! ## 动机
//! 旧版 `crypto.rs` 把 master_key 当成单一静态密钥(env / 文件直接读),无 envelope、
//! 无轮换路径。一旦想接 AWS KMS / HashiCorp Vault(密钥永不离开 HSM,只在云端做
//! wrap/unwrap),就得改一堆调用点。本模块把「拿 master_key」和「用 master_key 包/解
//! per-data DEK」抽象成 [`KeyProvider`]:
//!
//! - [`EnvKeyProvider`] —— 现有 env/文件逻辑的薄包装,**默认实现,行为不变**。
//! - [`KmsKeyProvider`] —— KEK 留在 KMS,本地只持 wrapped DEK 的骨架(见下文 TODO)。
//!
//! ## Envelope encryption 模型
//! ```text
//!   KEK(Key-Encrypting-Key,master_key / KMS CMK)
//!     └── wrap_dek(dek)  → wrapped: Vec<u8>   // 落库,KEK 永不落库
//!         unwrap_dek(wrapped) → dek           // 用 DEK 真正加解密业务数据
//! ```
//! `EnvKeyProvider` 的 wrap/unwrap 用本地 master_key 跑 AES-256-GCM(本地信封);
//! `KmsKeyProvider` 则把 wrap/unwrap 委托给云端 KMS(KEK 不出 HSM)。两者对调用方
//! 接口一致 —— crypto.rs 只认 [`KeyProvider`] trait。
//!
//! ## 如何切到真实 KMS / Vault(部署时)
//! 1. **AWS KMS**:加依赖 `aws-sdk-kms`(或老项目用 `rusoto_kms`)。在
//!    [`KmsKeyProvider::wrap_dek`] / [`unwrap_dek`](KmsKeyProvider::unwrap_dek) 里
//!    分别调用 `kms.encrypt(KeyId, Plaintext=dek)` / `kms.decrypt(CiphertextBlob)`,
//!    KeyId 取自 `RPG_KMS_KEY_ID`。`master_key()` 改为 `GenerateDataKey` 拿到的
//!    plaintext DEK(或直接禁用 master_key,只走 envelope)。
//! 2. **HashiCorp Vault Transit**:`POST {RPG_KMS_ENDPOINT}/v1/transit/encrypt/<key>`
//!    传 base64(dek) 拿 ciphertext;`/decrypt/<key>` 反向。用 reqwest + Vault token
//!    (`RPG_KMS_TOKEN`)。
//! 3. 工厂 [`default_provider`] 已按 `RPG_KMS_ENDPOINT` 选 provider —— 接好上面任一
//!    SDK 后,把 `KmsKeyProvider` 里标 `TODO(kms)` 的桩替换为真实调用即可,调用方
//!    (crypto.rs)零改动。

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Key, Nonce,
};
use rand::RngCore;
use zeroize::Zeroizing;

use crate::error::{PlatformError, PlatformResult};

const NONCE_LEN: usize = 12;
const TAG_LEN: usize = 16;

/// master_key 来源 + DEK 信封操作的统一抽象。
///
/// 实现者保证 [`master_key`](KeyProvider::master_key) 返回的 32 字节在 Drop 时擦除
/// (`Zeroizing`)。wrap/unwrap 默认基于本地 master_key 做信封,KMS 实现可覆写为云端调用。
pub trait KeyProvider: Send + Sync {
    /// 取 master_key(KEK)。32 字节,`Zeroizing` 包裹确保用完擦内存。
    ///
    /// **注意**:接真实 KMS 后,KEK 可能不出 HSM —— 那种部署应避免调用本方法做本地
    /// 派生,改走 [`wrap_dek`](KeyProvider::wrap_dek) / [`unwrap_dek`](KeyProvider::unwrap_dek)。
    /// 为兼容现有 `crypto::derive_user_key`(HKDF 本地派生),默认实现仍暴露 master_key。
    fn master_key(&self) -> PlatformResult<Zeroizing<[u8; 32]>>;

    /// 用 KEK 包一个 per-data DEK,产出可落库的 wrapped blob(KEK 永不落库)。
    ///
    /// 默认实现:用本地 master_key 跑 AES-256-GCM(本地信封)。KMS 实现覆写为
    /// `kms.encrypt`。`aad` 绑定上下文(如 `user=<id>&purpose=<p>`)防密文错位复用。
    fn wrap_dek(&self, dek: &[u8; 32], aad: &[u8]) -> PlatformResult<Vec<u8>> {
        let kek = self.master_key()?;
        envelope_seal(&kek, dek, aad)
    }

    /// 用 KEK 解开 wrapped DEK。默认实现:本地 AES-256-GCM 解封。KMS 实现覆写为
    /// `kms.decrypt`。返回 `Zeroizing` 的 32 字节 DEK。
    fn unwrap_dek(&self, wrapped: &[u8], aad: &[u8]) -> PlatformResult<Zeroizing<[u8; 32]>> {
        let kek = self.master_key()?;
        let dek = envelope_open(&kek, wrapped, aad)?;
        if dek.len() != 32 {
            return Err(PlatformError::validation("unwrap_dek: DEK 长度 != 32"));
        }
        let mut out = [0u8; 32];
        out.copy_from_slice(&dek);
        Ok(Zeroizing::new(out))
    }

    /// provider 标识(日志 / 运维诊断用)。
    fn provider_name(&self) -> &'static str;
}

// ───────────────────────── 本地信封原语(供 default wrap/unwrap 复用) ─────────────────────────

/// 用 32 字节 KEK + AES-256-GCM 封一段明文(`nonce || ct || tag`)。
pub fn envelope_seal(kek: &[u8; 32], plaintext: &[u8], aad: &[u8]) -> PlatformResult<Vec<u8>> {
    let key = Key::<Aes256Gcm>::from_slice(kek);
    let cipher = Aes256Gcm::new(key);
    let mut nonce_bytes = [0u8; NONCE_LEN];
    rand::thread_rng().fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);
    let ct = cipher
        .encrypt(nonce, Payload { msg: plaintext, aad })
        .map_err(|e| PlatformError::Other(anyhow::anyhow!("envelope seal 失败: {e}")))?;
    let mut out = Vec::with_capacity(NONCE_LEN + ct.len());
    out.extend_from_slice(&nonce_bytes);
    out.extend_from_slice(&ct);
    Ok(out)
}

/// 解封 [`envelope_seal`] 的输出。
pub fn envelope_open(kek: &[u8; 32], blob: &[u8], aad: &[u8]) -> PlatformResult<Vec<u8>> {
    if blob.len() < NONCE_LEN + TAG_LEN {
        return Err(PlatformError::validation("envelope blob too short"));
    }
    let key = Key::<Aes256Gcm>::from_slice(kek);
    let cipher = Aes256Gcm::new(key);
    let nonce = Nonce::from_slice(&blob[..NONCE_LEN]);
    cipher
        .decrypt(
            nonce,
            Payload {
                msg: &blob[NONCE_LEN..],
                aad,
            },
        )
        .map_err(|e| PlatformError::Other(anyhow::anyhow!("envelope open 失败: {e}")))
}

// ───────────────────────── EnvKeyProvider(默认,行为不变) ─────────────────────────

/// 现有 env/文件 master_key 逻辑的薄包装 —— 默认 provider,保持 `crypto.rs` 原行为。
///
/// 实际取值委托给 `crypto::load_master_key_raw`(env `RPG_MASTER_KEY` / 本地 fallback 文件),
/// 不改变 fail-fast 部署语义。
pub struct EnvKeyProvider;

impl KeyProvider for EnvKeyProvider {
    fn master_key(&self) -> PlatformResult<Zeroizing<[u8; 32]>> {
        let k = crate::crypto::load_master_key_raw()?;
        Ok(Zeroizing::new(k))
    }
    fn provider_name(&self) -> &'static str {
        "env"
    }
}

// ───────────────────────── KmsKeyProvider(KMS-ready 骨架) ─────────────────────────

/// KMS / Vault 信封 provider 骨架。
///
/// **结构完整、调用待接**:envelope(KEK 包 DEK)的接缝已就位,真实 KMS 网络调用留
/// `TODO(kms)`。生产接入步骤见本模块顶部「如何切到真实 KMS / Vault」。
///
/// - `endpoint` —— KMS / Vault HTTP 端点(env `RPG_KMS_ENDPOINT`)。
/// - `key_id` —— KMS CMK id / Vault transit key 名(env `RPG_KMS_KEY_ID`)。
/// - `fallback` —— 当前桩阶段:KMS 调用未接通时,退回本地 master_key 跑信封,
///   保证「配了 endpoint 但 SDK 没接」也能编译运行不炸(降级 + WARN)。
pub struct KmsKeyProvider {
    pub endpoint: String,
    pub key_id: Option<String>,
    fallback: EnvKeyProvider,
}

impl KmsKeyProvider {
    pub fn new(endpoint: String, key_id: Option<String>) -> Self {
        Self {
            endpoint,
            key_id,
            fallback: EnvKeyProvider,
        }
    }
}

impl KeyProvider for KmsKeyProvider {
    fn master_key(&self) -> PlatformResult<Zeroizing<[u8; 32]>> {
        // TODO(kms): 真实部署应改为 KMS `GenerateDataKey` 取 plaintext DEK,或彻底禁用
        // 本地 master_key(KEK 不出 HSM)。当前桩:委托 fallback 本地 master_key,保证
        // crypto::derive_user_key 的 HKDF 本地派生路径在「endpoint 已配但 SDK 未接」时仍可运行。
        self.fallback.master_key()
    }

    fn wrap_dek(&self, dek: &[u8; 32], aad: &[u8]) -> PlatformResult<Vec<u8>> {
        // TODO(kms): 替换为云端调用:
        //   AWS:   aws_sdk_kms encrypt(KeyId=self.key_id, Plaintext=dek) → CiphertextBlob
        //   Vault: POST {self.endpoint}/v1/transit/encrypt/<key_id>  body={plaintext: b64(dek)}
        tracing::warn!(
            target: "rpg_platform::infra::key_provider",
            endpoint = %self.endpoint,
            "KmsKeyProvider::wrap_dek 仍为本地信封桩(KMS SDK 未接)— 见模块 TODO(kms)"
        );
        let kek = self.master_key()?;
        envelope_seal(&kek, dek, aad)
    }

    fn unwrap_dek(&self, wrapped: &[u8], aad: &[u8]) -> PlatformResult<Zeroizing<[u8; 32]>> {
        // TODO(kms): 替换为云端调用:
        //   AWS:   aws_sdk_kms decrypt(CiphertextBlob=wrapped) → Plaintext(=dek)
        //   Vault: POST {self.endpoint}/v1/transit/decrypt/<key_id> body={ciphertext: wrapped}
        let kek = self.master_key()?;
        let dek = envelope_open(&kek, wrapped, aad)?;
        if dek.len() != 32 {
            return Err(PlatformError::validation("unwrap_dek: DEK 长度 != 32"));
        }
        let mut out = [0u8; 32];
        out.copy_from_slice(&dek);
        Ok(Zeroizing::new(out))
    }

    fn provider_name(&self) -> &'static str {
        "kms"
    }
}

// ───────────────────────── 工厂 ─────────────────────────

const KMS_ENDPOINT_ENV: &str = "RPG_KMS_ENDPOINT";
const KMS_KEY_ID_ENV: &str = "RPG_KMS_KEY_ID";

/// 进程级 KeyProvider。`RPG_KMS_ENDPOINT` 设了 → [`KmsKeyProvider`],否则 [`EnvKeyProvider`]。
pub static GLOBAL_PROVIDER: once_cell::sync::Lazy<Box<dyn KeyProvider>> =
    once_cell::sync::Lazy::new(default_provider);

/// 工厂:`RPG_KMS_ENDPOINT` 设了用 KMS provider,否则默认 Env provider(行为不变)。
pub fn default_provider() -> Box<dyn KeyProvider> {
    match std::env::var(KMS_ENDPOINT_ENV) {
        Ok(ep) if !ep.trim().is_empty() => {
            let key_id = std::env::var(KMS_KEY_ID_ENV)
                .ok()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty());
            tracing::info!(
                target: "rpg_platform::infra::key_provider",
                endpoint = %ep.trim(),
                "KeyProvider = KMS({KMS_ENDPOINT_ENV} 已设;wrap/unwrap 当前为本地信封桩,见 TODO(kms))"
            );
            Box::new(KmsKeyProvider::new(ep.trim().to_string(), key_id))
        }
        _ => Box::new(EnvKeyProvider),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set_test_master_key() {
        // 与 crypto 测试一致:全 0 的 64-hex master_key。
        std::env::set_var("RPG_MASTER_KEY", "0".repeat(64));
    }

    #[test]
    fn env_provider_returns_master_key() {
        set_test_master_key();
        let p = EnvKeyProvider;
        let k = p.master_key().unwrap();
        assert_eq!(k.len(), 32);
        assert_eq!(p.provider_name(), "env");
    }

    #[test]
    fn envelope_dek_roundtrip_via_env_provider() {
        set_test_master_key();
        let p = EnvKeyProvider;
        let dek = [9u8; 32];
        let aad = b"user=1&purpose=test";
        let wrapped = p.wrap_dek(&dek, aad).unwrap();
        // wrapped 不等于明文 DEK,且带 nonce+tag 开销。
        assert!(wrapped.len() > 32);
        let unwrapped = p.unwrap_dek(&wrapped, aad).unwrap();
        assert_eq!(*unwrapped, dek);
    }

    #[test]
    fn envelope_wrong_aad_fails() {
        set_test_master_key();
        let p = EnvKeyProvider;
        let dek = [3u8; 32];
        let wrapped = p.wrap_dek(&dek, b"aad-A").unwrap();
        // AAD 不匹配 → AEAD 鉴权失败。
        assert!(p.unwrap_dek(&wrapped, b"aad-B").is_err());
    }

    #[test]
    fn kms_provider_skeleton_roundtrips_via_local_envelope() {
        set_test_master_key();
        // KMS 桩阶段:wrap/unwrap 走本地信封,round-trip 必须成立(保证未接 SDK 也可用)。
        let p = KmsKeyProvider::new("http://kms.local:8200".to_string(), Some("cmk-1".to_string()));
        assert_eq!(p.provider_name(), "kms");
        let dek = [5u8; 32];
        let aad = b"envelope-aad";
        let wrapped = p.wrap_dek(&dek, aad).unwrap();
        let unwrapped = p.unwrap_dek(&wrapped, aad).unwrap();
        assert_eq!(*unwrapped, dek);
    }

    #[test]
    fn factory_defaults_to_env_provider_without_kms_endpoint() {
        std::env::remove_var(KMS_ENDPOINT_ENV);
        let p = default_provider();
        assert_eq!(p.provider_name(), "env");
    }
}
