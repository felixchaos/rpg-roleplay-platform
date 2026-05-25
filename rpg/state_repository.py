"""
state_repository.py — 统一的 GameState 读写仓库

设计目标（来自 CLAUDE_CODE_HANDOFF.md TODO #1）：
- DB 是权威源；JSON 文件只是本地兼容镜像
- 优先从 runtime_checkouts.state_snapshot / game_saves.state_snapshot 加载
- 保存时同时写 DB + 本地 JSON 镜像
- 提供降级路径：DB 不可用时回退到 SAVE_FILE

调用者：
- ui.py 的 _ensure_loaded()
- ui.py 的 /api/save、/api/new
- 任何需要持久化 state 的 endpoint
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from state import GameState, SAVE_FILE
from platform_app import branches as _branches
from platform_app import runtime as _runtime
from platform_app.db import connect, init_db


# ── 读取 ──────────────────────────────────────────────────────────
def load_active_state(user_id: int | None = None) -> tuple[GameState, dict[str, Any] | None]:
    """
    加载当前激活的 GameState，DB 优先 + JSON 镜像兜底。

    返回 (state, runtime_meta)，runtime_meta 包含 save_id / commit_id 等信息。

    多用户安全：必须按 user_id 隔离。匿名 (user_id=None) 才允许走全局 legacy 文件。
    """
    runtime_meta = _runtime.read_runtime(user_id=user_id)

    # 安全检查：如果 runtime_meta 不属于当前 user，作废重新 bootstrap
    if user_id and runtime_meta and int(runtime_meta.get("user_id") or 0) != int(user_id):
        runtime_meta = None

    # 1. 尝试从 runtime checkout 读取（DB 权威快照）
    if runtime_meta:
        snapshot = _load_runtime_snapshot(runtime_meta, expected_user_id=user_id)
        if snapshot:
            return GameState(snapshot), runtime_meta

    # 2. 尝试从 game_saves.state_snapshot 读取
    if user_id:
        snapshot = _load_save_snapshot(user_id)
        if snapshot:
            return GameState(snapshot), runtime_meta

    # 3. 兜底：bootstrap binding（找当前 active save 自动恢复）
    if not runtime_meta:
        runtime_meta = _branches.bootstrap_runtime_binding(user_id=user_id)
        if runtime_meta:
            snapshot = _load_runtime_snapshot(runtime_meta, expected_user_id=user_id)
            if snapshot:
                return GameState(snapshot), runtime_meta

    # 4. 用户已登录但没存档：返回空白新状态（避免读到 SAVE_FILE 里别人的内容）
    if user_id:
        return GameState.new(), runtime_meta

    # 5. 最终兜底：本地匿名才允许 fallback 到 JSON
    return GameState.load_or_new(), runtime_meta


def _load_runtime_snapshot(runtime_meta: dict[str, Any], expected_user_id: int | None = None) -> dict[str, Any] | None:
    """优先从 runtime_checkouts 拿快照。

    expected_user_id 给定时强制校验 user_id 匹配，防止读到别人存档。
    """
    try:
        save_id = int(runtime_meta.get("save_id") or 0)
        if not save_id:
            return None
        with connect() as db:
            if expected_user_id:
                row = db.execute(
                    """
                    select state_snapshot
                    from runtime_checkouts
                    where save_id = %s and user_id = %s
                    order by updated_at desc
                    limit 1
                    """,
                    (save_id, int(expected_user_id)),
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
                    (save_id,),
                ).fetchone()
            if row and row.get("state_snapshot"):
                return _ensure_dict(row["state_snapshot"])
    except Exception:
        pass

    # 退化到 source_state_path
    source = runtime_meta.get("source_state_path") or runtime_meta.get("runtime_state_path")
    if source:
        try:
            return json.loads(Path(source).read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _load_save_snapshot(user_id: int) -> dict[str, Any] | None:
    """从 game_saves.state_snapshot 拿最新快照"""
    try:
        with connect() as db:
            row = db.execute(
                """
                select state_snapshot
                from game_saves
                where user_id = %s
                order by updated_at desc
                limit 1
                """,
                (user_id,),
            ).fetchone()
            if row and row.get("state_snapshot"):
                return _ensure_dict(row["state_snapshot"])
    except Exception:
        pass
    return None


# ── 保存 ──────────────────────────────────────────────────────────
def save_active_state(state: GameState, user_id: int | None = None) -> dict[str, Any]:
    """
    保存 state：DB 是权威源；server 模式不再写本地 JSON 镜像。

    返回 {"ok": True, "commit_id": ..., "mirror_path": ...}
    本地模式 mirror_path 是实际写盘路径；server 模式为 "db://..." 占位。
    """
    result: dict[str, Any] = {"ok": False, "commit_id": None, "mirror_path": ""}

    # 1. 本地模式才写 JSON 镜像；server 模式 state.save() 会返回空串
    try:
        written = state.save()
        result["mirror_path"] = written or "db://runtime_checkouts"
    except Exception as e:
        result["mirror_error"] = str(e)
        result["mirror_path"] = "db://runtime_checkouts"

    # 2. 同步到 DB（权威源）
    try:
        init_db()
        # 优先使用 user_runtime/legacy runtime 里的 runtime_state_path；
        # server 模式 runtime_state_path 为空字符串，branches 会兜底用 DB snapshot
        persist = _branches.persist_runtime_state(
            runtime_state_path=None,  # 让 branches 自己从 runtime 元数据找
            user_id=user_id,
            state_data=state.data,
        )
        result["ok"] = bool(persist.get("ok"))
        result["commit_id"] = persist.get("commit_id")
        if not result["ok"] and not result.get("mirror_path", "").startswith("db://"):
            # 本地模式 DB 写失败：fallback 也算 ok
            result["ok"] = True
        elif not result["ok"]:
            result["db_error"] = persist.get("reason", "DB persist 失败")
    except Exception as e:
        result["db_error"] = str(e)
        # 本地模式 DB 失败仍算 ok（mirror 还在），server 模式则必须报错
        if not result.get("mirror_path", "").startswith("db://"):
            result["ok"] = True

    return result


# ── 健康检查 ──────────────────────────────────────────────────────
def repository_status() -> dict[str, Any]:
    """诊断信息：当前 runtime / DB 是否健康"""
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
