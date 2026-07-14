"""Phase 5:落档 record_turn + save + DB + done。拆包自 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from state import strip_json_state_ops, strip_leaked_scaffold, strip_meta_tool_preamble

from ._common import PipelineContext, SSEEvent, log


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
    # 确定性兜底(反馈 #77):弱模型把检索/世界线脚手架块(=== 时间线检索锚点 === 等)+ 内部推理
    # 直接吐进正文 → 整块剥掉。这些 header 是后端注入的隐形上下文,正常叙事永不产出,零误伤。
    visible_response = strip_leaked_scaffold(visible_response)

    # 确定性玩家选项兜底(用户反馈"选项有时不弹"):整个选择机制原本只在 GM 主动调 ask_player_choice
    # 时才弹 —— GM 常把选项直接写进正文 markdown 列表却不调工具 → 前端无 chips。这里【确定性】解析
    # 正文结尾的选项列表(≥2 项),把它移出正文、合成一个 pending_question 走选择组件。不靠 GM 听话。
    # 仅当本回合 GM 没有已给出结构化选择(避免重复)时才兜底;过期清理已在回合开头跑过,故 pending
    # 里只剩本回合的。放在沉浸感剥句之前:先把列表抽走,残留的"你想怎么做?"问句再被下面的剥句清掉。
    _auto_choice_opts: list[str] = []
    try:
        from state.parsers import _extract_trailing_markdown_options
        _existing_pqs = ((state.data.get("permissions") or {}).get("pending_questions") or [])
        _has_choice = any((q.get("options") or q.get("choices")) for q in _existing_pqs)
        if not _has_choice:
            _body, _opts = _extract_trailing_markdown_options(visible_response)
            if len(_opts) >= 2:
                visible_response = _body
                _auto_choice_opts = _opts
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
                _ll.pop(); continue
            _in_quote = any(c in _last for c in _quote_chars)
            if (not _in_quote) and (_q_pat.search(_last) or _plead_pat.search(_last)) and len(_last) <= 60:
                _ll.pop(); _changed = True; continue
            break
        if _changed:
            _new = "\n".join(_ll).rstrip()
            if _new:  # 不要把整段删空(防极端情况)
                visible_response = _new
    except Exception:
        pass

    # 落实上面确定性解析出的玩家选项(列表已移出正文)→ 合成选择组件。source 用 "gm:" 前缀,
    # 使其与开场的 gm:opening_options 一样被 expire_stale_gm_questions 视为系统来源、下回合自动清理
    # (system_sources 含 "gm";"auto" 不在其中会导致 chips 永不过期变残留)。
    if _auto_choice_opts:
        try:
            state.add_pending_question("你想怎么做?", source="gm:auto_choice", options=_auto_choice_opts)
        except Exception:
            pass

    # 反馈#93:用户自定义输出正则(SillyTavern regex,输出/显示作用域)—— 对清洗后的可见正文做确定性
    # find/replace。安全在 state.regex_scripts 内(每条脚本线程超时 + try/except,异常/超时跳过,绝不断轮)。
    try:
        from state.regex_scripts import apply_output_regex
        _rx_uid = int(api_user.get("id")) if api_user and api_user.get("id") else 0
        if _rx_uid:
            visible_response = apply_output_regex(visible_response, _rx_uid)
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
        elif ctx.tavern_character_set:
            # 酒馆角色卡工具成功但 first_mes 为空 — 正常干净结束,不报 error
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
    # 渠道健康门控(韧性战役):本回合走到这里 = GM 主响应流式成功完成,清零该
    # (user_id, api_id) 的被动失败计数,别让早前的暂时性 502/限流继续把渠道钉在 degraded。
    try:
        import model_probe
        model_probe.note_channel_success(
            getattr(gm, "api_id", ""), user_id=(api_user or {}).get("id"),
        )
    except Exception:
        pass
    usage_payload = build_usage_payload(
        api_user, gm, bundle, message_for_model,
        ctx.persist_user_id, ctx.active_save_id, ctx.context_run_id,
    )
    if usage_payload:
        yield ("usage", usage_payload)
    # 跨渠道 fallback 发生过 → 玩家必须知情(模型质量可能有差异),附进本回合 updates。
    if getattr(ctx, "fallback_note", ""):
        updates = list(updates or []) + [str(ctx.fallback_note)]
    yield ("updates", {"items": updates})
    yield ("done", {"status": payload_fn(api_user), "interrupted": False, "usage": usage_payload})
