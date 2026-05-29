//! 限流后端抽象 —— 可选下沉 Redis,缺依赖优雅降级到进程内 Memory。
//!
//! ## 为什么要这层
//! 旧版 `quota.rs` 的 per-user 滑窗 + 并发计数、`auth/rate_limit.rs` 的登录失败桶
//! 都是进程内 `parking_lot::Mutex<HashMap>`。多副本部署时各算各的,限流形同虚设
//! (N 副本 = N 倍额度)。本模块把「速率判定 + 并发计数」抽象成 [`RateLimitBackend`],
//! 让 quota / auth 都接同一个后端;生产多副本设 `RPG_REDIS_URL` 即可共享计数。
//!
//! ## 优雅降级策略
//! - 未设 `RPG_REDIS_URL` → 直接用 [`MemoryRateLimiter`](单副本完全正确)。
//! - 设了但**建连失败** → WARN 一行,fallback Memory(宁可单副本限流,也不要因为
//!   Redis 抖动把整个登录/计费闸门挂掉)。
//! - 运行期 Redis 命令失败 → 见各方法 docstring 的 fail-open / fail-closed 选择。
//!
//! ## 语义
//! - [`RateLimitBackend::check_rate`] —— 滑动窗口:在 `window` 内为 `key` 登记一次命中,
//!   命中数 `<= limit` 返回 `true`(放行),否则 `false`(拒)。
//! - [`RateLimitBackend::incr_concurrent`] / [`decr_concurrent`](RateLimitBackend::decr_concurrent)
//!   —— per-key 在飞计数。incr 时若 `>= max` 返回 `false` 且不占槽。

use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use parking_lot::Mutex;

/// 限流后端:速率滑窗 + 并发计数。两实现共享此接口,quota / auth 都接它。
///
/// 所有方法 `async`,因为 Redis 实现要走网络;Memory 实现是 `async` 包同步逻辑(零成本)。
#[async_trait]
pub trait RateLimitBackend: Send + Sync {
    /// 滑动窗口速率判定:在 `window` 内为 `key` 登记一次命中。
    ///
    /// 返回 `true` = 放行(命中数含本次 `<= limit`);`false` = 超限拒绝。
    /// 后端故障时由实现决定 fail-open / fail-closed(见实现 docstring)。
    async fn check_rate(&self, key: &str, limit: u32, window: Duration) -> bool;

    /// 在飞并发 +1。返回 `true` = 占槽成功(占用后 `<= max`);
    /// `false` = 已达上限,**未占槽**(调用方不应继续)。
    async fn incr_concurrent(&self, key: &str, max: u32) -> bool;

    /// 在飞并发 -1(释放槽位)。幂等;计数已为 0 时安全 no-op。
    async fn decr_concurrent(&self, key: &str);

    /// 当前在飞并发数(观测 / 测试用)。后端不可用返回 0。
    async fn concurrent_count(&self, key: &str) -> u32;
}

/// 共享句柄类型 —— quota / auth 都持 `Arc<dyn RateLimitBackend>`。
pub type SharedBackend = Arc<dyn RateLimitBackend>;

// ───────────────────────── 工厂 ─────────────────────────

const REDIS_URL_ENV: &str = "RPG_REDIS_URL";

/// 进程级共享后端。首次访问按 `RPG_REDIS_URL` 决定后端,失败 fallback Memory。
///
/// quota / auth 默认都取这个(也可注入自定义后端便于测试)。
pub static GLOBAL_BACKEND: once_cell::sync::Lazy<SharedBackend> =
    once_cell::sync::Lazy::new(default_backend);

/// 工厂:`RPG_REDIS_URL` 设了且能建连 → [`RedisRateLimiter`],否则 [`MemoryRateLimiter`]。
///
/// **优雅降级**:Redis 建连失败不 panic、不阻塞启动,只 WARN 后回退内存版。
pub fn default_backend() -> SharedBackend {
    match std::env::var(REDIS_URL_ENV) {
        Ok(url) if !url.trim().is_empty() => match RedisRateLimiter::connect(url.trim()) {
            Ok(r) => {
                tracing::info!(
                    target: "rpg_platform::infra::rate_limit",
                    "限流后端 = Redis ({REDIS_URL_ENV} 已设),多副本共享计数"
                );
                Arc::new(r)
            }
            Err(e) => {
                tracing::warn!(
                    target: "rpg_platform::infra::rate_limit",
                    error = %e,
                    "{REDIS_URL_ENV} 已设但 Redis 建连失败 — 优雅降级到进程内 Memory 限流(单副本正确,多副本各算各的)"
                );
                Arc::new(MemoryRateLimiter::new())
            }
        },
        _ => {
            tracing::debug!(
                target: "rpg_platform::infra::rate_limit",
                "{REDIS_URL_ENV} 未设 — 使用进程内 Memory 限流"
            );
            Arc::new(MemoryRateLimiter::new())
        }
    }
}

// ───────────────────────── Memory 后端 ─────────────────────────

struct MemState {
    /// 滑窗命中时间戳(单调毫秒)。
    hits: VecDeque<u64>,
    /// 在飞并发计数。
    in_flight: u32,
}

/// 进程内限流后端 —— 搬自旧 `quota.rs` 的滑窗 + 并发逻辑。单副本完全正确。
#[derive(Default)]
pub struct MemoryRateLimiter {
    table: Mutex<HashMap<String, MemState>>,
}

impl MemoryRateLimiter {
    pub fn new() -> Self {
        Self::default()
    }

    /// 单调时钟毫秒(避免系统时间回拨影响滑窗)。
    fn now_millis() -> u64 {
        use std::sync::OnceLock;
        use std::time::Instant;
        static START: OnceLock<Instant> = OnceLock::new();
        let start = START.get_or_init(Instant::now);
        start.elapsed().as_millis() as u64
    }

    /// 测试辅助:清空全部状态。
    #[doc(hidden)]
    pub fn _reset(&self) {
        self.table.lock().clear();
    }
}

#[async_trait]
impl RateLimitBackend for MemoryRateLimiter {
    async fn check_rate(&self, key: &str, limit: u32, window: Duration) -> bool {
        let now = Self::now_millis();
        let window_ms = window.as_millis() as u64;
        let mut tbl = self.table.lock();
        let st = tbl.entry(key.to_string()).or_insert_with(|| MemState {
            hits: VecDeque::new(),
            in_flight: 0,
        });
        // 清窗口外旧命中。
        while let Some(&front) = st.hits.front() {
            if now.saturating_sub(front) >= window_ms {
                st.hits.pop_front();
            } else {
                break;
            }
        }
        if st.hits.len() as u32 >= limit {
            return false;
        }
        st.hits.push_back(now);
        true
    }

    async fn incr_concurrent(&self, key: &str, max: u32) -> bool {
        let mut tbl = self.table.lock();
        let st = tbl.entry(key.to_string()).or_insert_with(|| MemState {
            hits: VecDeque::new(),
            in_flight: 0,
        });
        if st.in_flight >= max {
            return false;
        }
        st.in_flight += 1;
        true
    }

    async fn decr_concurrent(&self, key: &str) {
        let mut tbl = self.table.lock();
        if let Some(st) = tbl.get_mut(key) {
            st.in_flight = st.in_flight.saturating_sub(1);
        }
    }

    async fn concurrent_count(&self, key: &str) -> u32 {
        self.table
            .lock()
            .get(key)
            .map(|s| s.in_flight)
            .unwrap_or(0)
    }
}

// ───────────────────────── Redis 后端 ─────────────────────────

/// Redis 限流后端 —— 多副本共享计数。
///
/// - 速率:用 Lua 脚本做原子滑窗(`ZADD`/`ZREMRANGEBYSCORE`/`ZCARD`),保证「清旧 +
///   计数 + 写入」三步不被并发穿插,避免 `INCR`+`EXPIRE` 非原子导致的窗口边界泄漏。
/// - 并发:`INCR`/`DECR` 一个计数 key;incr 后若超限立即回退(`DECR`),不占槽。
///   计数 key 设较长 TTL 兜底,防进程崩溃后槽位永久泄漏。
///
/// **fail-open**:任何 Redis 命令失败都放行(返回 `true`)。限流是护栏不是主逻辑,
/// Redis 抖动时宁可短暂少限一点,也不要把登录 / 计费闸门整个挂掉。失败均 WARN 可观测。
pub struct RedisRateLimiter {
    mgr: redis::aio::ConnectionManager,
    rate_script: redis::Script,
    /// 并发计数 key 的兜底 TTL(秒)—— 防进程崩溃后 in-flight 永久不归零。
    concurrent_ttl_sec: u64,
}

impl RedisRateLimiter {
    /// 同步建连(用临时 runtime block_on 拿到 ConnectionManager)。
    ///
    /// 在 [`default_backend`] 工厂里调用,失败返回 `Err` 让工厂 fallback Memory。
    pub fn connect(url: &str) -> Result<Self, redis::RedisError> {
        let client = redis::Client::open(url)?;
        // ConnectionManager 是 async 构造;工厂处于同步上下文,用一个临时 runtime 拉起。
        // 若已在 tokio runtime 内,用 Handle 阻塞会 panic,故显式新建一个单线程 runtime。
        let mgr = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| {
                redis::RedisError::from((
                    redis::ErrorKind::IoError,
                    "建临时 runtime 失败",
                    e.to_string(),
                ))
            })?
            .block_on(redis::aio::ConnectionManager::new(client))?;
        Ok(Self {
            mgr,
            rate_script: redis::Script::new(RATE_LUA),
            concurrent_ttl_sec: 300,
        })
    }
}

/// 原子滑窗 Lua:KEYS[1]=zset, ARGV[1]=now_ms, ARGV[2]=window_ms, ARGV[3]=limit, ARGV[4]=member。
/// 返回 1=放行 / 0=超限。清旧 → 计数 → (未超限则)写入,全程原子。
const RATE_LUA: &str = r#"
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return 1
"#;

#[async_trait]
impl RateLimitBackend for RedisRateLimiter {
    async fn check_rate(&self, key: &str, limit: u32, window: Duration) -> bool {
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        let window_ms = window.as_millis() as u64;
        // member 用 now_ms + 随机后缀,避免同毫秒并发命中互相覆盖 ZADD score。
        let member = format!("{now_ms}-{}", rand::random::<u32>());
        let mut conn = self.mgr.clone();
        let res: Result<i64, _> = self
            .rate_script
            .key(format!("rl:rate:{key}"))
            .arg(now_ms)
            .arg(window_ms)
            .arg(limit)
            .arg(member)
            .invoke_async(&mut conn)
            .await;
        match res {
            Ok(v) => v == 1,
            Err(e) => {
                tracing::warn!(
                    target: "rpg_platform::infra::rate_limit",
                    error = %e, key,
                    "Redis check_rate 失败 — fail-open 放行"
                );
                true
            }
        }
    }

    async fn incr_concurrent(&self, key: &str, max: u32) -> bool {
        let ckey = format!("rl:conc:{key}");
        let mut conn = self.mgr.clone();
        let incred: Result<i64, _> = redis::cmd("INCR").arg(&ckey).query_async(&mut conn).await;
        match incred {
            Ok(n) => {
                // 兜底 TTL,防崩溃泄漏。
                let _: Result<(), _> = redis::cmd("EXPIRE")
                    .arg(&ckey)
                    .arg(self.concurrent_ttl_sec)
                    .query_async(&mut conn)
                    .await;
                if n as u32 > max {
                    // 超限:回退本次 incr,不占槽。
                    let _: Result<(), _> =
                        redis::cmd("DECR").arg(&ckey).query_async(&mut conn).await;
                    false
                } else {
                    true
                }
            }
            Err(e) => {
                tracing::warn!(
                    target: "rpg_platform::infra::rate_limit",
                    error = %e, key,
                    "Redis incr_concurrent 失败 — fail-open 占槽"
                );
                true
            }
        }
    }

    async fn decr_concurrent(&self, key: &str) {
        let ckey = format!("rl:conc:{key}");
        let mut conn = self.mgr.clone();
        // 用 Lua 保证不减到负数(并发 DECR 竞争 + 崩溃残留场景)。
        let lua = redis::Script::new(
            "local n = tonumber(redis.call('GET', KEYS[1]) or '0') if n > 0 then return redis.call('DECR', KEYS[1]) else return 0 end",
        );
        let res: Result<i64, _> = lua.key(&ckey).invoke_async(&mut conn).await;
        if let Err(e) = res {
            tracing::warn!(
                target: "rpg_platform::infra::rate_limit",
                error = %e, key,
                "Redis decr_concurrent 失败 — 槽位可能短暂泄漏(有 TTL 兜底)"
            );
        }
    }

    async fn concurrent_count(&self, key: &str) -> u32 {
        let ckey = format!("rl:conc:{key}");
        let mut conn = self.mgr.clone();
        let res: Result<Option<i64>, _> =
            redis::cmd("GET").arg(&ckey).query_async(&mut conn).await;
        res.ok().flatten().unwrap_or(0).max(0) as u32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn memory_rate_blocks_after_limit_and_recovers() {
        let be = MemoryRateLimiter::new();
        let w = Duration::from_millis(50);
        // limit=3:前 3 次放行,第 4 次拒。
        assert!(be.check_rate("u1", 3, w).await);
        assert!(be.check_rate("u1", 3, w).await);
        assert!(be.check_rate("u1", 3, w).await);
        assert!(!be.check_rate("u1", 3, w).await);
        // 跨窗口后恢复。
        tokio::time::sleep(Duration::from_millis(60)).await;
        assert!(be.check_rate("u1", 3, w).await);
    }

    #[tokio::test]
    async fn memory_rate_keys_are_isolated() {
        let be = MemoryRateLimiter::new();
        let w = Duration::from_secs(60);
        assert!(be.check_rate("a", 1, w).await);
        assert!(!be.check_rate("a", 1, w).await);
        // 不同 key 独立。
        assert!(be.check_rate("b", 1, w).await);
    }

    #[tokio::test]
    async fn memory_concurrent_incr_decr() {
        let be = MemoryRateLimiter::new();
        assert!(be.incr_concurrent("u", 2).await);
        assert!(be.incr_concurrent("u", 2).await);
        assert_eq!(be.concurrent_count("u").await, 2);
        // 达上限拒,且不占槽。
        assert!(!be.incr_concurrent("u", 2).await);
        assert_eq!(be.concurrent_count("u").await, 2);
        // 释放后又能占。
        be.decr_concurrent("u").await;
        assert_eq!(be.concurrent_count("u").await, 1);
        assert!(be.incr_concurrent("u", 2).await);
    }

    #[tokio::test]
    async fn memory_decr_never_underflows() {
        let be = MemoryRateLimiter::new();
        // 从未 incr 也安全。
        be.decr_concurrent("ghost").await;
        assert_eq!(be.concurrent_count("ghost").await, 0);
    }

    #[tokio::test]
    async fn factory_falls_back_to_memory_without_redis_url() {
        // 不设 RPG_REDIS_URL:工厂应给 Memory 后端且可用。
        let be = default_backend();
        assert!(be.check_rate("factory-test", 1, Duration::from_secs(1)).await);
    }
}
