//! branches —— 分支图(branch graph)管理。
//!
//! 完成度: **主路径 + 扩展功能已实现**
//!
//! 子模块对应 Python `rpg/platform_app/branches/`:
//! - `helpers`    : 文本工具 + 状态文件 IO + snapshot 工具(纯函数,完整翻译)
//! - `commits`    : `BranchCommit` struct + hash 工具(纯函数,完整翻译)
//! - `refs`       : `BranchRef` + upsert/checkout(完整实现)
//! - `tree_ops`   : `tree(user, save)` + `collect_ids` + `resolve_commit_id_by_message` + `round_start_node`(完整实现)
//! - `activation` : `continue_from / activate_node / activate_save`(已实现)
//! - `seed`       : `seed_tree` + `migrate_legacy_nodes` + `seed_and_bootstrap`(已实现)
//! - `runtime`    : `record_runtime_turn`(已实现)
//! - `summary`    : `schedule_llm_summary`(stub,依赖 rpg-llm pipeline)
//! - `merge`      : `merge_branch` —— fast-forward / merge commit
//! - `gc`         : `gc_orphaned_commits` —— 孤儿 commit 清理
//! - `maintenance`: `ensure_state_snapshots` / `ensure_summaries`(已实现)
//! - `deletion`   : `delete_subtree` / `rollback_to_message`(已实现)

pub mod activation;
pub mod commits;
pub mod deletion;
pub mod gc;
pub mod helpers;
pub mod maintenance;
pub mod merge;
pub mod refs;
pub mod runtime;
pub mod seed;
pub mod summary;
pub mod tree_ops;

pub use commits::{state_file_hash, state_snapshot_hash, BranchCommit};
pub use helpers::{
    clean_text, compact, first_clause, is_continue, load_state, round_preview, rough_summary,
    snapshot_for_history, MAIN_REF,
};
pub use refs::BranchRef;
pub use tree_ops::TreeResult;

/// 整个分支图的对外服务,持有 pool。
pub struct BranchService {
    pub pool: sqlx::PgPool,
}

impl BranchService {
    pub fn new(pool: sqlx::PgPool) -> Self {
        Self { pool }
    }
}
