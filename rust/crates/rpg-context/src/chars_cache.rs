//! chars_cache — script-scoped 角色卡 lazy-load + 进程内 TTL 缓存。
//!
//! 对应 Python:
//!   - `rpg/context_engine/loaders.py::_load_characters_db`
//!   - `rpg/context_engine/loaders.py::_load_characters` (DB-first 路径)
//!   - `rpg/context_engine/loaders.py::_safe_load_chars` (kind 守门)
//!
//! Wave 7-A 目标:
//!   - 把 `engine.rs` 里 `chars = json!({})` 的占位换成真 DB 加载。
//!   - 每次 build_context 都查 DB 浪费 RT,加 60s TTL 的 DashMap 缓存。
//!   - cache key 用 (script_id, book_id),与 Python `_load_characters` 入参一致。
//!
//! 行为约定:
//!   - cache hit + 未过期 → 直接返 `Arc<Value>`(零拷贝)。
//!   - cache miss / 过期 → 走 DB,写回 cache,返新值。
//!   - DB 错误 → 返 `Arc::new(json!({}))`,不污染 cache(下次重试)。
//!   - script_id / book_id 都没传 → 返 `Arc::new(json!({}))`(Python 在 scoped=false
//!     时才回退 JSON 文件,Rust 端没必要保留这条遗留路径)。

use dashmap::DashMap;
use once_cell::sync::Lazy;
use serde_json::{json, Value};
use sqlx::PgPool;
use std::sync::Arc;
use std::time::{Duration, Instant};

/// TTL 60s。同一 save 的几轮 build_context 在 1 分钟内共用同一份 chars,
/// 角色卡变更(编辑器、import)后最多滞后 60s 生效 —— 给运行时 GM 上下文够用。
const CHARS_CACHE_TTL: Duration = Duration::from_secs(60);

/// 缓存条目:`(写入时间, JSON 内容)`。
struct CachedChars {
    fetched_at: Instant,
    value: Arc<Value>,
}

/// cache key = (script_id, book_id)。两个都是 `Option<i64>`,用 `(i64, i64)` 表示
/// (用 -1 占位"未指定")。Python 那边 `_load_characters_db` 用 script_id ELIF book_id,
/// 二选一,我们这里完全 mirror。
type CacheKey = (i64, i64);

static CACHE: Lazy<DashMap<CacheKey, CachedChars>> = Lazy::new(DashMap::new);

/// 仅测试可见:清空缓存。其它代码不应直接动 cache。
#[cfg(test)]
pub(crate) fn _clear_cache_for_test() {
    CACHE.clear();
}

/// 仅测试可见:返回当前 cache 大小。
#[cfg(test)]
pub(crate) fn _cache_len_for_test() -> usize {
    CACHE.len()
}

fn make_key(script_id: Option<i64>, book_id: Option<i64>) -> CacheKey {
    (script_id.unwrap_or(-1), book_id.unwrap_or(-1))
}

/// Wave 7-A 主入口:取 script/book scope 下的角色卡 JSON。
///
/// 对应 Python `_load_characters(script_id, book_id)` 的 DB 路径。
///
/// 返 `Arc<Value>` 而非 `Value`,让多 task 共享同一份 JSON 不拷贝。
///
/// 命中策略:
///   1. cache 有 + 未过期 → 直接返。
///   2. cache 有但过期 → 删 entry,走 DB。
///   3. cache 无 → 走 DB,写回 cache。
///
/// 没传 pool 或两个 id 都没传 → 返 `Arc<Value::Object>` 空对象,不查 DB。
pub async fn load_chars_cached(
    pool: Option<&PgPool>,
    script_id: Option<i64>,
    book_id: Option<i64>,
) -> Arc<Value> {
    if script_id.is_none() && book_id.is_none() {
        return Arc::new(json!({}));
    }
    let pool = match pool {
        Some(p) => p,
        None => return Arc::new(json!({})),
    };
    let key = make_key(script_id, book_id);

    // ── cache hit + 未过期 ─────────────────────────────────────
    if let Some(entry) = CACHE.get(&key) {
        if entry.fetched_at.elapsed() < CHARS_CACHE_TTL {
            return entry.value.clone();
        }
    }
    // 过期:remove 再走 DB(避免持 read guard 期间 write)
    CACHE.remove(&key);

    // ── cache miss / 过期:走 DB ───────────────────────────────
    let value = match load_chars_from_db(pool, script_id, book_id).await {
        Ok(v) => Arc::new(v),
        Err(e) => {
            tracing::warn!(
                error = %e,
                script_id = ?script_id,
                book_id = ?book_id,
                "chars_cache: DB 加载失败,返空对象,不写 cache"
            );
            return Arc::new(json!({}));
        }
    };
    CACHE.insert(
        key,
        CachedChars {
            fetched_at: Instant::now(),
            value: value.clone(),
        },
    );
    value
}

/// 对应 Python `_load_characters_db(script_id, book_id)` 的查询 + 行 → dict 转换。
///
/// 返回 `Value::Object`:
/// ```text
/// {
///   "<name>": {
///     "aliases": [...],
///     "identity": "...",
///     "appearance": "...",
///     "personality": "...",
///     "speech_style": "...",
///     "current_status": "...",
///     "secrets": "...",
///     "sample_dialogue": [...],
///     "priority": 100,
///     "token_budget": 450,
///   },
///   ...
/// }
/// ```
async fn load_chars_from_db(
    pool: &PgPool,
    script_id: Option<i64>,
    book_id: Option<i64>,
) -> anyhow::Result<Value> {
    // Python 是 script_id 优先,book_id elif。
    // 这里完全 mirror —— 两个都传时只用 script_id。
    let (sql, bind_val): (&str, i64) = if let Some(sid) = script_id {
        (
            "select name, aliases, identity, appearance, personality, \
                    speech_style, current_status, secrets, sample_dialogue, \
                    token_budget, priority \
             from character_cards \
             where enabled = true and script_id = $1 \
             order by priority desc, id asc",
            sid,
        )
    } else if let Some(bid) = book_id {
        (
            "select name, aliases, identity, appearance, personality, \
                    speech_style, current_status, secrets, sample_dialogue, \
                    token_budget, priority \
             from character_cards \
             where enabled = true and book_id = $1 \
             order by priority desc, id asc",
            bid,
        )
    } else {
        return Ok(json!({}));
    };

    let rows = sqlx::query(sql).bind(bind_val).fetch_all(pool).await?;
    use sqlx::Row;
    let mut out = serde_json::Map::new();
    for r in rows {
        let name: String = r.try_get("name").unwrap_or_default();
        if name.is_empty() {
            continue;
        }
        let aliases: Value = r
            .try_get::<Value, _>("aliases")
            .unwrap_or_else(|_| json!([]));
        let identity: String = r.try_get("identity").unwrap_or_default();
        let appearance: String = r.try_get("appearance").unwrap_or_default();
        let personality: String = r.try_get("personality").unwrap_or_default();
        let speech_style: String = r.try_get("speech_style").unwrap_or_default();
        let current_status: String = r.try_get("current_status").unwrap_or_default();
        let secrets: String = r.try_get("secrets").unwrap_or_default();
        let sample_dialogue: Value = r
            .try_get::<Value, _>("sample_dialogue")
            .unwrap_or_else(|_| json!([]));
        let priority: i32 = r.try_get("priority").unwrap_or(100);
        let token_budget: i32 = r.try_get("token_budget").unwrap_or(450);

        out.insert(
            name,
            json!({
                "aliases": aliases,
                "identity": identity,
                "appearance": appearance,
                "personality": personality,
                "speech_style": speech_style,
                "current_status": current_status,
                "secrets": secrets,
                "sample_dialogue": sample_dialogue,
                "priority": priority,
                "token_budget": token_budget,
            }),
        );
    }
    Ok(Value::Object(out))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn load_chars_returns_empty_when_no_id_no_pool() {
        _clear_cache_for_test();
        let v = load_chars_cached(None, None, None).await;
        assert!(v.as_object().map(|m| m.is_empty()).unwrap_or(false));
        // 不应写 cache
        assert_eq!(_cache_len_for_test(), 0);
    }

    #[tokio::test]
    async fn load_chars_returns_empty_when_pool_none_but_id_some() {
        _clear_cache_for_test();
        let v = load_chars_cached(None, Some(42), None).await;
        assert!(v.as_object().map(|m| m.is_empty()).unwrap_or(false));
        assert_eq!(_cache_len_for_test(), 0);
    }

    #[test]
    fn make_key_distinguishes_script_and_book_axes() {
        assert_ne!(make_key(Some(1), None), make_key(None, Some(1)));
        assert_eq!(make_key(Some(1), None), make_key(Some(1), None));
        // 两个都给 → key 唯一
        assert_ne!(make_key(Some(1), Some(2)), make_key(Some(1), Some(3)));
    }

    #[test]
    fn cache_ttl_constant_is_60_seconds() {
        assert_eq!(CHARS_CACHE_TTL, Duration::from_secs(60));
    }

    // ── Wave 9-A 补单测 ─────────────────────────────────────────────

    #[test]
    fn make_key_both_none_gives_sentinel_values() {
        // 两个 id 都没传 → (-1, -1)
        let key = make_key(None, None);
        assert_eq!(key, (-1i64, -1i64));
    }

    #[test]
    fn make_key_script_id_and_book_id_differ_from_each_other() {
        // (Some(5), None) ≠ (None, Some(5)):不同轴不能混淆
        let k1 = make_key(Some(5), None);
        let k2 = make_key(None, Some(5));
        assert_ne!(k1, k2, "script_id=5 和 book_id=5 的 key 不能相同");
    }

    #[tokio::test]
    async fn load_chars_both_ids_none_does_not_write_cache() {
        _clear_cache_for_test();
        // 两个 id 都 None,即使有 pool 也不应走 DB 或写 cache
        let _v = load_chars_cached(None, None, None).await;
        assert_eq!(_cache_len_for_test(), 0, "不应写 cache");
    }

    #[tokio::test]
    async fn load_chars_db_error_returns_empty_without_caching() {
        // pool 传 None 代表无法连接 DB,应返回空对象,且不写 cache。
        _clear_cache_for_test();
        let v = load_chars_cached(None, Some(99), Some(77)).await;
        assert!(
            v.as_object().map(|m| m.is_empty()).unwrap_or(false),
            "DB 不可达应返回空对象"
        );
        assert_eq!(_cache_len_for_test(), 0, "DB 失败不应写 cache");
    }
}
