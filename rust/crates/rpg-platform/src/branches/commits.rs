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

/// 主路径版 `insert_commit` —— 跳过 metadata 哈希反推。返回插入的 id。
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
    let row = sqlx::query(
        r#"
        insert into branch_commits(save_id, parent_id, turn_index, kind, title,
                                   message, summary, content_preview,
                                   state_path, state_snapshot, player_input,
                                   gm_output, metadata)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        returning *
        "#,
    )
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
    .fetch_one(pool)
    .await
    .map_err(PlatformError::from)?;
    BranchCommit::from_row(&row).map_err(Into::into)
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
}
