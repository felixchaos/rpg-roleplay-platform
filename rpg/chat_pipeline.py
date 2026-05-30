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
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from agents.context_agent import run_context_agent
from core.logging import get_logger
from state import GameState, strip_json_state_ops

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Pipeline context: 在 phase 之间传递的可变状态
# ---------------------------------------------------------------------------


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
    # task 87: 提前解析 persist target,让 dispatcher 拿到 save_id 做作用域校验。
    _early_persist_user_id, _early_active_save_id = resolve_persist_target(api_user)
    ctx.early_persist_user_id = _early_persist_user_id
    ctx.early_active_save_id = _early_active_save_id

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
    for item in _rca(
        state, message_for_model,
        stop_requested=stop_event.is_set,
        user_id=api_user["id"] if api_user else None,
        script_id=active_script_id(api_user),
        api_id_override=_sub_api,
        model_override=_sub_model,
    ):
        if item["type"] == "step":
            yield ("agent", item["step"])
            await asyncio.sleep(0)
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

    yield ("retrieval", {"text": ctx_text})
    yield ("context", {"debug": bundle["debug"]})
    yield ("status", payload_fn(api_user))

    # (d) clarify 短路
    _curator_plan = agent_result.get("curator_plan", {}) or {}
    _confidence = float(_curator_plan.get("confidence") or 1.0)
    _clarify = (_curator_plan.get("clarifying_question") or "").strip()
    _confidence_threshold = clarify_threshold(api_user)
    _route_to_clarify = bool(_clarify) or _confidence < _confidence_threshold
    if _route_to_clarify and _clarify:
        try:
            state.add_pending_question(_clarify, source="curator:clarify")
        except Exception:
            pass
        try:
            from datetime import datetime as _dt
            audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
            audit.append({
                "ts": _dt.now().isoformat(timespec="seconds"),
                "kind": "clarify_yield",
                "source": "curator",
                "hint": f"confidence={_confidence:.2f}；curator 主动询问：{_clarify[:160]}",
                "turn": state.data.get("turn", 0),
            })
            if len(audit) > 200:
                state.data["permissions"]["audit_log"] = audit[-200:]
        except Exception:
            pass
        _q_text = f"【需要先确认】{_clarify}"
        yield ("token", {"text": _q_text})
        try:
            persist_chat_turn(
                api_user, state, message_for_model, _q_text,
                persist_user_id=persist_user_id, active_save_id=active_save_id,
            )
        except Exception:
            pass
        mark_context_run(
            context_run_id, "done",
            duration_ms=int((time.time() - ctx.chat_start_time) * 1000),
        )
        yield ("status", payload_fn(api_user))
        yield ("done", {"status": payload_fn(api_user), "interrupted": False, "clarify": True})
        ctx.early_return = True
        return


# ---------------------------------------------------------------------------
# Phase 4: GM 主响应 (流式 token + tool_call + 后处理 extractor / acceptance)
# ---------------------------------------------------------------------------


async def run_gm_phase(
    ctx: PipelineContext,
    *,
    payload_fn: Callable[[dict[str, Any] | None], dict[str, Any]],
    persist_chat_turn: Callable[..., None],
    mark_context_run: Callable[..., None],
    current_run_id_fn: Callable[[dict[str, Any] | None], int],
    is_stop_requested_global: Callable[[dict[str, Any] | None, int], bool],
    is_extractor_enabled: Callable[[dict[str, Any] | None], bool],
    acceptance_verifier_mode: Callable[[dict[str, Any] | None], str],
    verify_acceptance: Callable[..., list[str]],
    active_script_id: Callable[[dict[str, Any] | None], int | None],
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
    for event in gm.respond_stream_with_tools(
        message_for_model, bundle["prompt"], state,
        tools=unified_tools, max_iterations=8,
        tool_call_router=gm_tool_router,
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

    # 时间线 user_set 跳跃叙事检测
    try:
        from agents.timeline_narrative_guard import (
            detect_time_jump_violations,
            record_violations_to_audit,
        )
        if response.strip():
            _tj_violations = detect_time_jump_violations(response, state)
            if _tj_violations:
                record_violations_to_audit(state, _tj_violations)
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
    except Exception as _tj_err:
        log.warning(f"[chat] timeline_narrative_guard 检测失败: {_tj_err}")

    # sprint 5: 黑天鹅子代理 post-GM hook (默认关闭,RPG_ENABLE_BLACK_SWAN=1 开启)
    try:
        from core.config import enable_black_swan as _enable_black_swan
        if _enable_black_swan():
            from agents.black_swan_agent import maybe_trigger as _maybe_trigger
            # task: harness 适配 — black_swan_agent 自带 _harness 通道,
            # 沿用 sub_gm 的 api_id/model(便宜模型),不再需要 caller 注入 llm_caller。
            _sub_gm = getattr(ctx, "sub_gm", None)
            _swan_api = getattr(_sub_gm, "api_id", None) if _sub_gm else None
            _swan_backend = getattr(_sub_gm, "_backend", None) if _sub_gm else None
            _swan_model = getattr(_swan_backend, "model_name", None) if _swan_backend else None
            _swan_result = _maybe_trigger(
                state,
                user_id=int(api_user.get("id")) if api_user else 0,
                save_id=ctx.early_active_save_id or 0,
                script_id=active_script_id(api_user),
                api_id_override=_swan_api,
                model_override=_swan_model,
                enable_llm=bool(api_user),  # 匿名 user 不调外部 LLM
            )
            if _swan_result.get("triggered"):
                from datetime import datetime as _dt
                _audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                _audit.append({
                    "ts": _dt.now().isoformat(timespec="seconds"),
                    "kind": "black_swan_triggered",
                    "source": "black_swan_agent",
                    "hint": (_swan_result.get("proposal") or {}).get("summary", "")[:200],
                    "turn": state.data.get("turn", 0),
                })
                if len(_audit) > 200:
                    state.data["permissions"]["audit_log"] = _audit[-200:]
    except Exception as _swan_err:
        log.warning(f"[black_swan] failed silently: {_swan_err}")

    # task 62 / 65 / 69: extractor 第二步
    extractor_active = False
    try:
        if is_extractor_enabled(api_user) and response.strip():
            extractor_active = True
            from agents import extractor as _extractor
            extractor_ops = _extractor.extract_state_ops(
                narrative_text=response,
                state_data=state.data,
                user_id=int(api_user.get("id")) if api_user else None,
                timeout_sec=15,
            )
            if extractor_ops:
                response_with_ops = response + "\n\n```json\n" + json.dumps(extractor_ops, ensure_ascii=False) + "\n```"
            else:
                response_with_ops = response
        else:
            response_with_ops = response
    except Exception as exc:
        log.warning(f"[chat] extractor pipeline failed: {exc}; falling back to single-step")
        try:
            from datetime import datetime as _dt
            audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
            audit.append({
                "ts": _dt.now().isoformat(timespec="seconds"),
                "kind": "extractor_error",
                "source": "extractor",
                "hint": f"GM 第二步失败：{type(exc).__name__}: {str(exc)[:200]}",
                "turn": state.data.get("turn", 0),
            })
            if len(audit) > 200:
                state.data["permissions"]["audit_log"] = audit[-200:]
        except Exception:
            pass
        response_with_ops = response

    # task 87 Phase 6: 设置 chat write context,让 state.apply_state_write_typed 拿到
    # user/save/trace,把 GM JSON op 直调 apply_state_write 路径转 dispatcher 工具调用。
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
        # task 69：extractor 开启时让 state.py 跳过 regex 兜底
        updates = ctx.directive_updates + state.apply_structured_updates(
            response_with_ops, skip_regex_fallback=extractor_active,
        )
    finally:
        _clear_write_ctx(_ctx_token)

    # task 81 / 84: acceptance 自动验证
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
            if unmet:
                from datetime import datetime as _dt
                audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                for item in unmet[:5]:
                    audit.append({
                        "ts": _dt.now().isoformat(timespec="seconds"),
                        "kind": "acceptance_unmet",
                        "source": "curator:acceptance",
                        "hint": f"未通过验收：{item[:160]}",
                        "turn": state.data.get("turn", 0),
                    })
                if len(audit) > 200:
                    state.data["permissions"]["audit_log"] = audit[-200:]
                yield ("agent", {
                    "phase": "acceptance_check",
                    "message": f"本轮 GM 输出有 {len(unmet)} 条 acceptance 未通过；已记 audit_log",
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
