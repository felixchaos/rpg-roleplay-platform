//! KeyProvider —— master_key 来源抽象 + envelope encryption(KMS-ready)。
//!
//! ## 动机
//! 旧版 `crypto.rs` 把 master_key 当成单一静态密钥(env / 文件直接读),无 envelope、
//! 无轮换路径。一旦想接 GCP KMS / HashiCorp Vault(密钥永不离开 HSM,只在云端做
//! wrap/unwrap),就得改一堆调用点。本模块把「拿 master_key」和「用 master_key 包/解
//! per-data DEK」抽象成 [`KeyProvider`]:
//!
//! - [`EnvKeyProvider`] —— 现有 env/文件逻辑的薄包装,**默认实现,行为不变**。
//! - [`GcpKmsProvider`] —— 调 GCP Cloud KMS REST API 的 `:encrypt` / `:decrypt`(KEK 留在 HSM)。
//! - [`VaultProvider`] —— 调 HashiCorp Vault transit engine `/v1/transit/encrypt/<key>`。
//!
//! ## Envelope encryption 模型
//! ```text
//!   KEK(Key-Encrypting-Key,master_key / KMS CMK)
//!     └── wrap_dek(dek)  → wrapped: Vec<u8>   // 落库,KEK 永不落库
//!         unwrap_dek(wrapped) → dek           // 用 DEK 真正加解密业务数据
//! ```
//! `EnvKeyProvider` 的 wrap/unwrap 用本地 master_key 跑 AES-256-GCM(本地信封);
//! `GcpKmsProvider` / `VaultProvider` 则把 wrap/unwrap 委托给云端 KMS(KEK 不出 HSM)。
//! 三者对调用方接口一致 —— crypto.rs 只认 [`KeyProvider`] trait。
//!
//! ## Wave 8-A 接入完成度
//! - REST 请求 body 构造 / 响应解析为 pub 纯函数([`gcp_encrypt_body`] /
//!   [`gcp_decrypt_body`] / [`vault_encrypt_body`] / [`vault_decrypt_body`] +
//!   `parse_*_response`),可在无网络的单测里直接验。
//! - 网络层:`reqwest::Client` async POST + 3 次指数退避 retry(100ms / 200ms / 400ms);
//!   网络错误 / 5xx 重试,4xx 立即失败。
//! - 同步 trait 兼容:KMS 网络调用通过专属 `tokio::Runtime`(多线程 1 worker)分发,
//!   `spawn + std::sync::mpsc::channel` 阻塞收结果 —— 无论调用方在同步还是异步上下文都可用,
//!   不会撞「nested runtime」panic(参考 `infra::rate_limit::RedisRateLimiter::connect`)。
//!
//! ## 装配
//! 环境变量 `KEY_PROVIDER` 决定 provider:
//! - 未设 / `local` / `env` → [`EnvKeyProvider`](默认,行为不变)
//! - `gcp_kms` → [`GcpKmsProvider`],读 `GCP_KMS_KEY_ID`(完整资源名)+ 可选 `GCP_KMS_ACCESS_TOKEN`
//! - `vault` → [`VaultProvider`],读 `VAULT_ADDR` / `VAULT_TOKEN` / `VAULT_TRANSIT_KEY`
//!
//! 兼容遗留:`RPG_KMS_ENDPOINT` 仍可触发 KMS 路径(优先级低于显式 `KEY_PROVIDER`)。

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Key, Nonce,
};
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::time::Duration;
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

// ───────────────────────── retry + 远程调用 (pub 纯函数) ─────────────────────────

/// 单次 retry 间的指数退避基准(乘 2^attempt)。Wave 8-A 固定 3 次:100ms / 200ms / 400ms。
const RETRY_BASE_DELAY: Duration = Duration::from_millis(100);
/// 最大 retry 次数(首次 + 重试,合计 3 次请求)。
const RETRY_MAX_ATTEMPTS: u32 = 3;
/// 单次 HTTP 请求超时,防 KMS 抖动把 axum 工作线程挂死。
const HTTP_TIMEOUT: Duration = Duration::from_secs(5);

/// GCP KMS `:encrypt` 请求 body —— 见 https://cloud.google.com/kms/docs/reference/rest 。
///
/// `plaintext` 字段 = base64(DEK);`additionalAuthenticatedData` = base64(AAD),
/// 用于 AEAD 上下文绑定(GCP KMS 支持的 AAD 字段)。
#[derive(Serialize, Debug)]
pub struct GcpEncryptBody {
    pub plaintext: String,
    #[serde(rename = "additionalAuthenticatedData", skip_serializing_if = "Option::is_none")]
    pub additional_authenticated_data: Option<String>,
}

/// GCP KMS `:encrypt` 响应。`ciphertext` 是 base64(wrapped DEK)。
#[derive(Deserialize, Debug)]
pub struct GcpEncryptResponse {
    pub ciphertext: String,
    #[allow(dead_code)]
    #[serde(default)]
    pub name: Option<String>,
}

/// GCP KMS `:decrypt` 请求 body。
#[derive(Serialize, Debug)]
pub struct GcpDecryptBody {
    pub ciphertext: String,
    #[serde(rename = "additionalAuthenticatedData", skip_serializing_if = "Option::is_none")]
    pub additional_authenticated_data: Option<String>,
}

/// GCP KMS `:decrypt` 响应。`plaintext` 是 base64(DEK)。
#[derive(Deserialize, Debug)]
pub struct GcpDecryptResponse {
    pub plaintext: String,
}

/// 构造 GCP KMS `:encrypt` 请求 body(纯函数,无网络)。
pub fn gcp_encrypt_body(dek: &[u8], aad: &[u8]) -> GcpEncryptBody {
    GcpEncryptBody {
        plaintext: B64.encode(dek),
        additional_authenticated_data: if aad.is_empty() {
            None
        } else {
            Some(B64.encode(aad))
        },
    }
}

/// 构造 GCP KMS `:decrypt` 请求 body(纯函数,无网络)。
/// `wrapped` 必须是 KMS encrypt 返回的原始 ciphertext(已 base64 之前的字节)—— 调用方负责持库 raw。
pub fn gcp_decrypt_body(wrapped: &[u8], aad: &[u8]) -> GcpDecryptBody {
    GcpDecryptBody {
        ciphertext: B64.encode(wrapped),
        additional_authenticated_data: if aad.is_empty() {
            None
        } else {
            Some(B64.encode(aad))
        },
    }
}

/// 解析 GCP KMS `:encrypt` 响应,返回 wrapped DEK 原始字节(供落库)。
pub fn parse_gcp_encrypt_response(body: &str) -> PlatformResult<Vec<u8>> {
    let resp: GcpEncryptResponse = serde_json::from_str(body)
        .map_err(|e| PlatformError::validation(format!("GCP KMS encrypt 响应 JSON 解析失败: {e}")))?;
    B64.decode(resp.ciphertext.as_bytes())
        .map_err(|e| PlatformError::validation(format!("GCP KMS ciphertext base64 解码失败: {e}")))
}

/// 解析 GCP KMS `:decrypt` 响应,返回明文 DEK 字节。
pub fn parse_gcp_decrypt_response(body: &str) -> PlatformResult<Vec<u8>> {
    let resp: GcpDecryptResponse = serde_json::from_str(body)
        .map_err(|e| PlatformError::validation(format!("GCP KMS decrypt 响应 JSON 解析失败: {e}")))?;
    B64.decode(resp.plaintext.as_bytes())
        .map_err(|e| PlatformError::validation(format!("GCP KMS plaintext base64 解码失败: {e}")))
}

/// Vault transit `/encrypt/<key>` 请求 body(`plaintext` = base64(DEK),`context` = base64(AAD))。
///
/// 见 https://developer.hashicorp.com/vault/api-docs/secret/transit#encrypt-data 。
/// AAD 通过 transit `context` 绑定 —— key 必须是 derived(`derived=true`)或 datakey 模式。
/// 若 transit key 未启用 derivation,可省略 `context`(传 None)。
#[derive(Serialize, Debug)]
pub struct VaultEncryptBody {
    pub plaintext: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<String>,
}

/// Vault transit encrypt 响应:`data.ciphertext` 形如 `"vault:v1:<base64>"`(整串原样持库)。
#[derive(Deserialize, Debug)]
pub struct VaultEncryptResponse {
    pub data: VaultEncryptData,
}
#[derive(Deserialize, Debug)]
pub struct VaultEncryptData {
    pub ciphertext: String,
}

/// Vault transit `/decrypt/<key>` 请求 body。
#[derive(Serialize, Debug)]
pub struct VaultDecryptBody {
    pub ciphertext: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<String>,
}

/// Vault transit decrypt 响应:`data.plaintext` = base64(DEK)。
#[derive(Deserialize, Debug)]
pub struct VaultDecryptResponse {
    pub data: VaultDecryptData,
}
#[derive(Deserialize, Debug)]
pub struct VaultDecryptData {
    pub plaintext: String,
}

/// 构造 Vault transit encrypt 请求 body(纯函数)。
pub fn vault_encrypt_body(dek: &[u8], aad: &[u8]) -> VaultEncryptBody {
    VaultEncryptBody {
        plaintext: B64.encode(dek),
        context: if aad.is_empty() {
            None
        } else {
            Some(B64.encode(aad))
        },
    }
}

/// 构造 Vault transit decrypt 请求 body。`wrapped` 是 Vault 返回的 `vault:v1:...` 串原文。
pub fn vault_decrypt_body(wrapped: &str, aad: &[u8]) -> VaultDecryptBody {
    VaultDecryptBody {
        ciphertext: wrapped.to_string(),
        context: if aad.is_empty() {
            None
        } else {
            Some(B64.encode(aad))
        },
    }
}

/// 解析 Vault transit encrypt 响应,返回 `vault:v1:...` ciphertext 串(供落库;UTF-8 字节)。
pub fn parse_vault_encrypt_response(body: &str) -> PlatformResult<String> {
    let resp: VaultEncryptResponse = serde_json::from_str(body)
        .map_err(|e| PlatformError::validation(format!("Vault encrypt 响应 JSON 解析失败: {e}")))?;
    if !resp.data.ciphertext.starts_with("vault:") {
        return Err(PlatformError::validation(format!(
            "Vault ciphertext 前缀异常: {}",
            resp.data.ciphertext
        )));
    }
    Ok(resp.data.ciphertext)
}

/// 解析 Vault transit decrypt 响应,返回明文 DEK 字节。
pub fn parse_vault_decrypt_response(body: &str) -> PlatformResult<Vec<u8>> {
    let resp: VaultDecryptResponse = serde_json::from_str(body)
        .map_err(|e| PlatformError::validation(format!("Vault decrypt 响应 JSON 解析失败: {e}")))?;
    B64.decode(resp.data.plaintext.as_bytes())
        .map_err(|e| PlatformError::validation(format!("Vault plaintext base64 解码失败: {e}")))
}

// ───────────────────────── 专属 KMS Runtime(供同步 trait 阻塞收结果) ─────────────────────────
//
// crypto.rs 大量同步路径(`derive_user_key` 等)直接调 `KeyProvider::wrap_dek`,但 reqwest
// 是 async-only。直接在调用线程上 `Runtime::new().block_on(...)` 在已处于 tokio runtime 的
// async 上下文里会撞 panic(参考 rate_limit.rs 的同款注释)。
//
// 方案:**进程级专属多线程 runtime**(1 worker),用 `spawn + std::sync::mpsc` 把异步结果
// 「投递」回调用线程 —— `spawn` 不阻塞,`recv` 是纯同步阻塞,在任何上下文都安全。

static KMS_RUNTIME: once_cell::sync::Lazy<Option<tokio::runtime::Runtime>> =
    once_cell::sync::Lazy::new(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .thread_name("rpg-kms-rt")
            .enable_all()
            .build()
            .map_err(|e| {
                tracing::error!(
                    target: "rpg_platform::infra::key_provider",
                    error = %e,
                    "KMS 专属 runtime 创建失败 —— KMS provider 调用将全部 fail"
                );
                e
            })
            .ok()
    });

/// 在专属 runtime 上跑 future,同步收结果。任意上下文(sync / async, current_thread / multi_thread)
/// 均安全,无 nested-runtime panic。
fn run_blocking_on_kms_rt<F>(fut: F) -> PlatformResult<F::Output>
where
    F: std::future::Future + Send + 'static,
    F::Output: Send + 'static,
{
    let rt = KMS_RUNTIME
        .as_ref()
        .ok_or_else(|| PlatformError::Other(anyhow::anyhow!("KMS 专属 runtime 不可用")))?;
    let (tx, rx) = std::sync::mpsc::channel();
    rt.spawn(async move {
        let _ = tx.send(fut.await);
    });
    rx.recv()
        .map_err(|e| PlatformError::Other(anyhow::anyhow!("KMS runtime channel 关闭: {e}")))
}

/// 单次 POST + retry 包装。`should_retry` 决定状态码是否进入下一轮(5xx / 网络错误 yes,4xx no)。
async fn post_with_retry(
    client: &reqwest::Client,
    url: &str,
    headers: Vec<(&str, String)>,
    body: serde_json::Value,
) -> PlatformResult<String> {
    let mut last_err: Option<String> = None;
    for attempt in 0..RETRY_MAX_ATTEMPTS {
        if attempt > 0 {
            // 100ms / 200ms / 400ms 指数退避(attempt 从 1 起)。
            let delay = RETRY_BASE_DELAY * (1u32 << (attempt - 1));
            tokio::time::sleep(delay).await;
        }
        let mut req = client.post(url).json(&body);
        for (k, v) in &headers {
            req = req.header(*k, v);
        }
        match req.send().await {
            Ok(resp) => {
                let status = resp.status();
                if status.is_success() {
                    return resp.text().await.map_err(|e| {
                        PlatformError::Other(anyhow::anyhow!("KMS 响应 body 读取失败: {e}"))
                    });
                } else if status.is_server_error() {
                    last_err = Some(format!("HTTP {}: {}", status, resp.text().await.unwrap_or_default()));
                    tracing::warn!(
                        target: "rpg_platform::infra::key_provider",
                        attempt = attempt + 1,
                        status = %status,
                        "KMS 请求 5xx,准备 retry"
                    );
                    continue;
                } else {
                    // 4xx 立即失败(配置 / 鉴权 / 输入错误,retry 无意义)。
                    return Err(PlatformError::Other(anyhow::anyhow!(
                        "KMS 请求失败 HTTP {}: {}",
                        status,
                        resp.text().await.unwrap_or_default()
                    )));
                }
            }
            Err(e) => {
                last_err = Some(format!("网络错误: {e}"));
                tracing::warn!(
                    target: "rpg_platform::infra::key_provider",
                    attempt = attempt + 1,
                    error = %e,
                    "KMS 网络错误,准备 retry"
                );
                continue;
            }
        }
    }
    Err(PlatformError::Other(anyhow::anyhow!(
        "KMS 请求 {} 次仍失败: {}",
        RETRY_MAX_ATTEMPTS,
        last_err.unwrap_or_else(|| "unknown".to_string())
    )))
}

// ───────────────────────── GcpKmsProvider ─────────────────────────

/// GCP Cloud KMS provider。调 `:encrypt` / `:decrypt` REST endpoint,KEK 永不出 HSM。
///
/// **认证**:`access_token` 必填(短期 OAuth2 bearer token,部署侧用 workload identity /
/// metadata server 周期刷新后写入 env `GCP_KMS_ACCESS_TOKEN`)。本 provider 不内置
/// `yup-oauth2` 流程 —— 部署用 sidecar 刷 token,简化可测性。
///
/// **endpoint_base**:覆写 base URL 用,默认 `https://cloudkms.googleapis.com/v1`。单测里
/// 指向本地 mock server。
///
/// **key_id**:完整 resource name,形如
/// `projects/<p>/locations/<l>/keyRings/<kr>/cryptoKeys/<k>`。
pub struct GcpKmsProvider {
    pub key_id: String,
    pub access_token: String,
    pub endpoint_base: String,
    pub client: reqwest::Client,
    /// fallback EnvKeyProvider 用于 `master_key()` —— GCP KMS 路径下,本地 HKDF 派生仍需要
    /// 一个 master_key(crypto::derive_user_key)。生产部署里 RPG_MASTER_KEY 仍要设。
    fallback: EnvKeyProvider,
}

impl GcpKmsProvider {
    /// 默认 endpoint 指向 GCP 公共 API。
    pub const DEFAULT_ENDPOINT: &'static str = "https://cloudkms.googleapis.com/v1";

    pub fn new(key_id: String, access_token: String, endpoint_base: Option<String>) -> PlatformResult<Self> {
        if key_id.trim().is_empty() {
            return Err(PlatformError::validation("GcpKmsProvider: key_id 不能为空"));
        }
        if access_token.trim().is_empty() {
            return Err(PlatformError::validation("GcpKmsProvider: access_token 不能为空"));
        }
        let client = reqwest::Client::builder()
            .timeout(HTTP_TIMEOUT)
            .build()
            .map_err(|e| PlatformError::Other(anyhow::anyhow!("reqwest::Client 构建失败: {e}")))?;
        Ok(Self {
            key_id,
            access_token,
            endpoint_base: endpoint_base.unwrap_or_else(|| Self::DEFAULT_ENDPOINT.to_string()),
            client,
            fallback: EnvKeyProvider,
        })
    }

    fn encrypt_url(&self) -> String {
        format!("{}/{}:encrypt", self.endpoint_base.trim_end_matches('/'), self.key_id)
    }
    fn decrypt_url(&self) -> String {
        format!("{}/{}:decrypt", self.endpoint_base.trim_end_matches('/'), self.key_id)
    }
}

impl KeyProvider for GcpKmsProvider {
    fn master_key(&self) -> PlatformResult<Zeroizing<[u8; 32]>> {
        // GCP KMS 模式仍委托本地 master_key 给 HKDF 派生路径用;真实部署里 RPG_MASTER_KEY
        // 应设为一个独立 secret(不是 KEK 本身),与 KMS-wrapped DEK 解耦。
        self.fallback.master_key()
    }

    fn wrap_dek(&self, dek: &[u8; 32], aad: &[u8]) -> PlatformResult<Vec<u8>> {
        let url = self.encrypt_url();
        let token = format!("Bearer {}", self.access_token);
        let body_struct = gcp_encrypt_body(dek, aad);
        let body = serde_json::to_value(&body_struct)
            .map_err(|e| PlatformError::Other(anyhow::anyhow!("GCP encrypt body 序列化失败: {e}")))?;
        let client = self.client.clone();
        let text = run_blocking_on_kms_rt(async move {
            post_with_retry(
                &client,
                &url,
                vec![("Authorization", token), ("Content-Type", "application/json".to_string())],
                body,
            )
            .await
        })??;
        parse_gcp_encrypt_response(&text)
    }

    fn unwrap_dek(&self, wrapped: &[u8], aad: &[u8]) -> PlatformResult<Zeroizing<[u8; 32]>> {
        let url = self.decrypt_url();
        let token = format!("Bearer {}", self.access_token);
        let body_struct = gcp_decrypt_body(wrapped, aad);
        let body = serde_json::to_value(&body_struct)
            .map_err(|e| PlatformError::Other(anyhow::anyhow!("GCP decrypt body 序列化失败: {e}")))?;
        let client = self.client.clone();
        let text = run_blocking_on_kms_rt(async move {
            post_with_retry(
                &client,
                &url,
                vec![("Authorization", token), ("Content-Type", "application/json".to_string())],
                body,
            )
            .await
        })??;
        let plain = parse_gcp_decrypt_response(&text)?;
        if plain.len() != 32 {
            return Err(PlatformError::validation(format!(
                "GCP KMS unwrap_dek: DEK 长度 != 32(实际 {})",
                plain.len()
            )));
        }
        let mut out = [0u8; 32];
        out.copy_from_slice(&plain);
        Ok(Zeroizing::new(out))
    }

    fn provider_name(&self) -> &'static str {
        "gcp_kms"
    }
}

// ───────────────────────── VaultProvider ─────────────────────────

/// HashiCorp Vault transit engine provider。`/v1/transit/encrypt|decrypt/<key>`。
///
/// `wrapped DEK` 是 Vault 返回的字符串 `"vault:v1:<base64>"` —— 全串持库,Vault 自带版本号
/// 支持 transit key rotation 时旧密文仍可解。本 provider 在 `wrap_dek` 返回这串的 **UTF-8 字节**,
/// 在 `unwrap_dek` 接收同样字节并恢复成 string 发回 decrypt。
pub struct VaultProvider {
    pub addr: String,
    pub token: String,
    pub transit_key: String,
    pub client: reqwest::Client,
    fallback: EnvKeyProvider,
}

impl VaultProvider {
    pub fn new(addr: String, token: String, transit_key: String) -> PlatformResult<Self> {
        if addr.trim().is_empty() {
            return Err(PlatformError::validation("VaultProvider: addr 不能为空"));
        }
        if token.trim().is_empty() {
            return Err(PlatformError::validation("VaultProvider: token 不能为空"));
        }
        if transit_key.trim().is_empty() {
            return Err(PlatformError::validation("VaultProvider: transit_key 不能为空"));
        }
        let client = reqwest::Client::builder()
            .timeout(HTTP_TIMEOUT)
            .build()
            .map_err(|e| PlatformError::Other(anyhow::anyhow!("reqwest::Client 构建失败: {e}")))?;
        Ok(Self {
            addr,
            token,
            transit_key,
            client,
            fallback: EnvKeyProvider,
        })
    }

    fn encrypt_url(&self) -> String {
        format!(
            "{}/v1/transit/encrypt/{}",
            self.addr.trim_end_matches('/'),
            self.transit_key
        )
    }
    fn decrypt_url(&self) -> String {
        format!(
            "{}/v1/transit/decrypt/{}",
            self.addr.trim_end_matches('/'),
            self.transit_key
        )
    }
}

impl KeyProvider for VaultProvider {
    fn master_key(&self) -> PlatformResult<Zeroizing<[u8; 32]>> {
        // 同 GCP:仍走本地 master_key 给 HKDF 派生路径用。
        self.fallback.master_key()
    }

    fn wrap_dek(&self, dek: &[u8; 32], aad: &[u8]) -> PlatformResult<Vec<u8>> {
        let url = self.encrypt_url();
        let token = self.token.clone();
        let body_struct = vault_encrypt_body(dek, aad);
        let body = serde_json::to_value(&body_struct)
            .map_err(|e| PlatformError::Other(anyhow::anyhow!("Vault encrypt body 序列化失败: {e}")))?;
        let client = self.client.clone();
        let text = run_blocking_on_kms_rt(async move {
            post_with_retry(
                &client,
                &url,
                vec![
                    ("X-Vault-Token", token),
                    ("Content-Type", "application/json".to_string()),
                ],
                body,
            )
            .await
        })??;
        let ciphertext = parse_vault_encrypt_response(&text)?;
        Ok(ciphertext.into_bytes())
    }

    fn unwrap_dek(&self, wrapped: &[u8], aad: &[u8]) -> PlatformResult<Zeroizing<[u8; 32]>> {
        let wrapped_str = std::str::from_utf8(wrapped).map_err(|e| {
            PlatformError::validation(format!("Vault wrapped 必须是 UTF-8 (vault:v1:...): {e}"))
        })?;
        let url = self.decrypt_url();
        let token = self.token.clone();
        let body_struct = vault_decrypt_body(wrapped_str, aad);
        let body = serde_json::to_value(&body_struct)
            .map_err(|e| PlatformError::Other(anyhow::anyhow!("Vault decrypt body 序列化失败: {e}")))?;
        let client = self.client.clone();
        let text = run_blocking_on_kms_rt(async move {
            post_with_retry(
                &client,
                &url,
                vec![
                    ("X-Vault-Token", token),
                    ("Content-Type", "application/json".to_string()),
                ],
                body,
            )
            .await
        })??;
        let plain = parse_vault_decrypt_response(&text)?;
        if plain.len() != 32 {
            return Err(PlatformError::validation(format!(
                "Vault unwrap_dek: DEK 长度 != 32(实际 {})",
                plain.len()
            )));
        }
        let mut out = [0u8; 32];
        out.copy_from_slice(&plain);
        Ok(Zeroizing::new(out))
    }

    fn provider_name(&self) -> &'static str {
        "vault"
    }
}

// ───────────────────────── 工厂 ─────────────────────────

/// Wave 8-A 新增:显式选 provider 类型。优先级高于历史 `RPG_KMS_ENDPOINT`。
const KEY_PROVIDER_ENV: &str = "KEY_PROVIDER";
/// 历史 env(Wave 7- 之前):非空时触发 KMS 路径(现在的入口已退化为 EnvKeyProvider + WARN)。
const KMS_ENDPOINT_ENV: &str = "RPG_KMS_ENDPOINT";
/// 历史 env:KMS CMK id / Vault transit key 名(GcpKmsProvider 仍读它作为 key_id)。
const KMS_KEY_ID_ENV: &str = "RPG_KMS_KEY_ID";

// GCP KMS 专属 env。
const GCP_KMS_KEY_ID_ENV: &str = "GCP_KMS_KEY_ID";
const GCP_KMS_ACCESS_TOKEN_ENV: &str = "GCP_KMS_ACCESS_TOKEN";
const GCP_KMS_ENDPOINT_ENV: &str = "GCP_KMS_ENDPOINT";

// Vault 专属 env。
const VAULT_ADDR_ENV: &str = "VAULT_ADDR";
const VAULT_TOKEN_ENV: &str = "VAULT_TOKEN";
const VAULT_TRANSIT_KEY_ENV: &str = "VAULT_TRANSIT_KEY";

/// 进程级 KeyProvider。`KEY_PROVIDER` env 决定 provider 类型,默认 [`EnvKeyProvider`]。
pub static GLOBAL_PROVIDER: once_cell::sync::Lazy<Box<dyn KeyProvider>> =
    once_cell::sync::Lazy::new(default_provider);

fn env_nonempty(key: &str) -> Option<String> {
    std::env::var(key).ok().and_then(|v| {
        let t = v.trim().to_string();
        if t.is_empty() {
            None
        } else {
            Some(t)
        }
    })
}

/// 工厂:按 `KEY_PROVIDER` env 选 provider。失败 / 缺配置时降级 EnvKeyProvider + WARN,
/// 保证进程不因 KMS 配置错误而启动失败(保留 fail-fast 由 master_key 加载侧负责)。
pub fn default_provider() -> Box<dyn KeyProvider> {
    let kind = env_nonempty(KEY_PROVIDER_ENV)
        .map(|s| s.to_ascii_lowercase())
        .unwrap_or_else(|| {
            // 历史兼容:RPG_KMS_ENDPOINT 设了但没显式声明 KEY_PROVIDER → WARN 并 fallback Env。
            if env_nonempty(KMS_ENDPOINT_ENV).is_some() {
                tracing::warn!(
                    target: "rpg_platform::infra::key_provider",
                    "RPG_KMS_ENDPOINT 已设但未指定 KEY_PROVIDER —— Wave 8-A 后请显式设 \
                     KEY_PROVIDER=gcp_kms|vault,否则按 local 走"
                );
            }
            "local".to_string()
        });
    match kind.as_str() {
        "gcp_kms" | "gcp" | "gcpkms" => {
            let key_id = env_nonempty(GCP_KMS_KEY_ID_ENV).or_else(|| env_nonempty(KMS_KEY_ID_ENV));
            let token = env_nonempty(GCP_KMS_ACCESS_TOKEN_ENV);
            let endpoint = env_nonempty(GCP_KMS_ENDPOINT_ENV);
            match (key_id, token) {
                (Some(k), Some(t)) => match GcpKmsProvider::new(k, t, endpoint) {
                    Ok(p) => {
                        tracing::info!(
                            target: "rpg_platform::infra::key_provider",
                            key_id = %p.key_id,
                            endpoint = %p.endpoint_base,
                            "KeyProvider = gcp_kms"
                        );
                        Box::new(p)
                    }
                    Err(e) => {
                        tracing::error!(
                            target: "rpg_platform::infra::key_provider",
                            error = %e,
                            "GcpKmsProvider 初始化失败,降级 EnvKeyProvider"
                        );
                        Box::new(EnvKeyProvider)
                    }
                },
                _ => {
                    tracing::error!(
                        target: "rpg_platform::infra::key_provider",
                        "KEY_PROVIDER=gcp_kms 但缺 GCP_KMS_KEY_ID / GCP_KMS_ACCESS_TOKEN —— 降级 EnvKeyProvider"
                    );
                    Box::new(EnvKeyProvider)
                }
            }
        }
        "vault" | "hcvault" => {
            let addr = env_nonempty(VAULT_ADDR_ENV);
            let token = env_nonempty(VAULT_TOKEN_ENV);
            let key = env_nonempty(VAULT_TRANSIT_KEY_ENV).or_else(|| env_nonempty(KMS_KEY_ID_ENV));
            match (addr, token, key) {
                (Some(a), Some(t), Some(k)) => match VaultProvider::new(a, t, k) {
                    Ok(p) => {
                        tracing::info!(
                            target: "rpg_platform::infra::key_provider",
                            addr = %p.addr,
                            transit_key = %p.transit_key,
                            "KeyProvider = vault"
                        );
                        Box::new(p)
                    }
                    Err(e) => {
                        tracing::error!(
                            target: "rpg_platform::infra::key_provider",
                            error = %e,
                            "VaultProvider 初始化失败,降级 EnvKeyProvider"
                        );
                        Box::new(EnvKeyProvider)
                    }
                },
                _ => {
                    tracing::error!(
                        target: "rpg_platform::infra::key_provider",
                        "KEY_PROVIDER=vault 但缺 VAULT_ADDR / VAULT_TOKEN / VAULT_TRANSIT_KEY —— 降级 EnvKeyProvider"
                    );
                    Box::new(EnvKeyProvider)
                }
            }
        }
        "local" | "env" | "" => Box::new(EnvKeyProvider),
        other => {
            tracing::error!(
                target: "rpg_platform::infra::key_provider",
                value = %other,
                "未知 KEY_PROVIDER 值,降级 EnvKeyProvider"
            );
            Box::new(EnvKeyProvider)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// 全部 env-mutating 测试串行,避免 env race(rpg-platform 其他测试同款约定)。
    static ENV_LOCK: once_cell::sync::Lazy<Mutex<()>> = once_cell::sync::Lazy::new(|| Mutex::new(()));

    fn set_test_master_key() {
        std::env::set_var("RPG_MASTER_KEY", "0".repeat(64));
    }

    #[test]
    fn env_provider_returns_master_key() {
        let _g = ENV_LOCK.lock().unwrap();
        set_test_master_key();
        let p = EnvKeyProvider;
        let k = p.master_key().unwrap();
        assert_eq!(k.len(), 32);
        assert_eq!(p.provider_name(), "env");
    }

    #[test]
    fn envelope_dek_roundtrip_via_env_provider() {
        let _g = ENV_LOCK.lock().unwrap();
        set_test_master_key();
        let p = EnvKeyProvider;
        let dek = [9u8; 32];
        let aad = b"user=1&purpose=test";
        let wrapped = p.wrap_dek(&dek, aad).unwrap();
        assert!(wrapped.len() > 32);
        let unwrapped = p.unwrap_dek(&wrapped, aad).unwrap();
        assert_eq!(*unwrapped, dek);
    }

    #[test]
    fn envelope_wrong_aad_fails() {
        let _g = ENV_LOCK.lock().unwrap();
        set_test_master_key();
        let p = EnvKeyProvider;
        let dek = [3u8; 32];
        let wrapped = p.wrap_dek(&dek, b"aad-A").unwrap();
        assert!(p.unwrap_dek(&wrapped, b"aad-B").is_err());
    }

    // ───────── 纯函数 body / response 测试(无网络) ─────────

    #[test]
    fn gcp_encrypt_body_shape() {
        let dek = [7u8; 32];
        let body = gcp_encrypt_body(&dek, b"ctx");
        let j = serde_json::to_value(&body).unwrap();
        assert_eq!(j["plaintext"], B64.encode(dek));
        assert_eq!(j["additionalAuthenticatedData"], B64.encode(b"ctx"));
        // 空 AAD 时字段省略。
        let body_no_aad = gcp_encrypt_body(&dek, b"");
        let j2 = serde_json::to_value(&body_no_aad).unwrap();
        assert!(j2.get("additionalAuthenticatedData").is_none());
    }

    #[test]
    fn gcp_decrypt_response_parsed() {
        let dek = [5u8; 32];
        let body = serde_json::json!({ "plaintext": B64.encode(dek) }).to_string();
        let out = parse_gcp_decrypt_response(&body).unwrap();
        assert_eq!(out, dek);
    }

    #[test]
    fn vault_encrypt_body_and_response() {
        let dek = [1u8; 32];
        let body = vault_encrypt_body(&dek, b"ctx");
        let j = serde_json::to_value(&body).unwrap();
        assert_eq!(j["plaintext"], B64.encode(dek));
        assert_eq!(j["context"], B64.encode(b"ctx"));

        let resp_body = serde_json::json!({
            "data": { "ciphertext": "vault:v1:abcdef==" }
        })
        .to_string();
        let ct = parse_vault_encrypt_response(&resp_body).unwrap();
        assert_eq!(ct, "vault:v1:abcdef==");

        // 前缀错误立即报错(防 KMS 切了实现仍持错误密文)。
        let bad = serde_json::json!({ "data": { "ciphertext": "notvault:xyz" } }).to_string();
        assert!(parse_vault_encrypt_response(&bad).is_err());
    }

    #[test]
    fn vault_decrypt_response_parsed() {
        let dek = [2u8; 32];
        let body = serde_json::json!({
            "data": { "plaintext": B64.encode(dek) }
        })
        .to_string();
        let out = parse_vault_decrypt_response(&body).unwrap();
        assert_eq!(out, dek);
    }

    // ───────── HTTP mock(tokio::net::TcpListener)端到端 + retry ─────────
    //
    // 不引入 wiremock,用纯 tokio TcpListener 起一行 HTTP server:
    // - 第 1 / 2 次返回 500(触发 retry)
    // - 第 3 次返回 200 + 合法 GCP encrypt 响应
    // 验:同步 wrap_dek 经 retry 拿到 ciphertext;请求 body 含正确 plaintext base64。

    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    /// 把单个 HTTP/1.1 请求读到 \r\n\r\n + Content-Length 字节,返回 (headers, body)。
    async fn read_request(stream: &mut tokio::net::TcpStream) -> (String, String) {
        let mut buf = Vec::with_capacity(4096);
        let mut tmp = [0u8; 1024];
        // 先读 header
        loop {
            let n = stream.read(&mut tmp).await.unwrap();
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&tmp[..n]);
            if buf.windows(4).any(|w| w == b"\r\n\r\n") {
                break;
            }
        }
        let s = String::from_utf8_lossy(&buf).to_string();
        let header_end = s.find("\r\n\r\n").unwrap_or(s.len());
        let headers = s[..header_end].to_string();
        let body_start_in_buf = header_end + 4;

        // Content-Length 解析
        let mut content_len: usize = 0;
        for line in headers.lines() {
            if let Some(rest) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                content_len = rest.trim().parse().unwrap_or(0);
            }
        }
        let mut body = if buf.len() > body_start_in_buf {
            buf[body_start_in_buf..].to_vec()
        } else {
            Vec::new()
        };
        while body.len() < content_len {
            let n = stream.read(&mut tmp).await.unwrap();
            if n == 0 {
                break;
            }
            body.extend_from_slice(&tmp[..n]);
        }
        let body_str = String::from_utf8_lossy(&body[..content_len.min(body.len())]).to_string();
        (headers, body_str)
    }

    /// 启一个 mock server,返回 (base_url, captured_bodies, attempt_count_handle, shutdown_handle)。
    /// `responses` 是按顺序逐请求返回的 (status, body)。耗尽后默认 500。
    async fn spawn_mock_server(
        responses: Vec<(u16, String)>,
    ) -> (String, Arc<Mutex<Vec<String>>>, Arc<AtomicUsize>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let captured: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let attempts: Arc<AtomicUsize> = Arc::new(AtomicUsize::new(0));
        let captured_c = captured.clone();
        let attempts_c = attempts.clone();
        tokio::spawn(async move {
            let responses = responses;
            loop {
                let (mut stream, _) = match listener.accept().await {
                    Ok(s) => s,
                    Err(_) => break,
                };
                let captured = captured_c.clone();
                let attempts = attempts_c.clone();
                let responses = responses.clone();
                tokio::spawn(async move {
                    let (_headers, body) = read_request(&mut stream).await;
                    let idx = attempts.fetch_add(1, Ordering::SeqCst);
                    captured.lock().unwrap().push(body);
                    let (status, resp_body) = responses
                        .get(idx)
                        .cloned()
                        .unwrap_or((500, "{\"error\":\"exhausted\"}".to_string()));
                    let reason = match status {
                        200 => "OK",
                        500 => "Internal Server Error",
                        _ => "Other",
                    };
                    let resp = format!(
                        "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        status,
                        reason,
                        resp_body.len(),
                        resp_body
                    );
                    let _ = stream.write_all(resp.as_bytes()).await;
                    let _ = stream.shutdown().await;
                });
            }
        });
        (format!("http://{}", addr), captured, attempts)
    }

    #[test]
    fn gcp_wrap_dek_retries_then_succeeds_via_mock() {
        let _g = ENV_LOCK.lock().unwrap();
        set_test_master_key();
        // 用一个独立 tokio runtime 启 mock(测试自己的),与 KMS_RUNTIME 解耦。
        let test_rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .unwrap();
        let dek = [42u8; 32];
        let success_body = serde_json::json!({
            "ciphertext": B64.encode(b"wrapped-dek-bytes-from-kms"),
            "name": "projects/p/locations/l/keyRings/kr/cryptoKeys/k/cryptoKeyVersions/1"
        })
        .to_string();
        let (base_url, captured, attempts) = test_rt.block_on(spawn_mock_server(vec![
            (500, "{\"err\":\"transient\"}".to_string()),
            (500, "{\"err\":\"transient\"}".to_string()),
            (200, success_body),
        ]));

        let p = GcpKmsProvider::new(
            "projects/p/locations/l/keyRings/kr/cryptoKeys/k".to_string(),
            "fake-token".to_string(),
            Some(base_url),
        )
        .unwrap();

        let aad = b"user=1&purpose=test";
        let wrapped = p.wrap_dek(&dek, aad).expect("应在第 3 次成功");
        assert_eq!(wrapped, b"wrapped-dek-bytes-from-kms");
        assert_eq!(attempts.load(Ordering::SeqCst), 3, "应 retry 2 次共 3 请求");

        // 验证 request body 含正确 base64 plaintext + AAD。
        let bodies = captured.lock().unwrap();
        for body in bodies.iter() {
            let v: serde_json::Value = serde_json::from_str(body).unwrap();
            assert_eq!(v["plaintext"], B64.encode(dek));
            assert_eq!(v["additionalAuthenticatedData"], B64.encode(aad));
        }
    }

    #[test]
    fn gcp_wrap_dek_4xx_fails_without_retry() {
        let _g = ENV_LOCK.lock().unwrap();
        set_test_master_key();
        let test_rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .unwrap();
        let (base_url, _captured, attempts) = test_rt.block_on(spawn_mock_server(vec![
            (400, "{\"error\":\"bad request\"}".to_string()),
            (200, "{}".to_string()),
        ]));

        let p = GcpKmsProvider::new(
            "projects/p/locations/l/keyRings/kr/cryptoKeys/k".to_string(),
            "fake-token".to_string(),
            Some(base_url),
        )
        .unwrap();
        let dek = [1u8; 32];
        let err = p.wrap_dek(&dek, b"aad").unwrap_err();
        assert!(format!("{err}").contains("400"));
        assert_eq!(attempts.load(Ordering::SeqCst), 1, "4xx 不应 retry");
    }

    #[test]
    fn vault_wrap_dek_via_mock_succeeds() {
        let _g = ENV_LOCK.lock().unwrap();
        set_test_master_key();
        let test_rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .unwrap();
        let success_body = serde_json::json!({
            "data": { "ciphertext": "vault:v1:AAAA==" }
        })
        .to_string();
        let (base_url, captured, attempts) = test_rt.block_on(spawn_mock_server(vec![
            (200, success_body),
        ]));

        let p = VaultProvider::new(
            base_url,
            "vault-token".to_string(),
            "rpg-key".to_string(),
        )
        .unwrap();
        let dek = [9u8; 32];
        let wrapped = p.wrap_dek(&dek, b"ctx").unwrap();
        assert_eq!(wrapped, b"vault:v1:AAAA==");
        assert_eq!(attempts.load(Ordering::SeqCst), 1);

        let bodies = captured.lock().unwrap();
        let v: serde_json::Value = serde_json::from_str(&bodies[0]).unwrap();
        assert_eq!(v["plaintext"], B64.encode(dek));
        assert_eq!(v["context"], B64.encode(b"ctx"));
    }

    // ───────── 工厂派发 ─────────

    #[test]
    fn factory_defaults_to_env_provider_without_explicit_key_provider() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::remove_var(KEY_PROVIDER_ENV);
        std::env::remove_var(KMS_ENDPOINT_ENV);
        let p = default_provider();
        assert_eq!(p.provider_name(), "env");
    }

    #[test]
    fn factory_gcp_kms_missing_token_falls_back_to_env() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::set_var(KEY_PROVIDER_ENV, "gcp_kms");
        std::env::remove_var(GCP_KMS_ACCESS_TOKEN_ENV);
        std::env::remove_var(GCP_KMS_KEY_ID_ENV);
        std::env::remove_var(KMS_KEY_ID_ENV);
        let p = default_provider();
        assert_eq!(p.provider_name(), "env", "缺 token 必须降级");
        std::env::remove_var(KEY_PROVIDER_ENV);
    }

    #[test]
    fn factory_picks_vault_when_fully_configured() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::set_var(KEY_PROVIDER_ENV, "vault");
        std::env::set_var(VAULT_ADDR_ENV, "http://127.0.0.1:8200");
        std::env::set_var(VAULT_TOKEN_ENV, "fake-token");
        std::env::set_var(VAULT_TRANSIT_KEY_ENV, "rpg-key");
        let p = default_provider();
        assert_eq!(p.provider_name(), "vault");
        std::env::remove_var(KEY_PROVIDER_ENV);
        std::env::remove_var(VAULT_ADDR_ENV);
        std::env::remove_var(VAULT_TOKEN_ENV);
        std::env::remove_var(VAULT_TRANSIT_KEY_ENV);
    }
}
