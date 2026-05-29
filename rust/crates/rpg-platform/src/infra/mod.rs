//! infra —— 平台基础设施抽象层(待办A)。
//!
//! 两块可插拔后端,核心目标都是「单副本能跑、多副本能共享、缺依赖优雅降级」:
//!
//! - [`rate_limit`] —— per-key 滑窗速率 + 并发计数的 [`RateLimitBackend`]。
//!   `MemoryRateLimiter`(进程内,搬自 quota/auth 的旧逻辑)+ `RedisRateLimiter`
//!   (`INCR`+`EXPIRE` Lua 脚本原子滑窗)。工厂按 `RPG_REDIS_URL` 选后端,未设
//!   或连接失败 → fallback Memory(单副本仍然正确)。
//!
//! - [`key_provider`] —— master_key 的 [`KeyProvider`] envelope 抽象。
//!   `EnvKeyProvider`(env/文件,保持现有行为)+ `KmsKeyProvider`(KEK 包 DEK 的
//!   骨架,留 AWS KMS / Vault HTTP 接缝)。

pub mod key_provider;
pub mod rate_limit;
