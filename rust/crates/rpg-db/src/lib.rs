//! rpg-db — sqlx::PgPool + 迁移器 + repo 层
//! 对应 Python: rpg/db/*.sql + rpg/platform_app/db/

pub mod metrics;
pub mod migrations;
pub mod pool;
pub mod repos;

pub use pool::DbError;
pub use sqlx::PgPool;
