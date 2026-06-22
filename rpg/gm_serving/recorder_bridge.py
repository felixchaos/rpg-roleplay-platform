"""gm_serving/recorder_bridge.py — Q_three_sage_pipeline Phase 2 桥接层

把 agents.recorder 的统一史官 LLM 调用结果路由到现有落库器，
让调用方(chat_pipeline)只翻一个 flag 就能切换到新路径。

公开 API
========
    run_unified_recorder(state, response, save_id, user_id, *,
                         acceptance_clauses, tasks) -> dict

设计目标
========
- 单次 LLM 调用(由 recorder.record_turn 完成)
- 锚点落库复用 anchor_reconcile.reconcile_anchors_for_turn，注入预计算判定器，
  绝不触发第二次 LLM 调用
- ops 不在本层写库(由调用方决定是否 apply)
- 任何子步骤独立吞异常，不破回合
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.logging import get_logger

if TYPE_CHECKING:
    pass  # GameState 来自调用方动态传入，不在此 import 避免循环

log = get_logger(__name__)

# 默认启用全部三个任务
_DEFAULT_TASKS = ["ops", "anchors", "acceptance"]

# anchor_reconcile 单回合最多传入的 pending 数量(镜像 anchor_reconcile._MAX_PENDING_PER_TURN)
_MAX_PENDING = 12


def run_unified_recorder(
    state: Any,                              # live GameState，带 .data dict
    response: str,                           # 本回合 GM 正文
    save_id: int | None,
    user_id: int | None,
    *,
    acceptance_clauses: list[str] | None = None,
    tasks: list[str] | None = None,          # 默认 ["ops","anchors","acceptance"]
) -> dict:
    """ONE recorder LLM call，然后把输出路由到现有落库器。

    返回 {"ops": list[dict], "unmet": list[str], "anchors_marked": int, "chapter": int|None}。
    任何失败静默吞掉，返回空安全默认，绝不上抛。
    """
    # ── 空安全默认 ─────────────────────────────────────────────────────
    _empty: dict[str, Any] = {
        "ops": [], "unmet": [], "anchors_marked": 0, "chapter": None,
    }

    active_tasks: list[str] = tasks if tasks is not None else _DEFAULT_TASKS

    # 1. 构造锚点输入(仅 "anchors" in tasks 且有 save_id 时)
    pending_anchors: list[dict] | None = None
    chapter_map: list[dict] | None = None

    if "anchors" in active_tasks and save_id:
        try:
            pending_anchors, chapter_map = _build_anchor_inputs(save_id)
        except Exception as exc:
            # 锚点备料失败不阻断 ops / acceptance
            log.warning("[recorder_bridge] 锚点输入备料失败(跳过 anchors): %s", exc)
            pending_anchors = None
            chapter_map = None

    # 2. 单次 recorder LLM 调用
    try:
        from agents import recorder as _recorder
        state_data: dict = state.data if hasattr(state, "data") else {}
        rec = _recorder.record_turn(
            response,
            state_data,
            pending_anchors=pending_anchors,
            chapter_map=chapter_map,
            acceptance_clauses=acceptance_clauses,
            tasks=active_tasks,
            user_id=user_id,
        )
    except Exception as exc:
        log.warning("[recorder_bridge] recorder.record_turn 失败，返回空结果: %s", exc)
        return _empty

    # 3. 锚点落库：复用 reconcile_anchors_for_turn，注入预计算判定器(零新增 LLM 调用)
    anchors_marked = 0
    if "anchors" in active_tasks and save_id and user_id:
        try:
            anchors_marked = _persist_anchors(save_id, user_id, response, rec)
        except Exception as exc:
            # 锚点落库失败不丢 ops / unmet
            log.warning("[recorder_bridge] 锚点落库失败(已吞): %s", exc)
            anchors_marked = 0

    # 4. 汇总返回(ops 由调用方 apply，不在此层写库)
    return {
        "ops": rec.get("ops") or [],
        "unmet": rec.get("unmet") or [],
        "anchors_marked": anchors_marked,
        "chapter": rec.get("current_chapter"),
    }


# ── 私有辅助 ───────────────────────────────────────────────────────────


def _build_anchor_inputs(
    save_id: int,
) -> tuple[list[dict], list[dict] | None]:
    """备料锚点判定所需的 pending 列表 + 章节地图。

    镜像 anchor_reconcile._reconcile_impl 第 2-3 步的取数逻辑：
      · get_progress_window(save_id) → ch_min / ch_max
      · list_pending_for_phase(save_id, None, limit=12, chapter_min=..., chapter_max=...,
                               order_by_chapter=True)
      · _load_estimate_context(save_id) → window_chapters

    pending 的字段名 anchor_reconcile 和 recorder 都兼容(anchor_key/summary/is_fatal)。
    chapter_map 映射为 recorder 期望的 {chapter, story_time_label, summary}：
      anchor_reconcile._load_estimate_context 返回 {chapter, label, summary}，
      recorder._build_user_prompt 同时接受 "story_time_label" 和 "label"(已在 recorder.py 中
      做 c.get("story_time_label") or c.get("label") 兼容)，直接传穿即可。
    """
    from agents.anchor_seed_agent import get_progress_window, list_pending_for_phase
    from gm_serving.anchor_reconcile import _load_estimate_context

    # 进度窗口
    win = get_progress_window(int(save_id))
    ch_min = win.get("chapter_min")
    ch_max = win.get("chapter_max")

    # anchor_pace:与 _reconcile_impl 一致收窄候选窗口到 [ch_min, ch_min+_MARK_WINDOW],
    # 否则备料喂给 LLM 的 pending 含远章锚点 → 误判到达 → 进度跳(本函数原漏了这步收窄)。
    from core.feature_flags import feature_enabled_for_save
    from gm_serving.anchor_reconcile import _MARK_WINDOW
    if feature_enabled_for_save("anchor_pace", int(save_id)) and ch_min is not None:
        ch_max = int(ch_min) + _MARK_WINDOW

    # 窗口内 pending 锚点(与 _reconcile_impl 完全一致)
    pending_raw = list_pending_for_phase(
        int(save_id),
        None,
        limit=_MAX_PENDING,
        chapter_min=ch_min,
        chapter_max=ch_max,
        order_by_chapter=True,
    )
    # 映射为 recorder 需要的最小字段集(anchor_key/summary/is_fatal)
    pending_anchors = [
        {
            "anchor_key": a.get("anchor_key") or "",
            "summary": a.get("summary") or "",
            "is_fatal": bool(a.get("is_fatal")),
        }
        for a in pending_raw
        if a.get("anchor_key")
    ]

    # 章节地图(估章上下文)；_load_estimate_context 无 script_id 时返 None
    chapter_map: list[dict] | None = None
    try:
        est_ctx = _load_estimate_context(int(save_id))
        if est_ctx:
            # 返回的 window_chapters 含 {chapter, label, summary}
            # recorder._build_user_prompt 兼容 "label" 与 "story_time_label"，直接传
            chapter_map = est_ctx.get("window_chapters") or None
    except Exception as exc:
        log.info("[recorder_bridge] 估章上下文备料失败(跳过章节地图): %s", exc)
        chapter_map = None

    return pending_anchors, chapter_map


def _persist_anchors(
    save_id: int,
    user_id: int,
    turn_text: str,
    rec: dict,
) -> int:
    """把 recorder 预计算的锚点结果注入 reconcile_anchors_for_turn。

    核心技巧：向 reconcile_anchors_for_turn 注入一个 _judge，该 judge 忽略所有参数，
    直接返回 recorder 已经计算好的 reached + current_chapter。
    reconcile_anchors_for_turn 会用这个结果做校验 + 落库 + 进度推进，
    完全复用现有写路径，零新增 LLM 调用。

    _judge 签名(anchor_reconcile._reconcile_impl 第 4 步)：
        _judge(user_id, text, pending, *, save_id) -> dict | list
    返回 dict {"reached": [...], "estimated_chapter": int|None} 时走新式路径。
    """
    from gm_serving.anchor_reconcile import reconcile_anchors_for_turn

    # 预计算结果:recorder 用的是 "current_chapter"；_normalize_judge_result 读 "estimated_chapter"
    precomputed_reached: list[dict] = rec.get("reached") or []
    precomputed_chapter: int | None = rec.get("current_chapter")

    def _precomputed_judge(*_args: Any, **_kwargs: Any) -> dict:
        """忽略 reconcile_anchors_for_turn 传来的参数，直接返回 recorder 预计算结果。"""
        return {
            "reached": precomputed_reached,
            "estimated_chapter": precomputed_chapter,
        }

    return reconcile_anchors_for_turn(
        save_id,
        user_id,
        turn_text,
        _judge=_precomputed_judge,
    )


__all__ = ["run_unified_recorder"]
