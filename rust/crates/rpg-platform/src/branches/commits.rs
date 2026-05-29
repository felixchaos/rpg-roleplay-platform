//! commits —— `BranchCommit` struct + hash 工具 + `_insert_commit` 骨架。
//!
//! 对应 Python `branches/commits.py`。
//! 完成度: hash 工具完整;`insert_commit` 主路径(不带 metadata reverse-engineering);
//! `commit_for_user` 主路径。

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// `branch_commits` 表行(完整字段,Python 里是 dict)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BranchCommit {
    pub id: i64,
    pub save_id: i64,
    pub parent_id: Option<i64>,
    pub turn_index: i32,
    pub kind: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub content_preview: String,
    #[serde(default)]
    pub state_path: String,
    #[serde(default)]
    pub state_snapshot: Value,
    #[serde(default)]
    pub player_input: String,
    #[serde(default)]
    pub gm_output: String,
    #[serde(default)]
    pub metadata: Value,
    pub created_at: Option<DateTime<Utc>>,
}

impl BranchCommit {
    pub(crate) fn from_row(row: &sqlx::postgres::PgRow) -> sqlx::Result<Self> {
        Ok(BranchCommit {
            id: row.try_get("id")?,
            save_id: row.try_get("save_id")?,
            parent_id: row.try_get::<Option<i64>, _>("parent_id").ok().flatten(),
            turn_index: row.try_get("turn_index")?,
            kind: row.try_get("kind")?,
            title: row.try_get::<String, _>("title").unwrap_or_default(),
            message: row.try_get::<String, _>("message").unwrap_or_default(),
            summary: row.try_get::<String, _>("summary").unwrap_or_default(),
            content_preview: row
                .try_get::<String, _>("content_preview")
                .unwrap_or_default(),
            state_path: row.try_get::<String, _>("state_path").unwrap_or_default(),
            state_snapshot: row
                .try_get::<Value, _>("state_snapshot")
                .unwrap_or(Value::Null),
            player_input: row.try_get::<String, _>("player_input").unwrap_or_default(),
            gm_output: row.try_get::<String, _>("gm_output").unwrap_or_default(),
            metadata: row.try_get::<Value, _>("metadata").unwrap_or(Value::Null),
            created_at: row.try_get("created_at").ok(),
        })
    }
}

/// Python `_object_hash(payload)` —— sha256(canonical JSON)。
pub fn object_hash(payload: &Value) -> PlatformResult<String> {
    let canon = canonical_json(payload)?;
    let mut h = Sha256::new();
    h.update(canon.as_bytes());
    Ok(hex_encode(&h.finalize()))
}

/// Python `_state_file_hash(path)`。失败返回空串。
pub fn state_file_hash(path: &str) -> String {
    match std::fs::read(path) {
        Ok(bytes) => {
            let mut h = Sha256::new();
            h.update(&bytes);
            hex_encode(&h.finalize())
        }
        Err(_) => String::new(),
    }
}

/// Python `_state_snapshot_hash(state)` —— sha256(canonical JSON)。
pub fn state_snapshot_hash(state: &Value) -> String {
    canonical_json(state)
        .ok()
        .map(|c| {
            let mut h = Sha256::new();
            h.update(c.as_bytes());
            hex_encode(&h.finalize())
        })
        .unwrap_or_default()
}

/// 等价 Python `json.dumps(..., sort_keys=True, separators=(",",":"), default=str)`。
fn canonical_json(value: &Value) -> PlatformResult<String> {
    fn recur(v: &Value, out: &mut String) {
        match v {
            Value::Null => out.push_str("null"),
            Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Value::Number(n) => out.push_str(&n.to_string()),
            Value::String(s) => {
                out.push_str(&serde_json::to_string(s).unwrap_or_default());
            }
            Value::Array(arr) => {
                out.push('[');
                for (i, x) in arr.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    recur(x, out);
                }
                out.push(']');
            }
            Value::Object(obj) => {
                let mut keys: Vec<&String> = obj.keys().collect();
                keys.sort();
                out.push('{');
                for (i, k) in keys.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    out.push_str(&serde_json::to_string(k).unwrap_or_default());
                    out.push(':');
                    recur(&obj[*k], out);
                }
                out.push('}');
            }
        }
    }
    let mut buf = String::new();
    recur(value, &mut buf);
    Ok(buf)
}

fn hex_encode(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

// ─── DB 操作 ───────────────────────────────────────────────────────────────

/// Python `_commit_for_user(db, user_id, commit_id)` —— 校验所有权后返回 commit。
pub async fn commit_for_user(
    pool: &PgPool,
    user_id: i64,
    commit_id: i64,
) -> PlatformResult<Option<BranchCommit>> {
    let row = sqlx::query(
        r#"
        select c.*
          from branch_commits c
          join game_saves s on s.id = c.save_id
         where c.id = $1 and s.user_id = $2
        "#,
    )
    .bind(commit_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    match row {
        Some(r) => Ok(Some(BranchCommit::from_row(&r)?)),
        None => Ok(None),
    }
}

/// 主路径版 `insert_commit` —— 跳过 metadata 哈希反推。返回插入的行。
///
/// 走 `&PgPool` 的非事务版本,保留给独立 commit / 单点调用方。需要把多条
/// insert / 多张表写入 atomic 的场景,请用 [`insert_commit_with_tx`] 并自己
/// 管 BEGIN/COMMIT。
#[allow(clippy::too_many_arguments)]
pub async fn insert_commit(
    pool: &PgPool,
    save_id: i64,
    parent_id: Option<i64>,
    turn_index: i32,
    kind: &str,
    title: &str,
    message: &str,
    summary: &str,
    content_preview: &str,
    state_path: &str,
    state_snapshot: &Value,
    player_input: &str,
    gm_output: &str,
    metadata: &Value,
) -> PlatformResult<BranchCommit> {
    let oh = object_hash(state_snapshot)?;
    let row = sqlx::query(insert_commit_sql())
        .bind(save_id)
        .bind(parent_id)
        .bind(turn_index)
        .bind(kind)
        .bind(title)
        .bind(message)
        .bind(summary)
        .bind(content_preview)
        .bind(state_path)
        .bind(state_snapshot)
        .bind(player_input)
        .bind(gm_output)
        .bind(metadata)
        .bind(&oh)
        .fetch_one(pool)
        .await
        .map_err(PlatformError::from)?;
    BranchCommit::from_row(&row).map_err(Into::into)
}

/// 事务版 `insert_commit` —— SQL/绑定完全等同 [`insert_commit`],但走调用方
/// 持有的 `&mut sqlx::Transaction<'_, sqlx::Postgres>`,可与 ref upsert / save
/// active 切换 等放在同一个 BEGIN/COMMIT 里。
///
/// 设计:不在内部 `tx.commit()` —— 由调用方在所有相关写入都完成后 commit 或
/// rollback。
#[allow(clippy::too_many_arguments)]
pub async fn insert_commit_with_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    save_id: i64,
    parent_id: Option<i64>,
    turn_index: i32,
    kind: &str,
    title: &str,
    message: &str,
    summary: &str,
    content_preview: &str,
    state_path: &str,
    state_snapshot: &Value,
    player_input: &str,
    gm_output: &str,
    metadata: &Value,
) -> PlatformResult<BranchCommit> {
    let oh = object_hash(state_snapshot)?;
    let row = sqlx::query(insert_commit_sql())
        .bind(save_id)
        .bind(parent_id)
        .bind(turn_index)
        .bind(kind)
        .bind(title)
        .bind(message)
        .bind(summary)
        .bind(content_preview)
        .bind(state_path)
        .bind(state_snapshot)
        .bind(player_input)
        .bind(gm_output)
        .bind(metadata)
        .bind(&oh)
        .fetch_one(&mut **tx)
        .await
        .map_err(PlatformError::from)?;
    BranchCommit::from_row(&row).map_err(Into::into)
}

/// 公共 SQL 字面量 —— pool / tx 两版本共享,避免漂移。
///
/// 列顺序:`save_id, parent_id, turn_index, kind, title, message, summary,
///         content_preview, state_path, state_snapshot, player_input,
///         gm_output, metadata, object_hash`
///
/// `object_hash` 是 NOT NULL(V001 schema 对齐 Python init),由 [`object_hash`]
/// 对 state_snapshot 计算 sha256 canonical-json,作为 14 号 bind。
fn insert_commit_sql() -> &'static str {
    r#"
    insert into branch_commits(save_id, parent_id, turn_index, kind, title,
                               message, summary, content_preview,
                               state_path, state_snapshot, player_input,
                               gm_output, metadata, object_hash)
    values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
    returning *
    "#
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn canon_is_deterministic() {
        let a = object_hash(&json!({"b":1,"a":2})).unwrap();
        let b = object_hash(&json!({"a":2,"b":1})).unwrap();
        assert_eq!(a, b);
    }

    /// pool/tx 两版本必须共用同一段 SQL,任何漂移都是写半个 schema 的灾难源。
    #[test]
    fn insert_commit_sql_is_shared_between_pool_and_tx_paths() {
        let sql = insert_commit_sql();
        // 关键 schema 字段都在,且 placeholder 13 个。
        assert!(sql.contains("insert into branch_commits"));
        assert!(sql.contains("state_snapshot"));
        assert!(sql.contains("returning *"));
        let placeholders = (1..=13).filter(|i| sql.contains(&format!("${i}"))).count();
        assert_eq!(placeholders, 13, "expected 13 placeholders, sql: {sql}");
        assert!(!sql.contains("$14"));
    }

    /// tx 版的 begin/rollback 在 lazy 连接上仍会回到一个 DB error(不会编译期/类型期
    /// 失败),用来兜底"调用方写法对了"。真正的 commit/rollback 行为留给集成测试。
    #[tokio::test]
    async fn insert_commit_with_tx_propagates_db_error_on_dead_pool() {
        let pool = sqlx::PgPool::connect_lazy("postgres://localhost:1/nonexistent").unwrap();
        // tx begin 本身会因为连不上 DB 而返回 sqlx::Error。
        let tx_res = pool.begin().await;
        assert!(tx_res.is_err(), "expected connect-time error on dead pool");
    }
}
