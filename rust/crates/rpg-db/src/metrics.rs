//! metrics — DB 查询延迟埋点。
//!
//! 指标列表:
//!   * `db_query_duration_seconds` — Histogram,label `kind`(select/insert/update/delete/upsert) + `crate`
//!   * `db_query_total`            — Counter,label `kind`, `crate`, `status`(ok/error)
//!
//! 使用 `metrics` crate 0.24 API，与 Wave 8-D 已安装的 Prometheus recorder 共享全局注册表。
//!
//! ## 使用方式
//!
//! ```rust,ignore
//! use crate::metrics::query_timed;
//!
//! let result = query_timed!("select", "rpg-db", {
//!     sqlx::query_as::<_, MyRow>(SQL)
//!         .bind(id)
//!         .fetch_optional(pool)
//!         .await
//! });
//! ```

/// 记录一次 DB 查询的延迟与状态。
///
/// - `kind`   — "select" / "insert" / "update" / "delete" / "upsert"
/// - `krate`  — 调用所在 crate 名称，如 "rpg-db"
/// - `dur`    — 查询耗时
/// - `ok`     — true = sqlx::Ok, false = sqlx::Error
pub fn record_db_query(kind: &'static str, krate: &'static str, dur: std::time::Duration, ok: bool) {
    let status = if ok { "ok" } else { "error" };

    metrics::histogram!(
        "db_query_duration_seconds",
        "kind"  => kind,
        "crate" => krate,
    )
    .record(dur.as_secs_f64());

    metrics::counter!(
        "db_query_total",
        "kind"   => kind,
        "crate"  => krate,
        "status" => status,
    )
    .increment(1);
}

/// 计时并上报一次 sqlx 查询。
///
/// 语法：
/// ```rust,ignore
/// let result = query_timed!("select", "rpg-db", {
///     sqlx::query_as::<_, Row>(SQL).bind(x).fetch_optional(pool).await
/// });
/// ```
///
/// - 第一个参数: query kind 字面量 (&'static str)
/// - 第二个参数: crate 名称字面量 (&'static str)
/// - 花括号内: 完整的 sqlx 异步表达式（含 .await）
///
/// 返回 `Result<T, sqlx::Error>`，原样透传给调用方。
#[macro_export]
macro_rules! query_timed {
    ($kind:literal, $krate:literal, { $query:expr }) => {{
        let __t0 = ::std::time::Instant::now();
        let __result = $query;
        let __dur = __t0.elapsed();
        $crate::metrics::record_db_query($kind, $krate, __dur, __result.is_ok());
        __result
    }};
}

// Re-export 让 repos 模块直接用 `use crate::metrics::record_db_query`
pub use record_db_query as record;

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    /// 验证 record_db_query 在无 recorder 时不 panic (metrics 0.24 noop recorder)。
    #[test]
    fn record_ok_no_panic() {
        record_db_query("select", "rpg-db", Duration::from_millis(5), true);
        record_db_query("insert", "rpg-db", Duration::from_millis(10), true);
        record_db_query("update", "rpg-db", Duration::from_millis(3), false);
        record_db_query("delete", "rpg-db", Duration::from_micros(800), true);
        record_db_query("upsert", "rpg-db", Duration::from_millis(12), true);
    }

    /// 验证 record_db_query 对 error 状态上报不 panic。
    #[test]
    fn record_error_no_panic() {
        record_db_query("select", "rpg-db", Duration::from_millis(100), false);
        record_db_query("upsert", "rpg-db", Duration::from_millis(200), false);
    }

    /// 验证 query_timed! macro 编译通过，且能正确穿透 Ok 返回值。
    #[test]
    fn macro_compiles_and_passthrough() {
        // 用同步闭包模拟 Ok/Err 结果，验证 macro 不改变值语义
        fn fake_ok() -> Result<i32, sqlx::Error> {
            Ok(42)
        }
        fn fake_err() -> Result<i32, sqlx::Error> {
            Err(sqlx::Error::RowNotFound)
        }

        let r_ok = query_timed!("select", "rpg-db", { fake_ok() });
        assert!(r_ok.is_ok());
        assert_eq!(r_ok.unwrap(), 42);

        let r_err = query_timed!("insert", "rpg-db", { fake_err() });
        assert!(r_err.is_err());
    }
}
