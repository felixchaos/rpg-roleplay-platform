//! provider 配置子模块。
//!
//! 每家 provider 一个文件,导出:
//! - `config()` 返回 [`openai_compat::OpenAICompatConfig`];
//! - `static_catalog()` 返回兜底 `Vec<ModelInfo>`。

pub mod deepseek;
pub mod openai;
pub mod openai_compat;
pub mod openrouter;
pub mod tencent_hunyuan;
pub mod xai;
pub mod xiaomi_mimo;
