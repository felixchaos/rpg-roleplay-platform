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
     "help": "rail=贴原著(强力锚点):把下一个待发生锚点当成必须推进的下一拍,GM 主动收束、"
             "偏离 1-3 轮内拉回(仍允许合理变体);guided=软引导(默认温和):软目标朝锚点自然推进;"
             "free=自由:不注入引导。"},
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
    # P4(S7):flag on 时 progress_chapter 降级为【前沿派生只读】(与 GM 门控同源,根治 over-shoot);
    # 过渡期保留 _legacy(=worldline 标量)供前端/影子核对。前端数值语义不变(玩家读到第几章)。
    # floor 用【已确认锚点最大原著章】(可靠、确定性)而非 worldline 标量 —— 后者可能被旧猜章器冲高,
    # 拿它当 floor 会把 over-shoot 带回显示。前沿未种时(灰度窗口)floor 兜住,不坍缩到第1章;
    # 前沿已种时 derived==floor。与 retrieval.py:495 的 max(1,_last_sat,_derived) 同源。
    try:
        from kb.reveal import _frontier_on, derived_progress_chapter
        if _frontier_on(save_id):
            out["progress_chapter_legacy"] = out.get("progress_chapter")
            _derived = derived_progress_chapter(save_id, db=db)
            _fr = db.execute(
                "select coalesce(max(source_chapter),0) c from save_anchor_states "
                "where save_id=%s and status in ('occurred','variant')", (save_id,)).fetchone()
            _floor = max(1, int((_fr or {}).get("c") or 0))
            out["progress_chapter"] = max(_floor, int(_derived))
    except Exception:
        pass
    return out


def apply_settings(db, save_id: int, updates: dict[str, Any], *, is_create: bool = False) -> dict:
    """写设置(存 game_sessions.worldline)。建档后锁死项拒改。返回 {applied, rejected}。"""
    from psycopg.types.json import Jsonb
    sess = _ensure_session(db, save_id)
    if not sess:
        return {"error": "存档无 session"}
    wl_existing = sess.get("worldline") if isinstance(sess.get("worldline"), dict) else {}
    # is_create 不信客户端:客户端传 is_create=true 即可在后续 PATCH 改锁死项(starting_worldline)。
    # 改由服务端判定——仅当锁死项尚未落库(worldline 不含任何 _LOCKED 键=未初始化)才放行。
    # 建档首发 PATCH(worldline 空)→ 放行;之后(已含锁死键)→ 任何 is_create 都被忽略。
    server_uninitialized = not any(k in wl_existing for k in _LOCKED)
    effective_is_create = bool(is_create) and server_uninitialized
    applied, rejected = {}, {}
    for k, v in (updates or {}).items():
        if k not in _DEFAULTS and k != "progress_chapter":
            rejected[k] = "未知设置"
            continue
        if k in _LOCKED and not effective_is_create:
            rejected[k] = "建档后锁死"
            continue
        if k in _VALID and v not in _VALID[k]:
            rejected[k] = f"非法值(允许:{sorted(_VALID[k])})"
            continue
        applied[k] = v
    # 原子按键合并(jsonb ||):只覆盖本次改的键,不整列读改写 → 避免 workers=2 并发丢更新/抹掉对方键。
    if applied:
        db.execute(
            "update game_sessions set worldline = coalesce(worldline, '{}'::jsonb) || %s::jsonb, "
            "updated_at=now() where save_id=%s",
            (Jsonb(applied), save_id),
        )
    return {"applied": applied, "rejected": rejected}


def advance_progress(db, save_id: int, chapter: int) -> None:
    """推进玩家进度(取 max,只增不减)。防剧透集合随之扩。
    用单条原子 SQL(greatest 在 DB 内算)替代「读-改-写整 jsonb」:workers=2 下两并发回合
    各读到旧 progress 再写回会丢更新,且整列覆盖还会抹掉对方刚写的其它 worldline 键。"""
    _ensure_session(db, save_id)
    db.execute(
        "update game_sessions set worldline = jsonb_set(coalesce(worldline, '{}'::jsonb), "
        "'{progress_chapter}', to_jsonb(greatest(coalesce((worldline->>'progress_chapter')::int, 0), %s)), true) "
        "where save_id=%s",
        (int(chapter), save_id),
    )
