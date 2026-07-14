"""Phase 3:5E rules preflight(GamePolicy.preflight + combat gate)+ rule candidates + context_run。
拆包自 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from state import GameState

from ._common import PipelineContext, SSEEvent, _sync_active_entities_from_bundle, log


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
    # 作上下文;主 GM 照常出场推进剧情,回合末用结构化 question op 给出动作选项
    # (finalize 阶段确定性兜底会剥掉漏进正文的"问玩家下一步"句子;选项本身依赖 GM 走 question op)。
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
