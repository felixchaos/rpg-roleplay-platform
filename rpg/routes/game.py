"""game.py — 游戏核心流程路由 (new / opening / chat / stop / save)。"""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from schemas.game import NewGameRequest, ChatEstimateRequest, ChatRequest

router = APIRouter()


@router.post("/api/new")
async def api_new(body: NewGameRequest, request: Request) -> JSONResponse:
    """创建新存档。

    切换角色卡（user persona / 用户自创 NPC / 剧本预置角色）一律走这个接口，
    不会污染现有存档。优先级（高 → 低）：
      1. script_card_id + script_id  (扮演某剧本里的角色)
      2. user_card_id                 (用户自创 NPC 卡)
      3. persona_id                   (用户自己的 persona)
      4. body 里直接传 name/role/background
    """
    from app import (
        _require_api_user, _payload, _backup_save, _invalidate_user_cache,
        _user_key, _state_lock, _state_by_user, _persist_runtime_checkpoint,
        GameState, ROLES,
    )
    api_user = _require_api_user(request)
    body_dict = body.model_dump(exclude_none=True)
    backup = _backup_save("before_new_game") if api_user is None else None

    source_meta: dict | None = None
    source_kind = ""

    # 优先级 1：剧本预置角色卡
    script_card_id = body_dict.get("script_card_id")
    script_id = body_dict.get("script_id")
    if script_card_id and script_id and api_user:
        from platform_app import knowledge as _know
        card = _know.get_character_card(api_user["id"], int(script_id), int(script_card_id))
        if card:
            source_meta = card
            source_kind = "script_card"

    # 优先级 2：用户自创 NPC 卡
    if source_meta is None:
        user_card_id = body_dict.get("user_card_id")
        if user_card_id and api_user:
            from platform_app import user_cards as _ucards
            card = _ucards.get_user_card(api_user["id"], int(user_card_id))
            if card:
                source_meta = card
                source_kind = "user_card"

    # 优先级 3：persona
    if source_meta is None:
        persona_id = body_dict.get("persona_id")
        if persona_id and api_user:
            from platform_app import user_cards as _ucards
            persona = _ucards.get_persona(api_user["id"], int(persona_id))
            if persona:
                source_meta = persona
                source_kind = "persona"

    if source_meta:
        # 字段映射：script_card / user_card 用 identity 作 role，persona 用 role 字段
        name = source_meta.get("name") or "无名者"
        if source_kind == "persona":
            role = source_meta.get("role") or "未指定"
            background = source_meta.get("background") or "（无背景）"
        else:
            role = source_meta.get("identity") or "未指定"
            background = source_meta.get("appearance") or source_meta.get("personality") or "（来自角色卡）"
    else:
        # 通用 RPG 底座：默认 role 不再 fallback 到《我蕾穆丽娜不爱你》的『穿越者·魔女』。
        # ROLES 字典里有该剧本的 role label，作为兼容映射保留，但不再当默认值。
        role_label = (body_dict.get("role") or "").strip() or "未指定"
        role = ROLES.get(role_label, role_label)
        name = (body_dict.get("name") or "无名者").strip()
        background = (body_dict.get("background") or "").strip()

    state = GameState.new()
    state.setup_player(name, role, background)
    if source_meta:
        state.data["player"]["source_kind"] = source_kind
        state.data["player"]["source_id"] = int(source_meta["id"])
        for field in ("appearance", "personality", "speech_style"):
            if source_meta.get(field):
                state.data["player"][field] = source_meta[field]
    state.save()
    # 清掉缓存，下次 _ensure_loaded 会用新 state
    _invalidate_user_cache(api_user)
    uid = _user_key(api_user)
    with _state_lock:
        _state_by_user[uid] = state
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "backup": backup, "state": _payload(api_user)})


@router.post("/api/opening")
async def api_opening(request: Request) -> StreamingResponse:
    from app import (
        _require_api_user, _payload, _ensure_loaded, _get_gm, _sse,
        _active_script_id, _resolve_persist_target, _build_turn_context,
        _persist_runtime_checkpoint, retrieve_context,
    )
    api_user = _require_api_user(request)
    state = _ensure_loaded(api_user)
    gm = _get_gm(api_user)

    async def stream():
        # task 121a: 4 阶段 stage 事件让前端能显示 thinking pill,避免 5-15s 无反馈
        yield _sse("stage", {"phase": "retrieving", "label": "翻阅剧本设定中…"})
        # 修(task 117):走 phase 算法路径 — 不硬编码"第一章"。
        # retrieve_context 内部消费 state.world.timeline.current_phase / save.active_phase_index
        # 来限定 chapter window;空 state 时 fallback 到 phase 0 的 chapter_range,适用任意小说。
        script_id = _active_script_id(api_user)
        if script_id:
            world = state.data.get("world", {}) or {}
            player = state.data.get("player", {}) or {}
            memory = state.data.get("memory", {}) or {}
            events = world.get("known_events") or []
            query_parts = [
                str(player.get("current_location") or ""),
                str(world.get("time") or ""),
                str(memory.get("current_objective") or ""),
                *[str(e) for e in events[:2]],
            ]
            query = " ".join(p for p in query_parts if p).strip() or "开场"
        else:
            query = "柏林 图卢兹 娅赛兰 蛇信 蕾穆丽娜"
        ctx = retrieve_context(
            query,
            state=state,
            user_id=api_user["id"] if api_user else None,
            script_id=script_id,
        )
        state.set_last_retrieval(ctx)
        # task 107E: 把 save_id 透传给 context_engine, 让 runtime_phase_digests provider 工作
        _, _save_id_for_ctx = _resolve_persist_target(api_user)
        yield _sse("stage", {"phase": "building_context", "label": "组装上下文…"})
        bundle = _build_turn_context(state, query, ctx, script_id=script_id, save_id=_save_id_for_ctx)
        yield _sse("status", _payload(api_user))
        yield _sse("stage", {"phase": "generating", "label": "GM 构思开场中…"})
        text = ""
        try:
            opening = gm.generate_opening(state, retrieved_context=bundle["prompt"])
            text = opening
            yield _sse("stage", {"phase": "done", "label": ""})
            yield _sse("token", {"text": opening})
            state.data["history"].append({"role": "assistant", "content": opening})
            state.save()
            _persist_runtime_checkpoint(state, api_user)
            yield _sse("done", {"status": _payload(api_user)})
        except Exception as exc:
            yield _sse("error", {"message": str(exc), "partial": text})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/api/chat/estimate")
async def api_chat_estimate(body: ChatEstimateRequest, request: Request) -> JSONResponse:
    """实时上下文预估。前端 debounce 用户输入后调用，显示 ctx X/Y (Z%) · in~A out~B。

    估算思路（轻量，避免真的跑 retrieval）：
      input_tokens ≈ system_prompt + history_window + retrieved_budget + 当前输入
      output_tokens ≈ 该用户最近 10 轮该模型的平均输出
    """
    from app import (
        _require_api_user, _ensure_loaded, _resolve_persist_target, selected_model,
    )
    api_user = _require_api_user(request)
    body_dict = body.model_dump(exclude_none=True)
    message = (body_dict.get("message") or "").strip()
    include_retrieval = bool(body_dict.get("include_retrieval", True))

    state = _ensure_loaded(api_user)
    model = selected_model()
    api_id = model["api_id"]
    model_name = model["real_name"]

    # 各部分粗估
    from platform_app.usage import estimate_input_tokens, context_window_for, average_output_tokens
    history = state.history_messages()  # 已限制 MAX_HISTORY_TURNS
    history_text = "\n".join(m.get("content", "") for m in history)
    # system prompt 用 GM 模板的近似长度；不真正构建避免昂贵
    system_estimate = 1200  # 世界观+伯林局势+穿越者补丁 加起来约 1.2K tokens
    # 召回部分按预算（context_engine 配置的 ~800 token）
    retrieval_estimate = 800 if include_retrieval else 0
    # 玩家档案/记忆摘要
    profile_estimate = estimate_input_tokens(state.short_summary())

    input_tokens = (
        system_estimate
        + profile_estimate
        + estimate_input_tokens(history_text)
        + retrieval_estimate
        + estimate_input_tokens(message)
    )
    persist_user_id, _ = _resolve_persist_target(api_user)
    output_estimate = average_output_tokens(persist_user_id, model_name) if persist_user_id else 600
    if output_estimate <= 0:
        output_estimate = 600  # 没历史时的默认猜测

    ctx_max = context_window_for(api_id, model_name) or 0
    total_estimate = input_tokens + output_estimate
    ctx_pct = round(100 * input_tokens / ctx_max, 1) if ctx_max else 0
    will_overflow = (input_tokens + output_estimate > ctx_max) if ctx_max else False

    return JSONResponse({
        "ok": True,
        "api_id": api_id,
        "model": model_name,
        "context_used": input_tokens,
        "context_max": ctx_max,
        "context_pct": ctx_pct,
        "estimated_output_tokens": output_estimate,
        "estimated_total_tokens": total_estimate,
        "will_overflow": will_overflow,
        "breakdown": {
            "system_prompt": system_estimate,
            "profile_and_memory": profile_estimate,
            "history": estimate_input_tokens(history_text),
            "retrieval_budget": retrieval_estimate,
            "current_input": estimate_input_tokens(message),
        },
        "headroom_tokens": max(0, ctx_max - input_tokens - output_estimate) if ctx_max else 0,
    })


@router.post("/api/chat")
async def api_chat(body: ChatRequest, request: Request) -> StreamingResponse:
    import time
    from app import (
        _require_api_user, _payload, _ensure_loaded, _get_gm, _get_sub_gm,
        _sse, _save_attachments, _message_with_attachments, _command_response,
        _get_run_state, _persist_runtime_checkpoint, _resolve_persist_target,
        _active_script_id, _clarify_threshold, _persist_chat_turn, _mark_context_run,
        _apply_chat_rule_candidates, _chat_rule_candidates, _rule_results_prompt,
        _is_set_parser_enabled, _current_run_id, _is_stop_requested_global,
        _is_extractor_enabled, _acceptance_verifier_mode, _verify_acceptance,
        _build_usage_payload,
    )
    import app as _self_mod
    from platform_app import knowledge as platform_knowledge

    api_user = _require_api_user(request)
    body_dict = body.model_dump(exclude_none=True)
    # task 31：前端历史上同时存在 {message:...} 和 {text:...} 两套契约。
    # 老的 Game Console.html 发 text，新的 game-app.jsx 也偶尔走 message。
    # 后端必须两边兼容，否则用户输入直接被 "空消息" error 吞掉。
    message = (body_dict.get("message") or body_dict.get("text") or "").strip()
    attachments = _save_attachments(body_dict.get("attachments") or [], user_id=api_user["id"] if api_user else None)
    message_for_model = _message_with_attachments(message, attachments)
    if not message_for_model.strip():
        return StreamingResponse(iter([_sse("error", {"message": "空消息"})]), media_type="text/event-stream")
    _chat_start_time = time.time()

    # 多用户隔离：当前用户的 run_id 自增、stop_event 清零
    run_id, stop_event = _get_run_state(api_user)

    state = _ensure_loaded(api_user)
    gm = _get_gm(api_user)

    async def stream():
        # task #51: chat 主流程拆到 chat_pipeline.py 5 个 phase。
        # 这里只剩:
        #   - /命令短路 (本 endpoint 自己处理,不进 pipeline)
        #   - 构造 PipelineContext + 依次跑 phase + SSE 透传
        #   - 兜底 except 包到 error 事件
        from chat_pipeline import (
            PipelineContext,
            apply_player_directives_phase,
            run_context_phase,
            run_rules_phase,
            run_gm_phase,
            persist_turn_phase,
        )

        response = ""
        command_text, changed = ("", False) if attachments else _command_response(message, state)
        if command_text:
            if changed:
                _persist_runtime_checkpoint(state, api_user)
                yield _sse("status", _payload(api_user))
            yield _sse("token", {"text": command_text})
            yield _sse("done", {"status": _payload(api_user), "interrupted": False})
            return

        sub_gm = _get_sub_gm(api_user)
        pipeline_ctx = PipelineContext(
            api_user=api_user,
            state=state,
            gm=gm,
            sub_gm=sub_gm,
            message_for_model=message_for_model,
            run_id=run_id,
            stop_event=stop_event,
            chat_start_time=_chat_start_time,
        )

        try:
            # Phase 1: 玩家 directive (过期问题 + /set 工具化 + 正则 fallback + set_parser + timeline anchor)
            async for evt, data in apply_player_directives_phase(
                pipeline_ctx,
                resolve_persist_target=_resolve_persist_target,
                persist_runtime_checkpoint=_persist_runtime_checkpoint,
                payload_fn=_payload,
                is_set_parser_enabled=_is_set_parser_enabled,
                active_script_id=_active_script_id,
            ):
                yield _sse(evt, data)
            if pipeline_ctx.early_return:
                return

            # Phase 2: context agent (子 GM curator)
            # 注入 run_context_agent 让测试 monkeypatch (app.run_context_agent = ...) 能透到 pipeline。
            async for evt, data in run_context_phase(
                pipeline_ctx,
                resolve_persist_target=_resolve_persist_target,
                payload_fn=_payload,
                active_script_id=_active_script_id,
                clarify_threshold=_clarify_threshold,
                persist_chat_turn=_persist_chat_turn,
                mark_context_run=_mark_context_run,
                apply_chat_rule_candidates=_apply_chat_rule_candidates,
                chat_rule_candidates=_chat_rule_candidates,
                rule_results_prompt=_rule_results_prompt,
                persist_runtime_checkpoint=_persist_runtime_checkpoint,
                platform_knowledge_mod=platform_knowledge,
                run_context_agent_fn=getattr(_self_mod, "run_context_agent", None),
            ):
                yield _sse(evt, data)
            if pipeline_ctx.early_return:
                return

            # Phase 2.5 — task 86/87: 世界书子代理 (确定性, 不调 LLM, ~20ms)
            # 翻阅 phase_digests + chapter_facts + worldbook → 注入 ctx_text。
            # SSE 广播 worldbook_consulting/ready, 前端显示"翻阅设定中"。
            try:
                from agents import worldbook_agent
                script_id_for_wb = _active_script_id(api_user)
                world = state.data.get("world", {}) or {}
                memory = state.data.get("memory", {}) or {}
                cur_phase = str((world.get("timeline") or {}).get("current_phase") or "")
                cur_time = str(world.get("time") or "")
                yield _sse("worldbook_consulting", {
                    "query": message_for_model[:80],
                    "phase": cur_phase,
                    "time": cur_time,
                })
                wb_query = " ".join(filter(None, [
                    message_for_model,
                    str(memory.get("current_objective") or ""),
                ]))[:300]
                wb_result = worldbook_agent.consult(
                    script_id=int(script_id_for_wb or 0),
                    query=wb_query,
                    current_phase=cur_phase,
                    current_time=cur_time,
                )
                yield _sse("worldbook_ready", {
                    "confidence": round(wb_result.confidence, 2),
                    "sources": wb_result.sources,
                    "phase": (wb_result.timeline_anchor or {}).get("phase"),
                    "elapsed_ms": wb_result.elapsed_ms,
                })
                if wb_result.confidence > 0:
                    wb_text = wb_result.to_context_text()
                    if wb_text:
                        pipeline_ctx.ctx_text = (pipeline_ctx.ctx_text or "") + "\n\n" + wb_text
                # 把 confidence + progress_note 也塞 bundle 让 GM prompt 知道是否"翻阅未果"
                if pipeline_ctx.bundle is None:
                    pipeline_ctx.bundle = {}
                pipeline_ctx.bundle.setdefault("worldbook", {})
                pipeline_ctx.bundle["worldbook"].update({
                    "confidence": wb_result.confidence,
                    "progress_note": wb_result.progress_note,
                    "sources": wb_result.sources,
                })
            except Exception as wb_exc:
                yield _sse("worldbook_ready", {
                    "confidence": 0.0, "error": f"{type(wb_exc).__name__}: {wb_exc}",
                })

            # Phase 3: 5E rules preflight + rule candidates + clarify 短路
            async for evt, data in run_rules_phase(
                pipeline_ctx,
                payload_fn=_payload,
                persist_chat_turn=_persist_chat_turn,
                persist_runtime_checkpoint=_persist_runtime_checkpoint,
                resolve_persist_target=_resolve_persist_target,
                mark_context_run=_mark_context_run,
                clarify_threshold=_clarify_threshold,
                apply_chat_rule_candidates=_apply_chat_rule_candidates,
                chat_rule_candidates=_chat_rule_candidates,
                rule_results_prompt=_rule_results_prompt,
                platform_knowledge_mod=platform_knowledge,
            ):
                yield _sse(evt, data)
            if pipeline_ctx.early_return:
                return

            # Phase 4: GM 主响应 (token + tool_call + extractor + acceptance)
            async for evt, data in run_gm_phase(
                pipeline_ctx,
                payload_fn=_payload,
                persist_chat_turn=_persist_chat_turn,
                mark_context_run=_mark_context_run,
                current_run_id_fn=_current_run_id,
                is_stop_requested_global=_is_stop_requested_global,
                is_extractor_enabled=_is_extractor_enabled,
                acceptance_verifier_mode=_acceptance_verifier_mode,
                verify_acceptance=_verify_acceptance,
                active_script_id=_active_script_id,
            ):
                yield _sse(evt, data)
            if pipeline_ctx.early_return:
                return

            # Phase 5: 持久化 + done
            async for evt, data in persist_turn_phase(
                pipeline_ctx,
                payload_fn=_payload,
                persist_chat_turn=_persist_chat_turn,
                build_usage_payload=_build_usage_payload,
            ):
                yield _sse(evt, data)
        except Exception as exc:
            _mark_context_run(
                pipeline_ctx.context_run_id,
                "failed",
                error=str(exc),
                duration_ms=int((time.time() - _chat_start_time) * 1000),
            )
            yield _sse("error", {"message": str(exc), "partial": pipeline_ctx.response or response})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/api/stop")
async def api_stop(request: Request) -> JSONResponse:
    """打断当前用户正在跑的 chat。其他用户的 chat 不受影响。
    task 87 Phase 6: 同时调 dispatcher stop_current_chat 工具,把 stop_signal 写到 state.permissions。"""
    from app import _require_api_user, _stop_user, _ensure_loaded, _resolve_persist_target
    api_user = _require_api_user(request)
    _stop_user(api_user)  # 真正的 stop_event 仍由 _stop_user 处理 (跨 chat handler 协程)
    # 同时通过 dispatcher 记录 audit 与 state.permissions.stop_signal
    try:
        state = _ensure_loaded(api_user)
        from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
        dispatch_ui_tool(
            tool_name="stop_current_chat", args={},
            user_id=int(api_user.get("id")) if api_user else 0,
            save_id=_resolve_persist_target(api_user)[1] or 0,
            state=state,
        )
    except Exception:
        pass
    return JSONResponse({"ok": True})


@router.post("/api/save")
async def api_save(request: Request) -> JSONResponse:
    """task 87 Phase 6: 走 dispatcher save_runtime。"""
    from app import (
        _require_api_user, _payload, _ensure_loaded, _resolve_persist_target,
        _persist_runtime_checkpoint,
    )
    api_user = _require_api_user(request)
    state = _ensure_loaded(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="save_runtime", args={},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})
