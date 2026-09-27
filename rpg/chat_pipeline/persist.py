"""Phase 5:落档 record_turn + save + DB + done。拆包自 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from state import strip_json_state_ops, strip_leaked_scaffold, strip_meta_tool_preamble

from ._common import (
    _TOOL_MARKUP_FINISH_REASONS,
    PipelineContext,
    SSEEvent,
    _gm_mode_of,
    _is_content_filter_reason,
    _last_usage_of,
    _norm_finish_reason,
    log,
)

# 空回合兜底文案:分诊本身出异常时才用(分诊是纯函数,正常走不到)。
_EMPTY_FALLBACK_MESSAGE = "这一轮模型没有写出正文,请重试。"


def _as_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _empty_turn_diagnosis(
    ctx: PipelineContext, raw_len: int, *, regex_emptied: bool = False,
) -> tuple[str, str, dict[str, Any]]:
    """空回合(清洗后没有正文)的确定性分诊 → (reason, 给玩家的文案, 证据 facts)。

    只读现成的确定性信号,不猜正文:
      · ctx.gm._backend.last_usage 的 finish_reason / output_tokens / reasoning_tokens / max_tokens
        (与 _stop_reason_notice 共用 _last_usage_of;每回合入口已清零,读到的是本回合的)
      · state.data 的 _turn_reasoning(思考流)/ _turn_tool_ops(工具调用),本回合 Phase 4 累积
      · ctx.turn_tool_errors(工具调用标记解析失败 / DSML 解析出 0 个调用)
    regex_emptied:清洗到输出正则之前还有正文、被玩家自己的输出正则整段换空了(stripped_to_empty 的
    特例,单列 output_regex_emptied —— 这种情况重试没用,得去改正则)。
    优先级自上而下,先命中先返回:
      output_regex_emptied > stripped_to_empty > reasoning_exhausted > length > content_filter > tool_markup_unparsed
      > tool_only > reasoning_only > upstream_empty
    无工具路径(stream())不发思考事件、部分服务也不回传 reasoning_tokens,所以 length 且看不到
    思考信号时文案仍点明「多半是思考占满」。
    """
    lu = _last_usage_of(ctx)
    state_data = getattr(getattr(ctx, "state", None), "data", None) or {}
    try:
        reasoning_chars = sum(len(str(x or "")) for x in (state_data.get("_turn_reasoning") or []))
    except Exception:
        reasoning_chars = 0
    try:
        tool_count = len(state_data.get("_turn_tool_ops") or [])
    except Exception:
        tool_count = 0
    tool_errors = len(getattr(ctx, "turn_tool_errors", None) or [])
    fr = _norm_finish_reason(lu.get("finish_reason"))
    out_tokens = _as_int(lu.get("output_tokens"))
    reasoning_tokens = _as_int(lu.get("reasoning_tokens"))
    max_tokens = _as_int(lu.get("max_tokens")) or _as_int(getattr(ctx, "gm_max_tokens", 0))
    try:
        gm = getattr(ctx, "gm", None)
    except Exception:
        gm = None
    facts: dict[str, Any] = {
        "api_id": str(getattr(gm, "api_id", "") or ""),
        "model": str(getattr(getattr(gm, "_backend", None), "model_name", "") or ""),
        "finish_reason": str(lu.get("finish_reason") or ""),
        "out_tokens": out_tokens,
        "reasoning_tokens": reasoning_tokens,
        "max_tokens": max_tokens,
        "reasoning_chars": reasoning_chars,
        "tool_count": tool_count,
        "tool_errors": tool_errors,
        "raw_len": int(raw_len or 0),
    }
    has_reasoning = reasoning_tokens > 0 or reasoning_chars > 0
    cap = f"(上限 {max_tokens} tokens)" if max_tokens else ""
    # 不引用具体控件名:设置页叫「最大生成 token」、酒馆参数抽屉叫「最大输出 Tokens」,两处都在「模型参数」下。
    raise_cap = "可以在「模型参数」里调高最大输出 token 数,或把推理强度调低、换一个不带思考的模型,然后重试。"

    if raw_len > 0 and regex_emptied:
        return ("output_regex_emptied",
                "模型写了正文,但被你启用的输出正则脚本整段替换成了空内容。重试也会这样,"
                "请检查一下正则脚本的查找和替换规则。", facts)
    if raw_len > 0:
        return ("stripped_to_empty",
                "模型这一轮只输出了状态指令或内部标记,没有写正文。直接重试一般就好。", facts)
    if fr == "length" and has_reasoning:
        used = f"(思考约 {reasoning_tokens} tokens)" if reasoning_tokens else ""
        return ("reasoning_exhausted",
                f"模型把这一轮的输出额度{cap}全用在了思考上{used},正文还没开始写就到顶了。" + raise_cap,
                facts)
    if fr == "length":
        return ("length",
                f"模型用满了这一轮的输出额度{cap},却一个字的正文都没写出来,多半是思考过程占满了额度"
                "(有些服务不回传思考内容,所以这里看不到)。" + raise_cap,
                facts)
    if _is_content_filter_reason(fr):
        return ("content_filter",
                "这一轮被所用模型的内容策略拦下了,没有生成正文。可以换个说法重述,"
                "或在设置的「模型」页换一个对该题材更宽松的模型。", facts)
    if tool_errors > 0 or fr in _TOOL_MARKUP_FINISH_REASONS:
        return ("tool_markup_unparsed",
                "模型想调用工具,但写出来的调用格式解析不了,这一轮没能写出正文。"
                "可以直接重试;经常这样的话,换个模型或渠道试试。", facts)
    if tool_count > 0:
        return ("tool_only",
                f"模型这一轮只调用了工具({tool_count} 次),没有接着写正文。可以直接重试。", facts)
    if has_reasoning:
        return ("reasoning_only",
                "模型这一轮只输出了思考过程,没有写正文。可以直接重试;经常这样的话,"
                "在设置的「模型参数」里把推理强度调低,或换个模型。", facts)
    return ("upstream_empty",
            "模型服务这一轮返回了空内容:没有正文,没有思考,也没有调用工具。多半是中转站或服务端"
            "临时出了问题,请重试;反复出现的话换个渠道。", facts)


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
    _before_output_regex = visible_response
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
            # 确定性分诊:此前这里是写死的「可能触发了安全过滤 / 上下文出错,换个说法」,而最常见的
            # 成因(思考模型把单轮输出上限吃光)换说法根本没用;现场信号也一条没留。
            try:
                # raw_len 按去空白后算:原始输出只有空白 = 上游确实没给东西,不该判成「清洗后为空」。
                _reason, _msg, _facts = _empty_turn_diagnosis(
                    ctx, len((response or "").strip()),
                    regex_emptied=bool((_before_output_regex or "").strip()),
                )
            except Exception:
                _reason, _msg, _facts = "unknown", _EMPTY_FALLBACK_MESSAGE, {}
            _mode = _gm_mode_of(state)
            _pid = ctx.persist_user_id or ctx.early_persist_user_id
            _sid = ctx.active_save_id or ctx.early_active_save_id
            log.warning(
                "[chat] WARN: GM 返回空响应 reason=%s api_id=%s model=%s finish_reason=%s out_tokens=%s "
                "reasoning_tokens=%s max_tokens=%s reasoning_chars=%s tools=%s tool_errors=%s mode=%s "
                "len(raw)=%s user_msg='%s' save_id=%s",
                _reason, _facts.get("api_id"), _facts.get("model"), _facts.get("finish_reason"),
                _facts.get("out_tokens"), _facts.get("reasoning_tokens"), _facts.get("max_tokens"),
                _facts.get("reasoning_chars"), _facts.get("tool_count"), _facts.get("tool_errors"),
                _mode or "?", len(response or ""), message_for_model[:80], _sid,
            )
            # 证据落库:空回合也实际花了玩家的输入 + 思考 token。写一条 chat token_usage,metadata 带
            # empty_response/reason,生产上能直接按 reason 统计成因分布。只在游戏台发 usage SSE(footer
            # 显示本轮用量);酒馆页把 usage 挂在「最后一条消息」下面,空回合那条是上一回合的 GM 回复,
            # 会把这轮的用量错挂过去,所以酒馆只落库不发。
            _usage = None
            try:
                _usage = build_usage_payload(
                    api_user, gm, bundle, message_for_model, _pid, _sid, ctx.context_run_id,
                    extra_metadata={"empty_response": True, "reason": _reason},
                )
            except Exception:
                _usage = None
            if _usage and _mode != "tavern_gm":
                yield ("usage", _usage)
            yield ("error", {"message": _msg, "kind": "empty_response", "reason": _reason})
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
