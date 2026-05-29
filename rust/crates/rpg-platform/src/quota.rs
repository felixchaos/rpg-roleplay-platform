//! quota —— 计费路径防灰产护栏(预算 / 配额 / 速率 / 并发 / max_tokens)。
//!
//! 出血点:盗 session 或刷接口即可刷爆用户 API 资产。本模块在**调 LLM 之前**
//! 强制过一道闸 [`check_and_reserve`],调用之后用 [`record_actual`] 回填真实用量。
//!
//! 六道防线:
//!   1. 月度预算   —— `token_usage` 当月 `sum(cost_usd)` ≥ 预算 → [`QuotaError::BudgetExceeded`]
//!   2. 日 token 配额 —— `token_usage` 当日 `sum(total_tokens)` ≥ 日上限 → [`QuotaError::DailyQuotaExceeded`]
//!   3. 每分钟速率 —— per-user 滑动窗口(内存,后续可换 Redis) → [`QuotaError::RateLimited`]
//!   4. 并发会话   —— per-user 在飞请求数 → [`QuotaError::TooManyConcurrent`]
//!   5. max_tokens —— 由 LLM backend 服务端 clamp(见 `rpg-llm/{anthropic,openai,vertex}.rs`)
//!   6. 强鉴权     —— 计费路由用 `require_user`(见 `rpg-routes/src/game.rs`),匿名严禁触达 LLM
//!
//! 聚合查询复用本 crate `usage` 模块的写入/计价基础设施;配额读取走纯 SQL `sum`。

use std::time::Duration;

use rpg_core::UserId;
use sqlx::{PgPool, Row};

use crate::infra::rate_limit::{SharedBackend, GLOBAL_BACKEND};
use crate::usage::{self, UsageBreakdown};

/// 服务端硬上限:任何客户端 `max_tokens` 都会被 clamp 到这个值。
/// LLM backend 各自引用此常量做 `.min(HARD_MAX_TOKENS)`。
pub const HARD_MAX_TOKENS: u32 = 8192;

/// per-user 月度预算默认值(USD)。env `RPG_USER_MONTHLY_BUDGET_USD` 可覆盖。
pub const DEFAULT_MONTHLY_BUDGET_USD: f64 = 10.0;
/// per-user 日 token 配额默认值。env `RPG_USER_DAILY_TOKEN_LIMIT` 可覆盖。
pub const DEFAULT_DAILY_TOKEN_LIMIT: i64 = 2_000_000;
/// per-user 每分钟请求数默认值。env `RPG_USER_RATE_PER_MIN` 可覆盖。
pub const DEFAULT_RATE_PER_MIN: u32 = 30;
/// per-user 最大并发会话默认值。env `RPG_USER_MAX_CONCURRENT` 可覆盖。
pub const DEFAULT_MAX_CONCURRENT: u32 = 4;

/// 配额配置。默认值从 env 读,预留 per-user 覆写接口([`QuotaConfig::with_user_overrides`])。
#[derive(Debug, Clone)]
pub struct QuotaConfig {
    /// 月度预算上限(USD)。`sum(cost_usd)` 当月 ≥ 此值即拒。
    pub monthly_budget_usd: f64,
    /// 日 token 配额。`sum(total_tokens)` 当日 ≥ 此值即拒。
    pub daily_token_limit: i64,
    /// 每分钟最大请求数(滑动窗口)。
    pub rate_per_min: u32,
    /// 每用户最大并发会话(在飞 LLM 调用数)。
    pub max_concurrent_sessions: u32,
    /// max_tokens 服务端硬上限(与 [`HARD_MAX_TOKENS`] 一致,供路由层透传给客户端展示)。
    pub hard_max_tokens: u32,
}

impl Default for QuotaConfig {
    fn default() -> Self {
        Self {
            monthly_budget_usd: DEFAULT_MONTHLY_BUDGET_USD,
            daily_token_limit: DEFAULT_DAILY_TOKEN_LIMIT,
            rate_per_min: DEFAULT_RATE_PER_MIN,
            max_concurrent_sessions: DEFAULT_MAX_CONCURRENT,
            hard_max_tokens: HARD_MAX_TOKENS,
        }
    }
}

impl QuotaConfig {
    /// 从环境变量构造(缺失/非法回落默认值)。
    pub fn from_env() -> Self {
        let mut cfg = Self::default();
        if let Some(v) = env_f64("RPG_USER_MONTHLY_BUDGET_USD") {
            if v > 0.0 {
                cfg.monthly_budget_usd = v;
            }
        }
        if let Some(v) = env_i64("RPG_USER_DAILY_TOKEN_LIMIT") {
            if v > 0 {
                cfg.daily_token_limit = v;
            }
        }
        if let Some(v) = env_u32("RPG_USER_RATE_PER_MIN") {
            if v > 0 {
                cfg.rate_per_min = v;
            }
        }
        if let Some(v) = env_u32("RPG_USER_MAX_CONCURRENT") {
            if v > 0 {
                cfg.max_concurrent_sessions = v;
            }
        }
        cfg
    }

    /// per-user 覆写预留接口:`None` 字段沿用当前配置。
    /// 后续可由 `user_preferences` 表注入(套餐 / 配额包)。
    pub fn with_user_overrides(
        mut self,
        monthly_budget_usd: Option<f64>,
        daily_token_limit: Option<i64>,
    ) -> Self {
        if let Some(b) = monthly_budget_usd {
            if b > 0.0 {
                self.monthly_budget_usd = b;
            }
        }
        if let Some(d) = daily_token_limit {
            if d > 0 {
                self.daily_token_limit = d;
            }
        }
        self
    }
}

fn env_f64(key: &str) -> Option<f64> {
    std::env::var(key).ok()?.trim().parse::<f64>().ok()
}
fn env_i64(key: &str) -> Option<i64> {
    std::env::var(key).ok()?.trim().parse::<i64>().ok()
}
fn env_u32(key: &str) -> Option<u32> {
    std::env::var(key).ok()?.trim().parse::<u32>().ok()
}

/// 配额拒绝原因。携带 `retry_after_sec` 供路由层回 429 + `Retry-After`。
#[derive(Debug, Clone, thiserror::Error)]
pub enum QuotaError {
    #[error("月度预算已用尽:已花 ${spent:.4} / 预算 ${budget:.4}")]
    BudgetExceeded { spent: f64, budget: f64 },

    #[error("当日 token 配额已用尽:已用 {used} / 上限 {limit}")]
    DailyQuotaExceeded { used: i64, limit: i64 },

    #[error("请求过于频繁:{count}/min 超过 {limit}/min,请 {retry_after_sec}s 后重试")]
    RateLimited {
        count: u32,
        limit: u32,
        retry_after_sec: u64,
    },

    #[error("并发会话过多:{active} 个在飞,上限 {limit}")]
    TooManyConcurrent { active: u32, limit: u32 },

    #[error("配额查询失败:{0}")]
    Backend(String),
}

impl QuotaError {
    /// 建议的 `Retry-After` 秒数(无限制类返回 None)。
    pub fn retry_after_sec(&self) -> Option<u64> {
        match self {
            QuotaError::RateLimited {
                retry_after_sec, ..
            } => Some(*retry_after_sec),
            // 日配额 / 并发:让客户端稍后重试。预算超限不建议重试(需充值)。
            QuotaError::DailyQuotaExceeded { .. } => Some(60),
            QuotaError::TooManyConcurrent { .. } => Some(5),
            _ => None,
        }
    }

    /// 机器可读 code(对齐前端错误协议)。
    pub fn code(&self) -> &'static str {
        match self {
            QuotaError::BudgetExceeded { .. } => "budget_exceeded",
            QuotaError::DailyQuotaExceeded { .. } => "daily_quota_exceeded",
            QuotaError::RateLimited { .. } => "rate_limited",
            QuotaError::TooManyConcurrent { .. } => "too_many_concurrent",
            QuotaError::Backend(_) => "quota_backend_error",
        }
    }
}

/// 通过闸门后发放的凭据。`record_actual` 必须传回,用于:
/// 1. 计费回填(写 `token_usage`)
/// 2. 释放并发槽位(Drop 时自动归还)
#[derive(Debug)]
pub struct QuotaGrant {
    pub user_id: UserId,
    pub api_id: String,
    pub model: String,
    /// 进闸时的预估 token(供观测 / 调试)。
    pub est_tokens: i64,
    /// 并发槽位守卫(Drop 兜底释放),`record_actual` 显式释放后失效。
    _slot: ConcurrencyGuard,
}

// ───────────────────────── 滑动窗口速率 + 并发(经可插拔后端) ─────────────────────────
//
// 待办A:速率/并发计数下沉到 [`RateLimitBackend`](crate::infra::rate_limit) —— 默认进程内
// Memory,设 `RPG_REDIS_URL` 则共享到 Redis(多副本统一限流)。key 用 `quota:user:<id>`。

/// 60s 滑窗。
const RATE_WINDOW: Duration = Duration::from_secs(60);

/// per-user 限流 key —— 进程内/Redis 共用同一命名空间。
fn rate_key(user_id: i64) -> String {
    format!("quota:user:{user_id}")
}

/// 并发守卫:Drop 时兜底把后端 `in_flight` 减回去(防 record_actual 未走到的早退/panic 路径)。
///
/// 正常路径由 [`record_actual`] 显式 `decr` 后 `disarm`;Drop 仅在异常路径生效。
/// Drop 不能 async,故 spawn 一个 detached 任务做 decr(Redis 后端需要)。
struct ConcurrencyGuard {
    backend: SharedBackend,
    key: String,
    /// true = 已显式释放,Drop 不再重复 decr。
    released: bool,
}

impl std::fmt::Debug for ConcurrencyGuard {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ConcurrencyGuard")
            .field("key", &self.key)
            .field("released", &self.released)
            .finish()
    }
}

impl ConcurrencyGuard {
    /// 标记已释放(record_actual 显式 decr 后调用),阻止 Drop 重复减。
    fn disarm(&mut self) {
        self.released = true;
    }
}

impl Drop for ConcurrencyGuard {
    fn drop(&mut self) {
        if self.released {
            return;
        }
        // 异常路径兜底:spawn detached decr。若不在 tokio runtime 内(理论上不该发生),
        // 只 warn —— Redis 后端有 TTL 兜底,Memory 后端槽位最终随进程结束释放。
        let backend = self.backend.clone();
        let key = self.key.clone();
        match tokio::runtime::Handle::try_current() {
            Ok(h) => {
                h.spawn(async move {
                    backend.decr_concurrent(&key).await;
                });
            }
            Err(_) => {
                tracing::warn!(
                    target: "rpg_platform::quota",
                    key = %key,
                    "ConcurrencyGuard drop 不在 tokio runtime 内,无法异步释放并发槽(后端 TTL 兜底)"
                );
            }
        }
    }
}

/// 速率 + 并发联合闸门(经后端)。成功则占一个并发槽并返回守卫。
///
/// 顺序与旧版一致:先查并发(占槽前先验),再过速率;但本版**先过速率再占槽**,
/// 因为后端 incr 是原子占槽,需在速率放行后才占,避免速率拒绝时虚占 Redis 计数。
async fn reserve_rate_and_slot(
    backend: &SharedBackend,
    user_id: i64,
    rate_per_min: u32,
    max_concurrent: u32,
) -> Result<ConcurrencyGuard, QuotaError> {
    let key = rate_key(user_id);
    // 1) 速率滑窗。
    if !backend.check_rate(&key, rate_per_min, RATE_WINDOW).await {
        return Err(QuotaError::RateLimited {
            count: rate_per_min,
            limit: rate_per_min,
            // 滑窗后端不回传精确出窗时刻;给保守的整窗秒数让客户端稍后重试。
            retry_after_sec: RATE_WINDOW.as_secs().max(1),
        });
    }
    // 2) 并发占槽(原子;超限不占)。
    if !backend.incr_concurrent(&key, max_concurrent).await {
        let active = backend.concurrent_count(&key).await;
        return Err(QuotaError::TooManyConcurrent {
            active,
            limit: max_concurrent,
        });
    }
    Ok(ConcurrencyGuard {
        backend: backend.clone(),
        key,
        released: false,
    })
}

// ───────────────────────── DB 聚合查询 ─────────────────────────

/// 当月(日历自然月,UTC)已花费 USD。复用 `token_usage`。
pub async fn month_to_date_cost_usd(pool: &PgPool, user_id: UserId) -> Result<f64, sqlx::Error> {
    let row = sqlx::query(
        "select coalesce(sum(cost_usd), 0)::float8 as spent \
         from token_usage \
         where user_id = $1 and created_at >= date_trunc('month', now())",
    )
    .bind(user_id)
    .fetch_one(pool)
    .await?;
    Ok(row.try_get::<f64, _>("spent").unwrap_or(0.0))
}

/// 当日(自然日,UTC)已用 total_tokens 之和。
pub async fn day_to_date_tokens(pool: &PgPool, user_id: UserId) -> Result<i64, sqlx::Error> {
    let row = sqlx::query(
        "select coalesce(sum(total_tokens), 0)::bigint as used \
         from token_usage \
         where user_id = $1 and created_at >= date_trunc('day', now())",
    )
    .bind(user_id)
    .fetch_one(pool)
    .await?;
    Ok(row.try_get::<i64, _>("used").unwrap_or(0))
}

// ───────────────────────── 纯逻辑判定(可单测) ─────────────────────────

/// 预算闸:当月已花 `spent` ≥ `budget` → 拒。
pub fn check_budget(spent: f64, budget: f64) -> Result<(), QuotaError> {
    if spent >= budget {
        Err(QuotaError::BudgetExceeded { spent, budget })
    } else {
        Ok(())
    }
}

/// 日配额闸:当日已用 `used` ≥ `limit` → 拒(预估也加进来:`used + est ≥ limit` 提前拦)。
pub fn check_daily(used: i64, est_tokens: i64, limit: i64) -> Result<(), QuotaError> {
    let projected = used.saturating_add(est_tokens.max(0));
    if used >= limit || projected > limit {
        Err(QuotaError::DailyQuotaExceeded { used, limit })
    } else {
        Ok(())
    }
}

// ───────────────────────── 对外主闸 ─────────────────────────

/// **调 LLM 前必须过这道闸。** 依次校验:预算 → 日配额 → 速率 → 并发,
/// 全过则占用一个并发槽,返回 [`QuotaGrant`]。任何一道不过返回对应 [`QuotaError`]。
///
/// `est_tokens` 为本轮预估总 token(input+预期 output),用于日配额提前拦截。
#[tracing::instrument(skip(pool, cfg), fields(user_id, model))]
pub async fn check_and_reserve(
    pool: &PgPool,
    cfg: &QuotaConfig,
    user_id: UserId,
    api_id: &str,
    model: &str,
    est_tokens: i64,
) -> Result<QuotaGrant, QuotaError> {
    check_and_reserve_with(&GLOBAL_BACKEND, pool, cfg, user_id, api_id, model, est_tokens).await
}

/// [`check_and_reserve`] 的可注入后端版本 —— 便于测试传入显式 [`RateLimitBackend`],
/// 也供需要自定义后端的部署场景调用。
#[allow(clippy::too_many_arguments)]
pub async fn check_and_reserve_with(
    backend: &SharedBackend,
    pool: &PgPool,
    cfg: &QuotaConfig,
    user_id: UserId,
    api_id: &str,
    model: &str,
    est_tokens: i64,
) -> Result<QuotaGrant, QuotaError> {
    // 1) 月度预算
    let spent = month_to_date_cost_usd(pool, user_id)
        .await
        .map_err(|e| QuotaError::Backend(e.to_string()))?;
    check_budget(spent, cfg.monthly_budget_usd)?;

    // 2) 日 token 配额(把预估也算进去提前拦)
    let used = day_to_date_tokens(pool, user_id)
        .await
        .map_err(|e| QuotaError::Backend(e.to_string()))?;
    check_daily(used, est_tokens, cfg.daily_token_limit)?;

    // 3+4) 速率(滑窗) + 并发(占槽),经可插拔后端(Memory / Redis)。
    let slot = reserve_rate_and_slot(
        backend,
        user_id.get(),
        cfg.rate_per_min,
        cfg.max_concurrent_sessions,
    )
    .await?;

    Ok(QuotaGrant {
        user_id,
        api_id: api_id.to_string(),
        model: model.to_string(),
        est_tokens,
        _slot: slot,
    })
}

/// 调用 LLM 之后回填真实用量:写一条 `token_usage`,并释放并发槽(消费 grant)。
///
/// 失败只记 warn,不影响主流程(计费写入是 best-effort,但 grant 仍会 drop 释放槽位)。
#[tracing::instrument(skip(pool, grant, actual), fields(user_id = %grant.user_id))]
pub async fn record_actual(
    pool: &PgPool,
    grant: QuotaGrant,
    save_id: Option<i64>,
    context_run_id: Option<i64>,
    actual: &UsageBreakdown,
    context_used: i32,
    context_max: i32,
) {
    let res = usage::record_token_usage(
        pool,
        grant.user_id,
        save_id,
        context_run_id,
        &grant.api_id,
        &grant.model,
        actual,
        context_used,
        context_max,
        serde_json::json!({ "via": "quota.record_actual", "est_tokens": grant.est_tokens }),
    )
    .await;
    if let Err(e) = res {
        tracing::warn!(error = %e, user_id = %grant.user_id, "record_actual 写 token_usage 失败");
    }
    // 显式释放并发槽(经后端;Redis 后端需 await),再 disarm 守卫避免 Drop 重复 decr。
    let mut grant = grant;
    grant._slot.backend.decr_concurrent(&grant._slot.key).await;
    grant._slot.disarm();
    drop(grant);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn breakdown(input: i32, output: i32) -> UsageBreakdown {
        UsageBreakdown {
            input_tokens: input,
            output_tokens: output,
            cached_input_tokens: 0,
            reasoning_tokens: 0,
            total_tokens: input + output,
        }
    }

    #[test]
    fn budget_blocks_when_spent_reaches_limit() {
        // 恰好等于预算即拒(用尽)。
        let err = check_budget(10.0, 10.0).unwrap_err();
        assert!(matches!(err, QuotaError::BudgetExceeded { .. }));
        assert_eq!(err.code(), "budget_exceeded");
        // 超过也拒。
        assert!(check_budget(12.5, 10.0).is_err());
    }

    #[test]
    fn budget_allows_when_under_limit() {
        assert!(check_budget(9.999, 10.0).is_ok());
        assert!(check_budget(0.0, 10.0).is_ok());
    }

    #[test]
    fn budget_exceeded_not_retryable() {
        let err = check_budget(10.0, 10.0).unwrap_err();
        // 预算超限不建议自动重试(需充值)。
        assert_eq!(err.retry_after_sec(), None);
    }

    #[test]
    fn daily_quota_blocks_when_used_reaches_limit() {
        let err = check_daily(2_000_000, 0, 2_000_000).unwrap_err();
        assert!(matches!(err, QuotaError::DailyQuotaExceeded { .. }));
        assert_eq!(err.code(), "daily_quota_exceeded");
    }

    #[test]
    fn daily_quota_projected_overflow_blocks_early() {
        // 已用 1.9M,预估 200k,上限 2M → 投影 2.1M 提前拦。
        let err = check_daily(1_900_000, 200_000, 2_000_000).unwrap_err();
        assert!(matches!(err, QuotaError::DailyQuotaExceeded { .. }));
        // 日配额建议 60s 后重试。
        assert_eq!(err.retry_after_sec(), Some(60));
    }

    #[test]
    fn daily_quota_allows_under_limit() {
        assert!(check_daily(1_000_000, 50_000, 2_000_000).is_ok());
        assert!(check_daily(0, 0, 2_000_000).is_ok());
    }

    #[tokio::test]
    async fn rate_window_blocks_after_limit_via_backend() {
        // 速率滑窗逻辑现在由 RateLimitBackend(Memory)承载,这里验证 quota 层接缝:
        // limit 内放行、超限给 RateLimited、窗口外恢复。
        use crate::infra::rate_limit::MemoryRateLimiter;
        let be: SharedBackend = Arc::new(MemoryRateLimiter::new());
        let limit = 3u32;
        let w = RATE_WINDOW;
        let k = "quota:user:99";
        assert!(be.check_rate(k, limit, w).await);
        assert!(be.check_rate(k, limit, w).await);
        assert!(be.check_rate(k, limit, w).await);
        // 第 4 次窗口内被拒。
        assert!(!be.check_rate(k, limit, w).await);
    }

    #[tokio::test]
    async fn reserve_rejects_rate_then_concurrency() {
        use crate::infra::rate_limit::MemoryRateLimiter;
        let be: SharedBackend = Arc::new(MemoryRateLimiter::new());
        // rate 充足,但并发上限 1:第二次占槽应 TooManyConcurrent。
        let g1 = reserve_rate_and_slot(&be, 7, 100, 1).await.unwrap();
        let err = reserve_rate_and_slot(&be, 7, 100, 1).await.unwrap_err();
        assert_eq!(err.code(), "too_many_concurrent");
        // 释放后又能占。
        drop(g1);
        // Drop 走 detached 任务释放;给它一拍。
        tokio::time::sleep(Duration::from_millis(20)).await;
        let _g2 = reserve_rate_and_slot(&be, 7, 100, 1).await.unwrap();
    }

    #[tokio::test]
    async fn reserve_rate_limited_is_retryable() {
        use crate::infra::rate_limit::MemoryRateLimiter;
        let be: SharedBackend = Arc::new(MemoryRateLimiter::new());
        // rate=1:第一次占成功,第二次速率拒。
        let _g = reserve_rate_and_slot(&be, 8, 1, 10).await.unwrap();
        let err = reserve_rate_and_slot(&be, 8, 1, 10).await.unwrap_err();
        assert_eq!(err.code(), "rate_limited");
        assert!(err.retry_after_sec().is_some());
    }

    #[test]
    fn config_defaults_are_sane() {
        let cfg = QuotaConfig::default();
        assert_eq!(cfg.monthly_budget_usd, DEFAULT_MONTHLY_BUDGET_USD);
        assert_eq!(cfg.hard_max_tokens, HARD_MAX_TOKENS);
        assert!(cfg.daily_token_limit > 0);
        assert!(cfg.rate_per_min > 0);
        assert!(cfg.max_concurrent_sessions > 0);
    }

    #[test]
    fn user_overrides_apply_and_ignore_nonpositive() {
        let cfg = QuotaConfig::default().with_user_overrides(Some(50.0), Some(5_000_000));
        assert_eq!(cfg.monthly_budget_usd, 50.0);
        assert_eq!(cfg.daily_token_limit, 5_000_000);
        // 非正数 / None 不覆盖。
        let cfg2 = QuotaConfig::default().with_user_overrides(Some(-1.0), None);
        assert_eq!(cfg2.monthly_budget_usd, DEFAULT_MONTHLY_BUDGET_USD);
    }

    #[test]
    fn breakdown_helper_sums_total() {
        let b = breakdown(100, 50);
        assert_eq!(b.total_tokens, 150);
    }
}
