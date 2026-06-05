"""game.py — 游戏核心流程路由 (new / opening / chat / stop / save)。"""
from __future__ import annotations

import asyncio
import logging as _logging
import secrets as _secrets
import threading
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from routes._deps_fastapi import get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, GenericOkResponse, OkResponse, StateResponse
from schemas.game import ChatEstimateRequest, ChatRequest, NewGameRequest
from state.parsers import _extract_trailing_markdown_options

_log = _logging.getLogger(__name__)

_CLIENT_SAFE_RUNTIME_PREFIXES = (
    "未找到 Vertex AI Service Account。",
    "Vertex AI 调用被拒(403)。",
)

_CLIENT_SAFE_AUTH_MARKERS = (
    "incorrect api key",
    "invalid api key",
    "please pass a valid api key",
    "401 unauthorized",
)


def _client_safe_error(exc: Exception) -> str:
    """把未预期异常转成对客户端安全的泛化文案 + error_id。

    str(exc) 可能含 DB 表名/连接串、文件路径、第三方 SDK 内部细节(乃至凭据上下文),
    绝不能直透进 SSE 给玩家。原始异常带 error_id 写服务端日志,客户端只拿 id 便于排障对账。
    """
    error_id = _secrets.token_hex(4)
    raw_message = str(exc).strip()
    if isinstance(exc, RuntimeError) and raw_message.startswith(_CLIENT_SAFE_RUNTIME_PREFIXES):
        _log.warning("[chat] client-safe stream error (error_id=%s): %s", error_id, raw_message)
        return f"{raw_message}\n\n如果已经上传,请重新测试凭证或切换到已配置的模型。(错误码 {error_id})"
    raw_lower = raw_message.lower()
    if any(marker in raw_lower for marker in _CLIENT_SAFE_AUTH_MARKERS):
        _log.warning("[chat] client-safe auth stream error (error_id=%s): %s", error_id, type(exc).__name__)
        return (
            "当前模型的 API Key 无效或已过期。"
            "请到「设置 → API 设置」重新测试凭证，或切换到已配置的模型。"
            f"(错误码 {error_id})"
        )
    _log.exception("[chat] unhandled stream error (error_id=%s)", error_id)
    return f"本轮处理出错,请重试(错误码 {error_id})"


async def _bridge_sync_generator_to_async(gen_factory, stop_event: threading.Event | None = None):
    """跑同步 generator,SSE 取消时设置 stop_event 让 generator 早退。

    gen_factory: 无参 callable,返回 sync generator (内部可持有 stop_event 引用检查)。
    stop_event:  外部传入的 threading.Event;未传时内部新建一个。
    SSE 客户端断开 / 异常 / 正常完成时 finally 均会 set(),确保 sync 端早退。
    """
    if stop_event is None:
        stop_event = threading.Event()
    loop = asyncio.get_running_loop()
    # asyncio.Queue + call_soon_threadsafe:工作线程产出的 item 投递回 event loop,
    # async 端 `await q.get()` 真正挂起协程,不忙等。
    # 旧实现用 `await asyncio.sleep(0)` 轮询 → 每条并发流 spin 紧循环烧满 CPU、
    # 饿死 event loop(新请求分配延迟 + SSE 事件被挤掉 = 并发阻碍/丢事件根因)。
    q: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    def _put(item) -> None:
        # 从工作线程安全地把 item 投递回 event loop 线程
        try:
            loop.call_soon_threadsafe(q.put_nowait, item)
        except RuntimeError:
            # loop 已关闭(进程收尾):忽略,runner 下一轮靠 stop_event 早退
            pass

    def _runner():
        try:
            for item in gen_factory():
                if stop_event.is_set():
                    break
                _put(item)
        except Exception as exc:
            _put(exc)
        finally:
            _put(SENTINEL)

    fut = loop.run_in_executor(None, _runner)
    try:
        while True:
            item = await q.get()
            if item is SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        # SSE 客户端断开 / 异常 / 正常完成都通知 sync 端早退
        stop_event.set()
        await fut

router = APIRouter()


@router.post("/api/new", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_new(
    body: NewGameRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """创建新存档。

    切换角色卡（user persona / 用户自创 NPC / 剧本预置角色）一律走这个接口，
    不会污染现有存档。优先级（高 → 低）：
      1. script_card_id + script_id  (扮演某剧本里的角色)
      2. user_card_id                 (用户自创 NPC 卡)
      3. persona_id                   (用户自己的 persona)
      4. body 里直接传 name/role/background
    """
    from app import (
        ROLES,
        GameState,
        _backup_save,
        _invalidate_user_cache,
        _payload,
        _persist_runtime_checkpoint,
        _state_by_user,
        _state_lock,
        _user_key,
    )
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
        state.data["player"]["source_id"] = int(source_meta.get("id") or 0)
        for field in ("appearance", "personality", "speech_style"):
            if source_meta.get(field):
                state.data["player"][field] = source_meta[field]
    state.save()
    # 清掉缓存，下次 _ensure_loaded 会用新 state
    _invalidate_user_cache(api_user)
    uid = _user_key(api_user)
    with _state_lock:
        from app import _lru_set as _lru_set_inner
        _lru_set_inner(_state_by_user, uid, state)
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "backup": backup, "state": _payload(api_user)})


@router.post("/api/opening")
async def api_opening(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    from app import (
        _active_script_id,
        _build_turn_context,
        _ensure_loaded,
        _get_gm,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
        _sse,
        platform_branches,
        platform_knowledge,
        retrieve_context,
    )
    state = _ensure_loaded(api_user)
    gm = _get_gm(api_user)

    async def stream():
        # task 121a: 4 阶段 stage 事件让前端能显示 thinking pill,避免 5-15s 无反馈
        yield _sse("stage", {"phase": "retrieving", "label": "翻阅剧本设定中…"})
        # 修(task 117):走 phase 算法路径 — 不硬编码"第一章"。
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
            query = "开场"

        # P0-1: retrieve_context + build_context_bundle 包进 to_thread,不阻塞 event loop
        def _retrieve_and_build():
            _ctx = retrieve_context(
                query,
                state=state,
                user_id=api_user["id"] if api_user else None,
                script_id=script_id,
            )
            state.set_last_retrieval(_ctx)
            _, _save_id_for_ctx = _resolve_persist_target(api_user)
            _bundle = _build_turn_context(state, query, _ctx, script_id=script_id, save_id=_save_id_for_ctx)
            return _bundle

        yield _sse("stage", {"phase": "building_context", "label": "组装上下文…"})
        bundle = await asyncio.to_thread(_retrieve_and_build)
        yield _sse("status", _payload(api_user))
        yield _sse("stage", {"phase": "generating", "label": "GM 构思开场中…"})
        text = ""
        try:
            # P0-1: generate_opening_stream 是同步 generator,通过 bridge 异步化
            # stop_event 在 SSE 断开时由 bridge finally 设置,让 sync generator 提前退出
            _opening_stop = threading.Event()
            async for chunk in _bridge_sync_generator_to_async(
                lambda: gm.generate_opening_stream(state, retrieved_context=bundle["prompt"], stop_event=_opening_stop),
                stop_event=_opening_stop,
            ):
                text += chunk
                yield _sse("token", {"text": chunk})
            opening = text
            yield _sse("stage", {"phase": "done", "label": ""})
            opening_for_history, opening_options = _extract_trailing_markdown_options(opening)
            state.data["history"].append({"role": "assistant", "content": opening_for_history})
            # 让开场也走结构化解析,把【询问玩家】+JSON ops 解析进 pending_questions / state
            before_questions = len((state.data.get("permissions") or {}).get("pending_questions") or [])
            try:
                state.apply_structured_updates(opening_for_history)
            except Exception:
                import logging as _logging
                _logging.getLogger(__name__).warning("opening apply_structured_updates failed", exc_info=True)
            after_questions = len((state.data.get("permissions") or {}).get("pending_questions") or [])
            if opening_options and after_questions == before_questions:
                state.add_pending_question("你想怎么行动？", source="gm:opening_options", options=opening_options)
            state.save()
            try:
                persist_user_id, active_save_id = _resolve_persist_target(api_user)
                if api_user and persist_user_id and active_save_id:
                    platform_branches.record_runtime_turn(
                        "",
                        opening_for_history,
                        user_id=api_user["id"],
                        state_data=state.data,
                    )
                    platform_knowledge.ensure_game_session(persist_user_id, active_save_id, state.data)
                else:
                    _persist_runtime_checkpoint(state, api_user)
            except Exception:
                _persist_runtime_checkpoint(state, api_user)
            yield _sse("done", {"status": _payload(api_user)})
        except Exception as exc:
            yield _sse("error", {"message": _client_safe_error(exc), "partial": text})
            yield _sse("done", {"interrupted": True, "status": _payload(api_user)})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/api/chat/estimate", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_chat_estimate(
    body: ChatEstimateRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """实时上下文预估。前端 debounce 用户输入后调用，显示 ctx X/Y (Z%) · in~A out~B。

    估算思路（轻量，避免真的跑 retrieval）：
      input_tokens ≈ system_prompt + history_window + retrieved_budget + 当前输入
      output_tokens ≈ 该用户最近 10 轮该模型的平均输出
    """
    from app import (
        _ensure_loaded,
        _resolve_persist_target,
        selected_model,
    )
    body_dict = body.model_dump(exclude_none=True)
    message = (body_dict.get("message") or "").strip()
    include_retrieval = bool(body_dict.get("include_retrieval", True))

    state = _ensure_loaded(api_user)
    model = selected_model()
    api_id = model["api_id"]
    model_name = model["real_name"]

    # 各部分粗估
    from platform_app.usage import average_output_tokens, context_window_for, estimate_input_tokens
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


# layer id → (category key, label, color)
_LAYER_CATEGORY = {
    # 对话历史
    "recent_chat": ("history", "对话历史", "#4f8ef7"),
    # 系统提示 / 规则
    "rules": ("system_prompt", "系统提示", "#9b6bdf"),
    "agent_runtime": ("system_prompt", "系统提示", "#9b6bdf"),
    "timeline_pending": ("system_prompt", "系统提示", "#9b6bdf"),
    "worldline": ("system_prompt", "系统提示", "#9b6bdf"),
    "write_results": ("system_prompt", "系统提示", "#9b6bdf"),
    "context_agent": ("system_prompt", "系统提示", "#9b6bdf"),
    "candidate_actions": ("system_prompt", "系统提示", "#9b6bdf"),
    "state_schema": ("system_prompt", "系统提示", "#9b6bdf"),
    # 状态摘要
    "state": ("system_prompt", "系统提示", "#9b6bdf"),
    # RAG 召回
    "rag": ("retrieved_chunks", "RAG 召回", "#2bae8a"),
    "novel_retrieval": ("retrieved_chunks", "RAG 召回", "#2bae8a"),
    # 长期记忆
    "fact_groups": ("memory_facts", "长期记忆", "#e6a817"),
    "hypotheses": ("memory_facts", "长期记忆", "#e6a817"),
    "memory": ("memory_facts", "长期记忆", "#e6a817"),
    # 角色卡
    "player_card": ("character_cards", "角色卡", "#e05c7a"),
    "npc_cards": ("character_cards", "角色卡", "#e05c7a"),
    "novel_characters": ("character_cards", "角色卡", "#e05c7a"),
    # 世界书
    "worldbook": ("worldbook", "世界书", "#3dbad4"),
    "novel_worldbook": ("worldbook", "世界书", "#3dbad4"),
    "module_worldbook": ("worldbook", "世界书", "#3dbad4"),
    # 阶段摘要
    "novel_timeline": ("phase_digests", "阶段摘要", "#f07a3c"),
    "runtime_phase_digests": ("phase_digests", "阶段摘要", "#f07a3c"),
    # 玩家输入（不计入 breakdown，归入 history）
    "user_input": ("history", "对话历史", "#4f8ef7"),
}

_CATEGORY_ORDER = [
    ("history", "对话历史", "#4f8ef7"),
    ("system_prompt", "系统提示", "#9b6bdf"),
    ("retrieved_chunks", "RAG 召回", "#2bae8a"),
    ("memory_facts", "长期记忆", "#e6a817"),
    ("character_cards", "角色卡", "#e05c7a"),
    ("worldbook", "世界书", "#3dbad4"),
    ("phase_digests", "阶段摘要", "#f07a3c"),
    ("tools", "工具/MCP", "#8899aa"),
]


@router.get("/api/chat/context-breakdown", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_context_breakdown(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    from app import _ensure_loaded
    from platform_app.usage import context_window_for
    state = _ensure_loaded(api_user)
    last_ctx = (state.data.get("memory") or {}).get("last_context") or {}
    layers = last_ctx.get("layers") or []
    total_tokens = int(last_ctx.get("estimated_tokens") or 0)

    # 按 category 累加
    cat_tokens: dict[str, int] = {}
    for layer in layers:
        lid = layer.get("id") or ""
        tok = int(layer.get("estimated_tokens") or 0)
        mapping = _LAYER_CATEGORY.get(lid)
        if mapping:
            key = mapping[0]
        else:
            # 未知 layer → 归入 system_prompt
            key = "system_prompt"
        cat_tokens[key] = cat_tokens.get(key, 0) + tok

    from app import selected_model
    model = selected_model()
    ctx_limit = int(context_window_for(model["api_id"], model["real_name"]) or 1_000_000)

    breakdown = []
    used_sum = 0
    for key, label, color in _CATEGORY_ORDER:
        tok = cat_tokens.get(key, 0)
        used_sum += tok
        pct = round(100 * tok / ctx_limit, 1) if ctx_limit else 0.0
        breakdown.append({"key": key, "label": label, "tokens": tok, "pct": pct, "color": color})

    free_tokens = max(0, ctx_limit - used_sum)
    free_pct = round(100 * free_tokens / ctx_limit, 1) if ctx_limit else 0.0
    breakdown.append({"key": "free", "label": "剩余空间", "tokens": free_tokens, "pct": free_pct, "color": "#555e6a"})

    return JSONResponse({
        "ok": True,
        "total_tokens": total_tokens or used_sum,
        "ctx_limit": ctx_limit,
        "breakdown": breakdown,
    })


@router.post("/api/chat")
async def api_chat(
    body: ChatRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    import time

    import app as _self_mod
    from app import (
        _acceptance_verifier_mode,
        _active_script_id,
        _apply_chat_rule_candidates,
        _build_usage_payload,
        _chat_max_tokens,
        _chat_rule_candidates,
        _clarify_threshold,
        _command_response,
        _current_run_id,
        _ensure_loaded,
        _get_gm,
        _get_run_state,
        _get_sub_gm,
        _is_black_swan_enabled,
        _is_extractor_enabled,
        _is_set_parser_enabled,
        _is_stop_requested_global,
        _mark_context_run,
        _message_with_attachments,
        _payload,
        _persist_chat_turn,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
        _rule_results_prompt,
        _save_attachments,
        _sse,
        _verify_acceptance,
    )
    from platform_app import knowledge as platform_knowledge

    body_dict = body.model_dump(exclude_none=True)
    # task 31：前端历史上同时存在 {message:...} 和 {text:...} 两套契约。
    # 老的 Game Console.html 发 text，新的 game-app.jsx 也偶尔走 message。
    # 后端必须两边兼容，否则用户输入直接被 "空消息" error 吞掉。
    message = (body_dict.get("message") or body_dict.get("text") or "").strip()
    # 输入上限:nginx client_max_body_size=50m 是外层兜底,但 app 层缺单条消息上限 →
    # 超长消息会进上下文撑爆 LLM(困惑的 context overflow)并膨胀 history/DB。给清晰 400。
    # 32KB 对正常角色扮演输入极宽裕(约万余汉字)。
    _MAX_CHAT_MSG_CHARS = 32000
    if len(message) > _MAX_CHAT_MSG_CHARS:
        return StreamingResponse(
            iter([_sse("error", {"message": f"消息过长({len(message)} 字符,上限 {_MAX_CHAT_MSG_CHARS});请拆分后发送"})]),
            media_type="text/event-stream",
        )
    attachments = _save_attachments(body_dict.get("attachments") or [], user_id=api_user["id"] if api_user else None)
    message_for_model = _message_with_attachments(message, attachments)
    if not message_for_model.strip():
        return StreamingResponse(iter([_sse("error", {"message": "空消息"})]), media_type="text/event-stream")

    # task #61: 多 tab 冲突检测 — 前端带 save_id 时校验是否与 user_runtime 匹配
    client_save_id = body_dict.get("save_id")
    if client_save_id and api_user:
        from platform_app import runtime as _platform_runtime
        _rt = _platform_runtime.read_runtime(user_id=api_user["id"])
        _active_sid = int((_rt or {}).get("save_id") or 0)
        if _active_sid and int(client_save_id) != _active_sid:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "save_id_mismatch",
                    "message": "当前激活存档已切换，请刷新页面后重试",
                    "client_save_id": int(client_save_id),
                    "active_save_id": _active_sid,
                },
            )

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
            persist_turn_phase,
            run_context_phase,
            run_gm_phase,
            run_rules_phase,
        )

        response = ""
        command_text, changed = ("", False) if attachments else _command_response(message, state)
        if command_text:
            if changed:
                _persist_runtime_checkpoint(state, api_user)
                yield _sse("status", _payload(api_user))
            # #13 沉浸感: 斜杠命令回执是确定性后端字符串(非 GM 叙事),不再以
            # token 流出(会被前端当正文累加进主聊天 transcript),改 system_receipt
            # 事件 → 前端 toast。changed=True 时侧栏已由上面的 status 事件刷新。
            yield _sse("system_receipt", {"text": command_text, "changed": changed})
            yield _sse("done", {"status": _payload(api_user), "interrupted": False, "command": True})
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
                is_black_swan_enabled=_is_black_swan_enabled,
                acceptance_verifier_mode=_acceptance_verifier_mode,
                verify_acceptance=_verify_acceptance,
                active_script_id=_active_script_id,
                chat_max_tokens=_chat_max_tokens,
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
            yield _sse("error", {"message": _client_safe_error(exc), "partial": pipeline_ctx.response or response})
            yield _sse("done", {"interrupted": True, "status": _payload(api_user)})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/api/stop", response_model=OkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_stop(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """打断当前用户正在跑的 chat。其他用户的 chat 不受影响。
    task 87 Phase 6: 同时调 dispatcher stop_current_chat 工具,把 stop_signal 写到 state.permissions。"""
    from app import _ensure_loaded, _resolve_persist_target, _stop_user
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


@router.post("/api/save", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_save(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 87 Phase 6: 走 dispatcher save_runtime。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
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
