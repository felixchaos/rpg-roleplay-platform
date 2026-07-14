"""Phase 2:context agent(子 GM curator)+ clarifying-question 短路。
拆包自 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from agents.context_agent import run_context_agent
from state import GameState

from ._common import PipelineContext, SSEEvent, _bridge_sync_generator_to_async, log


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

    # 上下文用量面板(ContextUsage 圆环 + breakdown)读 state.data.memory.last_context。
    # 原本只在 run_rules_phase(Phase 3)末尾写,而酒馆(tavern_gm)跳过 Phase 3 → last_context
    # 永不写入 → 前端 /api/chat/context-breakdown 全 0。这里在 context 组装后先记一次(所有模式
    # 都经过 Phase 2);非酒馆模式 run_rules_phase 会再以含规则层的版本覆盖,酒馆模式靠这次写入。
    try:
        state.set_last_context(bundle.get("debug") or {})
    except Exception:
        pass
