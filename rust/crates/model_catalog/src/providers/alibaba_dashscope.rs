//! Alibaba DashScope provider (native protocol,**非** OpenAI-compat 模式)。
//!
//! 为什么走 native:`native` 模式才同时支持 `tools` 与 `stream`。Bailian 已用 Bearer (sk-xxx) 鉴权,
//! HMAC 模式可绕过。
//!
//! ## native chat 端点(供 rpg-llm 引用,非 catalog 用)
//! `POST https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation`
//! ```json
//! {
//!   "model": "qwen3-max",
//!   "input": {"messages": [{"role": "user", "content": "..."}]},
//!   "parameters": {
//!     "result_format": "message",
//!     "stream": true,
//!     "incremental_output": true,
//!     "tools": [...]
//!   }
//! }
//! ```
//!
//! ## /models discovery
//! DashScope 官方 **不暴露** `/models` REST 端点。本 catalog **始终走 static**,
//! `fetch_models` 即直接返回 `static_catalog()`,语义保持与其他 native client 一致(可统一调用)。
//!
//! 鉴权:`Authorization: Bearer <sk-xxx>` (DashScope API key)。

use crate::providers::openai_compat::load_static_catalog;
use crate::schema::{CatalogError, ModelInfo, ProviderId};

pub const STATIC_JSON: &str = include_str!("../../data/alibaba_dashscope.json");
pub const NATIVE_GENERATION_ENDPOINT: &str =
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation";
pub const API_KEY_ENV: &str = "DASHSCOPE_API_KEY";

/// 静态 catalog,DashScope 无 live `/models` 端点,catalog 阶段直接用。
pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(
        STATIC_JSON,
        ProviderId::AlibabaQwen,
        "data/alibaba_dashscope.json",
    )
}

/// 与其他 native provider 同签名 — 但 DashScope 无 discovery,直接返回 static catalog,
/// 即"live = static fallback"。`_api_key` / `_client` 保留参数位以便未来若官方上线 /models 时无缝替换。
pub async fn fetch_models(
    _client: &reqwest::Client,
    _api_key: Option<&str>,
) -> Result<Vec<ModelInfo>, CatalogError> {
    static_catalog()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn static_catalog_has_six_qwen_models() {
        let v = static_catalog().expect("static");
        assert!(v.len() >= 6, "至少 6 个常用 Qwen 模型,实际 {}", v.len());
        assert!(v.iter().any(|m| m.id == "qwen3-max"));
        assert!(v.iter().any(|m| m.id == "qwq-32b-preview"));
        assert!(v.iter().any(|m| m.id == "qwen-vl-max"));
    }
}
