//! migrations.rs — 对应 rpg/platform_app/db/migrations.py
//!
//! 设计:
//!   - `MigrationStep` 描述一个版本化迁移条目(id + name + sql 字符串切片)。
//!   - 每条 migration 的 SQL 都来自 `migrations/NNN_<name>.sql` 文件,
//!     通过 `include_str!` 内嵌到二进制中;`migrations/` 是 schema 的"单一事实源"。
//!   - 版本分区:
//!       * v001..v016 一对一对应 Python `rpg/platform_app/db/migrations.py`
//!         MIGRATIONS 列表(以及 Python `init.py` 的基表),Wave 5-A 起 v001
//!         直接对齐 Python init_db() 完整 schema(bigserial 主键、users/scripts/
//!         game_saves 等基表全部内置),后续 v019/v021/v022 的 "create if not
//!         exists" 在 fresh Rust DB 上变成 no-op 但仍保留以兼容 Python 共享库。
//!       * v017..v022 是 Rust 端原生新增/重排(详见各文件顶部注释)。
//!   - `run_migrations(pool)` 负责:
//!       1. 用 pg_advisory_lock 串行化 DDL
//!       2. 确保 schema_migrations 表存在
//!       3. 跳过已应用的版本,顺序执行未应用的版本
//!
//! Python 原版用 `pg_advisory_lock`(阻塞),这里用 `pg_try_advisory_lock` 轮询
//! (避免 sqlx 长时间持有事务连接),超时后返回 `DbError::LockTimeout`。
//!
//! ## SQL 文件多语句执行
//! 每个 .sql 文件可以包含多条 DDL 语句,以分号分隔。底层走 Postgres simple query
//! 协议(`sqlx::query(&str).execute(pool)`,无参数 → 不走 prepared statement),
//! 支持一次发送多条语句。`DO $$ ... $$` 块内的 `;` 不会被切分,因为整段文件
//! 作为一个 SQL 字符串送入,Postgres 自己解析。

use sqlx::postgres::PgPool;
use std::time::{Duration, Instant};

use crate::pool::DbError;

// Postgres advisory lock ID,与 Python 端保持一致:
//   'rpg_platform_migrate' → 0x52504D49475254AB
const MIGRATION_ADVISORY_LOCK_ID: i64 = 0x52504D49475254ABu64 as i64;

/// 单个版本化迁移条目。
///
/// 对应 Python: `(version: int, name: str, statements: list[str])`
/// Rust 端把所有语句合并到同一个 `.sql` 文件,由 Postgres 自己解析多语句。
pub struct MigrationStep {
    pub id: i64,
    pub name: &'static str,
    /// 本步骤的 SQL 文本(整个 .sql 文件)。
    pub sql: &'static str,
}

// ──────────────────────────────────────────────────────────────
//  迁移 SQL 内嵌(全部来自 migrations/ 目录)
// ──────────────────────────────────────────────────────────────

static SQL_001: &str = include_str!("../migrations/001_init.sql");
static SQL_002: &str = include_str!("../migrations/002_ensure_context_runs_status.sql");
static SQL_003: &str = include_str!("../migrations/003_ensure_model_apis_base_url.sql");
static SQL_004: &str = include_str!("../migrations/004_user_api_credentials.sql");
static SQL_005: &str = include_str!("../migrations/005_token_usage.sql");
static SQL_006: &str = include_str!("../migrations/006_user_preferences.sql");
static SQL_007: &str = include_str!("../migrations/007_login_audit.sql");
static SQL_008: &str = include_str!("../migrations/008_user_personas_and_character_cards.sql");
static SQL_009: &str = include_str!("../migrations/009_import_jobs.sql");
static SQL_010: &str = include_str!("../migrations/010_pgvector_columns_and_hnsw.sql");
static SQL_011: &str = include_str!("../migrations/011_user_runtime_db_backed.sql");
static SQL_012: &str = include_str!("../migrations/012_import_jobs_kind_for_durable_sync.sql");
static SQL_013: &str = include_str!("../migrations/013_import_jobs_single_active_per_script.sql");
static SQL_014: &str = include_str!("../migrations/014_script_timeline_anchors.sql");
static SQL_015: &str = include_str!("../migrations/015_worldline_convergence_anchors.sql");
static SQL_016: &str = include_str!("../migrations/016_script_overrides.sql");
static SQL_017: &str = include_str!("../migrations/017_sessions_hashed_token.sql");
static SQL_018: &str = include_str!("../migrations/018_stop_signals.sql");
static SQL_019: &str = include_str!("../migrations/019_runtime_checkouts.sql");
static SQL_020: &str = include_str!("../migrations/020_user_card_public_audit.sql");
static SQL_021: &str = include_str!("../migrations/021_scripts_and_chapters.sql");
static SQL_022: &str = include_str!("../migrations/022_branches_extended_columns.sql");
static SQL_023: &str = include_str!("../migrations/023_phase_digests.sql");
static SQL_024: &str = include_str!("../migrations/024_provider_rename.sql");

/// 所有迁移的静态列表。
///
/// 顺序必须严格按 id 升序;`run_migrations` 依赖此约束跳过已应用版本。
pub static MIGRATIONS: &[MigrationStep] = &[
    MigrationStep { id: 1,  name: "initial_schema",                          sql: SQL_001 },
    MigrationStep { id: 2,  name: "ensure_context_runs_status",              sql: SQL_002 },
    MigrationStep { id: 3,  name: "ensure_model_apis_base_url",              sql: SQL_003 },
    MigrationStep { id: 4,  name: "user_api_credentials",                    sql: SQL_004 },
    MigrationStep { id: 5,  name: "token_usage",                             sql: SQL_005 },
    MigrationStep { id: 6,  name: "user_preferences",                        sql: SQL_006 },
    MigrationStep { id: 7,  name: "login_audit",                             sql: SQL_007 },
    MigrationStep { id: 8,  name: "user_personas_and_character_cards",       sql: SQL_008 },
    MigrationStep { id: 9,  name: "import_jobs",                             sql: SQL_009 },
    MigrationStep { id: 10, name: "pgvector_columns_and_hnsw",               sql: SQL_010 },
    MigrationStep { id: 11, name: "user_runtime_db_backed",                  sql: SQL_011 },
    MigrationStep { id: 12, name: "import_jobs_kind_for_durable_sync",       sql: SQL_012 },
    MigrationStep { id: 13, name: "import_jobs_single_active_per_script",    sql: SQL_013 },
    MigrationStep { id: 14, name: "script_timeline_anchors",                 sql: SQL_014 },
    MigrationStep { id: 15, name: "worldline_convergence_anchors",           sql: SQL_015 },
    MigrationStep { id: 16, name: "script_overrides",                        sql: SQL_016 },
    MigrationStep { id: 17, name: "sessions_hashed_token",                   sql: SQL_017 },
    MigrationStep { id: 18, name: "stop_signals",                            sql: SQL_018 },
    MigrationStep { id: 19, name: "runtime_checkouts",                       sql: SQL_019 },
    MigrationStep { id: 20, name: "user_card_public_audit",                  sql: SQL_020 },
    MigrationStep { id: 21, name: "scripts_and_chapters",                    sql: SQL_021 },
    MigrationStep { id: 22, name: "branches_extended_columns",               sql: SQL_022 },
    MigrationStep { id: 23, name: "phase_digests",                           sql: SQL_023 },
    MigrationStep { id: 24, name: "provider_rename",                         sql: SQL_024 },
];

// ──────────────────────────────────────────────────────────────
//  Advisory lock(try 轮询版)
// ──────────────────────────────────────────────────────────────

/// 尝试获取 Postgres advisory lock,最多等待 `timeout_ms` 毫秒。
///
/// 对应 Python `_migration_advisory_lock()` 的 `pg_advisory_lock`(阻塞版),
/// 这里改用 `pg_try_advisory_lock` 轮询,避免 sqlx 连接长期挂起。
async fn acquire_advisory_lock(pool: &PgPool, timeout_ms: u64) -> Result<(), DbError> {
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    loop {
        let locked: bool = sqlx::query_scalar(
            "SELECT pg_try_advisory_lock($1)",
        )
        .bind(MIGRATION_ADVISORY_LOCK_ID)
        .fetch_one(pool)
        .await?;

        if locked {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(DbError::LockTimeout { timeout_ms });
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

/// 释放 advisory lock(即使失败也只记日志,不 panic)。
async fn release_advisory_lock(pool: &PgPool) {
    let result: Result<bool, _> = sqlx::query_scalar(
        "SELECT pg_advisory_unlock($1)",
    )
    .bind(MIGRATION_ADVISORY_LOCK_ID)
    .fetch_one(pool)
    .await;

    if let Err(e) = result {
        tracing::warn!("pg_advisory_unlock failed (ignored): {e}");
    }
}

// ──────────────────────────────────────────────────────────────
//  公开入口
// ──────────────────────────────────────────────────────────────

/// 运行所有未应用的迁移。
///
/// 对应 Python `_apply_versioned_migrations()` + `_migration_advisory_lock()`。
///
/// 步骤:
/// 1. 获取 advisory lock(超时 5000 ms)
/// 2. 确保 `schema_migrations` 表存在
/// 3. 查已应用版本集合
/// 4. 按序执行未应用的迁移,并写入 `schema_migrations`
/// 5. 释放 advisory lock
pub async fn run_migrations(pool: &PgPool) -> Result<(), DbError> {
    const LOCK_TIMEOUT_MS: u64 = 5_000;

    acquire_advisory_lock(pool, LOCK_TIMEOUT_MS).await?;

    let result = do_run_migrations(pool).await;

    release_advisory_lock(pool).await;

    result
}

async fn do_run_migrations(pool: &PgPool) -> Result<(), DbError> {
    // 确保 schema_migrations 表存在(对应 Python 同名 DDL)
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS schema_migrations (
           version  integer PRIMARY KEY,
           name     text    NOT NULL,
           applied_at timestamptz NOT NULL DEFAULT now()
         )",
    )
    .execute(pool)
    .await?;

    // 查询已应用版本
    let applied: Vec<i64> = sqlx::query_scalar("SELECT version FROM schema_migrations")
        .fetch_all(pool)
        .await?;
    let applied_set: std::collections::HashSet<i64> = applied.into_iter().collect();

    for step in MIGRATIONS {
        if applied_set.contains(&step.id) {
            tracing::debug!("migration v{} '{}' already applied, skipping", step.id, step.name);
            continue;
        }

        tracing::info!("applying migration v{} '{}'", step.id, step.name);

        // 整个 .sql 文件作为一条 SQL 字符串送入。Postgres simple query 协议支持
        // 多语句以分号分隔;`DO $$ ... $$` 块由 PG 自己解析,不会因为内部分号被切分。
        sqlx::query(step.sql)
            .execute(pool)
            .await
            .map_err(|e| DbError::Migration(
                format!("v{} '{}': {e}", step.id, step.name)
            ))?;

        sqlx::query(
            "INSERT INTO schema_migrations(version, name) VALUES ($1, $2)
             ON CONFLICT DO NOTHING",
        )
        .bind(step.id)
        .bind(step.name)
        .execute(pool)
        .await?;

        tracing::info!("migration v{} '{}' done", step.id, step.name);
    }

    Ok(())
}

// ──────────────────────────────────────────────────────────────
//  单元测试:静态校验 MIGRATIONS 列表自身的健全性
// ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::MIGRATIONS;

    /// version 必须严格单调递增且无重复(对应 Python `_assert_migrations_monotonic`)。
    #[test]
    fn migrations_strictly_monotonic() {
        let mut last = 0i64;
        for step in MIGRATIONS {
            assert!(
                step.id > last,
                "migration v{} '{}' 顺序错乱:必须严格递增,前一个是 v{}",
                step.id, step.name, last,
            );
            last = step.id;
        }
    }

    /// 每条 migration 的 SQL 文本不能为空(防 include_str! 指错文件)。
    #[test]
    fn migrations_non_empty_sql() {
        for step in MIGRATIONS {
            assert!(
                !step.sql.trim().is_empty(),
                "migration v{} '{}' 的 SQL 内容为空",
                step.id, step.name,
            );
        }
    }

    /// name 必须唯一(配合 schema_migrations 表 name 列做诊断)。
    #[test]
    fn migrations_unique_names() {
        let mut seen = std::collections::HashSet::new();
        for step in MIGRATIONS {
            assert!(
                seen.insert(step.name),
                "migration v{} 名字 '{}' 重复",
                step.id, step.name,
            );
        }
    }

    /// v022 branches_extended_columns 文本里必须包含所有任务列出的扩展列名,
    /// 防止后续 sonnet 误删 alter 语句导致 schema 沉默回退。
    ///
    /// Wave 5-A 后:基表迁回 v001,此处不再要求 v022 含 create table,
    /// 但扩展列 alter 必须仍在;同时基表必须在 v001 出现(由 v001_creates_base_tables 守)。
    #[test]
    fn migration_022_covers_all_extended_columns() {
        let step = MIGRATIONS
            .iter()
            .find(|s| s.id == 22)
            .expect("migration v22 应存在");
        let sql = step.sql;
        for col in &[
            "row_version",
            "dirty",
            "snapshot_hash",
            "turn_at_commit",
            "turn_runtime",
            "state_snapshot",
        ] {
            assert!(
                sql.contains(col),
                "v022 SQL 缺扩展列 '{col}'(任务 Wave 1A 列名清单)"
            );
        }
    }

    /// Wave 5-A 新增:v001 必须建出所有基表(对齐 Python init.py),否则 v004+
    /// 的 FK 在 fresh Rust DB 上全部 dangling。
    #[test]
    fn migration_001_creates_base_tables() {
        let step = MIGRATIONS
            .iter()
            .find(|s| s.id == 1)
            .expect("migration v1 应存在");
        let sql = step.sql;
        // FK 目标表必须全部 create
        for tbl in &[
            "users",
            "scripts",
            "game_saves",
            "branch_nodes",
            "branch_commits",
            "branch_refs",
            "runtime_checkouts",
            "books",
            "documents",
            "script_chapters",
            "game_sessions",
            "messages",
        ] {
            assert!(
                sql.contains(&format!("create table if not exists {tbl}")),
                "v001 缺基表 '{tbl}'",
            );
        }
        // 主键必须是 bigserial(generated by default as identity)而非 uuid
        assert!(
            sql.contains("bigint generated by default as identity primary key"),
            "v001 users/scripts 主键应为 bigserial,与 Python 对齐",
        );
        assert!(
            !sql.contains("app_users"),
            "v001 不应再含 uuid 设计的 app_users",
        );
    }

    /// Wave 5-A 新增:v020 必须在 MIGRATIONS 数组里注册,否则 user_card_public_audit
    /// SQL 文件孤儿化,公开卡 audit 功能无法落库。
    #[test]
    fn migration_020_is_registered() {
        let step = MIGRATIONS
            .iter()
            .find(|s| s.id == 20)
            .expect("migration v20 (user_card_public_audit) 必须注册");
        assert_eq!(step.name, "user_card_public_audit");
        assert!(
            step.sql.contains("user_card_public_audit"),
            "v020 SQL 内容应建 user_card_public_audit 表",
        );
    }

    /// Wave 11.5-A 新增:v024 provider_rename 必须 (a) 在 MIGRATIONS 数组里注册,
    /// (b) SQL 包含 vertex / vertex_ai → agent_platform 的所有 UPDATE/DELETE 语句,
    /// (c) MIGRATIONS 版本号从 1 起严格连续(防止漏注册中间版本)。
    #[test]
    fn migration_024_provider_rename_registered_and_complete() {
        let step = MIGRATIONS
            .iter()
            .find(|s| s.id == 24)
            .expect("migration v24 (provider_rename) 必须注册");
        assert_eq!(step.name, "provider_rename");
        let sql = step.sql;
        // 三张目标表都要被改
        for table in &["model_apis", "model_entries", "user_api_credentials"] {
            assert!(
                sql.contains(table),
                "v024 SQL 缺表 '{table}'",
            );
        }
        // 改名方向: vertex* → agent_platform
        assert!(sql.contains("'vertex'"), "v024 SQL 缺老 id 'vertex'");
        assert!(sql.contains("'vertex_ai'"), "v024 SQL 缺老 id 'vertex_ai'");
        assert!(sql.contains("'agent_platform'"), "v024 SQL 缺新 id 'agent_platform'");
        // 显示名校正
        assert!(
            sql.contains("Agent Platform"),
            "v024 SQL 缺显示名 'Agent Platform' 校正",
        );
    }

    /// Wave 11.5-A 新增:MIGRATIONS 版本号必须从 1 起严格连续,
    /// 否则增量加 migration 时漏一个版本,后续 fresh DB 会缺 schema。
    #[test]
    fn migrations_ids_are_contiguous_from_1() {
        for (i, step) in MIGRATIONS.iter().enumerate() {
            let expected = (i as i64) + 1;
            assert_eq!(
                step.id, expected,
                "migration 列表 v{} 不连续 — 期望 v{expected},实际 v{}({})",
                step.id, step.id, step.name,
            );
        }
    }

    /// runtime_checkouts 在 Rust 端跨 019+022 两次 migration 装配,
    /// 022 必须只走 add column if not exists,不能 drop / rename(会丢数据)。
    #[test]
    fn migration_022_runtime_checkouts_uses_only_idempotent_alter() {
        let step = MIGRATIONS.iter().find(|s| s.id == 22).unwrap();
        let sql = step.sql.to_lowercase();
        // 必须看到 alter table runtime_checkouts add column
        assert!(
            sql.contains("alter table runtime_checkouts add column if not exists"),
            "v022 应只用 idempotent alter add column",
        );
        // 不能出现 drop column / rename column / drop table runtime_checkouts
        assert!(
            !sql.contains("drop column") && !sql.contains("rename column"),
            "v022 不能对 runtime_checkouts 做破坏性变更",
        );
        assert!(
            !sql.contains("drop table runtime_checkouts"),
            "v022 不能删 runtime_checkouts 表",
        );
    }
}
