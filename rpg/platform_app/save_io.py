"""
save_io.py — 存档导入 / 导出

导出包含：game_saves 主记录 + branch_commits（剧情分支历史）+ messages（对话）+
memories（记忆）+ worldline_variables。
导入时按当前 user_id 重映射 owner，分配新 save_id / commit_id。
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, init_db, expose


EXPORT_VERSION = 1


def export_save(user_id: int, save_id: int) -> dict[str, Any]:
    """打包整份存档为 JSON。"""
    init_db()
    with connect() as db:
        save = db.execute(
            "select * from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        commits = db.execute(
            "select * from branch_commits where save_id = %s order by id",
            (save_id,),
        ).fetchall()
        refs = db.execute(
            "select * from branch_refs where save_id = %s order by id",
            (save_id,),
        ).fetchall()
        sessions = db.execute(
            "select id from game_sessions where save_id = %s",
            (save_id,),
        ).fetchall()
        session_ids = [int(s["id"]) for s in sessions]
        messages = []
        memories_rows = []
        if session_ids:
            messages = db.execute(
                "select * from messages where session_id = ANY(%s::bigint[]) order by id",
                (session_ids,),
            ).fetchall()
            memories_rows = db.execute(
                "select * from memories where session_id = ANY(%s::bigint[]) order by id",
                (session_ids,),
            ).fetchall()

    return {
        "export_version": EXPORT_VERSION,
        "exported_at": __import__("time").time(),
        "save": expose(save),
        "commits": [expose(c) for c in commits],
        "refs": [expose(r) for r in refs],
        "messages": [expose(m) for m in messages],
        "memories": [expose(m) for m in memories_rows],
    }


def import_save(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """从导出 payload 重建存档。按当前 user 创建新 save_id。

    不导入 sessions / context_runs / token_usage 这些跨用户敏感数据。
    """
    init_db()
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    if int(payload.get("export_version") or 0) != EXPORT_VERSION:
        raise ValueError(f"export_version 不匹配（期望 {EXPORT_VERSION}）")
    save_data = payload.get("save") or {}
    if not save_data:
        raise ValueError("payload.save 缺失")

    new_title = (save_data.get("title") or "导入存档")
    script_id_raw = save_data.get("script_id")
    state_snapshot = save_data.get("state_snapshot") or {}

    with connect() as db:
        # 校验 script_id 归属（用户必须拥有这个剧本，否则用 user 第一个 script 兜底）
        script_id = None
        if script_id_raw:
            owned = db.execute(
                "select 1 from scripts where id = %s and owner_id = %s",
                (int(script_id_raw), user_id),
            ).fetchone()
            if owned:
                script_id = int(script_id_raw)
        if script_id is None:
            row = db.execute(
                "select id from scripts where owner_id = %s order by id limit 1",
                (user_id,),
            ).fetchone()
            if not row:
                raise ValueError("当前用户没有剧本，无法导入存档")
            script_id = int(row["id"])

        # 1. 新建 save
        new_save = db.execute(
            """
            insert into game_saves(user_id, script_id, title, state_path, state_snapshot)
            values (%s, %s, %s, %s, %s)
            returning *
            """,
            (user_id, script_id, new_title, "", Jsonb(state_snapshot)),
        ).fetchone()
        new_save_id = int(new_save["id"])

        # 2. 重建 branch_commits（保留 parent 关系，但 ID 重映射）
        old_to_new: dict[int, int] = {}
        for c in payload.get("commits") or []:
            old_id = int(c.get("id") or 0)
            old_parent = c.get("parent_id")
            new_parent = old_to_new.get(int(old_parent)) if old_parent else None
            new_commit = db.execute(
                """
                insert into branch_commits(
                  save_id, parent_id, object_hash, tree_hash, turn_index,
                  kind, title, message, summary, content_preview,
                  state_path, player_input, gm_output, metadata, state_snapshot
                ) values (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) returning id
                """,
                (
                    new_save_id, new_parent,
                    c.get("object_hash") or secrets.token_hex(20),
                    c.get("tree_hash") or "",
                    int(c.get("turn_index") or 0),
                    c.get("kind") or "round",
                    c.get("title") or "",
                    c.get("message") or "",
                    c.get("summary") or "",
                    c.get("content_preview") or "",
                    "",
                    c.get("player_input") or "",
                    c.get("gm_output") or "",
                    Jsonb(c.get("metadata") or {}),
                    Jsonb(c.get("state_snapshot") or {}),
                ),
            ).fetchone()
            old_to_new[old_id] = int(new_commit["id"])

        # 3. 创建 active ref 指向最新 commit
        if old_to_new:
            last_commit_id = list(old_to_new.values())[-1]
            db.execute(
                """
                insert into branch_refs(save_id, name, kind, target_commit_id, is_active)
                values (%s, %s, %s, %s, true)
                """,
                (new_save_id, "refs/heads/main", "head", last_commit_id),
            )
            db.execute(
                "update game_saves set active_commit_id = %s where id = %s",
                (last_commit_id, new_save_id),
            )

    return {
        "ok": True,
        "save_id": new_save_id,
        "commits_imported": len(old_to_new),
        "script_id": script_id,
    }
