"""Phase 4:主 GM 响应(流式 token + tool_call + 后处理调度)。
_POSTPROC_MODE / _recorder_unified / _narrator_slim 就近置此(run_gm_phase 唯一使用者;
测试经 chat_pipeline.gm.* 打桩)。拆包自 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from state import (
    GameState,
    StreamFenceGuard,
    strip_json_state_ops,
    strip_leaked_scaffold,
    strip_meta_tool_preamble,
)

from ._common import (
    PipelineContext,
    SSEEvent,
    _bridge_sync_generator_to_async,
    _gm_max_iters,
    _snippet_tool_result,
    _summarize_tool_args,
    _uid_of,
    log,
)
from ._input_signals import (
    _CONTINUE_DIRECTIVE,
    _SHORT_INPUT_DIRECTIVE,
    _immersive_request,
    _is_continue_request,
    _should_inject_short_input_directive,
)
from .postproc import (
    _ACCEPTANCE_AB_MIN_INTERVAL,
    _ACCEPTANCE_BG_TASKS,
    _acceptance_ab_pref_enabled,
    _apply_gm_json_ops,
    _log_acceptance_ab,
    _run_anchor_reconcile,
    _run_post_gm_parallel,
)


# W1 容量优化: RPG_POSTPROC_MODE=async (默认) → GM 流完即入队 Phase 4, 不阻塞 worker。
# RPG_POSTPROC_MODE=sync → 旧行为 (后处理阻塞主路径, 测试/debug 用)。
_POSTPROC_MODE = os.environ.get("RPG_POSTPROC_MODE", "async").lower()


def _recorder_unified(api_user: dict | None = None) -> bool:
    """Q Phase 2 史官三合一开关(每用户特性,默认开)。
    on 时 async 后处理把 ops 提取 + 锚点判定合成单次 recorder LLM 调用(替代
    extractor-skip + 独立 anchor_reconcile);off 时走原路径。"""
    from core.feature_flags import feature_enabled
    return feature_enabled("recorder_unified", _uid_of(api_user))


def _narrator_slim(api_user: dict | None = None) -> bool:
    """Q Phase 4 文宗去工具循环开关(每用户特性,默认开)。
    on 时主 GM(文宗)不带工具 → 单次纯散文,杀掉 ≤16 轮工具循环(最大 token 乘数);
    状态写入全交史官(Phase 2 recorder)。**必须同时开 RECORDER_UNIFIED**,否则状态无人写
    → 自动失效(见下游 guard)。tooling=none 档。"""
    from core.feature_flags import feature_enabled
    return feature_enabled("narrator_slim", _uid_of(api_user))


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

    # Q 分层缓存:回合动态前置项(Phase D 注入 + 短输入镜头指令)在 tiered 路径下要进「动态块最前」
    # 而非 prepend 到整串最前(否则污染缓存前缀)。这里收集起来,flag-on 时随 prompt_segments 一并传给 GM;
    # 同时保留对 bundle["prompt"] 的 prepend,供 flag-off(单串)回退路径使用。两路径互斥,不会重复注入。
    _dynamic_prefix_parts: list[str] = []

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
                _dynamic_prefix_parts.append(_inj)
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
        if _is_continue_request(message_for_model):
            # 「继续」按钮=把主动权交给 GM 要求推进。注入推进规则(而非镜头规则——
            # 后者会把 GM 钉在原地写反应戏,与按钮承诺相反=点继续必水文)。
            if _CONTINUE_DIRECTIVE not in (bundle.get("prompt") or ""):
                bundle["prompt"] = _CONTINUE_DIRECTIVE + "\n\n" + (bundle.get("prompt") or "")
                _dynamic_prefix_parts.insert(0, _CONTINUE_DIRECTIVE)
                bundle.setdefault("debug", {})["continue_directive"] = True
        elif _should_inject_short_input_directive(message_for_model):
            if _SHORT_INPUT_DIRECTIVE not in (bundle.get("prompt") or ""):
                bundle["prompt"] = _SHORT_INPUT_DIRECTIVE + "\n\n" + (bundle.get("prompt") or "")
                _dynamic_prefix_parts.insert(0, _SHORT_INPUT_DIRECTIVE)  # 短输入指令置最前(与 flat prepend 顺序一致)
                bundle.setdefault("debug", {})["short_input_directive"] = {
                    "len": len((message_for_model or "").strip())
                }
    except Exception as _si_err:
        log.warning(f"[chat] 短输入镜头指令注入跳过(不影响 gameplay): {_si_err}")

    # 沉浸式拟人模式(仅酒馆):每回合从 DB【新鲜读取】持久 flag 设到 gm 上(绕开 per-worker
    # state 缓存 → 跨 worker 安全 + UI 端点即时生效),由 master._build_system 在 tavern system
    # prompt 里确定性注入覆盖块。真相源 = runtime_checkouts.state_snapshot(working tree)。
    # 另:玩家本回合若【明确】请求开/关沉浸(确定性识别),先确定性落库,本回合即生效 —— 不指望
    # LLM 一定调 set_tavern_immersive 工具(harness 确定性铁律)。默认/失败 → False(零行为变化)。
    try:
        if gm is not None:
            _is_tav = False
            try:
                from context_providers.registry import resolve_content_pack as _rcp
                _is_tav = (_rcp(ctx.state).get("gm_policy") or {}).get("mode") == "tavern_gm"
            except Exception:
                _is_tav = False
            _imm_sid = int(ctx.early_active_save_id or 0)
            _imm_uid = int(api_user.get("id")) if api_user else 0
            if _is_tav and _imm_sid and _imm_uid:
                from platform_app.db import connect as _connect_im
                _req = _immersive_request(message_for_model)
                with _connect_im() as _db_im:
                    # 真相源 = game_saves.tavern_immersive 持久列(activate 重建工作树不碰列,跨重开对话不丢)。
                    if _req is not None:
                        _db_im.execute(
                            "update game_saves set tavern_immersive=%s, updated_at=now() "
                            "where id=%s and user_id=%s and save_kind='tavern'",
                            (_req, _imm_sid, _imm_uid))
                    _imr = _db_im.execute(
                        "select tavern_immersive as im from game_saves "
                        "where id=%s and user_id=%s", (_imm_sid, _imm_uid)).fetchone()
                gm._immersive_mode = bool((_imr or {}).get("im"))
            else:
                gm._immersive_mode = False
    except Exception as _imm_err:
        log.warning(f"[chat] 沉浸式 flag 处理跳过(不影响 gameplay): {_imm_err}")

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
        # 酒馆模式(tavern_gm)隐藏锚点/剧本/战斗/模组类工具,保留 memory/关系/世界书 overlay
        _gm_mode = None
        _tavern_bound_script_id = None
        try:
            from context_providers.registry import resolve_content_pack
            _gm_mode = (resolve_content_pack(state).get("gm_policy") or {}).get("mode")
        except Exception:
            _gm_mode = None
        # 酒馆 v2(R2):绑定剧本后,重开剧本读工具(search_canon / lookup_* / get_*)。
        try:
            _tv = (getattr(state, "data", {}) or {}).get("tavern") or {}
            _bsid = _tv.get("bound_script_id")
            _tavern_bound_script_id = int(_bsid) if _bsid else None
        except Exception:
            _tavern_bound_script_id = None
        unified_tools = build_unified_tool_list(
            mcp_tools, origin="llm_chat", mode=_gm_mode,
            bound_script_id=_tavern_bound_script_id,
        )
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

    # 工具流 + 思考流持久化:本轮累积进 state.data 临时键 → record_turn 落到 assistant 历史消息,
    # 重开/刷新后聊天记录里仍可见(酒馆沉浸:工具调用 + 思考流不该生成完就消失)。每轮开头清零。
    state.data["_turn_tool_ops"] = []
    state.data["_turn_reasoning"] = []
    state.data["_turn_images_generated"] = 0  # Phase 1 生图门控：每轮重置自主生图计数器

    # Q Phase 4 文宗精简档(slim,且 recorder 开):砍掉重型/动作类工具,但**保留存档级 KB
    # 维护工具**(GM_ALL_KB_TOOLS:world tree 读写 + 世界书叠加锚点 + 收束锚点)。
    # ⚠️ 不能给 tools=None —— 否则 GM 不再调 kb_* / 锚点工具,存档级 KB(kb_entities/events/
    # relationships/worldline_vars / save_worldbook_overlays / save_anchor_states)就此冻结,
    # 而史官只提取 state-JSON ops + reconcile 锚点、**不维护这些 KB 表** → 动态 KB 维护断掉。
    # 故 slim = 「文宗只保 KB 维护工具 + 史官补 state ops」,既省 145 个工具的 prompt、又不丢 KB 维护。
    # 这些 KB 工具受 dispatcher origin 闸约束(llm_chat 不能写 script 域),不会污染原始剧本。
    _gm_tools = unified_tools
    # 酒馆豁免:文宗精简会把工具收成 12 个 KB 工具,但酒馆的 GM 是唯一写者、需要自己的工具
    # (set_tavern_character / worldbook_add / 记忆·关系写 等),精简会令角色自举/记忆/世界书全断。
    # 故 tavern_gm 不走精简(保留其完整工具集);酒馆仍享 ctx_tiered 前缀缓存。
    if _narrator_slim(api_user) and _recorder_unified(api_user) and _gm_mode != "tavern_gm":
        try:
            from gm_serving.serve import GM_ALL_KB_TOOLS
            # ask_player_choice 必须保留:否则 slim 档 GM 无法弹玩家选择(用户报"选项有时不弹"
            # 的根因之一)。它是面向玩家的交互工具,不属 KB 维护但不可少。
            _keep = set(GM_ALL_KB_TOOLS) | {"ask_player_choice"}
            _kb_only = [t for t in (unified_tools or []) if t.get("name") in _keep]
        except Exception:
            _kb_only = []
        _gm_tools = _kb_only or None  # 极端无 KB 工具时退回 None(纯叙事,史官兜 state)
        yield ("agent", {"phase": "main_gm",
                         "message": f"文宗精简档:仅保留 {len(_kb_only)} 个存档级 KB 维护工具,state ops 由史官统一落库。",
                         "status": "running", "elapsed_ms": 0})

    # 流式 ops 围栏抑制:GM 按提示词在正文末尾追加 ```json ops fence,落库前清洗只处理
    # 完整文本 → 流式期间半截围栏原样漏给玩家(打出来又消失)。转发层状态机拦截,
    # response 累积不受影响(史官/落库/acceptance 仍读完整文本)。
    _fence_guard = StreamFenceGuard()

    # 流式重试+跨渠道 fallback(韧性战役):首个已提交事件(正文token/工具调用)之前的
    # upstream/ratelimit 失败先同渠道重试(≤2次退避);仍失败且 flag channel_fallback 开
    # → 切换用户自己的备用凭据渠道重新生成(严格 BYOK,每回合最多一次,fallback_notice
    # 事件告知玩家)。已提交后的失败保持原错误路径(partial 保留,防工具双重副作用)。
    from agents.gm.stream_retry import stream_with_channel_fallback as _st_fallback

    def _make_gm_stream_factory(_g):
        def _factory():
            return _g.respond_stream_with_tools(
                message_for_model, bundle["prompt"], state,
                tools=_gm_tools, max_iterations=_gm_max_iters(),
                max_tokens=_max_tokens,
                tool_call_router=gm_tool_router,
                stop_event=_gm_stop,
                prompt_segments=bundle.get("prompt_segments"),
                dynamic_prefix="\n\n".join(_dynamic_prefix_parts),
            )
        return _factory

    def _make_backup_factory(_cand_api: str, _cand_model: str):
        # 在切换点才构造备用 GameMaster(worker 线程内调用;凭据解密/构造失败会被
        # 包装器捕获并回落原错误)。usage 记账 v0 已知取舍:收尾 last_usage 读 ctx.gm
        # (主渠道),备用轮的用量在 backend 层照记、chat 行可能低估——可接受,注释备查。
        from agents.gm import GameMaster
        _bgm = GameMaster(model=_cand_model, api_id=_cand_api,
                          user_id=(api_user or {}).get("id"))
        for _attr in ("_active_state", "_immersive_mode"):
            try:
                setattr(_bgm, _attr, getattr(gm, _attr))
            except Exception:
                pass
        try:
            import model_probe
            model_probe.note_channel_failure(getattr(gm, "api_id", ""),
                                             user_id=(api_user or {}).get("id"))
        except Exception:
            pass
        ctx.fallback_note = (
            f"本回合由备用模型生成:{_cand_api}/{_cand_model}(主渠道 "
            f"{getattr(gm, 'api_id', '?')} 持续故障)"
        )
        return _make_gm_stream_factory(_bgm)

    async for event in _bridge_sync_generator_to_async(
        lambda: _st_fallback(
            _make_gm_stream_factory(gm),
            user_id=(api_user or {}).get("id"),
            primary_api_id=str(getattr(gm, "api_id", "") or ""),
            make_backup_factory=_make_backup_factory,
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
            # 保持 ctx.response 实时新鲜:断连/异常时 routes 层靠它拿到半截正文
            # (原先只在循环退出点赋值 → 中途断掉 partial 恒空,「打断即落库」无米下锅)。
            ctx.response = response
            _fence_fw = _fence_guard.feed(chunk)
            if _fence_fw:
                yield ("token", {"text": _fence_fw})
        elif etype == "retry_notice":
            # 流式重试包装器发出:上游拥堵自动重试中,给玩家可见进度别干等。
            yield ("agent", {
                "phase": "gm_retry",
                "message": (
                    f"模型服务暂时不可用({event.get('category', 'upstream')}),"
                    f"正在自动重试 {event.get('attempt')}/{event.get('max_retries')}…"
                ),
                "status": "running", "elapsed_ms": 0,
            })
        elif etype == "fallback_notice":
            # 跨渠道 fallback 包装器发出:主渠道重试耗尽,已切换玩家自己的备用凭据渠道。
            yield ("agent", {
                "phase": "gm_fallback",
                "message": (
                    f"主渠道 {event.get('from_api_id', '?')} 持续故障,"
                    f"已切换备用模型 {event.get('api_id')}/{event.get('model')},重新生成中…"
                ),
                "status": "running", "elapsed_ms": 0,
            })
        elif etype == "reasoning":
            # #7 reasoning 流式: 思考过程单独走 reasoning 事件 — 不进 token(叙事)、不累加进
            # response。但**累积进 _turn_reasoning** → record_turn 落到 assistant 历史消息,
            # 重开聊天后思考流仍可见(酒馆沉浸需求)。前端也用它显示思考流并重置 idle 计时。
            _rtext = event.get("text", "")
            yield ("reasoning", {"text": _rtext})
            try:
                state.data.setdefault("_turn_reasoning", []).append(_rtext)
            except Exception:
                pass
        elif etype == "tool_call":
            # R3/B4:小负载转发(tool 名 + args 摘要),供前端可折叠工具流;不淹没沉浸正文。
            # anchor=本工具触发时已产出的正文长度 → 前端按它把工具内联到正文对应位置(Claude 风,
            # 不再永远置顶)。len(response) 与前端累积的 content 长度一致(同一 token 流)。
            _t_args = _summarize_tool_args(event.get("arguments", {}))
            _anchor = len(response)
            yield ("tool_call", {
                "server_id": event.get("server_id", ""),
                "tool": event.get("tool", ""),
                "args_summary": _t_args,
                "anchor": _anchor,
            })
            try:
                state.data.setdefault("_turn_tool_ops", []).append({
                    "tool": event.get("tool", ""), "args": _t_args, "anchor": _anchor,
                    "ok": None, "result": None, "error": None, "_pending": True,
                })
            except Exception:
                pass
        elif etype == "tool_result":
            # R3/B4:转发 ok + result 片段 + error 摘要(裁剪,控制 SSE 体积)。
            _res_snip = _snippet_tool_result(event.get("result"))
            _err_snip = _snippet_tool_result(event.get("error"), 200) or None
            yield ("tool_result", {
                "tool": event.get("tool", ""),
                "ok": event.get("ok", False),
                "result_snippet": _res_snip,
                "error": _err_snip,
            })
            try:
                _ops = state.data.setdefault("_turn_tool_ops", [])
                _match = next((o for o in reversed(_ops) if o.get("_pending")), None)
                if _match is None:
                    _match = {"tool": event.get("tool", ""), "args": None, "_pending": False}
                    _ops.append(_match)
                _match["ok"] = bool(event.get("ok", False))
                _match["result"] = _res_snip
                _match["error"] = _err_snip
                _match["_pending"] = False
            except Exception:
                pass
            # 酒馆铁律:agent 设好角色后,开场用角色卡的 first_mes **确定性贴出** —— 绝不让 LLM
            # 现编开场(用户:不允许开局调用 llm;有 first_mes 就贴、没有就留空)。命中即丢弃本轮
            # LLM 续写(含可能的前导寒暄),以 first_mes 作本轮唯一可见输出并停掉后续生成。
            if _gm_mode == "tavern_gm" and event.get("tool") in ("set_tavern_character", "import_character_card") and event.get("ok"):
                _fm = str(((getattr(state, "data", {}) or {}).get("tavern") or {}).get("first_mes") or "").strip()
                response = _fm
                ctx.tavern_character_set = True  # first_mes 可能为空,Phase 5 不应视为 error
                if _fm:
                    yield ("token", {"text": _fm})
                _gm_stop.set()
                break
        elif etype == "tool_error":
            yield ("tool_error", {
                "error": event.get("error", ""),
                "raw": event.get("raw", ""),
            })
        await asyncio.sleep(0)

    _fence_tail = _fence_guard.flush()
    if _fence_tail:
        yield ("token", {"text": _fence_tail})

    ctx.response = response

    # acceptance 硬闸。
    # 【设计改版 · A/B 用户裁决 + 下线关键路径】
    #   ① 首稿(用户流式读到的)【永远是权威版】,response/state 都不动 → 无跳变、state 确定性不变。
    #   ② verify + audit + 节流决策【内联】(默认 rule 模式=确定性、极快,不阻塞)。
    #   ③ 改写候选的【第二次 GM 调用】绝不在回合关键路径同步跑 —— 那是行者无疆报的严重问题:
    #      正文流完后还要等一次完整 GM 生成(可 2-3 分钟 / 503 / 超时),期间 SSE 无事件 → 前端
    #      不活跃超时 →「生成失败,连接超时」,整回合被拖垮(即便首稿早已生成)。
    #      改为:async 生产路径把改写 fire-and-forget 丢后台任务(不阻塞 done),候选生成后经
    #      state_event_bus.emit(`acceptance_alt`)跨 worker 推给前端(前端长连 /state_events 收);
    #      回合本身立刻收尾。这也恢复了 W1 容量意图(回合 slot 不被第二次 LLM 占住)。
    #   ④ sync(测试/debug)路径保留内联,候选走 SSE 流事件(便于确定性测试)。
    #   逃生开关 RPG_ACCEPTANCE_RETRY=0 关掉候选生成。全程 try/except = 任何失败退回首稿。
    def _rewrite_candidate_text(_pre_hist, _player_action, _orig_clean, _unmet):
        """产出改写候选【文本】(A/B 对比用;不落 ops —— 首稿永远权威)。

        BUGFIX(行者无疆:『改写改到下一段去了』——原版末尾『传来一声尖叫』、改版开头顺着尖叫往下写):
        旧实现用 respond_stream_with_tools 追加一条 user 消息到【当前】state.history 之上,而 Phase 5
        record_turn 已把[玩家行动 + 首稿]写进 history → 模型把改写指令当成新回合、【续写】首稿末尾,
        而不是重写本轮。根治:用【首稿生成时的历史快照】(_pre_hist,不含首稿)+ 把玩家行动与首稿一并
        塞进【这一条改写指令】里,文本直调 backend,明确要求「改写替换、不是续写」。"""
        _rw_user = (
            (bundle.get("prompt") or "")
            + "\n\n【系统:改写请求 —— 是改写替换,不是续写】\n"
            + "下面给出玩家【本轮】的行动、以及你已经写好的【这一版】回应。请【重写这一版】,产出一个可以\n"
            + "【整段替换】它的完整新版本:同样承接玩家这次的行动、停在同样的剧情位置与时间点,\n"
            + "【不要接着往下写后续情节、不要顺着上一版的末尾继续】,只把漏掉的验收点自然地补进这一版里。\n\n"
            + "【玩家本轮行动】\n" + (str(_player_action or "").strip() or "(见上文对话)") + "\n\n"
            + "【你的上一版回应(待改写的对象)】\n" + (_orig_clean or "") + "\n\n"
            + "【上一版漏掉的验收点】\n" + "\n".join(f"  - {x}" for x in (_unmet or [])[:5]) + "\n\n"
            + "现在直接输出【改写后的完整正文】(整段替换上一版;不要解释、不要接着写之后发生的事):"
        )
        _msgs = list(_pre_hist or []) + [{"role": "user", "content": _rw_user}]
        try:
            gm._active_state = state  # _build_system 读它;文本直调不进工具循环、不改真状态
        except Exception:
            pass
        _parts = []
        for _chunk in gm._backend.stream(gm._build_system(), _msgs, max_tokens=_max_tokens):
            _parts.append(_chunk)
        return "".join(_parts).strip()

    async def _gen_candidate_bg(_resp_snapshot, _unmet, _turn_now, _save_id, _auid, _pre_hist, _player_action):
        """后台改写候选:文本直调 GM 拿第二稿(走 to_thread 不塞事件循环)→ 落 acceptance_ab_log →
        emit `acceptance_alt` 推前端。绝不阻塞回合;失败只记日志。首稿永远权威,这里只产候选。
        用【首稿时的历史快照 _pre_hist + 玩家行动】重建上下文,杜绝续写(见 _rewrite_candidate_text)。"""
        try:
            _orig_clean = strip_leaked_scaffold(strip_meta_tool_preamble(strip_json_state_ops(_resp_snapshot))).strip()

            def _run_gm():
                _raw = _rewrite_candidate_text(_pre_hist, _player_action, _orig_clean, _unmet)
                return strip_leaked_scaffold(strip_meta_tool_preamble(strip_json_state_ops(_raw))).strip()

            _r2 = await asyncio.to_thread(_run_gm)
            if _r2 and _r2 != _orig_clean:
                _alt_id = await asyncio.to_thread(
                    _log_acceptance_ab, _auid, _save_id, _turn_now, _unmet[:5], _orig_clean, _r2)
                if _alt_id and _auid:
                    # 权威 message_index:此刻 record_turn 已落库,按首稿全文内容匹配算展示序 index,随事件
                    # 下发前端(面板 original + 乐观替换 + 选择都用它),不靠前端「最后一条 assistant」启发式
                    # —— 异步候选到达时该启发式会指到相邻回合(行者无疆:改写改到前一个回合)。
                    def _compute_idx():
                        try:
                            from platform_app.db import connect as _c
                            from routes.game import _resolve_message_index_by_content as _rmi
                            with _c() as _db:
                                return _rmi(_db, int(_save_id), _orig_clean, role="assistant")
                        except Exception:
                            return None
                    _msg_idx = await asyncio.to_thread(_compute_idx)
                    from state_event_bus import emit as _emit
                    _emit(int(_auid), "acceptance_alt", "ready", {
                        "save_id": int(_save_id or 0), "alt_id": int(_alt_id),
                        "turn": int(_turn_now), "rewrite": _r2, "unmet": _unmet[:5],
                        "message_index": _msg_idx})
        except Exception as _bg:
            log.warning(f"[acceptance] 后台改写候选失败(仅首稿,已记 audit): {_bg}")

    def _acceptance_gate(_resp, _upd, *, inline: bool):
        _events = []
        try:
            _cur_plan = (agent_result or {}).get("curator_plan", {}) or {}
            _acc = _cur_plan.get("acceptance") or []
            if not (_acc and (_resp or "").strip()):
                return _resp, _upd, _events
            import os as _os2
            _rewrite_on = _os2.environ.get("RPG_ACCEPTANCE_RETRY", "1") not in ("0", "false", "False", "")
            _amode = acceptance_verifier_mode(api_user)
            _auid = int(api_user.get("id")) if api_user and api_user.get("id") else None
            unmet = verify_acceptance(_acc, _resp, _upd, mode=_amode, user_id=_auid)
            # 节流:每存档最多每 _ACCEPTANCE_AB_MIN_INTERVAL 回合提供一次改写候选。
            _turn_now = int(state.data.get("turn", 0) or 0)
            _save_id = ctx.early_active_save_id or ctx.active_save_id or 0
            _ab_meta = state.data.setdefault("_acceptance_ab", {})
            _last_offer = int(_ab_meta.get("last_offer_turn", -(10 ** 9)))
            _throttle_ok = (_turn_now - _last_offer) >= _ACCEPTANCE_AB_MIN_INTERVAL
            rewrite_offered = False
            # 用户级开关(游戏设置可手动关):关了就不生成候选(节流通过也不弹)。
            if unmet and _rewrite_on and _throttle_ok and _acceptance_ab_pref_enabled(_auid):
                rewrite_offered = True
                _ab_meta["last_offer_turn"] = _turn_now  # 节流消费(自 Phase 5 落盘)
                if inline:
                    # sync/测试路径:内联重写,候选走 SSE 流事件。用当前历史快照(此刻尚未 record_turn)
                    # + 玩家行动重建改写上下文,不再追加 user 消息到含首稿的历史之上(杜绝续写)。
                    try:
                        _orig_clean = strip_leaked_scaffold(strip_meta_tool_preamble(strip_json_state_ops(_resp))).strip()
                        _r2 = strip_leaked_scaffold(strip_meta_tool_preamble(strip_json_state_ops(
                            _rewrite_candidate_text(list(state.history_messages()), ctx.message_for_model, _orig_clean, unmet)))).strip()
                        if _r2 and _r2 != _orig_clean:
                            _alt_id = _log_acceptance_ab(_auid, _save_id, _turn_now, unmet[:5], _orig_clean, _r2)
                            if _alt_id:
                                _events.append(("acceptance_alt", {
                                    "alt_id": _alt_id, "turn": _turn_now, "rewrite": _r2, "unmet": unmet[:5]}))
                    except Exception as _re:
                        log.warning(f"[acceptance] inline rewrite candidate failed: {_re}")
                else:
                    # async 生产路径:改写丢后台,不阻塞回合;候选生成后 emit 推前端。
                    # 此刻在 Phase 5 record_turn 之前,state.history 尚不含本轮[玩家行动+首稿] —— 快照它 +
                    # 玩家行动一并交给后台任务重建改写上下文(后台任务运行时 history 已被 record_turn 污染)。
                    try:
                        _t = asyncio.get_running_loop().create_task(
                            _gen_candidate_bg(_resp, list(unmet), _turn_now, _save_id, _auid,
                                              list(state.history_messages()), ctx.message_for_model))
                        _ACCEPTANCE_BG_TASKS.add(_t)
                        _t.add_done_callback(_ACCEPTANCE_BG_TASKS.discard)
                    except Exception as _sp:
                        log.warning(f"[acceptance] 后台候选任务启动失败: {_sp}")
            if unmet:
                from datetime import datetime as _dt
                audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                for item in unmet[:5]:
                    audit.append({"ts": _dt.now().isoformat(timespec="seconds"),
                        "kind": "acceptance_unmet", "source": "curator:acceptance",
                        "rewrite_offered": rewrite_offered, "hint": f"未通过验收：{item[:160]}",
                        "turn": _turn_now})
                if len(audit) > 200:
                    state.data["permissions"]["audit_log"] = audit[-200:]
                _events.append(("agent", {"phase": "acceptance_check",
                    "message": (f"本轮 GM 输出有 {len(unmet)} 条 acceptance 未通过"
                        + ("(已生成改写候选供选择)" if rewrite_offered
                           else "(本轮不提供候选:节流/关闭,已记 audit_log)")),
                    "status": "warning", "elapsed_ms": 0, "unmet": unmet[:5]}))
        except Exception as _acc_exc:
            log.warning(f"[acceptance] gate failed: {_acc_exc}")
        return _resp, _upd, _events

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
            # 统一确定性叙事纠错(时间跳跃禁词 / 套路比喻 / 星期算错 / 未来的):一个入口跑全部,
            # 与 sync 路径共用同一个 run_narrative_guards,消除「每种检测在两路各手写一遍」的散落。
            try:
                from agents.timeline_narrative_guard import run_narrative_guards
                for _guard_ev in run_narrative_guards(response, ctx.message_for_model, state):
                    yield _guard_ev
            except Exception as _g_err:
                log.warning(f"[chat] async narrative_guards 跳过: {_g_err}")

            # 世界心跳(柱子1,docs/design/world_heartbeat_v0.md):与史官三合一【并行】跑,
            # 独立便宜 LLM 调用,只写 state 专属键(background_events/heartbeat_meta),
            # 两个 return 前 await,Phase 5 统一持久化。⚠️接线必须在这条【async 生产默认
            # 路径】—— v1.41.0 曾错接进 sync-only 的 _run_post_gm_parallel 导致生产永不
            # 触发(灰度双路径老坑);sync 路径的接线保留作 parity。
            _hb_task = None
            if _gm_mode != "tavern_gm":
                try:
                    from agents.world_heartbeat import run_heartbeat_tick as _hb_run
                    from agents.world_heartbeat import should_tick as _hb_should
                    _hb_uid = _uid_of(api_user)
                    if _hb_should(state.data, _hb_uid):
                        _hb_task = asyncio.create_task(asyncio.to_thread(_hb_run, state, _hb_uid))
                except Exception as _hb_err:
                    log.warning(f"[chat] world_heartbeat 启动失败,跳过: {_hb_err}")

            # Q Phase 2 史官三合一(flag on):一次 recorder LLM 调用同时产 ops + 锚点判定,
            # 替代「独立 extractor + 独立 anchor_reconcile LLM」两次调用。off 时走原路径。
            # 酒馆豁免:tavern_gm 的 GM 已用自己的工具写状态(slim 已豁免、工具齐全),史官三合一对酒馆
            # 实测不增 KB(关系/事实同结果)却多一次 LLM,且酒馆无锚点 → 走原 apply_gm_json_ops 即可。
            if _recorder_unified(api_user) and _gm_mode != "tavern_gm":
                _ru_sid = ctx.early_active_save_id or 0
                _ru_uid = int(api_user["id"]) if api_user and api_user.get("id") else 0
                try:
                    from gm_serving.recorder_bridge import run_unified_recorder
                    _ru = await asyncio.to_thread(
                        run_unified_recorder, state, response,
                        _ru_sid or None, _ru_uid or None,
                        acceptance_clauses=None, tasks=["ops", "anchors"],
                    )
                except Exception as _ru_err:
                    log.warning(f"[chat] 史官三合一失败,退回原 async 后处理: {_ru_err}")
                    _ru = None
                if _ru is not None:
                    # recorder(史官)给出的 ops 作为权威提取,拼回 response 走 JSON op 确定性 apply;
                    # 空 ops 也只是本回合无结构化写入(GM 本就没标),不再有「正文关键词 regex 兜底」。
                    _ru_ops = _ru.get("ops") or []
                    # 双源头修复:GM 正文可能已自带 json fence(提示词要求它写),史官 ops 再
                    # 追加一份 → 同 op 双 apply(updates 双报;set 幂等暂无害,add 类是真损坏)。
                    # 史官有产出时它是唯一权威 → 剥掉 GM 自带 fence 只留正文;史官空产出时
                    # 保留 GM fence 作为唯一兜底来源(行为不变)。
                    _resp_ops = (
                        strip_json_state_ops(response) + "\n\n```json\n" + json.dumps(_ru_ops, ensure_ascii=False) + "\n```"
                    ) if _ru_ops else response
                    try:
                        ctx._updates = _apply_gm_json_ops(
                            state=state, response_with_ops=_resp_ops, api_user=api_user,
                            active_script_id=active_script_id, ctx=ctx,
                        )
                    except Exception as _apply_err:
                        log.warning(f"[chat] 史官 ops apply 失败,退回 directive_updates: {_apply_err}")
                        ctx._updates = ctx.directive_updates[:]
                    _rec_marked = int(_ru.get("anchors_marked") or 0)
                    if _rec_marked:
                        yield ("agent", {
                            "phase": "anchor_reconcile",
                            "message": f"世界线锚点确定性兜底(史官):本回合自动标记 {_rec_marked} 个原著锚点已到达",
                            "status": "done", "elapsed_ms": 0, "marked": _rec_marked,
                        })
                    # acceptance:内联 verify+audit+节流(rule 模式确定性极快),改写候选丢后台任务
                    # (不阻塞 done;候选 emit 推前端)。直接调(非 to_thread)以便 create_task 拿到运行 loop。
                    response, ctx._updates, _acc_events = _acceptance_gate(
                        response, ctx._updates, inline=False)
                    ctx.response = response
                    for _ac, _ap in _acc_events:
                        yield (_ac, _ap)
                    if _hb_task is not None:
                        try:
                            await _hb_task  # 心跳写完 state 再进 Phase 5 持久化
                        except Exception as _hb_err:
                            log.warning(f"[chat] world_heartbeat 等待失败: {_hb_err}")
                    return
            # 关键修复:GM JSON op 确定性写回(async 早退路径也必须 apply,否则 GM 经
            # JSON op 写的 location/time/resources/quest/relationships/选项 全部丢失)。
            try:
                ctx._updates = _apply_gm_json_ops(
                    state=state,
                    response_with_ops=response,
                    api_user=api_user,
                    active_script_id=active_script_id,
                    ctx=ctx,
                )
            except Exception as _apply_err:
                log.warning(f"[chat] async apply_structured_updates 失败,退回 directive_updates: {_apply_err}")
                ctx._updates = ctx.directive_updates[:]
            # acceptance:内联 verify+audit+节流,改写候选丢后台(不阻塞;emit 推前端)。直接调以拿 loop。
            response, ctx._updates, _acc_events = _acceptance_gate(
                response, ctx._updates, inline=False)
            ctx.response = response
            for _ac, _ap in _acc_events:
                yield (_ac, _ap)
            # 每回合确定性锚点兜底(GM 自调工具 + JSON op 已 apply,已 occurred 不在 pending)。
            _rec_marked = await _run_anchor_reconcile(ctx, api_user, response)
            if _rec_marked:
                yield ("agent", {
                    "phase": "anchor_reconcile",
                    "message": f"世界线锚点确定性兜底:本回合自动标记 {_rec_marked} 个原著锚点已到达",
                    "status": "done", "elapsed_ms": 0, "marked": _rec_marked,
                })
            if _hb_task is not None:
                try:
                    await _hb_task  # 心跳写完 state 再进 Phase 5 持久化
                except Exception as _hb_err:
                    log.warning(f"[chat] world_heartbeat 等待失败: {_hb_err}")
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

    # 统一确定性叙事纠错(时间跳跃禁词 / 套路比喻 / 星期算错 / 未来的):与 async 路径共用同一个
    # run_narrative_guards(消除散落)。按固定顺序 yield,保前端时间线稳定。
    try:
        from agents.timeline_narrative_guard import run_narrative_guards
        for _guard_ev in run_narrative_guards(response, ctx.message_for_model, state):
            yield _guard_ev
    except Exception as _g_err:
        log.warning(f"[chat] sync narrative_guards 跳过: {_g_err}")

    response_with_ops = _post_results.get("response_with_ops") or response

    # task 87 Phase 6: 经 ChatWriteContext 把 GM JSON op 确定性 apply 回内存 state
    # (apply_state_write_typed 拿到 user/save/trace → dispatcher 工具调用)。
    # 与 async 早退路径共用 _apply_gm_json_ops,避免两处逻辑漂移。
    updates = _apply_gm_json_ops(
        state=state,
        response_with_ops=response_with_ops,
        api_user=api_user,
        active_script_id=active_script_id,
        ctx=ctx,
    )

    # sync 路径(测试/debug):内联跑改写候选,走 SSE 流事件(便于确定性测试)。生产走 async(候选丢后台)。
    response, updates, _acc_events = _acceptance_gate(response, updates, inline=True)
    for _ac, _ap in _acc_events:
        yield (_ac, _ap)

    # 把 updates 写到 ctx 留给 phase 5
    ctx.response = response
    # 用 ctx.__dict__ 也行,这里直接挂属性
    ctx._updates = updates

    # 每回合确定性锚点兜底(放在 GM 工具 / JSON op apply / acceptance retry 之后跑,
    # 用最终 response;GM 自调过的锚点已 occurred 不在 pending,天然不重复)。
    _rec_marked = await _run_anchor_reconcile(ctx, api_user, response)
    if _rec_marked:
        yield ("agent", {
            "phase": "anchor_reconcile",
            "message": f"世界线锚点确定性兜底:本回合自动标记 {_rec_marked} 个原著锚点已到达",
            "status": "done", "elapsed_ms": 0, "marked": _rec_marked,
        })
