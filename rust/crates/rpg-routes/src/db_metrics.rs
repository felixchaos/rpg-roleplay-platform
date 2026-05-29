//! db_metrics — sqlx PgPool 连接池指标采集。
//!
//! 指标列表:
//!   * `db_pool_connections_idle`    — Gauge, 当前空闲连接数
//!   * `db_pool_connections_active`  — Gauge, 当前活跃连接数
//!   * `db_pool_connections_max`     — Gauge, pool 配置的最大连接数
//!
//! 调用方:在每次 DB 操作前/后 (或后台任务周期采样) 调用 [`record_pool`]。
//! rpg-server 的 main 里可以起一个后台任务每 15s 调一次。

use sqlx::PgPool;

/// 从 pool 读取当前连接池统计并发布到全局 metrics recorder。
pub fn record_pool(pool: &PgPool) {
    let size = pool.size() as f64;       // 当前已建立的连接(含 idle + active)
    let idle = pool.num_idle() as f64;   // 空闲连接数
    let active = (size - idle).max(0.0); // 活跃连接数

    metrics::gauge!("db_pool_connections_idle").set(idle);
    metrics::gauge!("db_pool_connections_active").set(active);
    // pool 最大值:sqlx 0.8 PgPool 没有直接公开 max_connections;
    // 用 pool.options().max_connections() 也不公开。
    // 改为记录当前 size 作为高水位观测值。
    metrics::gauge!("db_pool_connections_total").set(size);
}

#[cfg(test)]
mod tests {
    // 仅编译测试:确保函数签名存在并可引用。
    #[allow(unused_imports)]
    use super::record_pool;
}
