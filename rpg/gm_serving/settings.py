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
            # 修正(去确定性绕过):此前把史官 progress_chapter 降级 legacy、只用 max(floor,derived)=纯 reached
            # 派生 → 发散局进度冻死。恢复:也纳入史官的进度判断(worldline.progress_chapter,由 _apply_estimate/
            # _apply_pace_fallback 有界写入),三者取 max:
            #   · _floor  = 已到达锚点最大原著章(reached 地板 = 防剧透护栏、可靠真值)
            #   · _derived= 前沿派生(可见锚点最大章)
            #   · _sage   = 史官估章/motion 兜底(发散前进靠它;有界:pace_cap/clamp/单调,不会乱跳)
            #   前沿模式下史官估章曾被关(est_on),故存量 _sage≈reached、无旧猜章器过冲值,纳入 max 安全。
            _sage_raw = out.get("progress_chapter")
            out["progress_chapter_legacy"] = _sage_raw
            _sage = int(_sage_raw) if isinstance(_sage_raw, int) else (
                int(_sage_raw) if isinstance(_sage_raw, str) and _sage_raw.strip().lstrip("-").isdigit() else 0)
            _derived = derived_progress_chapter(save_id, db=db)
            _fr = db.execute(
                "select coalesce(max(source_chapter),0) c from save_anchor_states "
                "where save_id=%s and status in ('occurred','variant')", (save_id,)).fetchone()
            _floor = max(1, int((_fr or {}).get("c") or 0))
            out["progress_chapter"] = max(_floor, int(_derived), _sage)
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


def set_user_progress_floor(db, save_id: int, chapter: int) -> None:
    """记录玩家【显式】设置的进度地板(/set advance_story_progress 逃生阀专用,max-only)。

    与估章推的 progress_chapter 分开存:揭示天花板的估章钳制(clamp_reveal_progress)
    只认确定性地板(occurred 锚点 / 本键),玩家显式跳章必须绕过钳制,否则 d50eb926a
    的逃生阀被废。键在 _session_repo._PRESERVE_SETTINGS_SQL 里 sticky。"""
    _ensure_session(db, save_id)
    db.execute(
        "update game_sessions set worldline = jsonb_set(coalesce(worldline, '{}'::jsonb), "
        "'{user_progress_floor}', to_jsonb(greatest(coalesce((worldline->>'user_progress_floor')::int, 0), %s)), true) "
        "where save_id=%s",
        (int(chapter), save_id),
    )


def realign_progress_signals(db, save_id: int, target_chapter: int) -> int:
    """回退方向的进度信号族对齐(同事务原子)。矩阵审计 M1-M4 收口。

    病根:软回退(rollback_to_message/rewind_last_round/delete_subtree/进度回退端点)
    只退 state_snapshot 或只改单个信号,不动 save_anchor_states / progress_chapter /
    user_progress_floor → 下一回合 retrieval self-heal 用 stale 的 occurred 锚点把
    progress 顶回去 = 玩家的回退被静默撤销,防剧透闸按回退前高进度继续泄漏后期设定。
    所有回退路径必须走本函数:
      A: progress_chapter 显式设 target(可降) + user_progress_floor clamp 到 ≤target;
      C: source_chapter > target 的 occurred/variant 锚点重锁回 pending;
      frontier(flag on): 删 >target 前沿行 + recompute_visible_set。
    返回重锁锚点数。异常向外传播(调用方事务整体回滚,防信号族半对齐脱节)。"""
    _ensure_session(db, save_id)
    t = max(1, int(target_chapter))
    db.execute(
        "update game_sessions set worldline = jsonb_set(jsonb_set(coalesce(worldline,'{}'::jsonb), "
        "'{progress_chapter}', to_jsonb(%s::int), true), "
        "'{user_progress_floor}', to_jsonb(least(coalesce((worldline->>'user_progress_floor')::int, 0), %s)), true), "
        "updated_at = now() where save_id=%s",
        (t, t, save_id),
    )
    relocked = db.execute(
        "update save_anchor_states set status='pending', occurred_at_turn=null, "
        "variant_description=null, drift_score=0, updated_at=now() "
        "where save_id=%s and source_chapter > %s and status in ('occurred','variant') "
        "returning id",
        (save_id, t),
    ).fetchall()
    from kb.reveal import _frontier_on, recompute_visible_set
    if _frontier_on(save_id):
        db.execute(
            "delete from save_reveal_frontier where save_id=%s and anchor_key in "
            "(select anchor_key from save_anchor_states where save_id=%s and source_chapter > %s)",
            (save_id, save_id, t),
        )
        _scr = db.execute(
            "select script_id from game_saves where id=%s", (save_id,)).fetchone()
        if _scr and _scr.get("script_id"):
            recompute_visible_set(db, save_id, int(_scr["script_id"]))
    return len(relocked or [])


def clamp_reveal_progress(progress: int, det_floor: int, lookahead: int) -> int:
    """揭示天花板的估章钳制(纯函数,时间线战役批次1)。

    生产实锤(save 268):occurred 冻结 ch7 十天,估章把 progress_chapter 顶到 17,
    ch8-17 的世界书/实体/NPC 被超前揭示(反馈#82 无关后期NPC)、剧情被当已发生。
    钳制:存在确定性地板(occurred/variant 最大章 或 玩家显式 /set 地板)时,
    揭示用进度 ≤ 地板 + lookahead;无地板(纯发散/无锚档)不钳,发散档解冻语义保留。
    lookahead <= 0 = 关闭钳制(env 逃生阀)。"""
    if lookahead <= 0 or det_floor < 1:
        return progress
    return min(progress, det_floor + lookahead)


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
