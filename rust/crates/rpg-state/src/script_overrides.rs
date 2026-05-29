//! script_overrides.rs — 剧本 overrides 从 DB lazy load + 写入 state
//!
//! 对应 Python: `rpg/state/core.py::_load_script_overrides` + `_detect_active_script_key`。
//!
//! Python 原版 `_load_script_overrides()` 是 `@lru_cache(maxsize=1)` 的全量
//! 拉取(所有 script 的 overrides 一次取完,按 `script_key` 字典存)。Rust 侧
//! 不再全量缓存(server 端常驻进程下"所有剧本"可能上千条;按需 per-save 拉
//! 一份即可),改成:
//!
//! 1. [`load_for_script`] — 按 `script_id` 查 DB,返回 `Option<Value>`。
//!    内部走 `rpg_db::repos::script_overrides::get`(已就绪),失败 fallback
//!    到 raw `sqlx::query` 兜底,保留与 Python `load_all_overrides_by_key`
//!    join 同语义的 raw SQL 路径。
//! 2. [`load_script_overrides`] — 接 `save_id`,先 raw SQL 解析出 script_id,
//!    再调 [`load_for_script`]。结果写到 state 上 `worldline.script_overrides`
//!    供 context_engine 渲染层读取。
//!
//! 与 Python 差异:
//! - `_detect_active_script_key` 留在 Python 侧 ContextEngine,这里不迁;
//!   Rust 侧 context 渲染由 rpg-context crate 接管时再做。
//! - DB 不可用 fallback 到 `modules/_script_overrides/*.json` 的逻辑不迁;
//!   server 部署默认必有 DB,缺 DB 的本地 fallback 等真有 desktop 模式再补。

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::PgPool;
use thiserror::Error;
use tracing::warn;

use crate::state::GameState;

#[derive(Debug, Error)]
pub enum ScriptOverridesError {
    #[error("db error: {0}")]
    Db(#[from] sqlx::Error),
    #[error("save_id {0} 没有关联 script_id")]
    NoScript(i64),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScriptOverridesPayload {
    pub script_id: i64,
    pub data: Value,
}

/// 按 script_id 查 overrides;无记录返回 None。
///
/// 优先走 rpg-db 的 typed repo,失败 fallback 到 raw SQL(留兜底口)。
pub async fn load_for_script(
    pool: &PgPool,
    script_id: i64,
) -> Result<Option<ScriptOverridesPayload>, ScriptOverridesError> {
    // 1) 首选:rpg-db typed repo
    match rpg_db::repos::script_overrides::get(pool, script_id).await {
        Ok(Some(row)) => {
            return Ok(Some(ScriptOverridesPayload {
                script_id: row.script_id,
                data: row.data,
            }))
        }
        Ok(None) => return Ok(None),
        Err(e) => {
            warn!(
                target: "rpg_state::script_overrides",
                "rpg_db::repos::script_overrides::get failed ({e}),fallback to raw SQL"
            );
        }
    }
    // 2) fallback:raw SQL(与 Python 一致的 SELECT data FROM script_overrides
    //    WHERE script_id = $1)。
    let row: Option<(serde_json::Value,)> = sqlx::query_as(
        "SELECT data FROM script_overrides WHERE script_id = $1",
    )
    .bind(script_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|(data,)| ScriptOverridesPayload {
        script_id,
        data,
    }))
}

/// 按 save_id 反向 join 出 script_id,再调 [`load_for_script`]。
///
/// 同时把结果写回 state.data.worldline.script_overrides(`{}` 表示无记录),
/// 让 context 渲染层不用再做 DB 查询。
pub async fn load_script_overrides(
    state: &mut GameState,
    pool: &PgPool,
    save_id: i64,
) -> Result<Option<ScriptOverridesPayload>, ScriptOverridesError> {
    // 1) save_id → script_id(raw SQL,saves repo 暂未落 rpg-db)
    let script_row: Option<(Option<i64>,)> = sqlx::query_as(
        "SELECT script_id FROM game_saves WHERE id = $1",
    )
    .bind(save_id)
    .fetch_optional(pool)
    .await?;
    let script_id = match script_row.and_then(|(opt,)| opt) {
        Some(id) => id,
        None => return Err(ScriptOverridesError::NoScript(save_id)),
    };

    // 2) 拉 overrides
    let payload = load_for_script(pool, script_id).await?;

    // 3) 写回 state(worldline.script_overrides — task 53 同款 namespace)。
    //    无记录就写空 object,避免 context 层每次都查"是不是 None"。
    let data = payload
        .as_ref()
        .map(|p| p.data.clone())
        .unwrap_or_else(|| Value::Object(serde_json::Map::new()));
    let pointer = "worldline.script_overrides";
    if let Err(e) = state.set_path(pointer, data) {
        warn!(
            target: "rpg_state::script_overrides",
            "set_path({pointer}) failed: {e}"
        );
    }
    Ok(payload)
}
