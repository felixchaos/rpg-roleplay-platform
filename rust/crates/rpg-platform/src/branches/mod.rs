//! branches —— 分支图(branch graph)管理。
//!
//! 完成度: **骨架 + 关键路径**
//!
//! 子模块对应 Python `rpg/platform_app/branches/`:
//! - `helpers`    : 文本工具 + 状态文件 IO + snapshot 工具(纯函数,完整翻译)
//! - `commits`    : `BranchCommit` struct + hash 工具(纯函数,完整翻译)
//! - `refs`       : `BranchRef` + upsert/checkout(骨架 + 主路径)
//! - `tree_ops`   : `tree(user, save)` —— 主路径
//! - `activation` : `continue_from / activate_node / activate_save` —— 骨架
//! - `seed`       : `seed_tree` —— 骨架(细节 TODO)
//! - `runtime`    : `record_runtime_turn` —— 骨架
//! - `summary`    : `schedule_llm_summary` —— TODO(依赖 rpg-llm pipeline)
//! - `maintenance`/`deletion` —— TODO 占位

pub mod activation;
pub mod commits;
pub mod deletion;
pub mod helpers;
pub mod maintenance;
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
