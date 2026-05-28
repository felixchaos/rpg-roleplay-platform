"""
command_tools_anchors.py — task 136: 世界线收束机制 · GM 工具

公开 3 个 dispatcher 工具:
  list_pending_anchors      — GM 查看待发生的原著锚点
  mark_anchor_satisfied     — GM 标记某锚点已经发生 (按原著或变体)
  mark_anchor_superseded    — GM 标记某锚点被剧情绕过 (rare,需 reason)

允许 origin: llm_chat (GM 主要用户) + ui_button + api_direct + console_assistant。
注意 satisfied / superseded 是【非破坏性】的状态变更,GM 必须有权限调,否则
"原著事件按变体方式发生"这种判断没人记账,锚点会反复重复触发。
"""
from __future__ import annotations

import json
from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry


_ANCHOR_READ_ORIGINS = frozenset({"ui_button", "api_direct", "console_assistant", "llm_chat", "llm_set"})
# GM 直接负责标记锚点状态, 必须给 llm_chat origin
_ANCHOR_MUTATE_ORIGINS = frozenset({"ui_button", "api_direct", "console_assistant", "llm_chat"})


# ────────────────────────────────────────────────────────────
# Tool executors
# ────────────────────────────────────────────────────────────


def _own_save(db, save_id: int, user_id: int) -> bool:
    row = db.execute(
        "select 1 from game_saves where id = %s and user_id = %s",
        (save_id, user_id),
    ).fetchone()
    return bool(row)


def _t_list_pending_anchors(user_id: int, args: dict) -> str:
    """list_pending_anchors — 列待发生锚点。

    args:
      save_id: 必填
      phase_label: 可选, 过滤当前阶段
      chapter_min / chapter_max: 可选, 章节窗口
      limit: 默认 5, 上限 20
      include_metadata: 默认 false, true 时附带 participants/locations/concepts
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    phase_label = (args.get("phase_label") or "").strip() or None
    try:
        chapter_min = int(args.get("chapter_min")) if args.get("chapter_min") is not None else None
        chapter_max = int(args.get("chapter_max")) if args.get("chapter_max") is not None else None
    except (TypeError, ValueError):
        return "失败: chapter_min / chapter_max 必须整数"
    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))
    include_meta = bool(args.get("include_metadata"))

    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
        from agents.anchor_seed_agent import list_pending_for_phase, summarize_save_anchor_state
        anchors = list_pending_for_phase(
            save_id, phase_label,
            limit=limit, chapter_min=chapter_min, chapter_max=chapter_max,
        )
        if not include_meta:
            for a in anchors:
                a.pop("metadata", None)
        summary = summarize_save_anchor_state(save_id)
        return json.dumps({
            "save_id": save_id,
            "filter": {
                "phase_label": phase_label,
                "chapter_min": chapter_min, "chapter_max": chapter_max,
                "limit": limit,
            },
            "pending_count_total": summary["pending"],
            "fatal_pending_count": summary["fatal_pending"],
            "occurred_count": summary["occurred"],
            "variant_count": summary["variant"],
            "superseded_count": summary["superseded"],
            "avg_drift": summary["avg_drift"],
            "anchors": anchors,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_mark_anchor_satisfied(user_id: int, args: dict) -> str:
    """mark_anchor_satisfied — 锚点已经发生。

    args:
      save_id: 必填
      anchor_key: 必填 (来自 list_pending_anchors 返回)
                  也支持 anchor_id (整数主键)
      how_it_happened: 必填,描述"实际怎么发生的"(可以是变体)
      drift_score: 可选 0.0-1.0, 默认 0.0 (完全按原著) / 0.5 (中度变体) / 1.0 (核心保留方式全变)
      occurred_at_turn: 可选,默认拿存档当前最大 turn
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    anchor_key = (args.get("anchor_key") or "").strip()
    anchor_id_raw = args.get("anchor_id")
    if not anchor_key and anchor_id_raw is None:
        return "失败: anchor_key 或 anchor_id 至少给一个"
    how = (args.get("how_it_happened") or "").strip()
    if not how:
        return "失败: how_it_happened 必填,描述事件实际怎么发生"
    if len(how) > 600:
        how = how[:600]
    try:
        drift = float(args.get("drift_score") if args.get("drift_score") is not None else 0.0)
    except (TypeError, ValueError):
        drift = 0.0
    drift = max(0.0, min(1.0, drift))
    new_status = "variant" if drift >= 0.15 else "occurred"
    try:
        occurred_turn = int(args.get("occurred_at_turn")) if args.get("occurred_at_turn") is not None else None
    except (TypeError, ValueError):
        return "失败: occurred_at_turn 必须整数"

    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            # 默认 occurred_turn 从 branch_commits 最大值取
            if occurred_turn is None:
                r = db.execute(
                    "select coalesce(max(turn_index), 0) as t from branch_commits where save_id = %s",
                    (save_id,),
                ).fetchone()
                occurred_turn = int((r or {}).get("t") or 0)
            # 锁定锚点
            if anchor_key:
                row = db.execute(
                    """
                    select id, status, summary from save_anchor_states
                    where save_id = %s and anchor_key = %s
                    """,
                    (save_id, anchor_key),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    select id, status, summary from save_anchor_states
                    where save_id = %s and id = %s
                    """,
                    (save_id, int(anchor_id_raw)),
                ).fetchone()
            if not row:
                return f"失败: 找不到锚点 (save={save_id}, key={anchor_key!r}, id={anchor_id_raw})"
            if row.get("status") in ("occurred", "variant"):
                return (
                    f"提示: 锚点 {anchor_key or row['id']} 已经是 {row['status']},未变动。"
                    f" (要重新标记请先 mark_anchor_superseded 再操作)"
                )
            db.execute(
                """
                update save_anchor_states set
                  status = %s,
                  variant_description = %s,
                  occurred_at_turn = %s,
                  drift_score = %s,
                  updated_at = now()
                where save_id = %s and id = %s
                """,
                (new_status, how, occurred_turn, drift, save_id, row["id"]),
            )
        return json.dumps({
            "ok": True,
            "anchor_id": row["id"],
            "anchor_key": anchor_key or None,
            "previous_status": row.get("status"),
            "new_status": new_status,
            "drift_score": drift,
            "occurred_at_turn": occurred_turn,
            "summary": row.get("summary", "")[:120],
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_mark_anchor_superseded(user_id: int, args: dict) -> str:
    """mark_anchor_superseded — 锚点被剧情绕过, 永远不会按这个 anchor 发生了。
    例如玩家穿越前就阻止了某事件的前置条件。需要 reason。
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    anchor_key = (args.get("anchor_key") or "").strip()
    anchor_id_raw = args.get("anchor_id")
    if not anchor_key and anchor_id_raw is None:
        return "失败: anchor_key 或 anchor_id 至少给一个"
    reason = (args.get("reason") or "").strip()
    if not reason:
        return "失败: reason 必填 (说明为什么这个锚点已经不可能发生)"
    if len(reason) > 600:
        reason = reason[:600]

    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            if anchor_key:
                row = db.execute(
                    "select id, status, is_fatal, summary from save_anchor_states "
                    "where save_id = %s and anchor_key = %s",
                    (save_id, anchor_key),
                ).fetchone()
            else:
                row = db.execute(
                    "select id, status, is_fatal, summary from save_anchor_states "
                    "where save_id = %s and id = %s",
                    (save_id, int(anchor_id_raw)),
                ).fetchone()
            if not row:
                return f"失败: 找不到锚点 (save={save_id}, key={anchor_key!r})"
            if row.get("is_fatal"):
                return (
                    "拒绝: 这是 is_fatal=true 的锚点 (死神来了模式),原则上必发生,"
                    "不能 superseded。请改用 mark_anchor_satisfied 描述"
                    "实际发生方式 (可以高 drift_score)。"
                )
            if row.get("status") == "superseded":
                return f"提示: 锚点已是 superseded 状态,未变动。"
            db.execute(
                """
                update save_anchor_states set
                  status = 'superseded',
                  variant_description = %s,
                  drift_score = 1.0,
                  updated_at = now()
                where save_id = %s and id = %s
                """,
                (reason, save_id, row["id"]),
            )
        return json.dumps({
            "ok": True,
            "anchor_id": row["id"],
            "anchor_key": anchor_key or None,
            "new_status": "superseded",
            "reason": reason,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_summarize_anchors(user_id: int, args: dict) -> str:
    """summarize_anchors — 当前存档的整体锚点收束状态。"""
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
        from agents.anchor_seed_agent import summarize_save_anchor_state
        s = summarize_save_anchor_state(save_id)
        return json.dumps(s, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────
# Registration
# ────────────────────────────────────────────────────────────


def register_anchor_tools() -> None:
    registry = get_registry()
    specs = [
        ToolSpec(
            name="list_pending_anchors",
            description=(
                "【世界线收束】查询当前存档待发生的原著锚点事件。"
                "GM 应每隔几轮调用一次,了解『剧本必须发生但还没发生』的关键事件,"
                "并主动设计场景把剧情往那里引。返回按 importance desc 排序的列表,"
                "含 anchor_key / chapter / summary / must_preserve / may_vary / is_fatal。"
                "is_fatal=true 表示死神来了模式 — 玩家任何阻止尝试都会以替代方式触发。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "phase_label": {"type": "string", "description": "可选,只取该 phase 下的锚点"},
                    "chapter_min": {"type": "integer", "description": "可选,章节范围下限"},
                    "chapter_max": {"type": "integer", "description": "可选,章节范围上限"},
                    "limit": {"type": "integer", "description": "返回条数 (1-20),默认 5", "default": 5},
                    "include_metadata": {"type": "boolean", "description": "true 时附带 participants/locations,默认 false"},
                },
                "required": ["save_id"],
            },
            executor=_t_list_pending_anchors,
            scope="user",
            origins=_ANCHOR_READ_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 1, "limit": 5},
                {"save_id": 1, "phase_label": "柏林暗流篇", "limit": 3},
                {"save_id": 1, "chapter_min": 10, "chapter_max": 30},
            ],
        ),
        ToolSpec(
            name="mark_anchor_satisfied",
            description=(
                "【世界线收束】标记某个原著锚点已经在本存档发生。"
                "drift_score=0 表示完全按原著方式发生; drift_score>=0.15 时 status 变为 variant "
                "(以变体方式发生,核心保留但具体不同)。how_it_happened 必填,"
                "描述本存档里这件事是怎么发生的 (会写入日志供后续 audit)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "anchor_key": {"type": "string", "description": "锚点 key (如 'chapter:12:event:3')"},
                    "anchor_id": {"type": "integer", "description": "或锚点主键 id (anchor_key 二选一)"},
                    "how_it_happened": {"type": "string", "description": "事件实际发生方式描述"},
                    "drift_score": {"type": "number", "description": "0.0-1.0, 偏离原著程度"},
                    "occurred_at_turn": {"type": "integer", "description": "可选,默认存档当前 turn"},
                },
                "required": ["save_id", "how_it_happened"],
            },
            executor=_t_mark_anchor_satisfied,
            scope="user",
            origins=_ANCHOR_MUTATE_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 1, "anchor_key": "chapter:12:event:0",
                 "how_it_happened": "穆蕾莉娅在地下车场对 MC 透露异端情报,而非原著的浴室场景",
                 "drift_score": 0.3},
                {"save_id": 1, "anchor_key": "chapter:7:event:2",
                 "how_it_happened": "完全按原著方式 — Kaiserin 当夜命令清空北区情报站", "drift_score": 0.0},
            ],
        ),
        ToolSpec(
            name="mark_anchor_superseded",
            description=(
                "【世界线收束】标记某个原著锚点已被剧情绕过,永远不会按这个锚点发生。"
                "is_fatal=true 锚点【拒绝】被 superseded (死神来了模式不可绕过)。"
                "非 fatal 锚点也需谨慎用 — 大多数偏离应该用 mark_anchor_satisfied 配 drift_score 来记录。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "anchor_key": {"type": "string", "description": "锚点 key"},
                    "anchor_id": {"type": "integer", "description": "或锚点主键 id"},
                    "reason": {"type": "string", "description": "为什么这个锚点已经不可能发生 (必填)"},
                },
                "required": ["save_id", "reason"],
            },
            executor=_t_mark_anchor_superseded,
            scope="user",
            origins=_ANCHOR_MUTATE_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 1, "anchor_key": "chapter:18:event:1",
                 "reason": "MC 提前 6 章拦截了图卢兹方面的密令,该事件的前置条件已不存在"},
            ],
        ),
        ToolSpec(
            name="summarize_anchors",
            description=(
                "【世界线收束】返回当前存档的锚点整体收束状态: pending / occurred / variant / superseded "
                "各多少,fatal_pending 数,avg_drift。GM 偶尔调用看一眼整体偏离度。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                },
                "required": ["save_id"],
            },
            executor=_t_summarize_anchors,
            scope="user",
            origins=_ANCHOR_READ_ORIGINS,
            destructive=False,
        ),
    ]
    for spec in specs:
        if not registry.has(spec.name):
            registry.register(spec)


__all__ = ["register_anchor_tools"]
