//! branches/runtime —— 把回合落地到 commit + 推 ref + dirty 标记 + bootstrap。
//!
//! 对应 Python `branches/runtime.py`(~260 行)。
//!
//! 完成度: **主路径完整**(`record_runtime_turn` / `persist_runtime_state` /
//!   `mark_runtime_dirty` / `bootstrap_runtime_binding`)。
//!
//! 关键事务边界(对照 Python `connect()` 上下文 = 1 tx):
//!   1. record_runtime_turn: **1 tx** —— select parent + insert commit + upsert ref + update save +
//!      write_checkout 全部在一个 BEGIN..COMMIT。失败 rollback。
//!   2. persist_runtime_state: **1 tx** —— update game_saves + upsert runtime_checkouts。
//!   3. bootstrap_runtime_binding: 多步,但每步本身是 atomic(seed_tree / activate_save 内部都用 tx)。
//!   4. spawn LLM summary 在 tx **之外**(commit 已落,异步补 summary 不阻塞返回)。

use serde_json::Value;
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};
use crate::runtime::{self as runtime_mod, UserRuntime};

use super::commits::{state_snapshot_hash, BranchCommit};
use super::helpers::{commit_state, rough_summary, round_preview};
use super::refs::{find_or_create_ref_for_commit, set_save_active, write_checkout};
use super::seed::seed_tree;
use super::summary::schedule_llm_summary;

/// `record_runtime_turn` 的成功返回:新 commit + 推后的 ref id。
#[derive(Debug, Clone)]
pub struct RecordedTurn {
    pub commit: BranchCommit,
    pub ref_id: i64,
}

/// Python `record_runtime_turn(...)` 主路径。
///
/// 参数:
/// - `parent_commit_id`: runtime 当前指向的 commit(从 `user_runtime.active_commit_id` 拿)。
/// - `ref_id`: 当前活跃 ref 的 id(`user_runtime.active_ref_id`);为 None 时从 DB 重查。
/// - `state`: 当前 runtime state JSON(就是 game_state.data)。
///
/// 失败/前置不满足时返回:
/// - `PlatformError::NotFound("runtime 指向的父节点不存在")`
/// - `PlatformError::Forbidden("runtime 不属于当前用户")`
///
/// 返回新 commit + ref_id(调用方可写回 user_runtime)。
#[allow(clippy::too_many_arguments)]
pub async fn record_runtime_turn(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    parent_commit_id: i64,
    ref_id: Option<i64>,
    player_input: &str,
    gm_output: &str,
    state: &Value,
    state_snapshot_path: &str,
) -> PlatformResult<RecordedTurn> {
    if save_id <= 0 || parent_commit_id <= 0 {
        return Err(PlatformError::validation("runtime 缺少存档或节点"));
    }
    let turn = state.get("turn").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
    let summary = rough_summary(player_input, gm_output, 22);
    let preview = round_preview(player_input, gm_output, 260);
    let nonce = random_hex(8);

    // ── 单事务:select parent → insert commit → upsert ref → set save active → write checkout
    let mut tx = pool.begin().await?;

    // 1. 取 parent commit + 校验同 save。
    let parent_row = sqlx::query(
        "select id from branch_commits where id = $1 and save_id = $2",
    )
    .bind(parent_commit_id)
    .bind(save_id)
    .fetch_optional(&mut *tx)
    .await?;
    if parent_row.is_none() {
        tx.rollback().await.ok();
        return Err(PlatformError::not_found("runtime 指向的父节点不存在"));
    }

    // 2. 校验 save 归属。
    let save_row = sqlx::query(
        "select user_id, active_commit_id from game_saves where id = $1",
    )
    .bind(save_id)
    .fetch_optional(&mut *tx)
    .await?;
    let save_row = match save_row {
        Some(r) => r,
        None => {
            tx.rollback().await.ok();
            return Err(PlatformError::not_found("存档不存在"));
        }
    };
    let save_owner: i64 = save_row.try_get("user_id").unwrap_or(0);
    if save_owner != user_id {
        tx.rollback().await.ok();
        return Err(PlatformError::forbidden("runtime 不属于当前用户"));
    }
    let fresh_active: Option<i64> = save_row.try_get("active_commit_id").ok().flatten();

    // 3. 若 DB 里 active_commit_id 已比 caller 传的更"新"(被别的请求 fast-forward),
    //    用最新的当父节点(对应 Python 里 fresh_parent 重读)。
    let effective_parent_id = match fresh_active {
        Some(active) if active != parent_commit_id => {
            // 校验 fresh active 仍在同 save 下,确认有效再用。
            let still_valid: Option<(i64,)> = sqlx::query_as(
                "select id from branch_commits where id = $1 and save_id = $2",
            )
            .bind(active)
            .bind(save_id)
            .fetch_optional(&mut *tx)
            .await?;
            if still_valid.is_some() {
                active
            } else {
                parent_commit_id
            }
        }
        _ => parent_commit_id,
    };

    // 4. 解析 ref_id —— 没有就找 / 建一个指向 effective_parent 的 ref。
    let effective_ref_id = match ref_id {
        Some(rid) if rid > 0 => rid,
        _ => find_or_create_ref(&mut tx, save_id, effective_parent_id).await?,
    };

    // 5. insert commit(kind=round,player_input + gm_output 都放在同一条)。
    let metadata = serde_json::json!({
        "source": "runtime",
        "parent_commit_id": effective_parent_id,
        "nonce": nonce,
    });
    let oh = super::commits::object_hash(state)?;
    let new_row = sqlx::query(
        r#"
        insert into branch_commits(save_id, parent_id, turn_index, kind, title,
                                   message, summary, content_preview,
                                   state_path, state_snapshot, player_input,
                                   gm_output, metadata, object_hash)
        values ($1,$2,$3,'round',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        returning *
        "#,
    )
    .bind(save_id)
    .bind(effective_parent_id)
    .bind(turn)
    .bind(format!("第 {} 回合", turn))
    .bind(&summary)
    .bind(&summary)
    .bind(&preview)
    .bind(state_snapshot_path)
    .bind(state)
    .bind(player_input)
    .bind(gm_output)
    .bind(metadata)
    .bind(&oh)
    .fetch_one(&mut *tx)
    .await?;
    let new_commit = BranchCommit::from_row(&new_row)?;

    // 6. 推 ref(把 effective_ref_id 指到新 commit + 标 active)。
    upsert_ref_by_id(&mut tx, effective_ref_id, new_commit.id, true).await?;

    // 7. 更新 game_saves.active_commit_id / active_branch_ref_id。
    // 注:列名是 `active_branch_ref_id`(game_saves);`active_ref_id` 是 `user_runtime` 的列。
    sqlx::query(
        r#"
        update game_saves
           set active_commit_id = $1,
               active_branch_ref_id = $2,
               updated_at = now()
         where id = $3
        "#,
    )
    .bind(new_commit.id)
    .bind(effective_ref_id)
    .bind(save_id)
    .execute(&mut *tx)
    .await?;

    // 8. 更新 runtime_checkouts(标 dirty=false,turn_runtime/turn_at_commit 同步)。
    let snap_hash = state_snapshot_hash(state);
    sqlx::query(
        r#"
        insert into runtime_checkouts(user_id, save_id, ref_id, commit_id, dirty,
                                       turn_at_commit, turn_runtime, snapshot_hash,
                                       updated_at)
        values ($1, $2, $3, $4, false, $5, $5, $6, now())
        on conflict(user_id, save_id) do update set
          ref_id = excluded.ref_id,
          commit_id = excluded.commit_id,
          dirty = false,
          turn_at_commit = excluded.turn_at_commit,
          turn_runtime = excluded.turn_runtime,
          snapshot_hash = excluded.snapshot_hash,
          updated_at = now()
        "#,
    )
    .bind(user_id)
    .bind(save_id)
    .bind(effective_ref_id)
    .bind(new_commit.id)
    .bind(turn as i64)
    .bind(&snap_hash)
    .execute(&mut *tx)
    .await?;

    tx.commit().await?;

    // ── 事务外:fire-and-forget LLM summary。
    schedule_llm_summary(
        pool.clone(),
        new_commit.id,
        player_input.to_string(),
        gm_output.to_string(),
    );

    Ok(RecordedTurn {
        commit: new_commit,
        ref_id: effective_ref_id,
    })
}

/// Python `persist_runtime_state(...)` 主路径。
///
/// 无损保存当前 runtime state:
/// - 更新 `game_saves.state_snapshot`(同步 active_commit_id / row_version);
/// - upsert `runtime_checkouts`(dirty=false,turn_at_commit/turn_runtime 一致)。
///
/// **不创建新 commit**。
pub async fn persist_runtime_state(
    pool: &PgPool,
    user_id: i64,
    save_id: i64,
    commit_id: i64,
    ref_id: Option<i64>,
    state: &Value,
    runtime_state_path: &str,
) -> PlatformResult<()> {
    if save_id <= 0 || commit_id <= 0 {
        return Err(PlatformError::validation("runtime 缺少存档或节点"));
    }
    let snap_hash = state_snapshot_hash(state);
    let turn = state.get("turn").and_then(|v| v.as_i64()).unwrap_or(0);

    let mut tx = pool.begin().await?;

    // 校验 save 归属。
    let save_row = sqlx::query("select user_id from game_saves where id = $1")
        .bind(save_id)
        .fetch_optional(&mut *tx)
        .await?;
    let save_row = match save_row {
        Some(r) => r,
        None => {
            tx.rollback().await.ok();
            return Err(PlatformError::not_found("存档不存在"));
        }
    };
    let owner: i64 = save_row.try_get("user_id").unwrap_or(0);
    if owner != user_id {
        tx.rollback().await.ok();
        return Err(PlatformError::forbidden("runtime 不属于当前用户"));
    }

    // 更新 game_saves。
    sqlx::query(
        r#"
        update game_saves
           set state_snapshot = $1,
               active_commit_id = $2,
               active_branch_ref_id = $3,
               row_version = row_version + 1,
               updated_at = now()
         where id = $4
        "#,
    )
    .bind(state)
    .bind(commit_id)
    .bind(ref_id)
    .bind(save_id)
    .execute(&mut *tx)
    .await?;

    // upsert runtime_checkouts(无损快照,dirty=false)。
    sqlx::query(
        r#"
        insert into runtime_checkouts(user_id, save_id, ref_id, commit_id,
                                       runtime_state_path, state_snapshot,
                                       snapshot_hash, dirty,
                                       turn_at_commit, turn_runtime, updated_at)
        values ($1, $2, $3, $4, $5, $6, $7, false, $8, $8, now())
        on conflict(user_id, save_id) do update set
          ref_id = excluded.ref_id,
          commit_id = excluded.commit_id,
          runtime_state_path = excluded.runtime_state_path,
          state_snapshot = excluded.state_snapshot,
          snapshot_hash = excluded.snapshot_hash,
          dirty = false,
          turn_at_commit = excluded.turn_at_commit,
          turn_runtime = excluded.turn_runtime,
          updated_at = now()
        "#,
    )
    .bind(user_id)
    .bind(save_id)
    .bind(ref_id)
    .bind(commit_id)
    .bind(runtime_state_path)
    .bind(state)
    .bind(&snap_hash)
    .bind(turn)
    .execute(&mut *tx)
    .await?;

    tx.commit().await?;
    Ok(())
}

/// Python `bootstrap_runtime_binding(user_id)` —— 新用户开存档 / 切换分支必经路径。
///
/// 流程对齐 Python:
/// 1. 取 user 最近一个 save(无 user_id 则取全局最近一个),没 save → 返回 default 空 UserRuntime
/// 2. 优先用 save.active_commit_id 拿 commit;不存在则取该 save 最新 commit
/// 3. 都没有 → 调 `seed_tree` 建 root,再回到 step 1 递归一次(seed 后必有 commit)
/// 4. 找/建 active ref → `set_save_active` → `write_checkout` → `activate_state_snapshot`
///
/// 返回写好元数据的 `UserRuntime`(对应 Python 返回 dict 的形态)。
pub async fn bootstrap_runtime_binding(
    pool: &PgPool,
    user_id: Option<i64>,
) -> PlatformResult<UserRuntime> {
    // ── step 1: 拿最近一个 save (含 owner_id)
    let save_row = match user_id {
        Some(uid) => {
            sqlx::query(
                r#"
                select game_saves.id as save_id,
                       game_saves.user_id as owner_id,
                       game_saves.active_commit_id,
                       game_saves.active_branch_node_id,
                       coalesce(game_saves.state_path, '') as state_path
                  from game_saves
                  join users on users.id = game_saves.user_id
                 where users.id = $1
                 order by game_saves.updated_at desc, game_saves.id desc
                 limit 1
                "#,
            )
            .bind(uid)
            .fetch_optional(pool)
            .await?
        }
        None => {
            sqlx::query(
                r#"
                select game_saves.id as save_id,
                       game_saves.user_id as owner_id,
                       game_saves.active_commit_id,
                       game_saves.active_branch_node_id,
                       coalesce(game_saves.state_path, '') as state_path
                  from game_saves
                  join users on users.id = game_saves.user_id
                 order by game_saves.updated_at desc, game_saves.id desc
                 limit 1
                "#,
            )
            .fetch_optional(pool)
            .await?
        }
    };
    let save = match save_row {
        Some(r) => r,
        None => return Ok(UserRuntime::default()),
    };
    let save_id: i64 = save.try_get("save_id")?;
    let owner_id: i64 = save.try_get("owner_id")?;
    let active_commit_id: Option<i64> = save.try_get("active_commit_id").ok().flatten();
    let active_branch_node_id: Option<i64> = save.try_get("active_branch_node_id").ok().flatten();
    let state_path: String = save.try_get("state_path").unwrap_or_default();

    // ── step 2: 找 commit;先 active_commit_id / active_branch_node_id,再 fallback 最新
    let candidate = active_commit_id.or(active_branch_node_id);
    let commit = fetch_commit(pool, save_id, candidate).await?;

    let commit = match commit {
        Some(c) => c,
        None => {
            // ── step 3: seed_tree 后重试
            seed_tree(pool, save_id, &state_path).await?;
            let after = fetch_commit(pool, save_id, None).await?;
            match after {
                Some(c) => c,
                None => return Ok(UserRuntime::default()),
            }
        }
    };

    // ── step 4: 找/建 active ref → set_save_active → write_checkout → activate snapshot
    let r = find_or_create_ref_for_commit(pool, owner_id, save_id, commit.id).await?;
    let ref_id = Some(r.id);
    set_save_active(pool, save_id, commit.id, ref_id).await?;
    write_checkout(pool, owner_id, save_id, ref_id, commit.id).await?;

    let state_snapshot = commit_state(Some(&commit.state_snapshot), &commit.state_path);
    let snapshot_path = if commit.state_path.is_empty() {
        state_path.clone()
    } else {
        commit.state_path.clone()
    };
    runtime_mod::activate_state_snapshot(
        pool,
        owner_id,
        save_id,
        commit.id,
        &state_snapshot,
        &snapshot_path,
        ref_id,
    )
    .await
}

/// 内部 helper:按候选 id 取 commit,失败则取该 save 最新 commit;均无则 None。
async fn fetch_commit(
    pool: &PgPool,
    save_id: i64,
    candidate: Option<i64>,
) -> PlatformResult<Option<BranchCommit>> {
    if let Some(cid) = candidate {
        let row = sqlx::query(
            "select * from branch_commits where id = $1 and save_id = $2",
        )
        .bind(cid)
        .bind(save_id)
        .fetch_optional(pool)
        .await?;
        if let Some(r) = row {
            return Ok(Some(BranchCommit::from_row(&r)?));
        }
    }
    let fallback = sqlx::query(
        "select * from branch_commits where save_id = $1 order by id desc limit 1",
    )
    .bind(save_id)
    .fetch_optional(pool)
    .await?;
    match fallback {
        Some(r) => Ok(Some(BranchCommit::from_row(&r)?)),
        None => Ok(None),
    }
}

/// Python `mark_runtime_dirty(save_id, runtime_state)` —— runtime state 被改但未 commit 时标 dirty。
pub async fn mark_runtime_dirty(
    pool: &PgPool,
    save_id: i64,
    runtime_state: &Value,
) -> PlatformResult<()> {
    let snap_hash = state_snapshot_hash(runtime_state);
    let turn = runtime_state.get("turn").and_then(|v| v.as_i64()).unwrap_or(0);
    sqlx::query(
        r#"
        update runtime_checkouts
           set dirty = true,
               state_snapshot = $1,
               snapshot_hash = $2,
               turn_runtime = $3,
               updated_at = now()
         where save_id = $4
        "#,
    )
    .bind(runtime_state)
    .bind(&snap_hash)
    .bind(turn)
    .bind(save_id)
    .execute(pool)
    .await?;
    Ok(())
}

// ─── private helpers ────────────────────────────────────────────────────

/// 在 tx 内找一个指向 commit_id 的 active ref,找不到就建一个 "refs/heads/from-<n>" 头。
async fn find_or_create_ref(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    save_id: i64,
    commit_id: i64,
) -> PlatformResult<i64> {
    if let Some((id,)) = sqlx::query_as::<_, (i64,)>(
        "select id from branch_refs where save_id = $1 and target_commit_id = $2 and is_active = true order by id desc limit 1",
    )
    .bind(save_id)
    .bind(commit_id)
    .fetch_optional(&mut **tx)
    .await?
    {
        return Ok(id);
    }
    // 没 active 的,看是否有 main ref。
    if let Some((id,)) = sqlx::query_as::<_, (i64,)>(
        "select id from branch_refs where save_id = $1 and name = 'refs/heads/main' limit 1",
    )
    .bind(save_id)
    .fetch_optional(&mut **tx)
    .await?
    {
        return Ok(id);
    }
    // 新建一个 head ref。
    let name = format!("refs/heads/from-{}-{}", commit_id, random_hex(4));
    let (id,) = sqlx::query_as::<_, (i64,)>(
        r#"
        insert into branch_refs(save_id, name, target_commit_id, kind, is_active, updated_at)
        values ($1, $2, $3, 'head', true, now())
        returning id
        "#,
    )
    .bind(save_id)
    .bind(&name)
    .bind(commit_id)
    .fetch_one(&mut **tx)
    .await?;
    Ok(id)
}

/// Python `_upsert_ref_by_id(db, ref_id, target_commit_id, active=True)`。
/// active=true 时同 save 下其他 ref 全部置 false。
async fn upsert_ref_by_id(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    ref_id: i64,
    target_commit_id: i64,
    active: bool,
) -> PlatformResult<()> {
    let row = sqlx::query("select save_id from branch_refs where id = $1")
        .bind(ref_id)
        .fetch_optional(&mut **tx)
        .await?;
    let save_id: i64 = match row {
        Some(r) => r.try_get("save_id").unwrap_or(0),
        None => return Err(PlatformError::not_found("runtime 指向的分支引用不存在")),
    };
    if active {
        sqlx::query("update branch_refs set is_active = false where save_id = $1")
            .bind(save_id)
            .execute(&mut **tx)
            .await?;
    }
    sqlx::query(
        r#"
        update branch_refs
           set target_commit_id = $1,
               is_active = $2,
               row_version = row_version + 1,
               updated_at = now()
         where id = $3
        "#,
    )
    .bind(target_commit_id)
    .bind(active)
    .bind(ref_id)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

fn random_hex(n: usize) -> String {
    let mut buf = vec![0u8; n];
    rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut buf);
    let mut s = String::with_capacity(n * 2);
    for b in buf {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// validation 错误路径(空 save_id):不连 DB,只校验入口拒绝。
    #[tokio::test]
    async fn record_rejects_zero_save() {
        // 用 lazy pool URL,只要不被命中 await 就不会真连。
        // 直接走 validation 早返,pool 不会被用。
        // 编译时校验签名,运行时早返。
        let pool = sqlx::PgPool::connect_lazy("postgres://localhost/nonexistent").unwrap();
        let res = record_runtime_turn(
            &pool, 1, 0, 1, None, "p", "g", &json!({"turn": 1}), "/tmp/x",
        )
        .await;
        assert!(matches!(res, Err(PlatformError::Validation(_))));
    }

    #[tokio::test]
    async fn record_rejects_zero_parent() {
        let pool = sqlx::PgPool::connect_lazy("postgres://localhost/nonexistent").unwrap();
        let res = record_runtime_turn(
            &pool, 1, 1, 0, None, "p", "g", &json!({"turn": 1}), "/tmp/x",
        )
        .await;
        assert!(matches!(res, Err(PlatformError::Validation(_))));
    }

    #[tokio::test]
    async fn persist_rejects_zero_commit() {
        let pool = sqlx::PgPool::connect_lazy("postgres://localhost/nonexistent").unwrap();
        let res = persist_runtime_state(
            &pool, 1, 1, 0, None, &json!({"turn": 1}), "/tmp/x",
        )
        .await;
        assert!(matches!(res, Err(PlatformError::Validation(_))));
    }

    /// bootstrap 在 user_id=Some(x) 但 DB 不可达时,网络/连接错误以 PlatformError 形式返回(不 panic)。
    /// 用 lazy pool 强制走 query 的真实路径,确保我们没有静默吞错误。
    #[tokio::test]
    async fn bootstrap_returns_error_on_unreachable_db() {
        let pool = sqlx::PgPool::connect_lazy("postgres://127.0.0.1:1/none").unwrap();
        let res = bootstrap_runtime_binding(&pool, Some(42)).await;
        assert!(res.is_err(), "unreachable DB should yield error, got {res:?}");
    }

    /// bootstrap 在 user_id=None 时,同样应该尝试查询 (而不是早返 default)。
    /// DB 不可达时 → 错误,而不是默认 UserRuntime。
    #[tokio::test]
    async fn bootstrap_none_user_id_still_queries() {
        let pool = sqlx::PgPool::connect_lazy("postgres://127.0.0.1:1/none").unwrap();
        let res = bootstrap_runtime_binding(&pool, None).await;
        // 期望:DB 拒绝连接 → Err,而不是 Ok(default)
        assert!(res.is_err());
    }

    /// fetch_commit(no candidate, no fallback) 在 DB 不可达时返 Err(而非静默 None)。
    /// 用 lazy pool 走真实 SQL 路径。
    #[tokio::test]
    async fn fetch_commit_propagates_db_error() {
        let pool = sqlx::PgPool::connect_lazy("postgres://127.0.0.1:1/none").unwrap();
        let res = fetch_commit(&pool, 999, Some(123)).await;
        assert!(res.is_err());
    }

    /// `RecordedTurn` 可序列化字段断言(防回归):commit.id / ref_id 必须公开可访问。
    #[test]
    fn recorded_turn_field_access() {
        let r = RecordedTurn {
            commit: BranchCommit {
                id: 7,
                save_id: 1,
                parent_id: None,
                turn_index: 0,
                kind: "round".into(),
                title: String::new(),
                message: String::new(),
                summary: String::new(),
                content_preview: String::new(),
                state_path: String::new(),
                state_snapshot: serde_json::Value::Null,
                player_input: String::new(),
                gm_output: String::new(),
                metadata: serde_json::Value::Null,
                created_at: None,
            },
            ref_id: 42,
        };
        assert_eq!(r.commit.id, 7);
        assert_eq!(r.ref_id, 42);
    }
}
