"""
runtime.py — per-user runtime 元数据存储

B2 重构：
- 服务器模式（RPG_REQUIRE_AUTH=1 或 RPG_RUNTIME_BACKEND=db）：runtime 元数据写
  DB 表 user_runtime，不再创建 platform_data/runtime/user_{id}.json
- 本地模式（默认）：兼容旧的文件存储，便于离线调试 / 导出
- env override: RPG_RUNTIME_BACKEND=db|file|auto

状态快照（state_snapshot）一直都是写 DB runtime_checkouts 表，本模块不再负责
落盘 state 文件。本地模式下保留 source_state_path 字段供导出/迁移使用。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from state import SAVE_FILE

BASE = Path(__file__).resolve().parents[1]
RUNTIME_DIR = BASE / "platform_data" / "runtime"
LEGACY_RUNTIME_FILE = BASE / "platform_data" / "runtime.json"
RUNTIME_STATE_ROOT = BASE / "platform_data" / "runtime_states"


# ── backend 选择 ──────────────────────────────────────────────────
def _runtime_backend() -> str:
    """db / file. 默认：server 模式用 db，本地用 file。"""
    from core.config import (
        is_local_mode as _is_local_mode,
    )
    from core.config import (
        require_auth as _require_auth,
    )
    from core.config import (
        runtime_backend as _runtime_backend_cfg,
    )
    backend = _runtime_backend_cfg().strip().lower()
    if backend in {"db", "file"}:
        return backend
    if _require_auth():
        return "db"
    return "file" if _is_local_mode() else "db"


def _should_mirror_save_file() -> bool:
    """是否把 runtime 镜像到全局 SAVE_FILE。
    只有本地 anonymous（未强制鉴权且 file backend）才镜像，多用户场景一定不要写。
    """
    if _runtime_backend() == "db":
        return False
    from core.config import is_local_mode as _is_local_mode
    from core.config import require_auth as _require_auth
    if _require_auth():
        return False
    return _is_local_mode()


def _runtime_file(user_id: int | None) -> Path:
    if user_id:
        return RUNTIME_DIR / f"user_{int(user_id)}.json"
    return LEGACY_RUNTIME_FILE


def _runtime_state_path(user_id: int, save_id: int) -> Path:
    return RUNTIME_STATE_ROOT / f"user_{int(user_id)}" / f"save_{int(save_id)}.json"


# ── DB backend ────────────────────────────────────────────────────
def _db_read_runtime(user_id: int) -> dict[str, Any]:
    from platform_app.db import connect, init_db
    try:
        init_db()
        with connect() as db:
            row = db.execute(
                """
                select user_id, save_id, active_commit_id, active_branch_node_id,
                       active_ref_id, source_state_path, runtime_state_path, game_url, metadata
                from user_runtime where user_id = %s
                """,
                (int(user_id),),
            ).fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    payload: dict[str, Any] = {
        "user_id": int(row["user_id"]),
        "save_id": int(row["save_id"] or 0),
        "active_commit_id": int(row["active_commit_id"] or 0),
        "active_branch_node_id": int(row["active_branch_node_id"] or 0),
        "active_ref_id": int(row["active_ref_id"]) if row["active_ref_id"] else None,
        "source_state_path": row["source_state_path"] or "",
        "runtime_state_path": row["runtime_state_path"] or "",
        "game_url": row["game_url"] or "/",
    }
    md = row.get("metadata") or {}
    if isinstance(md, dict):
        for k, v in md.items():
            payload.setdefault(k, v)
    return payload


def _db_write_runtime(payload: dict[str, Any]) -> None:
    from psycopg.types.json import Jsonb

    from platform_app.db import connect, init_db
    init_db()
    user_id = int(payload.get("user_id") or 0)
    if not user_id:
        return
    save_id = payload.get("save_id")
    md = {k: v for k, v in payload.items() if k not in {
        "user_id", "save_id", "active_commit_id", "active_branch_node_id",
        "active_ref_id", "source_state_path", "runtime_state_path", "game_url",
    }}
    with connect() as db:
        db.execute(
            """
            insert into user_runtime(user_id, save_id, active_commit_id, active_branch_node_id,
                                     active_ref_id, source_state_path, runtime_state_path,
                                     game_url, metadata, updated_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            on conflict(user_id) do update set
              save_id = excluded.save_id,
              active_commit_id = excluded.active_commit_id,
              active_branch_node_id = excluded.active_branch_node_id,
              active_ref_id = excluded.active_ref_id,
              source_state_path = excluded.source_state_path,
              runtime_state_path = excluded.runtime_state_path,
              game_url = excluded.game_url,
              metadata = excluded.metadata,
              updated_at = now()
            """,
            (
                user_id,
                int(save_id) if save_id else None,
                int(payload.get("active_commit_id") or 0) or None,
                int(payload.get("active_branch_node_id") or 0) or None,
                int(payload.get("active_ref_id") or 0) or None,
                str(payload.get("source_state_path") or ""),
                str(payload.get("runtime_state_path") or ""),
                str(payload.get("game_url") or "/"),
                Jsonb(md or {}),
            ),
        )
        # 无感自动存档语义:runtime 指向某存档即视为"正在游玩",刷新其最后游玩时间
        if save_id:
            db.execute(
                "update game_saves set last_played_at = now() where id = %s",
                (int(save_id),),
            )


# ── 公共 API ───────────────────────────────────────────────────────
def read_runtime(user_id: int | None = None) -> dict[str, Any]:
    """读取 runtime 元数据。
    server 模式: DB; 本地: 文件。
    """
    backend = _runtime_backend()
    if backend == "db" and user_id:
        payload = _db_read_runtime(int(user_id))
        return _attach_db_state(payload) if payload else {}
    # file backend
    path = _runtime_file(user_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        if user_id and LEGACY_RUNTIME_FILE.exists():
            try:
                legacy = json.loads(LEGACY_RUNTIME_FILE.read_text(encoding="utf-8"))
                if int(legacy.get("user_id") or 0) == int(user_id):
                    return _attach_db_state(legacy)
            except Exception:
                pass
        return {}
    return _attach_db_state(payload)


def _attach_db_state(payload: dict[str, Any]) -> dict[str, Any]:
    """附加 DB checkout 状态（dirty 标记），失败不影响主流程"""
    try:
        from platform_app.db import connect, init_db
        init_db()
        save_id = int(payload.get("save_id") or 0)
        owner_id = int(payload.get("user_id") or 0)
        if save_id and owner_id:
            with connect() as db:
                row = db.execute(
                    """
                    select dirty, snapshot_hash, turn_at_commit, turn_runtime, commit_id, ref_id
                    from runtime_checkouts
                    where user_id = %s and save_id = %s
                    """,
                    (owner_id, save_id),
                ).fetchone()
                if row:
                    payload["dirty"] = bool(row.get("dirty"))
                    payload["snapshot_hash"] = row.get("snapshot_hash") or ""
                    payload["turn_at_commit"] = int(row.get("turn_at_commit") or 0)
                    payload["turn_runtime"] = int(row.get("turn_runtime") or 0)
                    payload["turns_ahead"] = payload["turn_runtime"] - payload["turn_at_commit"]
    except Exception:
        pass
    return payload


def write_runtime(
    user_id: int,
    save_id: int,
    node_id: int,
    source_state_path: str,
    ref_id: int | None = None,
    runtime_state_path: str | None = None,
) -> dict[str, Any]:
    backend = _runtime_backend()
    if backend == "db":
        payload = {
            "user_id": int(user_id),
            "save_id": int(save_id),
            "active_commit_id": int(node_id),
            "active_branch_node_id": int(node_id),
            "active_ref_id": int(ref_id) if ref_id else None,
            "source_state_path": str(source_state_path or ""),
            "runtime_state_path": str(runtime_state_path or ""),
            "game_url": "/",
        }
        _db_write_runtime(payload)
        return payload

    # file backend（本地）
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    state_path = runtime_state_path or str(_runtime_state_path(user_id, save_id))
    runtime_state = Path(state_path)
    runtime_state.parent.mkdir(parents=True, exist_ok=True)
    source = Path(source_state_path)
    if source.exists() and source.resolve() != runtime_state.resolve():
        shutil.copy2(source, runtime_state)
        if _should_mirror_save_file():
            SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != SAVE_FILE.resolve():
                shutil.copy2(source, SAVE_FILE)
    payload = {
        "user_id": int(user_id),
        "save_id": int(save_id),
        "active_commit_id": int(node_id),
        "active_branch_node_id": int(node_id),
        "active_ref_id": int(ref_id) if ref_id else None,
        "source_state_path": str(source_state_path),
        "runtime_state_path": state_path,
        "game_url": "/",
    }
    out_path = _runtime_file(int(user_id))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def activate_state_file(
    user_id: int,
    save_id: int,
    node_id: int,
    source_state_path: str,
    ref_id: int | None = None,
) -> dict[str, Any]:
    backend = _runtime_backend()
    source = Path(source_state_path)
    if backend == "db":
        # DB 模式：不落盘 state，runtime_checkouts.state_snapshot 已经是权威；
        # 只在 user_runtime 写元数据指针
        return write_runtime(user_id, save_id, node_id, source_state_path, ref_id=ref_id)

    runtime_state = _runtime_state_path(user_id, save_id)
    runtime_state.parent.mkdir(parents=True, exist_ok=True)
    mirror = _should_mirror_save_file()
    if mirror:
        SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        if source.resolve() != runtime_state.resolve():
            shutil.copy2(source, runtime_state)
        if mirror and source.resolve() != SAVE_FILE.resolve():
            shutil.copy2(source, SAVE_FILE)
    else:
        fallback = json.dumps({"history": [], "turn": 0}, ensure_ascii=False, indent=2)
        runtime_state.write_text(fallback, encoding="utf-8")
        if mirror:
            SAVE_FILE.write_text(fallback, encoding="utf-8")
    return write_runtime(user_id, save_id, node_id, source_state_path, ref_id=ref_id, runtime_state_path=str(runtime_state))


def activate_state_snapshot(
    user_id: int,
    save_id: int,
    node_id: int,
    state_data: dict[str, Any],
    source_state_path: str = "",
    ref_id: int | None = None,
) -> dict[str, Any]:
    backend = _runtime_backend()
    if backend == "db":
        # 不落盘 state；snapshot 已经在 runtime_checkouts/game_saves 里
        return write_runtime(user_id, save_id, node_id, source_state_path, ref_id=ref_id)

    runtime_state = _runtime_state_path(user_id, save_id)
    runtime_state.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state_data or {"history": [], "turn": 0}, ensure_ascii=False, indent=2)
    runtime_state.write_text(text, encoding="utf-8")
    if _should_mirror_save_file():
        SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SAVE_FILE.write_text(text, encoding="utf-8")
    return write_runtime(user_id, save_id, node_id, source_state_path, ref_id=ref_id, runtime_state_path=str(runtime_state))


def update_active_node(node_id: int, source_state_path: str, ref_id: int | None = None, user_id: int | None = None) -> dict[str, Any]:
    payload = read_runtime(user_id=user_id)
    if not payload:
        return {}
    backend = _runtime_backend()
    payload["active_commit_id"] = int(node_id)
    payload["active_branch_node_id"] = int(node_id)
    if ref_id is not None:
        payload["active_ref_id"] = int(ref_id) if ref_id else None
    payload["source_state_path"] = str(source_state_path)
    payload["game_url"] = "/"

    if backend == "db":
        _db_write_runtime(payload)
        return payload

    runtime_state = Path(payload.get("runtime_state_path") or _runtime_state_path(payload["user_id"], payload["save_id"]))
    runtime_state.parent.mkdir(parents=True, exist_ok=True)
    source = Path(source_state_path)
    if source.exists():
        if source.resolve() != runtime_state.resolve():
            shutil.copy2(source, runtime_state)
        if _should_mirror_save_file():
            SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != SAVE_FILE.resolve():
                shutil.copy2(source, SAVE_FILE)
    payload["runtime_state_path"] = str(runtime_state)
    out_path = _runtime_file(int(payload.get("user_id") or 0) or user_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
