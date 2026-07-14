"""Phase 1:玩家 directive 落地(/set 工具化 + 正则 fallback + /compact + 重写回滚 + timeline anchor)。
拆包自 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from state import GameState

from ._common import PipelineContext, SSEEvent, log


# ---------------------------------------------------------------------------
# Phase 1: 玩家 directive 应用 (过期问题 + /set 工具化 + 正则 fallback + set_parser + timeline anchor)
# ---------------------------------------------------------------------------


async def apply_player_directives_phase(
    ctx: PipelineContext,
    *,
    resolve_persist_target: Callable[[dict[str, Any] | None], tuple[int | None, int | None]],
    persist_runtime_checkpoint: Callable[[GameState, dict[str, Any] | None], None],
    payload_fn: Callable[[dict[str, Any] | None], dict[str, Any]],
    is_set_parser_enabled: Callable[[dict[str, Any] | None], bool],
    active_script_id: Callable[[dict[str, Any] | None], int | None],
) -> AsyncIterator[SSEEvent]:
    """Phase 1: 玩家 directive 落地。

    步骤 (来自 app.py 注释 task 27 / task 86 / task 87):
      1. expire_stale_gm_questions (放弃上轮未答 GM 询问)
      2. /set 命令工具化路径 (command_agent.parse_set_command + ToolDispatcher)
      3. 正则 fallback (apply_player_directives) — 两条都跑,工具调用没覆盖的字段
         由正则补齐
      4. set_parser (老 JSON-ops 接口) — 仅当用户偏好启用 + 主路径没接管
      5. timeline anchor 解析 — directive 改了 current_label 时映射到剧本章节

    退出前把 directive_updates, early_persist_user_id, early_active_save_id
    写回 ctx 供后续 phase 使用。
    """
    state = ctx.state
    api_user = ctx.api_user
    message_for_model = ctx.message_for_model

    # step 1: 过期上轮 GM 询问
    try:
        _expired_n = state.expire_stale_gm_questions(reason="new_chat_turn")
        if _expired_n:
            yield ("updates", {
                "items": [f"自动过期 {_expired_n} 条上轮未回答的 GM 询问"],
                "stage": "pre_directive",
            })
    except Exception as _exp_err:
        log.warning(f"[chat] expire stale questions failed: {_exp_err}")

    directive_updates: list[str] = []
    command_tools_handled = False
    _msg_stripped = message_for_model.strip()
    _is_set_command = bool(_msg_stripped) and _msg_stripped.split(maxsplit=1)[0] in {
        "/set", "/设定", "/设置",
    }
    # iter#23: /compact 用户命令 — Claude Code 风格,立即压缩当前 phase 历史
    _is_compact_command = bool(_msg_stripped) and _msg_stripped.split(maxsplit=1)[0] in {
        "/compact", "/压缩",
    }
    # task 87: 提前解析 persist target,让 dispatcher 拿到 save_id 做作用域校验。
    _early_persist_user_id, _early_active_save_id = resolve_persist_target(api_user)
    ctx.early_persist_user_id = _early_persist_user_id
    ctx.early_active_save_id = _early_active_save_id
    # iter#23: 把 save_id 写到 state 一个"私有"键,让 state.history_messages()
    # 不用透传参数也能拉 save_phase_digests 做 Claude Code /compact 风格压缩。
    if _early_active_save_id:
        state.data["_active_save_id"] = int(_early_active_save_id)

    # iter#23 step 2a: /compact 用户命令 — 直接调 compact_phase 摘要当前阶段
    if _is_compact_command:
        try:
            _sid = ctx.early_active_save_id or 0
            if not _sid:
                yield ("agent", {
                    "phase": "compact",
                    "message": "/compact 失败:当前没有 active save",
                    "status": "error", "elapsed_ms": 0,
                })
                ctx.early_return = True
                return
            # 拿当前 phase_index (current 或 last closed - 1 都行,这里取 current phase)
            from platform_app.db import connect as _connect
            with _connect() as db:
                _row = db.execute(
                    "select coalesce(max(phase_index), 0) as pi "
                    "from save_phase_digests where save_id = %s",
                    (_sid,),
                ).fetchone()
            _phase = int((_row or {}).get("pi") or 0)
            yield ("agent", {
                "phase": "compact",
                "message": f"开始压缩 Phase {_phase} (LLM 摘要,~10-20s)",
                "status": "running", "elapsed_ms": 0,
            })
            from agents.phase_digest_agent import compact_phase
            _uid_compact = int(api_user.get("id")) if api_user else None
            _result = compact_phase(_sid, _phase, user_id=_uid_compact, force=True)
            if _result.get("error"):
                yield ("agent", {
                    "phase": "compact",
                    "message": f"/compact 失败:{_result['error']}",
                    "status": "error", "elapsed_ms": 0,
                })
            else:
                # 关键:compact_phase(force=True) 把当前 open phase 就地标 closed,但不重开。
                # 若不补开新 phase,ensure_initial_phase 会因"已存在(closed)phase 行"早退、
                # detect_phase_boundary 因无 active phase 恒 False → 该存档自此**永久停止**
                # 自动折叠历史,/compact 之后到最近 6 轮之间的剧情既无原文也无摘要 = GM 失忆
                # (与 /compact 目的相反)。这里立即开一个新 open phase 接管后续回合。
                try:
                    from save_phase_manager import open_new_phase as _open_new_phase
                    _cur_turn = int((state.data or {}).get("turn") or 0)
                    _open_new_phase(_sid, turn_index=_cur_turn + 1)
                except Exception:
                    pass
                _summary_excerpt = (_result.get("summary") or "")[:200]
                yield ("agent", {
                    "phase": "compact",
                    "message": (
                        f"压缩完成:Phase {_phase} ({_result.get('commit_count', 0)} 提交) "
                        f"→ {_summary_excerpt}..."
                    ),
                    "status": "done", "elapsed_ms": int(_result.get("elapsed_ms", 0)),
                    "phase_index": _phase,
                    "key_events_count": len(_result.get("key_events") or []),
                    "key_npcs": (_result.get("key_npcs") or [])[:5],
                })
                # 通知前端刷新存档(history_anchors 多了一条)
                try:
                    from state_event_bus import emit as _emit_event
                    _emit_event(api_user["id"] if api_user else None,
                                "save_history_anchors", "insert", {"source": "compact"})
                except Exception:
                    pass
        except Exception as _compact_err:
            yield ("agent", {
                "phase": "compact",
                "message": f"/compact 异常:{type(_compact_err).__name__}: {_compact_err}",
                "status": "error", "elapsed_ms": 0,
            })
        ctx.early_return = True
        return

    # 反馈#42: 重写型 /set —— 玩家 /set 纠正设定并要求"重新RP/重写/重来/重演"时,旧的
    # (被纠正的)那轮叙事如果留在上下文里,GM 下一稿只能编借口圆回去或突然改口,破坏沉浸感。
    # 确定性修复:把上一轮整体软回滚(移活跃指针到父 commit + trash 旧回合 + 清本回合 messages/
    # anchors/digests),把内存状态退回到上一轮之前,再让下面的 /set 在这个干净基线上应用,最后
    # 用"上一轮的原始玩家输入"在纠正后的状态下重演本轮(而不是把 /set 文本本身喂给 GM)。
    _REWRITE_SET_RE = r"重新\s*(rp|演|叙述|描述|生成|回应|回复|来|讲|写|说)|重写|重来|重演|\bredo\b"
    _set_body_for_rewrite = ""
    if os.getenv("RPG_REWRITE_SET", "1") != "0":
        for _p in ("/set", "/设定", "/设置"):
            if _msg_stripped.startswith(_p):
                _set_body_for_rewrite = _msg_stripped[len(_p):]
                break
    if (_set_body_for_rewrite and ctx.early_active_save_id and api_user
            and re.search(_REWRITE_SET_RE, _set_body_for_rewrite, re.IGNORECASE)):
        try:
            from platform_app.branches.deletion import rewind_last_round
            _rw = rewind_last_round(int(api_user["id"]), int(ctx.early_active_save_id))
            _redo = (str((_rw or {}).get("redo_player_input") or "")).strip()
            # 被回滚轮的原始输入若为空 / 本身又是斜杠命令,放弃重演(退化为普通 /set)
            if _rw and _redo and not _redo.startswith("/"):
                # 内存状态整体退回到上一轮之前(含 history/turn/world/memory/...),后面的 /set
                # 解析与应用都在这个纠正基线上发生。原对象身份保留,下游 phase 持有的引用仍有效。
                state.data.clear()
                state.data.update(_rw["reverted_state"])
                # clear() 抹掉了前面写入的私有键,重新挂回 save_id(history_messages 取 phase digest 要用)
                if ctx.early_active_save_id:
                    state.data["_active_save_id"] = int(ctx.early_active_save_id)
                # 下游 context/GM/persist 改用"原始输入"重演本轮;"/set"文本只在本 phase 用于解析指令
                ctx.message_for_model = _redo
                directive_updates.append(
                    f"/set 重写:已回滚上一轮(turn {_rw.get('deleted_turn')})并按修正后的设定重演本轮"
                )
                yield ("rewind", {
                    "replay_user": _redo,
                    "restored_turn": _rw.get("restored_turn"),
                    "reason": "rewrite_set",
                })
        except Exception as _rw_err:
            log.warning(f"[chat] rewrite-set rewind failed, fallback to plain /set: {_rw_err}")

    # step 2: /set 工具化路径
    if _is_set_command:
        try:
            from agents.command_agent import parse_set_command
            from tools_dsl.command_dispatcher import (
                ToolCallEnvelope,
                ToolDispatcher,
                get_registry,
            )
            from tools_dsl.command_tools_register import ensure_registered
            ensure_registered()  # 幂等

            _uid = int(api_user.get("id")) if api_user else 0
            _calls = parse_set_command(
                set_text=message_for_model,
                state_data=state.data,
                user_id=_uid or None,
                timeout_sec=15,
            )
            if _calls:
                _dispatcher = ToolDispatcher(
                    registry=get_registry(),
                    state_provider=lambda env, _state=state: _state,
                )
                import secrets as _secrets
                _trace_id = f"chat-{_secrets.token_urlsafe(6)}"
                # 一次 /set 拆出的多工具同 trace_id 并行 (彼此独立字段)
                for _call in _calls:
                    _env = ToolCallEnvelope(
                        user_id=_uid,
                        save_id=_early_active_save_id or 0,
                        tool=_call.get("name") or "",
                        args=_call.get("input") or {},
                        origin="llm_set",
                        trace_id=_trace_id,
                    )
                    _res = _dispatcher.dispatch_sync(_env)
                    if _res.ok:
                        directive_updates.append(f"{_env.tool}: {_res.result}")
                    else:
                        # 失败可能来自 DispatchError(error 有值)或工具自身返回的
                        # "X 失败: ..." 结果串(error=None,消息在 result 里)。
                        _err_txt = str(_res.error or _res.result or "未知原因")
                        directive_updates.append(
                            _err_txt if _err_txt.startswith(_env.tool)
                            else f"{_env.tool} 未生效: {_err_txt}"
                        )
                command_tools_handled = True
        except Exception as _cmd_exc:
            log.warning(f"[chat] command_agent/dispatcher failed, fallback to regex: {_cmd_exc}")

    # step 3: 正则 fallback — 总是跑,补齐 LLM 没覆盖的字段
    directive_updates.extend(state.apply_player_directives(message_for_model))

    # step 4: set_parser (老 JSON-ops 接口) 兜底
    if (not command_tools_handled and
            message_for_model.strip().startswith("/set") and
            is_set_parser_enabled(api_user)):
        try:
            import tools_dsl.set_parser as _set_parser
            parser_ops = _set_parser.parse_set_directive(
                set_text=message_for_model,
                state_data=state.data,
                user_id=int(api_user.get("id")) if api_user else None,
                timeout_sec=15,
            )
            for op in parser_ops:
                kind = (op.get("op") or "set").lower()
                try:
                    if kind == "hypothesis":
                        txt = op.get("text") or op.get("value") or ""
                        if txt:
                            mid = state.add_hypothesis(
                                text=txt, source="user:/set:parser",
                                time_label=op.get("time_label"),
                                characters=op.get("characters"),
                            )
                            directive_updates.append(f"推测登记（/set 解析）：{mid}")
                    elif kind in ("set", "append", "overwrite"):
                        path = (op.get("path") or "").strip()
                        if path:
                            spec = f"{path}={op.get('value', '')}"
                            res = state.apply_state_write(
                                spec, source="user:/set:parser",
                                force=True,
                                append=(kind == "append"),
                                overwrite=(kind == "overwrite"),
                            )
                            directive_updates.append(f"/set 解析: {res}")
                except Exception as op_exc:
                    log.warning(f"[set_parser] op apply failed: {op_exc} for {op}")
        except Exception as exc:
            log.warning(f"[chat] set_parser failed: {exc}; 继续走简单 /set 路径")
            try:
                from datetime import datetime as _dt
                audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": _dt.now().isoformat(timespec="seconds"),
                    "kind": "set_parser_error",
                    "source": "set_parser",
                    "hint": f"/set 自然语言解析失败：{type(exc).__name__}: {str(exc)[:200]}",
                    "turn": state.data.get("turn", 0),
                })
                if len(audit) > 200:
                    state.data["permissions"]["audit_log"] = audit[-200:]
            except Exception:
                pass

    # step 5: timeline anchor 解析
    try:
        _timeline_label = (state.data.get("world") or {}).get("timeline", {}).get("current_label", "")
        if directive_updates and _timeline_label:
            _script_id = active_script_id(api_user)
            if _script_id:
                from script_timeline import resolve_timeline_anchor as _resolve_anchor
                _anchor = _resolve_anchor(int(_script_id), _timeline_label)
                if _anchor:
                    _tl = state.data["world"]["timeline"]
                    _tl["anchor_chapter"] = _anchor["chapter_min"]
                    _tl["chapter_min"] = _anchor["chapter_min"]
                    _tl["chapter_max"] = _anchor["chapter_max"]
                    _tl["anchor_phase"] = _anchor["story_phase"]
                    _tl["anchor_event"] = (_anchor.get("sample_summary") or "")[:120]
                    _tl["anchor_confidence"] = _anchor.get("score", 0.0)
                    if _anchor.get("story_phase"):
                        _tl["current_phase"] = _anchor["story_phase"]
                    # 群反馈(白玖):/set 世界线跳转只写 timeline → 剧情跳成功但面板「当前」
                    # 按 worldline.progress_chapter 判定仍钉开局、GM 锚点窗口/揭示天花板也
                    # 不跟(「/set 跳章信号传播」第6缝)。显式跳转=玩家权威进度,与出生点/
                    # advance_story_progress 同语义推进(max-only 单调,回跳不回退)。
                    try:
                        if _early_active_save_id:
                            from platform_app.db import connect as _conn_jump
                            from gm_serving.settings import (
                                advance_progress as _adv_jump,
                                set_user_progress_floor as _floor_jump,
                            )
                            with _conn_jump() as _db_jump:
                                _adv_jump(_db_jump, int(_early_active_save_id), int(_anchor["chapter_min"]))
                                _floor_jump(_db_jump, int(_early_active_save_id), int(_anchor["chapter_min"]))
                            _wl_jump = state.data.setdefault("worldline", {})
                            try:
                                _wl_jump["progress_chapter"] = max(
                                    int(_wl_jump.get("progress_chapter") or 0),
                                    int(_anchor["chapter_min"]))
                            except (TypeError, ValueError):
                                _wl_jump["progress_chapter"] = int(_anchor["chapter_min"])
                    except Exception as _prog_err:
                        log.warning(f"[chat] 世界线跳转进度推进跳过(非致命): {_prog_err}")
                    directive_updates.append(
                        f"时间线锚点 → 第{_anchor['chapter_min']}-{_anchor['chapter_max']}章 · "
                        f"{_anchor['story_phase']}"
                    )
    except Exception as _anchor_err:
        log.warning(f"[chat] timeline anchor resolve failed: {_anchor_err}")

    if directive_updates:
        persist_runtime_checkpoint(state, api_user)
        yield ("status", payload_fn(api_user))
        yield ("updates", {"items": directive_updates, "stage": "pre_llm"})

    ctx.directive_updates = directive_updates
