"""gm_serving/settings.py — Phase F 创建引导 + 设置模型(后端)。

定义建档/游戏中设置的 schema(默认值 + 可改 vs 锁死)+ 读写(存 game_sessions.worldline jsonb,
KB 工具 _save_ctx 即从此读 foreknowledge_mode/progress_chapter)。设计 docs/design/F_onboarding_settings.md。
"""
from __future__ import annotations

from typing import Any

# 设置 schema:locked_after_create=True 的项建档后锁死(改了会损坏世界树)。
SETTINGS_SCHEMA: list[dict] = [
    {"key": "starting_worldline", "label": "起始世界线", "type": "string", "default": "main",
     "locked_after_create": True, "step": 1,
     "help": "从哪条规范世界线开局。建档后锁死(改动破坏已积累世界树)。"},
    {"key": "foreknowledge_mode", "label": "穿越者先知程度", "type": "enum", "default": "none",
     "options": ["none", "partial", "omniscient"], "locked_after_create": False, "step": 3,
     "help": "none=与角色同步无先知;partial=模糊知道著名未来大事;omniscient=全知原著。调节防剧透集合宽度。"},
    {"key": "npc_awareness", "label": "NPC 察觉异常先知", "type": "enum", "default": "oblivious",
     "options": ["oblivious", "suspicious"], "locked_after_create": False, "step": 3,
     "help": "suspicious 时 NPC 会对玩家的异常先知起疑。"},
    {"key": "steering_strength", "label": "剧情引导强度", "type": "enum", "default": "guided",
     "options": ["rail", "guided", "free"], "locked_after_create": False, "step": 4,
     "help": "rail=强力贴原著;guided=软目标引导(默认);free=最大自由。"},
    {"key": "spoiler_guard", "label": "防剧透强度", "type": "enum", "default": "strict",
     "options": ["strict", "loose"], "locked_after_create": False, "step": 4,
     "help": "strict=严格按进度过滤未揭示内容;loose=放宽。"},
]

_DEFAULTS = {s["key"]: s["default"] for s in SETTINGS_SCHEMA}
_LOCKED = {s["key"] for s in SETTINGS_SCHEMA if s["locked_after_create"]}
_VALID = {s["key"]: set(s["options"]) for s in SETTINGS_SCHEMA if s.get("options")}


def schema() -> dict:
    """前端建档向导用:分步字段 + 可改/锁死。"""
    return {"fields": SETTINGS_SCHEMA, "defaults": _DEFAULTS,
            "locked_after_create": sorted(_LOCKED)}


def _ensure_session(db, save_id: int) -> dict:
    row = db.execute("select id, worldline from game_sessions where save_id=%s", (save_id,)).fetchone()
    if row:
        return row
    # 没有 session 行就建一个(最小)
    db.execute(
        "insert into game_sessions(save_id, user_id, worldline) "
        "select %s, user_id, '{}'::jsonb from game_saves where id=%s "
        "on conflict (save_id) do nothing",
        (save_id, save_id),
    )
    return db.execute("select id, worldline from game_sessions where save_id=%s", (save_id,)).fetchone()


def read_settings(db, save_id: int) -> dict:
    row = db.execute("select worldline from game_sessions where save_id=%s", (save_id,)).fetchone()
    wl = (row or {}).get("worldline") if row else None
    wl = wl if isinstance(wl, dict) else {}
    out = dict(_DEFAULTS)
    for k in _DEFAULTS:
        if k in wl:
            out[k] = wl[k]
    out["progress_chapter"] = wl.get("progress_chapter")
    return out


def apply_settings(db, save_id: int, updates: dict[str, Any], *, is_create: bool = False) -> dict:
    """写设置(存 game_sessions.worldline)。建档后锁死项拒改。返回 {applied, rejected}。"""
    from psycopg.types.json import Jsonb
    sess = _ensure_session(db, save_id)
    if not sess:
        return {"error": "存档无 session"}
    wl = sess.get("worldline") if isinstance(sess.get("worldline"), dict) else {}
    wl = dict(wl)
    applied, rejected = {}, {}
    for k, v in (updates or {}).items():
        if k not in _DEFAULTS and k != "progress_chapter":
            rejected[k] = "未知设置"
            continue
        if k in _LOCKED and not is_create:
            rejected[k] = "建档后锁死"
            continue
        if k in _VALID and v not in _VALID[k]:
            rejected[k] = f"非法值(允许:{sorted(_VALID[k])})"
            continue
        wl[k] = v
        applied[k] = v
    db.execute("update game_sessions set worldline=%s, updated_at=now() where save_id=%s",
               (Jsonb(wl), save_id))
    return {"applied": applied, "rejected": rejected}


def advance_progress(db, save_id: int, chapter: int) -> None:
    """推进玩家进度(取 max,只增不减)。防剧透集合随之扩。"""
    from psycopg.types.json import Jsonb
    sess = _ensure_session(db, save_id)
    wl = dict(sess.get("worldline") if isinstance(sess.get("worldline"), dict) else {})
    cur = wl.get("progress_chapter") or 0
    wl["progress_chapter"] = max(cur, int(chapter))
    db.execute("update game_sessions set worldline=%s where save_id=%s", (Jsonb(wl), save_id))
