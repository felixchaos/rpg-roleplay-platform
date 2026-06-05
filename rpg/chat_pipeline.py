"""Chat pipeline phases (task #51).

把 app.py 里 /api/chat 内部的 stream() 拆出来,按 5 个 async-generator phase 串起来。
每个 phase 接收一个 PipelineContext + 必要参数,yield SSE event tuple
(event_name, data_dict),并在退出前把"留给下一个 phase"的产物写到 ctx 上。

ctx.early_return = True 表示这个 phase 已经发了 done/error,orchestrator 应当跳出。

这层只搬家,不改语义:SSE 事件名/payload/顺序/contextvar 设置/异常分支
都和原来 app.py inline 实现一致。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from agents.context_agent import run_context_agent
from core.logging import get_logger
from state import GameState, strip_json_state_ops, strip_meta_tool_preamble

log = get_logger(__name__)

# W1 容量优化: RPG_POSTPROC_MODE=async (默认) → GM 流完即入队 Phase 4, 不阻塞 worker。
# RPG_POSTPROC_MODE=sync → 旧行为 (后处理阻塞主路径, 测试/debug 用)。
_POSTPROC_MODE = os.environ.get("RPG_POSTPROC_MODE", "async").lower()

# 反馈 #28:玩家短输入(<= N 字)→ 该回合前置「镜头规则」元指令,避免 GM 扩写玩家自己的
# 动作而忽略对方反应。阈值可用 RPG_SHORT_INPUT_CHARS 调(默认 30,覆盖绝大多数单动作短 RP)。
try:
    _SHORT_INPUT_CHARS = max(0, int(os.environ.get("RPG_SHORT_INPUT_CHARS", "30")))
except (TypeError, ValueError):
    _SHORT_INPUT_CHARS = 30

_SHORT_INPUT_DIRECTIVE = (
    "【本回合元指令·镜头规则(最高优先级,静默遵守,绝不向玩家复述或确认本条)】\n"
    "玩家本回合的输入很简短,这是「我做出这个动作/反应,然后呢?」的信号——玩家想看的是"
    "【对方 NPC 与世界如何回应】,而不是让你替他把这个简短动作复述、美化、扩写成大段。请严格执行:\n"
    "1. 玩家的动作/反应至多用一两句话承接带过,绝不大段复述或替玩家加戏(不要替玩家臆想心理活动、"
    "加台词、延展他没写出来的后续动作)。\n"
    "2. 本回合叙事重心 = 对方 NPC 对该动作的具体反应(神态、话语、肢体、情绪与立场变化)以及"
    "环境/局势的后果与推进。\n"
    "3. 以一个落在「对方/世界」一侧、有张力的场景节拍收尾,把球自然交还给玩家,而不是停在"
    "玩家自己的动作上。"
)


def _should_inject_short_input_directive(raw_msg: str | None) -> bool:
    """反馈 #28:确定性判定本回合是否为「短 RP 输入」需要注入镜头规则元指令。

    True 当且仅当:非空、非斜杠命令(/set /reveal 等)、strip 后长度 <= 阈值。
    纯函数,便于单测与回归锁定。"""
    r = (raw_msg or "").strip()
    if not r or r.startswith("/"):
        return False
    return len(r) <= _SHORT_INPUT_CHARS


def _gm_max_iters() -> int:
    """GM 单轮工具调用上限。原 8 太紧:世界线收束后一轮常需
    update_state → list_pending_anchors → mark_anchor_satisfied → set_question → 写正文,
    8 轮经常没串完就被「已达工具上限」硬截,浪费整轮 token。默认提到 16,可用
    RPG_GM_MAX_ITERS 调。GM 不再需要工具时会自然停,调高只给上限不强制多调。"""
    try:
        return max(4, int(os.environ.get("RPG_GM_MAX_ITERS", "16")))
    except (TypeError, ValueError):
        return 16


def _should_route_to_curator_clarify(confidence: float, threshold: float, clarify: str) -> bool:
    """Only interrupt the GM when the curator is actually below confidence threshold."""
    return bool((clarify or "").strip()) and float(confidence) < float(threshold)

# ---------------------------------------------------------------------------
# Pipeline context: 在 phase 之间传递的可变状态
# ---------------------------------------------------------------------------


def _sync_active_entities_from_bundle(state, bundle) -> None:
    """把 context bundle 算出的 npc_cards / player_card 同步到 state.active_entities。

    小说剧本不走 rules_engine enter_room (那条路径才填 active_entities),
    所以前端 "当前在场" 面板永远是空。这里在每轮 GM context 注入后,把:
      · player_card.name → 玩家自己 (always 在场,第一位)
      · npc_cards.items[*].name → 当前轮 GM 上下文里的 NPC (anchor 强制注入 +
        grep 命中,都在 npc_cards layer 里)
    写回 state.active_entities,前端 PanelCharacters 自然能渲染。

    幂等:每轮重写一次,以 npc_cards 当前结果为准。
    """
    if not state or not bundle:
        return
    layers = (bundle.get("debug") or {}).get("layers") or []
    active: list[dict] = []
    # 玩家始终第一位
    p = (state.data.get("player") or {})
    if p.get("name"):
        active.append({
            "id": "player",
            "name": p["name"],
            "kind": "player",
            "disposition": "self",
            "source": "player",
            "card_id": "",
        })
    for lyr in layers:
        if lyr.get("id") != "npc_cards":
            continue
        for it in (lyr.get("items") or []):
            nm = (it.get("name") or "").strip()
            if not nm or nm == p.get("name"):
                continue
            active.append({
                "id": f"npc:{nm}",
                "name": nm,
                "kind": "npc",
                "disposition": (it.get("disposition") or "neutral"),
                "source": (it.get("_source") or "context_inject"),
                "card_id": nm,  # 用 name 做 card_id,前端可点开看卡
                "identity": it.get("identity") or "",
            })
    state.data["active_entities"] = active


@dataclass
class PipelineContext:
    """phases 之间共享的可变 state。

    每个 phase 读它需要的字段,把产物写回。orchestrator(api_chat)只
    检查 early_return 来决定要不要短路。
    """

    # 入参 (orchestrator 填好)
    api_user: dict[str, Any] | None
    state: GameState
    gm: Any                                       # GameMaster
    sub_gm: Any                                   # GameMaster (sub)
    message_for_model: str
    run_id: int
    stop_event: Event
    chat_start_time: float

    # phase 间结果
    directive_updates: list[str] = field(default_factory=list)
    early_persist_user_id: int | None = None
    early_active_save_id: int | None = None
    persist_user_id: int | None = None
    active_save_id: int | None = None
    context_run_id: int | None = None
    agent_result: dict[str, Any] | None = None
    bundle: dict[str, Any] | None = None
    ctx_text: str = ""
    response: str = ""

    # 流程控制
    early_return: bool = False


# 类型别名:phase generator 产物
SSEEvent = tuple[str, dict[str, Any]]


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
                        directive_updates.append(
                            f"{_env.tool} 被拒绝: {_res.error}"
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


# ---------------------------------------------------------------------------
# Phase 2: context agent (sub-GM curator) + clarifying-question 短路
# ---------------------------------------------------------------------------


async def run_context_phase(
    ctx: PipelineContext,
    *,
    resolve_persist_target: Callable[[dict[str, Any] | None], tuple[int | None, int | None]],
    payload_fn: Callable[[dict[str, Any] | None], dict[str, Any]],
    active_script_id: Callable[[dict[str, Any] | None], int | None],
    clarify_threshold: Callable[[dict[str, Any] | None], float],
    persist_chat_turn: Callable[..., None],
    mark_context_run: Callable[..., None],
    apply_chat_rule_candidates: Callable[..., list[dict[str, Any]]],
    chat_rule_candidates: Callable[..., list[dict[str, Any]]],
    rule_results_prompt: Callable[..., str],
    persist_runtime_checkpoint: Callable[[GameState, dict[str, Any] | None], None],
    platform_knowledge_mod: Any,
    run_context_agent_fn: Callable[..., Any] | None = None,
) -> AsyncIterator[SSEEvent]:
    """Phase 2: 跑 context agent (子 GM curator),记 context_run,
    并在 curator confidence 低/有 clarifying_question 时短路 clarify 输出。

    退出前在 ctx 上设置 agent_result, bundle, ctx_text, context_run_id,
    persist_user_id, active_save_id。短路时设置 ctx.early_return = True。
    """
    state = ctx.state
    api_user = ctx.api_user
    message_for_model = ctx.message_for_model
    stop_event = ctx.stop_event
    sub_gm = ctx.sub_gm

    agent_result = None
    # 通过参数注入可被测试 monkeypatch (test_set_persists_on_gm_failure 模拟 504)。
    # 调用方传 app.run_context_agent → 那里被 patch 时这里能拿到 patched 版本。
    _rca = run_context_agent_fn or run_context_agent
    # task: harness 适配统一 — 不再透传 llm_curator 回调；
    # 由 context_agent 内部走 agents._harness.call_agent_json,
    # 用 sub_gm 当前 backend 的 api_id+model 作 override(provider 透明 +
    # Anthropic 强 schema)。旧 llm_curator 参数仍保留兼容外部测试 monkeypatch。
    _sub_api = getattr(sub_gm, "api_id", None)
    _sub_backend = getattr(sub_gm, "_backend", None)
    _sub_model = getattr(_sub_backend, "model_name", None) if _sub_backend else None
    # task: context_agent async 化 — context_agent 内部是同步 generator,
    # 中间穿插 ThreadPoolExecutor + time.sleep 轮询 LLM 结果,会阻塞 asyncio
    # event loop ~2-5s,期间 SSE chunks 全部停吐。
    # 折中:不改 context_agent 内部签名(测试 / 老 caller 仍可同步 for-iter),
    # 在 chat_pipeline 用 asyncio.to_thread + thread-safe queue 桥接,让 event loop
    # 在 LLM 调用期间仍能 schedule 其它 SSE 事件(比如 timeline guard / GM stream 前置)。
    async for item in _bridge_sync_generator_to_async(
        _rca,
        state, message_for_model,
        stop_requested=stop_event.is_set,
        user_id=api_user["id"] if api_user else None,
        script_id=active_script_id(api_user),
        # task 107E: 透传 save_id,否则 RuntimePhaseDigestProvider(本存档历史摘要)+
        # 锚点 NPC 强制登场(_extract_anchor_npc_names)因 services.save_id=None 永远 skipped。
        save_id=ctx.early_active_save_id,
        api_id_override=_sub_api,
        model_override=_sub_model,
    ):
        if item["type"] == "step":
            yield ("agent", item["step"])
        elif item["type"] == "stopped":
            state.set_last_context_agent({"status": "stopped", "steps": item.get("steps", [])})
            yield ("done", {"status": payload_fn(api_user), "interrupted": True})
            ctx.early_return = True
            return
        elif item["type"] == "result":
            agent_result = item

    if agent_result is None:
        yield ("error", {"message": "上下文子代理未返回结果", "partial": ctx.response})
        ctx.early_return = True
        return

    ctx_text = agent_result["retrieved_context"]
    bundle = agent_result["bundle"]

    # 5E preflight 由 run_rules_phase 处理,这里只先把 agent_result / bundle 推给 ctx
    ctx.agent_result = agent_result
    ctx.bundle = bundle
    ctx.ctx_text = ctx_text


# ---------------------------------------------------------------------------
# Phase 3: 5E rules preflight (GamePolicy.preflight + combat gate)
# ---------------------------------------------------------------------------


async def run_rules_phase(
    ctx: PipelineContext,
    *,
    payload_fn: Callable[[dict[str, Any] | None], dict[str, Any]],
    persist_chat_turn: Callable[..., None],
    persist_runtime_checkpoint: Callable[[GameState, dict[str, Any] | None], None],
    resolve_persist_target: Callable[[dict[str, Any] | None], tuple[int | None, int | None]],
    mark_context_run: Callable[..., None],
    clarify_threshold: Callable[[dict[str, Any] | None], float],
    apply_chat_rule_candidates: Callable[..., list[dict[str, Any]]],
    chat_rule_candidates: Callable[..., list[dict[str, Any]]],
    rule_results_prompt: Callable[..., str],
    platform_knowledge_mod: Any,
) -> AsyncIterator[SSEEvent]:
    """Phase 3: GamePolicy.preflight (combat gate) + rule candidates + curator clarify 短路 + context_run 记录。

    分两段:
      (a) preflight combat gate — 命中则 gate 返回叙事,直接 done + early_return。
      (b) rule_results 注入 prompt + last_retrieval / last_context / last_context_agent。
      (c) context_run 记 DB + 发 retrieval / context / status SSE。
      (d) clarify 短路 (curator 自评 confidence 低时直接 yield 问询)。
    """
    state = ctx.state
    api_user = ctx.api_user
    message_for_model = ctx.message_for_model
    agent_result = ctx.agent_result
    bundle = ctx.bundle
    ctx_text = ctx.ctx_text
    sub_gm = ctx.sub_gm

    # (a) preflight combat gate
    from game_policy import get_game_policy as _get_game_policy
    _policy = _get_game_policy(state)
    _combat_gate = _policy.preflight(message_for_model, state)
    if _combat_gate:
        _q_text = _combat_gate.get("question") or ""
        _q_opts = list(_combat_gate.get("options") or [])
        try:
            state.add_pending_question(
                _q_text,
                source=_combat_gate.get("source") or "rules_engine",
                options=_q_opts,
            )
        except Exception:
            pass
        try:
            from datetime import datetime as _dt
            audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
            audit.append({
                "ts": _dt.now().isoformat(timespec="seconds"),
                "kind": "combat_gated",
                "source": "rules_engine",
                "hint": f"{_combat_gate.get('kind')}: {_combat_gate.get('reason') or ''}",
                "turn": state.data.get("turn", 0),
            })
            if len(audit) > 200:
                state.data["permissions"]["audit_log"] = audit[-200:]
        except Exception:
            pass
        state.save()
        persist_runtime_checkpoint(state, api_user)
        yield ("agent", {
            "phase": "rules_gate",
            "message": _combat_gate.get("reason") or "RulesEngine 要求玩家先明确动作",
            "status": "done",
            "elapsed_ms": 0,
            "gate_kind": _combat_gate.get("kind"),
        })
        yield ("status", payload_fn(api_user))
        # 把规则裁定的问询当 GM 正文流出去,前端 chat history 才有记录
        _gate_msg_lines = [f"【规则要求先确认】{_q_text}"]
        if _q_opts:
            _gate_msg_lines.append("可选:")
            _gate_msg_lines.extend(f"  · {opt}" for opt in _q_opts)
        _gate_msg = "\n".join(_gate_msg_lines)
        yield ("token", {"text": _gate_msg})
        # 注:gate 路径 persist_user_id/active_save_id 走 early_*  (在 phase 1 已解析)
        try:
            persist_chat_turn(
                api_user, state, message_for_model, _gate_msg,
                persist_user_id=ctx.early_persist_user_id,
                active_save_id=ctx.early_active_save_id,
            )
        except Exception:
            pass
        yield ("status", payload_fn(api_user))
        yield ("done", {
            "status": payload_fn(api_user),
            "interrupted": False,
            "rules_gated": True,
            "gate_kind": _combat_gate.get("kind"),
        })
        ctx.early_return = True
        return

    # (b) rule candidates
    rule_results = apply_chat_rule_candidates(
        state,
        chat_rule_candidates(
            state,
            message_for_model,
            (agent_result.get("curator_plan") or {}).get("rule_candidate_actions") or [],
        ),
    )
    if rule_results:
        state.save()
        persist_runtime_checkpoint(state, api_user)
        rule_prompt = rule_results_prompt(rule_results, state)
        if rule_prompt:
            bundle["prompt"] = f"{bundle.get('prompt', '')}\n\n{rule_prompt}"
        bundle.setdefault("debug", {})["rule_results"] = rule_results
        yield ("agent", {
            "phase": "rules_engine",
            "message": "RulesEngine 已完成本轮规则裁定。",
            "status": "done",
            "elapsed_ms": 0,
            "rule_results": rule_results,
        })
        yield ("status", payload_fn(api_user))
        yield ("updates", {
            "stage": "rules_engine",
            "items": [
                f"RulesEngine: {(r.get('action') or {}).get('kind')} 已裁定"
                for r in rule_results
            ],
        })

    state.set_last_retrieval(ctx_text)
    state.set_last_context(bundle["debug"])

    # B4: 子代理 usage 单独记账（metadata.kind='sub_agent'）
    try:
        sub_usage = getattr(sub_gm._backend, "last_usage", {}) or {}
        if sub_usage and api_user:
            from platform_app.usage import record_usage as _rec
            _rec(
                user_id=api_user["id"],
                save_id=None,
                context_run_id=None,
                api_id=sub_gm.api_id,
                model_real_name=sub_gm._backend.model_name,
                usage=sub_usage,
                metadata={"kind": "sub_agent", "phase": "context_curator"},
                scenario="tool",
            )
    except Exception:
        pass

    state.set_last_context_agent({
        "status": "done",
        "steps": agent_result["steps"],
        "prompt": agent_result.get("agent_prompt", ""),
        "curator_plan": agent_result.get("curator_plan", {}),
        "cache_plan": bundle["debug"].get("cache_plan", {}),
    })

    persist_user_id, active_save_id = resolve_persist_target(api_user)
    ctx.persist_user_id = persist_user_id
    ctx.active_save_id = active_save_id
    context_run_id = None
    if persist_user_id and active_save_id:
        try:
            run_row = platform_knowledge_mod.record_context_run(
                persist_user_id,
                active_save_id,
                state.data,
                message_for_model,
                agent_result,
                bundle,
                ctx_text,
                status="done",
                duration_ms=int((time.time() - ctx.chat_start_time) * 1000),
            )
            context_run_id = (run_row or {}).get("id")
        except Exception:
            pass
    ctx.context_run_id = context_run_id

    # task 141: 同步 npc_cards layer 里的 NPC 到 state.active_entities,
    # 让前端 "当前在场" 面板能显示场景人物。小说剧本不走 rules_engine enter_room,
    # active_entities 永远空 — 这里用 context 已计算好的 npc_cards.items 填回去,
    # 玩家自己也放第一位。
    try:
        _sync_active_entities_from_bundle(state, bundle)
    except Exception:
        pass

    yield ("retrieval", {"text": ctx_text})
    yield ("context", {"debug": bundle["debug"]})
    yield ("status", payload_fn(api_user))

    # (d) curator 低 confidence **不再短路**。
    # 用户 harness 要求:每轮必须先推进剧情,绝不"一上来甩 (A)(B) 菜单回去 + 跳过 GM"。
    # curator 的 clarifying_question / candidate_actions / risk_flags 已通过 bundle 传给主 GM
    # 作上下文;主 GM 照常出场推进剧情,回合末再用结构化 question op 给出动作选项
    # (finalize 阶段 extract_trailing_choice 会确定性兜底,强制任何提问/选项结构化、不入正文)。
    _curator_plan = agent_result.get("curator_plan", {}) or {}
    _confidence = float(_curator_plan.get("confidence") or 1.0)
    if _confidence < clarify_threshold(api_user):
        try:
            from datetime import datetime as _dt
            audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
            audit.append({
                "ts": _dt.now().isoformat(timespec="seconds"),
                "kind": "curator_low_confidence",
                "source": "curator",
                "hint": f"confidence={_confidence:.2f} 偏低,但 GM 仍推进剧情(不再短路反问)",
                "turn": state.data.get("turn", 0),
            })
            state.data["permissions"]["audit_log"] = audit[-200:]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Phase 4: GM 主响应 (流式 token + tool_call + 后处理 extractor / acceptance)
# ---------------------------------------------------------------------------


def _apply_gm_json_ops(
    *,
    state: GameState,
    response_with_ops: str,
    api_user: dict[str, Any] | None,
    active_script_id: Callable[[dict[str, Any] | None], int | None],
    ctx: PipelineContext,
    extractor_active: bool,
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
    )
    from state_write_context import (
        clear_context as _clear_write_ctx,
    )
    from state_write_context import (
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
        # task 69:extractor 开启时让 state.py 跳过 regex 兜底
        return ctx.directive_updates + state.apply_structured_updates(
            response_with_ops, skip_regex_fallback=extractor_active,
        )
    finally:
        _clear_write_ctx(_ctx_token)


async def run_gm_phase(
    ctx: PipelineContext,
    *,
    payload_fn: Callable[[dict[str, Any] | None], dict[str, Any]],
    persist_chat_turn: Callable[..., None],
    mark_context_run: Callable[..., None],
    current_run_id_fn: Callable[[dict[str, Any] | None], int],
    is_stop_requested_global: Callable[[dict[str, Any] | None, int], bool],
    is_extractor_enabled: Callable[[dict[str, Any] | None], bool],
    is_black_swan_enabled: Callable[[dict[str, Any] | None], bool] | None = None,
    acceptance_verifier_mode: Callable[[dict[str, Any] | None], str],
    verify_acceptance: Callable[..., list[str]],
    active_script_id: Callable[[dict[str, Any] | None], int | None],
    chat_max_tokens: Callable[[dict[str, Any] | None], int] | None = None,
) -> AsyncIterator[SSEEvent]:
    """Phase 4: 主 GM 响应 + 后处理。

    步骤:
      - 构造 unified_tools + tool_call_router (dispatcher + MCP)
      - 流式调 gm.respond_stream_with_tools,中途若 stop_event/run_id 不匹配,
        把已流出的 token 落档为"被打断"
      - 流完检测 timeline_narrative_guard 时间跳跃违规
      - extractor 第二步抽 JSON ops 追加到 response 末尾
      - 包一层 ChatWriteContext contextvar 跑 apply_structured_updates
      - acceptance verifier (rule/llm/hybrid)
    退出前在 ctx 上设置 response, visible_response (通过 ctx.response 持有完整),
    并把 updates 写到 ctx (留 phase 5 用)。
    """
    state = ctx.state
    api_user = ctx.api_user
    message_for_model = ctx.message_for_model
    stop_event = ctx.stop_event
    run_id = ctx.run_id
    gm = ctx.gm
    bundle = ctx.bundle
    agent_result = ctx.agent_result

    # Phase D: 注入规范层常驻骨架(治 1935)+ 规范世界线软目标。
    # 加固:任何失败都不影响既有 gameplay(纯增量 prepend)。KB 无 constant 条目时为空。
    try:
        _save_id_pd = ctx.early_active_save_id or 0
        _uid_pd = int(api_user.get("id")) if api_user else 0
        if _save_id_pd and _uid_pd:
            from gm_serving.serve import assemble_gm_context
            from platform_app.db import connect as _connect_pd
            with _connect_pd() as _db_pd:
                _pd = assemble_gm_context(
                    _db_pd, save_id=_save_id_pd, user_id=_uid_pd,
                    user_input=message_for_model or "",
                )
            _inj = (_pd or {}).get("injection_text") or ""
            if _inj and _inj not in (bundle.get("prompt") or ""):
                bundle["prompt"] = _inj + "\n\n" + (bundle.get("prompt") or "")
                bundle.setdefault("debug", {})["phase_d_injection"] = {
                    "tokens": _pd.get("tokens"), "budget": _pd.get("budget"),
                    "steering_next": (_pd.get("steering") or {}).get("next_node"),
                    "impact": _pd.get("impact"),
                }
    except Exception as _pd_err:
        log.warning(f"[chat] Phase D 注入跳过(不影响 gameplay): {_pd_err}")

    # 反馈 #28(确定性修复):玩家本回合输入很短时,GM 容易把叙事全用来扩写/复述玩家
    # 自己的动作,而玩家其实想看「对方 NPC 的反应」。这里在【代码侧】确定性判定短输入
    # (而非指望模型自己识别),命中就前置一条最高优先级元指令,把镜头钉在对方/世界的反应上。
    # 标成「元指令·静默遵守不得复述」契合 master.py 绝不复述铁律,不会被回显给玩家。
    try:
        if _should_inject_short_input_directive(message_for_model):
            if _SHORT_INPUT_DIRECTIVE not in (bundle.get("prompt") or ""):
                bundle["prompt"] = _SHORT_INPUT_DIRECTIVE + "\n\n" + (bundle.get("prompt") or "")
                bundle.setdefault("debug", {})["short_input_directive"] = {
                    "len": len((message_for_model or "").strip())
                }
    except Exception as _si_err:
        log.warning(f"[chat] 短输入镜头指令注入跳过(不影响 gameplay): {_si_err}")

    yield ("agent", {
        "phase": "main_gm",
        "message": "主 GM 正在读取上下文并生成正文。",
        "status": "running",
        "elapsed_ms": 0,
    })

    # MCP tools
    mcp_tools: list[dict[str, Any]] = []
    try:
        import mcp_broker
        mcp_tools = mcp_broker.discover_all_tools() or []
    except Exception:
        mcp_tools = []

    # task 87 Phase 5: 把 dispatcher 工具表 (按 origin=llm_chat 过滤) 注入 GM,
    # 并构造 unified tool router 统一路由到 dispatcher / mcp_broker。
    unified_tools = mcp_tools
    gm_tool_router = None
    try:
        import secrets as _secrets

        from tools_dsl.chat_tool_router import build_tool_call_router, build_unified_tool_list
        unified_tools = build_unified_tool_list(mcp_tools, origin="llm_chat")
        _gm_trace_id = f"gm-{_secrets.token_urlsafe(6)}"
        gm_tool_router = build_tool_call_router(
            user_id=int(api_user.get("id")) if api_user else 0,
            save_id=ctx.early_active_save_id or 0,
            script_id=active_script_id(api_user),
            trace_id=_gm_trace_id,
            state_provider=lambda env, _state=state: _state,
        )
    except Exception as _router_err:
        log.warning(f"[chat] unified tool router 构造失败,GM 仅用 MCP 工具: {_router_err}")

    response = ""
    # task 135: max_iterations 是【单轮】上限 (本轮 user 消息内的工具调用次数),
    # for-loop 每次新 chat 都重新计 0,不跨轮累计。
    # 原本 3 太紧 — GM 一轮里常需要:
    #   update_state -> list_pending_anchors -> set_pending_question -> 写正文
    # 现在世界线收束 (task 136) 还会再叠 mark_anchor_satisfied / record_anchor_variant,
    # 8 是平衡值: 够 GM 串完整轮工具流, 又不至于死循环烧 token。

    # P0-2: respond_stream_with_tools 是同步 generator,通过 _bridge_sync_generator_to_async 桥接。
    # stop_event 透传给 GM:客户端断开时 bridge.finally 设置 event,GM stream 循环检查后早退。
    import threading as _threading
    _gm_stop = _threading.Event()
    try:
        _max_tokens = int(chat_max_tokens(api_user)) if chat_max_tokens else 800
    except Exception as _mt_err:
        log.warning(f"[chat] max_tokens preference skipped: {_mt_err}")
        _max_tokens = 800

    async for event in _bridge_sync_generator_to_async(
        lambda: gm.respond_stream_with_tools(
            message_for_model, bundle["prompt"], state,
            tools=unified_tools, max_iterations=_gm_max_iters(),
            max_tokens=_max_tokens,
            tool_call_router=gm_tool_router,
            stop_event=_gm_stop,
        ),
        stop_event=_gm_stop,
    ):
        if stop_event.is_set() or run_id != current_run_id_fn(api_user) or is_stop_requested_global(api_user, run_id):
            if response.strip():
                response += "\n\n【本轮已被玩家打断】"
                persist_chat_turn(
                    api_user, state, message_for_model, response,
                    persist_user_id=ctx.persist_user_id,
                    active_save_id=ctx.active_save_id,
                    interrupted=True,
                )
            mark_context_run(
                ctx.context_run_id, "stopped",
                duration_ms=int((time.time() - ctx.chat_start_time) * 1000),
            )
            yield ("done", {"status": payload_fn(api_user), "interrupted": True})
            ctx.response = response
            ctx.early_return = True
            return
        etype = event.get("type")
        if etype == "text":
            chunk = event.get("text", "")
            # task 113 防御: Gemini 3.5 Flash 偶发把 tools schema 当 text echo —
            # 一旦看到 "default_api:dispatcher__" / 工具 JSON 特征 → 立即放弃本轮
            # 输出 + 抛 error, 不写回 history 避免污染存档。
            _accumulated_probe = response + chunk
            if "default_api:dispatcher__" in _accumulated_probe and \
               '"name":' in _accumulated_probe and '"description":' in _accumulated_probe:
                yield ("agent", {
                    "phase": "gm_schema_echo_detected",
                    "message": "GM 输出包含工具 schema dump (LLM 故障), 已截停本轮; 请重试。",
                    "status": "error",
                    "elapsed_ms": 0,
                })
                yield ("token", {"text": "\n\n[助手输出异常,本轮已截停。请重试或换个说法。]"})
                response = ""  # 清空避免被 persist 写入 history
                ctx.response = ""
                ctx.early_return = True
                return
            response += chunk
            yield ("token", {"text": chunk})
        elif etype == "reasoning":
            # #7 reasoning 流式: 思考过程单独走 reasoning 事件 — 不进 token(叙事)、
            # 不累加进 response、不写 history。前端用它显示思考流并重置 idle 计时。
            yield ("reasoning", {"text": event.get("text", "")})
        elif etype == "tool_call":
            yield ("tool_call", {
                "server_id": event.get("server_id", ""),
                "tool": event.get("tool", ""),
                "arguments": event.get("arguments", {}),
            })
        elif etype == "tool_result":
            yield ("tool_result", {
                "ok": event.get("ok", False),
                "result": event.get("result"),
                "error": event.get("error"),
            })
        elif etype == "tool_error":
            yield ("tool_error", {
                "error": event.get("error", ""),
                "raw": event.get("raw", ""),
            })
        await asyncio.sleep(0)

    ctx.response = response

    # ── W1 容量优化: fire-and-forget 模式 ──────────────────────────────────
    # async 模式(默认): GM 流完后立刻入队 Phase 4 任务,不等 LLM 后处理,
    # 直接 return。主 worker async slot 在此释放。容量 25 → ~55 并发回合。
    # sync 模式: 保留旧行为(后处理阻塞主路径, 供测试/debug 用)。
    if _POSTPROC_MODE != "sync":
        _is_bs = (is_black_swan_enabled(api_user) if is_black_swan_enabled is not None else False)
        try:
            from platform_app.db import connect as _pp_connect
            from platform_app.postproc_queue import enqueue_postproc as _enqueue
            _sub_gm_ref = getattr(ctx, "sub_gm", None)
            _pp_api_id = getattr(_sub_gm_ref, "api_id", None) if _sub_gm_ref else None
            _pp_backend = getattr(_sub_gm_ref, "_backend", None) if _sub_gm_ref else None
            _pp_model = getattr(_pp_backend, "model_name", None) if _pp_backend else None
            _curator_plan = (ctx.agent_result or {}).get("curator_plan", {}) or {}
            with _pp_connect() as _pp_db:
                _enqueued = _enqueue(
                    _pp_db,
                    user_id=ctx.persist_user_id or (int(api_user["id"]) if api_user else 0),
                    save_id=ctx.active_save_id or ctx.early_active_save_id or 0,
                    commit_id=None,
                    player_input=ctx.message_for_model,
                    gm_output=response,
                    api_user=api_user,
                    is_bs_enabled=_is_bs,
                    script_id=active_script_id(api_user),
                    api_id_override=_pp_api_id,
                    model_override=_pp_model,
                    curator_plan=_curator_plan,
                )
            log.info("[chat] fire-and-forget: enqueued %d postproc tasks", _enqueued)
        except Exception as _enq_err:
            log.warning("[chat] postproc enqueue failed (falling back to sync): %s", _enq_err)
            # enqueue 失败时降级到同步后处理,避免彻底丢失 extractor 等
            _POSTPROC_FALLBACK = True
        else:
            _POSTPROC_FALLBACK = False

        if not _POSTPROC_FALLBACK:
            # ── async 模式:确定性后处理必须仍在主进程内联跑,不能随早退一起跳过 ──
            # 早退只该省掉"费时 + 不依赖实时内存 state 的 LLM 任务"(acceptance verifier /
            # black_swan,上面已 enqueue 给独立 worker)。但下面三项是确定性、<50ms、且必须
            # 改写【实时内存 state】 —— worker 进程拿不到内存 state(payload state_data={} 是
            # no-op),一旦随早退跳过就永久丢失:
            #   1. apply_structured_updates —— GM 经 JSON op 写的 location/time/resources/
            #      main_quest/relationships/选项/推测(GM 写每轮核心状态的主通道)
            #   2. timeline_guard regex —— 时间跳跃禁词检测 + audit
            #   3. cliche regex —— 套路比喻检测 notice
            # 故此处内联补跑。相对 sync 路径的唯一退化:extractor(LLM 二次抽取,本就在
            # worker 内 no-op)与 acceptance retry 重写(依赖内存 state + GM 实例)不在 async
            # 跑 —— extractor 直接跳过(GM 自带 JSON op 已 apply),acceptance 退化为仅 worker
            # 内审计、不 retry(下面 log 标注)。
            log.info("[chat] async postproc: 内联跑确定性后处理(apply/guard),LLM 任务已入队;"
                     "acceptance retry 退化为不重写(仅 worker 审计)")
            try:
                from agents.timeline_narrative_guard import (
                    detect_time_jump_violations,
                    record_violations_to_audit,
                )
                _tj_violations = await asyncio.to_thread(
                    detect_time_jump_violations, response, state,
                )
                if _tj_violations:
                    await asyncio.to_thread(record_violations_to_audit, state, _tj_violations)
                    yield ("agent", {
                        "phase": "timeline_guard",
                        "message": f"GM 时间跳跃叙事检测到 {len(_tj_violations)} 处禁词(穿越/醒来/拨回 等过渡叙事)",
                        "status": "warning",
                        "elapsed_ms": 0,
                        "violations": [
                            {"label": v.get("pattern_label"), "match": v.get("match")}
                            for v in _tj_violations
                        ],
                    })
            except Exception as _tg_err:
                log.warning(f"[chat] async timeline_guard 跳过: {_tg_err}")

            try:
                from agents.timeline_narrative_guard import detect_cliche_violations
                _cliche = detect_cliche_violations(response)
            except Exception:
                _cliche = []
            if _cliche:
                yield ("cliche_notice", {
                    "phrases": [v.get("match") for v in _cliche][:5],
                    "labels": sorted({v.get("pattern_label") for v in _cliche}),
                })

            # 关键修复:GM JSON op 确定性写回(async 不跑 extractor → extractor_active=False
            # → apply_structured_updates 保留 regex 兜底,把 GM 漏标的也尽量捞回)。
            try:
                ctx._updates = _apply_gm_json_ops(
                    state=state,
                    response_with_ops=response,
                    api_user=api_user,
                    active_script_id=active_script_id,
                    ctx=ctx,
                    extractor_active=False,
                )
            except Exception as _apply_err:
                log.warning(f"[chat] async apply_structured_updates 失败,退回 directive_updates: {_apply_err}")
                ctx._updates = ctx.directive_updates[:]
            return
    # ── 同步后处理路径 (sync 模式 or enqueue 失败降级) ─────────────────────

    # 并行执行 GM 后处理三项(timeline_guard / black_swan / extractor):
    # - 均只读 response + state,互相无依赖
    # - timeline_guard 同步 regex(<50ms)
    # - black_swan 异步 LLM(3-8s,可选)
    # - extractor 异步 LLM(2-5s)
    # - asyncio.gather + to_thread 让总延迟 = max(三者) ≈ 减一次 LLM RTT
    # - 等齐后按固定顺序 yield SSE step,保前端 UI 时间线稳定
    _post_results = await _run_post_gm_parallel(
        response=response, state=state, api_user=api_user, ctx=ctx,
        active_script_id=active_script_id,
        is_extractor_enabled=is_extractor_enabled,
        is_black_swan_enabled=is_black_swan_enabled,
    )

    # 按固定顺序 yield 三组 SSE step(保前端时间线稳定)
    _tj_violations = _post_results.get("timeline_violations") or []
    if _tj_violations:
        yield ("agent", {
            "phase": "timeline_guard",
            "message": f"GM 时间跳跃叙事检测到 {len(_tj_violations)} 处禁词(穿越/醒来/拨回 等过渡叙事)",
            "status": "warning",
            "elapsed_ms": 0,
            "violations": [
                {"label": v.get("pattern_label"), "match": v.get("match")}
                for v in _tj_violations
            ],
        })

    # 反馈 #22: 套路比喻检测(每回合,通用,精准只命中比喻句式不碰投石机等字面词)。
    # harness: 确定性检测 + surface(前端 ConfirmStrip notice + 重生成),绝不 strip。
    try:
        from agents.timeline_narrative_guard import detect_cliche_violations
        _cliche = detect_cliche_violations(response)
    except Exception:
        _cliche = []
    if _cliche:
        yield ("cliche_notice", {
            "phrases": [v.get("match") for v in _cliche][:5],
            "labels": sorted({v.get("pattern_label") for v in _cliche}),
        })

    response_with_ops = _post_results.get("response_with_ops") or response
    extractor_active = bool(_post_results.get("extractor_active"))

    # task 87 Phase 6: 经 ChatWriteContext 把 GM JSON op 确定性 apply 回内存 state
    # (apply_state_write_typed 拿到 user/save/trace → dispatcher 工具调用)。
    # 与 async 早退路径共用 _apply_gm_json_ops,避免两处逻辑漂移。
    updates = _apply_gm_json_ops(
        state=state,
        response_with_ops=response_with_ops,
        api_user=api_user,
        active_script_id=active_script_id,
        ctx=ctx,
        extractor_active=extractor_active,
    )

    # task 81 / 84 / iter#3: acceptance 自动验证 + retry once (硬 gate 化)
    #
    # 之前:unmet 只写 audit_log 发个 warning event,GM 违规直接进 history 污染后续。
    # 现在:unmet 时同步再跑一次 GM (附"上一稿哪几条没满足,请重写"提示),拿第二稿
    # 重新 apply_structured_updates。最多 retry 1 次(防死循环 + 控成本)。
    # 客户端已经看到第一稿流式 token,服务端 state 用第二稿 — 设计上接受 UX 略不
    # 一致,因为 acceptance 是规则严格性的最后一道门,优先级高于 streaming 平滑。
    # 关:RPG_ACCEPTANCE_RETRY=0
    import os as _os
    _retry_enabled = _os.environ.get("RPG_ACCEPTANCE_RETRY", "1") not in ("0", "false", "False", "")
    try:
        _curator_plan_for_check = (agent_result or {}).get("curator_plan", {}) or {}
        _acceptance = _curator_plan_for_check.get("acceptance") or []
        if _acceptance and response.strip():
            _acc_mode = acceptance_verifier_mode(api_user)
            _acc_user_id = int(api_user.get("id")) if api_user and api_user.get("id") else None
            unmet = verify_acceptance(
                _acceptance, response, updates,
                mode=_acc_mode, user_id=_acc_user_id,
            )
            retry_used = False
            if unmet and _retry_enabled:
                retry_used = True
                yield ("agent", {
                    "phase": "acceptance_retry",
                    "message": f"acceptance 有 {len(unmet)} 条未通过,触发 retry once 补写",
                    "status": "running", "elapsed_ms": 0,
                    "unmet": unmet[:5],
                })
                # 构造 retry user message — 把 unmet 当成"用户的修订指令"
                _retry_msg = (
                    "【系统:本轮 acceptance 自检】上一稿正文没有覆盖到以下验收点:\n"
                    + "\n".join(f"  - {x}" for x in unmet[:5])
                    + "\n请在保持原叙事走向不变的前提下,重写本轮回应,确保覆盖每一条验收点。"
                    "如果某条确实不该满足(玩家行动本就不触发),也要在 JSON op 注明原因。"
                )
                try:
                    _retry_parts: list[str] = []
                    _retry_state_iter = gm.respond_stream_with_tools(
                        _retry_msg, bundle["prompt"], state,
                        tools=unified_tools, max_iterations=max(4, _gm_max_iters() // 2), max_tokens=_max_tokens,
                        tool_call_router=gm_tool_router,
                    )
                    for _ev in _retry_state_iter:
                        if isinstance(_ev, dict) and _ev.get("type") == "text":
                            _retry_parts.append(_ev.get("text", ""))
                    _retry_response = "".join(_retry_parts).strip()
                    if _retry_response:
                        # 第二稿覆盖第一稿 — 重新 apply_structured_updates
                        import secrets as _ctx_secrets

                        from state_write_context import (
                            ChatWriteContext,
                        )
                        from state_write_context import (
                            clear_context as _clear_write_ctx,
                        )
                        from state_write_context import (
                            set_context as _set_write_ctx,
                        )
                        _retry_ctx = ChatWriteContext(
                            user_id=int(api_user.get("id")) if api_user else 0,
                            save_id=ctx.early_active_save_id or 0,
                            script_id=active_script_id(api_user),
                            trace_id=f"gm-jsop-retry-{_ctx_secrets.token_urlsafe(6)}",
                            origin="llm_chat_json_op",
                        )
                        _retry_token = _set_write_ctx(_retry_ctx)
                        try:
                            retry_updates = state.apply_structured_updates(
                                _retry_response, skip_regex_fallback=extractor_active,
                            )
                        finally:
                            _clear_write_ctx(_retry_token)
                        # 用第二稿替换主 response / updates
                        response = _retry_response
                        updates = list(updates) + ["[acceptance_retry]"] + list(retry_updates or [])
                        ctx.response = response
                        ctx._updates = updates
                        # 重新校验
                        unmet_after = verify_acceptance(
                            _acceptance, response, updates,
                            mode=_acc_mode, user_id=_acc_user_id,
                        )
                        yield ("agent", {
                            "phase": "acceptance_retry",
                            "message": f"retry 完成 — 第二稿剩余 unmet {len(unmet_after)} 条",
                            "status": "done", "elapsed_ms": 0,
                            "unmet_after": unmet_after[:5],
                        })
                        unmet = unmet_after  # 落到下面 audit_log 的也是第二轮残余
                except Exception as _retry_err:
                    log.warning(f"[acceptance] retry once failed: {_retry_err}")
                    yield ("agent", {
                        "phase": "acceptance_retry",
                        "message": f"retry 跑挂(降级到只记 audit): {_retry_err}",
                        "status": "warning", "elapsed_ms": 0,
                    })
            if unmet:
                from datetime import datetime as _dt
                audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                for item in unmet[:5]:
                    audit.append({
                        "ts": _dt.now().isoformat(timespec="seconds"),
                        "kind": "acceptance_unmet",
                        "source": "curator:acceptance",
                        "retry_used": retry_used,
                        "hint": f"未通过验收：{item[:160]}",
                        "turn": state.data.get("turn", 0),
                    })
                if len(audit) > 200:
                    state.data["permissions"]["audit_log"] = audit[-200:]
                yield ("agent", {
                    "phase": "acceptance_check",
                    "message": (
                        f"本轮 GM 输出有 {len(unmet)} 条 acceptance 未通过"
                        + ("(retry 后仍存在,已记 audit_log)" if retry_used else "(retry 关闭,已记 audit_log)")
                    ),
                    "status": "warning",
                    "elapsed_ms": 0,
                    "unmet": unmet[:5],
                })
    except Exception as _acc_exc:
        log.warning(f"[acceptance] check failed: {_acc_exc}")

    # 把 updates 写到 ctx 留给 phase 5
    ctx.response = response
    # 用 ctx.__dict__ 也行,这里直接挂属性
    ctx._updates = updates


# ---------------------------------------------------------------------------
# Phase 5: 持久化 record_turn + save + DB + done
# ---------------------------------------------------------------------------


async def _bridge_sync_generator_to_async(
    gen_factory: Callable[[], Any],
    *args: Any,
    stop_event=None,
    **kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """把同步 generator 桥接成 async iterator,中途 LLM 调用不阻塞 event loop。

    gen_factory: 无参 callable 返回 sync generator。
                 若有额外位置/关键字参数,透传给 gen_factory(*args, **kwargs)。
                 推荐用 lambda 包装好后不传 args/kwargs。
    stop_event:  threading.Event;SSE 断开时由 bridge finally 设置,
                 让 sync generator 内部循环提前 break。未传时内部新建。

    实现:
    1. 在 ThreadPool 里跑 sync generator
    2. thread 内每 yield 一个 item,用 loop.call_soon_threadsafe 投到 asyncio.Queue
    3. async 端 await queue.get() 拿 item;SENTINEL 表示 generator 结束
    4. thread 异常通过 _Error wrapper 传回 async 端再抛
    5. finally 设置 stop_event,通知 sync 端早退

    用于 context_agent.run_context_agent 这种同步 generator + 内部阻塞调用
    (curator LLM 调用通过 ThreadPoolExecutor 等结果),让 chat_pipeline 的
    event loop 在 LLM 等待期间仍可调度其它协程。
    """
    import threading as _threading
    if stop_event is None:
        stop_event = _threading.Event()
    loop = asyncio.get_running_loop()
    aqueue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    class _Error:
        __slots__ = ("exc",)
        def __init__(self, exc: BaseException) -> None:
            self.exc = exc

    def _run_in_thread() -> None:
        try:
            for item in gen_factory(*args, **kwargs):
                if stop_event.is_set():
                    break
                loop.call_soon_threadsafe(aqueue.put_nowait, item)
        except BaseException as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(aqueue.put_nowait, _Error(exc))
        finally:
            loop.call_soon_threadsafe(aqueue.put_nowait, SENTINEL)

    # 用 asyncio.to_thread 跑 wrapper,task 在 generator 结束/异常后自然完成
    runner = asyncio.create_task(asyncio.to_thread(_run_in_thread))
    try:
        while True:
            item = await aqueue.get()
            if item is SENTINEL:
                break
            if isinstance(item, _Error):
                raise item.exc
            yield item
    finally:
        # SSE 断开 / 异常 / 正常完成:通知 sync 端早退
        stop_event.set()
        try:
            await runner
        except Exception:
            pass


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
    """并行跑 GM 后处理三项,返回 {timeline_violations, response_with_ops, extractor_active}。

    三项均只读 GM 完整 response + state(不修改),所以 asyncio.gather 安全。
    State mutation(audit_log append)在 worker 内部完成,但每个 worker 写不同
    audit kind,无冲突;Python GIL 保护单条 append 原子性。

    任何 worker 抛异常 → log + 返回该 worker 的中性值,不影响其它 worker。
    """
    if not response.strip():
        return {"timeline_violations": [], "response_with_ops": response, "extractor_active": False}

    user_id_int = int(api_user.get("id")) if api_user else None

    async def _worker_timeline_guard() -> list[dict[str, Any]]:
        try:
            from agents.timeline_narrative_guard import (
                detect_time_jump_violations,
                record_violations_to_audit,
            )
            violations = await asyncio.to_thread(detect_time_jump_violations, response, state)
            if violations:
                await asyncio.to_thread(record_violations_to_audit, state, violations)
            return violations
        except Exception as exc:
            log.warning(f"[chat] timeline_narrative_guard 检测失败: {exc}")
            return []

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

    # 并行执行,gather return_exceptions=False 但每个 worker 内部已 try/except,不会抛
    tg_result, _swan_unused, ex_result = await asyncio.gather(
        _worker_timeline_guard(),
        _worker_black_swan(),
        _worker_extractor(),
    )
    extractor_active, response_with_ops = ex_result
    return {
        "timeline_violations": tg_result,
        "response_with_ops": response_with_ops,
        "extractor_active": extractor_active,
    }


async def persist_turn_phase(
    ctx: PipelineContext,
    *,
    payload_fn: Callable[[dict[str, Any] | None], dict[str, Any]],
    persist_chat_turn: Callable[..., None],
    build_usage_payload: Callable[..., dict[str, Any] | None],
) -> AsyncIterator[SSEEvent]:
    """Phase 5: 落档 (chat turn / runtime turn / DB messages) + 发 usage / updates / done。"""
    state = ctx.state
    api_user = ctx.api_user
    message_for_model = ctx.message_for_model
    response = ctx.response
    bundle = ctx.bundle
    gm = ctx.gm
    updates = getattr(ctx, "_updates", []) or []

    visible_response = strip_json_state_ops(response)
    # 确定性兜底:剥掉 GM 在 native tool_use 前泄漏进正文的英文"工具预告"元叙述
    # (例:"Let me mark the anchors that have been satisfied...")。不依赖 GM 听提示词。
    visible_response = strip_meta_tool_preamble(visible_response)

    # harness 强约束(用户): GM 的提问/选项**必须结构化,绝不留在正文**。
    # 这是确定性兜底 —— 不依赖 GM 听话:把正文尾部的问题/选项块抽进 pending_questions,
    # 并从正文剥掉。本轮已有 GM 结构化 question op 时只清正文去重,不重复 push。
    try:
        from state.parsers import extract_trailing_choice
        _body, _q, _opts = extract_trailing_choice(visible_response)
        if len(_opts) >= 2:
            _perm = state.data.get("permissions") or {}
            _cur_turn = state.data.get("turn", 0)
            _has_gm_q = any(
                q.get("turn") == _cur_turn and str(q.get("source", "")).startswith("gm")
                for q in (_perm.get("pending_questions") or [])
            )
            if not _has_gm_q:
                state.add_pending_question(_q or "你接下来想怎么做?", source="gm:prose", options=_opts)
            visible_response = _body
    except Exception:
        pass

    # 沉浸感确定性兜底(用户头号反馈):剥掉结尾"旁白向玩家显式提问下一步"的句子
    # ——只命中明确的决策反问(你接下来想怎么做 / 你打算如何应对 / 请玩家决定 等),
    # 且必须是旁白行(不在引号内,绝不动角色台词)。不依赖 GM 听提示词。
    try:
        import re as _re_imm
        _q_pat = _re_imm.compile(
            r"(你|您)[^。！？\n]{0,16}(接下来|下一步|打算|准备|会|想|要不要|是否|如何|怎么)"
            r"[^。\n]{0,18}(做|办|应对|行动|选择|决定|应付)?[?？]\s*$"
        )
        _plead_pat = _re_imm.compile(r"(请|轮到|该)\s*(你|玩家)[^。\n]{0,10}(决定|选择|定夺|行动|出招)")
        _quote_chars = ("「", "」", "“", "”", "‘", "’", "\"", "『", "』")
        _ll = visible_response.rstrip().split("\n")
        _changed = False
        while _ll:
            _last = _ll[-1].strip()
            if not _last:
                _ll.pop()
                continue
            _in_quote = any(c in _last for c in _quote_chars)
            if (not _in_quote) and (_q_pat.search(_last) or _plead_pat.search(_last)) and len(_last) <= 60:
                _ll.pop()
                _changed = True
                continue
            break
        if _changed:
            _new = "\n".join(_ll).rstrip()
            if _new:  # 不要把整段删空(防极端情况)
                visible_response = _new
    except Exception:
        pass

    # task 128: GM 返回空时不写 history (避免出现"GM 主代理"标题但内容空的诡异消息),
    # 改为 yield error 让用户清楚知道并能重试。常见原因:
    #   · LLM 触发 safety filter (Gemini 对暴力/儿童虐待场景敏感)
    #   · backend stream 提前 EOF / 超时
    #   · 工具循环耗尽但没产出 text block
    # task 31/27: /set 命令已在 Phase 1 持久化 (directive_updates 非空),
    # 此时 GM 返空是正常的 — 不应 error，直接 done。
    if not visible_response.strip():
        if ctx.directive_updates:
            # /set 已落盘，GM 空响应无需报错
            yield ("done", {"status": payload_fn(api_user), "interrupted": False, "empty": True})
        else:
            log.warning(f"[chat] WARN: GM 返回空响应, len(raw)={len(response)} "
                        f"user_msg='{message_for_model[:80]}', save_id={ctx.active_save_id}")
            yield ("error", {
                "message": "GM 没生成内容(可能触发了模型的安全过滤,或者上下文出错)。请尝试换个说法重新发送。",
                "kind": "empty_response",
            })
            yield ("done", {"status": payload_fn(api_user), "interrupted": False, "empty": True})
        return
    persist_chat_turn(
        api_user, state, message_for_model, visible_response,
        persist_user_id=ctx.persist_user_id, active_save_id=ctx.active_save_id,
    )
    usage_payload = build_usage_payload(
        api_user, gm, bundle, message_for_model,
        ctx.persist_user_id, ctx.active_save_id, ctx.context_run_id,
    )
    if usage_payload:
        yield ("usage", usage_payload)
    yield ("updates", {"items": updates})
    yield ("done", {"status": payload_fn(api_user), "interrupted": False, "usage": usage_payload})
