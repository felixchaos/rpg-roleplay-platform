"""
command_tools_phase.py — task 107C

Phase management command tools:
  phase_list    — list all phase digests for a save
  phase_advance — manually open a new phase
  phase_rebuild — mark a phase as needing re-digest (107D placeholder)

Registered in command_tools_register.ensure_registered().
"""
from __future__ import annotations

import json
from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry


# Origins: console_assistant + ui_button + api_direct
# (LLM free-chat should not spontaneously advance phases)
_PHASE_READ_ORIGINS = frozenset({"ui_button", "api_direct", "console_assistant", "llm_chat", "llm_set"})
_PHASE_MUTATE_ORIGINS = frozenset({"ui_button", "api_direct", "console_assistant"})


# ────────────────────────────────────────────────────────────
# Tool executors
# ────────────────────────────────────────────────────────────


def _t_phase_list(user_id: int, args: dict) -> str:
    """List all save_phase_digests for the given save_id."""
    save_id = args.get("save_id")
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id)
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            # Verify ownership
            own = db.execute(
                "select 1 from game_saves where id = %s and user_id = %s",
                (save_id, user_id),
            ).fetchone()
            if not own:
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            rows = db.execute(
                """
                select phase_index, phase_label, turn_start, turn_end,
                       story_time_label, status, summary
                  from save_phase_digests
                 where save_id = %s
                 order by phase_index asc
                """,
                (save_id,),
            ).fetchall() or []
        result = []
        for r in rows:
            summary = (r.get("summary") or "")[:80]
            result.append({
                "phase_index": r["phase_index"],
                "phase_label": r.get("phase_label") or "",
                "turn_start": r.get("turn_start"),
                "turn_end": r.get("turn_end"),
                "story_time_label": r.get("story_time_label") or "",
                "status": r.get("status") or "open",
                "summary": summary + ("..." if len(r.get("summary") or "") > 80 else ""),
            })
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_phase_advance(user_id: int, args: dict) -> str:
    """Manually open a new phase for the given save_id."""
    save_id = args.get("save_id")
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id)
    label = (args.get("label") or "").strip()
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            own = db.execute(
                "select 1 from game_saves where id = %s and user_id = %s",
                (save_id, user_id),
            ).fetchone()
            if not own:
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            # Get current turn from the latest branch_commit for this save
            row = db.execute(
                "select turn_index from branch_commits where save_id = %s order by id desc limit 1",
                (save_id,),
            ).fetchone()
        turn_index = int((row or {}).get("turn_index") or 0)

        from save_phase_manager import open_new_phase

        new_phase = open_new_phase(
            save_id=save_id,
            turn_index=turn_index,
            phase_label=label,
            story_time_label="",
        )
        new_index = new_phase.get("phase_index", "?")
        return f"phase_advance: 新 phase {new_index} 已开启 (turn={turn_index}, label={label or '(未设置)'})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_phase_rebuild(user_id: int, args: dict) -> str:
    """Mark a phase as needing re-digest (metadata.needs_rebuild=true).

    Real LLM re-digest is implemented in 107D. This tool only flags the row
    so 107D can pick it up.
    """
    save_id = args.get("save_id")
    phase_index = args.get("phase_index")
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    if not isinstance(phase_index, (int, float, str)) or not str(phase_index).lstrip("-").isdigit():
        return "失败: phase_index 必须整数"
    save_id = int(save_id)
    phase_index = int(phase_index)
    try:
        from platform_app.db import connect, init_db
        from psycopg.types.json import Jsonb

        init_db()
        with connect() as db:
            own = db.execute(
                "select 1 from game_saves where id = %s and user_id = %s",
                (save_id, user_id),
            ).fetchone()
            if not own:
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            row = db.execute(
                "select id, metadata from save_phase_digests where save_id = %s and phase_index = %s",
                (save_id, phase_index),
            ).fetchone()
            if not row:
                return f"失败: phase_index={phase_index} 不存在 (save_id={save_id})"
            meta = dict(row.get("metadata") or {})
            meta["needs_rebuild"] = True
            db.execute(
                "update save_phase_digests set metadata = %s, updated_at = now() "
                "where save_id = %s and phase_index = %s",
                (Jsonb(meta), save_id, phase_index),
            )
        return f"phase_rebuild: phase {phase_index} 已标记 needs_rebuild=true (save_id={save_id})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────
# Registration
# ────────────────────────────────────────────────────────────


def register_phase_tools() -> None:
    """Register phase management tools into the global registry."""
    registry = get_registry()

    specs = [
        ToolSpec(
            name="phase_list",
            description=(
                "列出指定存档 (save_id) 的所有 phase 摘要阶段，"
                "含 phase_index / phase_label / turn 范围 / status / summary 前 80 字。"
                "用于查看剧情分段历史。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                },
                "required": ["save_id"],
            },
            executor=_t_phase_list,
            scope="user",
            origins=_PHASE_READ_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="phase_advance",
            description=(
                "手动为指定存档开启新的 phase 阶段 (不等自动 30-turn 检测)。"
                "label 可选，作为新阶段的标题。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "label": {"type": "string", "description": "新 phase 标签 (可选)"},
                },
                "required": ["save_id"],
            },
            executor=_t_phase_advance,
            scope="user",
            origins=_PHASE_MUTATE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="phase_rebuild",
            description=(
                "标记指定 phase 需要重新生成 LLM 摘要 (metadata.needs_rebuild=true)。"
                "实际重摘由 107D 后台任务处理。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "phase_index": {"type": "integer", "description": "目标 phase 下标 (0-based)"},
                },
                "required": ["save_id", "phase_index"],
            },
            executor=_t_phase_rebuild,
            scope="user",
            origins=_PHASE_MUTATE_ORIGINS,
            destructive=False,
        ),
    ]

    for spec in specs:
        if not registry.has(spec.name):
            registry.register(spec)


__all__ = ["register_phase_tools"]
