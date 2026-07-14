"""Phase 4 后处理助手:锚点兜底 / GM JSON op apply / acceptance A/B / 并行后处理。
run_gm_phase(gm.py)采用这些助手。拆包自 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from state import GameState

from ._common import PipelineContext, log


async def _run_anchor_reconcile(ctx: Any, api_user: dict | None, response: str) -> int:
    """每回合确定性「世界线锚点」兜底判定(task: anchor auto-reconcile)。

    在 GM 本轮工具调用 + JSON op apply 之后跑(GM 自调过的锚点已 occurred、不在 pending),
    把本回合剧情【明确到达】的 pending 锚点确定性标记 occurred/variant。

    全程不破回合:reconcile 内部已 try/except 吞掉一切异常;这里再包一层兜底。
    成本门控/保守判定/防剧透/确定性落库全在 reconcile 内部。返回标记数(供 SSE 事件)。
    """
    try:
        _save_id = ctx.active_save_id or ctx.early_active_save_id or 0
        _user_id = ctx.persist_user_id or (int(api_user["id"]) if api_user else 0)
        if not _save_id or not _user_id or not (response or "").strip():
            return 0
        from gm_serving.anchor_reconcile import reconcile_anchors_for_turn
        return await asyncio.to_thread(
            reconcile_anchors_for_turn, int(_save_id), int(_user_id), response,
        )
    except Exception as _rec_err:
        log.warning(f"[chat] anchor reconcile 跳过(不影响回合): {_rec_err}")
        return 0


# ---------------------------------------------------------------------------
# Phase 4: GM 主响应 (流式 token + tool_call + 后处理 extractor / acceptance)
# ---------------------------------------------------------------------------


def _apply_gm_json_ops(
    *,
    state: "GameState",
    response_with_ops: str,
    api_user: dict[str, Any] | None,
    active_script_id: Callable[[dict[str, Any] | None], int | None],
    ctx: "PipelineContext",
) -> list[str]:
    """把 GM 的 JSON op(set/append/overwrite/question/hypothesis/...)经 ChatWriteContext
    确定性 apply 回内存 state,返回 update 文案列表(已含 directive_updates 前缀)。

    sync 与 async 两条后处理路径**共用** —— async 早退前也必须调它。否则 GM 经
    `{"op":"set/append/overwrite/question/...}` 写的 player.current_location / world.time /
    memory.resources / memory.main_quest / relationships.* / 选项 / 推测全部丢失
    (worker 进程 state_data={} 是 no-op,补不回来)。dispatcher 工具调用走的是流式内联
    apply,不受影响,但 JSON op 是 GM 写核心每轮状态的主通道。
    """
    import secrets as _ctx_secrets

    from state_write_context import (
        ChatWriteContext,
        clear_context as _clear_write_ctx,
        set_context as _set_write_ctx,
    )
    _json_op_ctx = ChatWriteContext(
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=ctx.early_active_save_id or 0,
        script_id=active_script_id(api_user),
        trace_id=f"gm-jsop-{_ctx_secrets.token_urlsafe(6)}",
        origin="llm_chat_json_op",
    )
    _ctx_token = _set_write_ctx(_json_op_ctx)
    try:
        # 能力/资源只走结构化标签 + JSON op/extractor 写入；旧的「正文关键词 regex 兜底」
        # 已彻底移除（曾误把《无限恐怖》特定能力注入任意剧本）。
        return ctx.directive_updates + state.apply_structured_updates(response_with_ops)
    finally:
        _clear_write_ctx(_ctx_token)


# acceptance A/B 改写候选:节流 —— 每存档最多每 N 回合提供一次改写候选(防止每回合弹 A/B 打断沉浸)。
_ACCEPTANCE_AB_MIN_INTERVAL = 5
# 后台改写候选任务的强引用集(防 asyncio.create_task 被 GC);完成后自动移除。
_ACCEPTANCE_BG_TASKS: set = set()


def _acceptance_ab_pref_enabled(user_id) -> bool:
    """用户级开关:user_preferences.preferences['acceptance_ab.enabled']。缺省/非 False = 开(默认提供改写候选)。
    玩家可在游戏设置里手动关掉(行者无疆诉求)。仅在即将花一次 LLM 生成候选前读一次(热路径零额外开销)。"""
    if not user_id:
        return True
    try:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select preferences->>'acceptance_ab.enabled' as v from user_preferences where user_id = %s",
                (int(user_id),),
            ).fetchone()
        v = (row or {}).get("v")
        return not (str(v).strip().lower() in ("false", "0", "off", "no")) if v is not None else True
    except Exception:
        return True


def _log_acceptance_ab(user_id, save_id, turn, unmet, original_text, rewrite_text):
    """插入一条 acceptance A/B 候选(chosen=null 待玩家选),返回行 id;失败返回 None。
    数据采集层:统计玩家偏好首稿/改写稿 + 触发改写的验收点,用于迭代 acceptance 算法。"""
    try:
        from psycopg.types.json import Jsonb

        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "insert into acceptance_ab_log(user_id, save_id, turn, unmet, original_text, rewrite_text)"
                " values (%s,%s,%s,%s,%s,%s) returning id",
                (int(user_id) if user_id else None, int(save_id or 0), int(turn or 0),
                 Jsonb(list(unmet or [])), str(original_text or ""), str(rewrite_text or "")),
            ).fetchone()
            if hasattr(db, "commit"):
                db.commit()
            return int(row["id"]) if row else None
    except Exception as _e:
        log.warning(f"[acceptance] ab log insert failed: {_e}")
        return None


async def _run_post_gm_parallel(
    *,
    response: str,
    state: GameState,
    api_user: dict[str, Any] | None,
    ctx: PipelineContext,
    active_script_id: Callable[[dict[str, Any] | None], int | None],
    is_extractor_enabled: Callable[[dict[str, Any] | None], bool],
    is_black_swan_enabled: Callable[[dict[str, Any] | None], bool] | None = None,
) -> dict[str, Any]:
    """并行跑 GM 后处理(黑天鹅 + extractor + 世界心跳),返回 {response_with_ops, extractor_active}。
    时间跳跃/套路/星期等确定性叙事纠错已统一到 timeline_narrative_guard.run_narrative_guards
    (async/sync 两路共用,见 run_gm_phase),不再在此并行跑。

    世界心跳(_worker_heartbeat,见 docs/design/world_heartbeat_v0.md)接线在此而非
    postproc worker 队列:postproc worker 进程侧无法安全访问实时 state(见
    run_postproc_worker.py:101 的 black_swan handler enable_llm=False 同款理由)。

    extractor/black_swan 只读 response + state;heartbeat 会写 state.data 的
    background_events / heartbeat_meta 两个专属键(其它 worker 不碰这两键,键级不相交
    → gather 内并发安全),由本回合 Phase 5 统一持久化。
    任何 worker 抛异常 → log + 返回该 worker 的中性值,不影响其它 worker。
    """
    if not response.strip():
        return {"response_with_ops": response, "extractor_active": False}

    user_id_int = int(api_user.get("id")) if api_user else None

    async def _worker_black_swan() -> None:
        try:
            # 优先走 user-pref callable(app.py 注入);未注入时退回 env-var。
            if is_black_swan_enabled is not None:
                if not is_black_swan_enabled(api_user):
                    log.debug("[black_swan] disabled by user pref, skipping")
                    return
            else:
                from core.config import enable_black_swan as _enable_black_swan
                if not _enable_black_swan():
                    return
            from agents.black_swan_agent import maybe_trigger as _maybe_trigger
            _sub_gm = getattr(ctx, "sub_gm", None)
            _swan_api = getattr(_sub_gm, "api_id", None) if _sub_gm else None
            _swan_backend = getattr(_sub_gm, "_backend", None) if _sub_gm else None
            _swan_model = getattr(_swan_backend, "model_name", None) if _swan_backend else None
            result = await asyncio.to_thread(
                _maybe_trigger,
                state,
                user_id=user_id_int or 0,
                save_id=ctx.early_active_save_id or 0,
                script_id=active_script_id(api_user),
                api_id_override=_swan_api,
                model_override=_swan_model,
                enable_llm=bool(api_user),
            )
            if result.get("triggered"):
                from datetime import datetime as _dt
                audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": _dt.now().isoformat(timespec="seconds"),
                    "kind": "black_swan_triggered",
                    "source": "black_swan_agent",
                    "hint": (result.get("proposal") or {}).get("summary", "")[:200],
                    "turn": state.data.get("turn", 0),
                })
                if len(audit) > 200:
                    state.data["permissions"]["audit_log"] = audit[-200:]
        except Exception as exc:
            log.warning(f"[black_swan] failed silently: {exc}")

    async def _worker_extractor() -> tuple[bool, str]:
        """返回 (extractor_active, response_with_ops)。"""
        try:
            if not is_extractor_enabled(api_user):
                return False, response
            from agents import extractor as _extractor
            ops = await asyncio.to_thread(
                _extractor.extract_state_ops,
                narrative_text=response,
                state_data=state.data,
                user_id=user_id_int,
                timeout_sec=15,
            )
            if ops:
                return True, response + "\n\n```json\n" + json.dumps(ops, ensure_ascii=False) + "\n```"
            return True, response
        except Exception as exc:
            log.warning(f"[chat] extractor pipeline failed: {exc}; falling back to single-step")
            try:
                from datetime import datetime as _dt
                audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": _dt.now().isoformat(timespec="seconds"),
                    "kind": "extractor_error",
                    "source": "extractor",
                    "hint": f"GM 第二步失败:{type(exc).__name__}: {str(exc)[:200]}",
                    "turn": state.data.get("turn", 0),
                })
                if len(audit) > 200:
                    state.data["permissions"]["audit_log"] = audit[-200:]
            except Exception:
                pass
            return False, response

    async def _worker_heartbeat() -> None:
        """世界心跳 v0(活世界·柱子1):should_tick 判定不该跳立即零成本返回;
        该跳则一次便宜 LLM 调用产 1-2 条世界侧事件,写进 state.data["background_events"]
        (本回合 Phase 5 统一持久化,与 extractor 同命运)。只读/自写 state.data 的独立
        字段,与 black_swan/extractor 互不依赖,可安全并行。

        设计文档: docs/design/world_heartbeat_v0.md §5。
        """
        try:
            from agents.world_heartbeat import run_heartbeat_tick, should_tick
            if not should_tick(state.data, user_id_int):
                return
            await asyncio.to_thread(
                run_heartbeat_tick,
                state,
                user_id_int,
            )
        except Exception as exc:
            log.debug(f"[world_heartbeat] worker failed silently: {exc}")

    # 并行执行,gather return_exceptions=False 但每个 worker 内部已 try/except,不会抛
    _swan_unused, ex_result, _heartbeat_unused = await asyncio.gather(
        _worker_black_swan(),
        _worker_extractor(),
        _worker_heartbeat(),
    )
    extractor_active, response_with_ops = ex_result
    return {
        "response_with_ops": response_with_ops,
        "extractor_active": extractor_active,
    }
