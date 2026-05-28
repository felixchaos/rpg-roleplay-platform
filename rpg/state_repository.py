"""
state_repository.py — 统一的 GameState 读写仓库

## 重构后的设计 (v2,2026-05-26)

**单一真相源 (SSOT):**
  state(save_id) = branch_commits[user_runtime.active_commit_id].state_snapshot

也就是说,给定 save_id 和 active_commit_id,state 是不可变的。

## 读取优先级 (新)

1. **commit snapshot (真相源)** —
   读 user_runtime.active_commit_id → branch_commits[commit_id].state_snapshot
   这是稳定的、不可变的、跟激活 commit 严格绑定的快照。

2. **runtime_checkouts dirty buffer** (仅当 dirty=True 时) —
   chat 过程中、commit 还没落地之前的临时 state。一旦 record_runtime_turn
   写了新 commit,这个 buffer 就被 cleaned。

3. **bootstrap** — 没绑 runtime 时,从 active_save 的 main ref 重新绑定

4. **新空白 state** — 真没存档时用。

## 不再使用的退化路径 (v1 → v2 退役)

- ❌ `game_saves.state_snapshot` (不指定 save_id) — 这是 bug 现场:
  上次玩的 save 的 updated_at 最新,会被错误返回。即便指定 save_id,
  这个字段也是 chat 路径粗暴覆盖的,跟当前 active commit 可能脱钩。
  作为兼容仅保留 _legacy_load_save_snapshot,不在主路径调用。

- ❌ source_state_path / runtime_state_path JSON 文件 —
  本地兼容用,不在 server 模式读。

调用者:
  - app.py 的 _ensure_loaded()
  - app.py 的 /api/save / /api/new
  - 任何需要持久化 state 的 endpoint
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from platform_app import branches as _branches
from platform_app import runtime as _runtime
from platform_app.db import connect, init_db
from state import SAVE_FILE, GameState


# ── 读取 ──────────────────────────────────────────────────────────
def load_active_state(user_id: int | None = None) -> tuple[GameState, dict[str, Any] | None]:
    """加载当前激活的 GameState。

    返回 (state, runtime_meta),runtime_meta 包含 save_id / commit_id 等信息。
    """
    runtime_meta = _runtime.read_runtime(user_id=user_id)

    # 安全检查:runtime_meta 不属于当前 user → 作废
    if user_id and runtime_meta and int(runtime_meta.get("user_id") or 0) != int(user_id):
        runtime_meta = None

    # 没绑 runtime → 先 bootstrap 找当前 active save
    if user_id and not runtime_meta:
        runtime_meta = _branches.bootstrap_runtime_binding(user_id=user_id)

    if runtime_meta:
        save_id = int(runtime_meta.get("save_id") or 0)
        commit_id = int(
            runtime_meta.get("active_commit_id")
            or runtime_meta.get("active_branch_node_id")
            or 0
        )

        # 类 git working-tree 语义:
        # - runtime_checkouts.state_snapshot = working tree (可能有未 commit 的修改)
        # - branch_commits[commit_id].state_snapshot = HEAD commit 快照
        # 激活 commit 时 _write_checkout 会把 commit 的 state_snapshot 写入
        # runtime_checkouts;之后 /set / chat 修改也实时写 runtime_checkouts。
        # 所以 runtime_checkouts 始终是"最新 working state",优先读它。
        # 只有读不到 (新 save 还没 checkout) 才退化到 commit snapshot。

        # ── 优先级 1:runtime_checkouts.state_snapshot (working tree, per-user, 带 save_id 限制) ──
        if save_id:
            snapshot = _load_runtime_checkout_snapshot(save_id, user_id)
            if snapshot:
                return GameState(snapshot), runtime_meta

        # ── 优先级 2:branch_commits[active_commit_id].state_snapshot (commit 不可变快照) ──
        # 用于 runtime_checkouts 还没建立的极初始状态 (e.g. 刚导入 save 还没 chat 过)。
        if save_id and commit_id:
            snapshot = _load_commit_snapshot(commit_id, save_id, user_id)
            if snapshot:
                return GameState(snapshot), runtime_meta

        # ── 优先级 3:retry bootstrap(可能 user_runtime 已 stale)──
        rebound = _branches.bootstrap_runtime_binding(user_id=user_id)
        if rebound:
            new_save_id = int(rebound.get("save_id") or 0)
            new_commit_id = int(
                rebound.get("active_commit_id")
                or rebound.get("active_branch_node_id")
                or 0
            )
            if new_save_id:
                # 同样先 runtime_checkouts 后 commit
                snapshot = _load_runtime_checkout_snapshot(new_save_id, user_id)
                if snapshot:
                    return GameState(snapshot), rebound
                if new_commit_id:
                    snapshot = _load_commit_snapshot(new_commit_id, new_save_id, user_id)
                    if snapshot:
                        return GameState(snapshot), rebound

    # ── 最后兜底:真没存档,返回空白新状态 ──
    if user_id:
        return GameState.new(), runtime_meta

    # 匿名/本地:允许 fallback 到 JSON 镜像
    return GameState.load_or_new(), runtime_meta


def _load_commit_snapshot(commit_id: int, save_id: int, user_id: int | None) -> dict[str, Any] | None:
    """从 branch_commits[commit_id].state_snapshot 读真相源。

    严格 user_id + save_id 校验,防止跨 save / 跨 user 读错快照。
    """
    if not commit_id or not save_id:
        return None
    try:
        init_db()
        with connect() as db:
            # 先校验 save 归属
            if user_id is not None:
                save = db.execute(
                    "select id from game_saves where id = %s and user_id = %s",
                    (int(save_id), int(user_id)),
                ).fetchone()
                if not save:
                    return None
            row = db.execute(
                """
                select state_snapshot, state_path
                from branch_commits
                where id = %s and save_id = %s
                """,
                (int(commit_id), int(save_id)),
            ).fetchone()
            if not row:
                return None
            snapshot = row.get("state_snapshot")
            if isinstance(snapshot, dict) and snapshot:
                return _ensure_dict(snapshot)
            if isinstance(snapshot, str) and snapshot:
                try:
                    parsed = json.loads(snapshot)
                    if isinstance(parsed, dict) and parsed:
                        return parsed
                except Exception:
                    pass
            # 退化:branch_commits.state_path JSON 文件 (legacy 模组数据)
            path = row.get("state_path")
            if path:
                try:
                    return json.loads(Path(path).read_text(encoding="utf-8"))
                except Exception:
                    return None
    except Exception:
        return None
    return None


def _load_runtime_checkout_snapshot(save_id: int, user_id: int | None) -> dict[str, Any] | None:
    """从 runtime_checkouts.state_snapshot 拿 dirty buffer。

    严格 user_id + save_id 校验。
    """
    if not save_id:
        return None
    try:
        init_db()
        with connect() as db:
            if user_id is not None:
                row = db.execute(
                    """
                    select state_snapshot
                    from runtime_checkouts
                    where save_id = %s and user_id = %s
                    order by updated_at desc
                    limit 1
                    """,
                    (int(save_id), int(user_id)),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    select state_snapshot
                    from runtime_checkouts
                    where save_id = %s
                    order by updated_at desc
                    limit 1
                    """,
                    (int(save_id),),
                ).fetchone()
            if row and row.get("state_snapshot"):
                return _ensure_dict(row["state_snapshot"])
    except Exception:
        pass
    return None


def _legacy_load_save_snapshot(user_id: int, save_id: int | None = None) -> dict[str, Any] | None:
    """**仅诊断 / migration 工具用**。

    从 game_saves.state_snapshot 拿快照。**不再作为主读路径** —
    这是历史 bug 现场:用户切到 save A 但 _ensure_loaded 退化到这里时
    会被 save B (updated_at 更新) 的 snapshot 污染。
    新代码不要调这个函数;读 state 必须经 _load_commit_snapshot。
    """
    try:
        init_db()
        with connect() as db:
            if save_id:
                row = db.execute(
                    "select state_snapshot from game_saves where id = %s and user_id = %s",
                    (int(save_id), int(user_id)),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    select state_snapshot from game_saves
                    where user_id = %s order by updated_at desc limit 1
                    """,
                    (int(user_id),),
                ).fetchone()
            if row and row.get("state_snapshot"):
                return _ensure_dict(row["state_snapshot"])
    except Exception:
        pass
    return None


# ── 保存 ──────────────────────────────────────────────────────────
def save_active_state(state: GameState, user_id: int | None = None) -> dict[str, Any]:
    """保存 state:DB 是权威源;server 模式不再写本地 JSON 镜像。

    返回 {"ok": True, "commit_id": ..., "mirror_path": ...}
    本地模式 mirror_path 是实际写盘路径;server 模式为 "db://..." 占位。
    """
    result: dict[str, Any] = {"ok": False, "commit_id": None, "mirror_path": ""}

    # 1. 本地模式才写 JSON 镜像;server 模式 state.save() 会返回空串
    try:
        written = state.save()
        result["mirror_path"] = written or "db://runtime_checkouts"
    except Exception as e:
        result["mirror_error"] = str(e)
        result["mirror_path"] = "db://runtime_checkouts"

    # 2. 同步到 DB(权威源)
    try:
        init_db()
        persist = _branches.persist_runtime_state(
            runtime_state_path=None,
            user_id=user_id,
            state_data=state.data,
        )
        result["ok"] = bool(persist.get("ok"))
        result["commit_id"] = persist.get("commit_id")
        if not result["ok"] and not result.get("mirror_path", "").startswith("db://"):
            result["ok"] = True
        elif not result["ok"]:
            result["db_error"] = persist.get("reason", "DB persist 失败")
    except Exception as e:
        result["db_error"] = str(e)
        if not result.get("mirror_path", "").startswith("db://"):
            result["ok"] = True

    return result


# ── 健康检查 ──────────────────────────────────────────────────────
def repository_status() -> dict[str, Any]:
    """诊断信息:当前 runtime / DB 是否健康"""
    status: dict[str, Any] = {
        "save_file_exists": SAVE_FILE.exists(),
        "save_file_path": str(SAVE_FILE),
    }
    if SAVE_FILE.exists():
        status["save_file_size"] = SAVE_FILE.stat().st_size
    status["runtime_meta"] = _runtime.read_runtime() or {}
    try:
        init_db()
        with connect() as db:
            row = db.execute("select count(*) as n from game_saves").fetchone()
            status["db_saves"] = int(row["n"]) if row else 0
            row = db.execute("select count(*) as n from branch_commits").fetchone()
            status["db_commits"] = int(row["n"]) if row else 0
    except Exception as e:
        status["db_error"] = str(e)
    return status


# ── 工具 ──────────────────────────────────────────────────────────
def _ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}
