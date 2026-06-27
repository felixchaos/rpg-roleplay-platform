"""console_assistant.py — 侧栏控制台助手路由 (/api/console_assistant/*)。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from routes._deps_fastapi import get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, GenericOkResponse, OkResponse
from schemas.console_assistant import (
    ConsoleAssistantChatRequest,
    ConsoleAssistantConfirmRequest,
    ConsoleAssistantContinueRequest,
    ConsoleAssistantDeleteConversationRequest,
)

router = APIRouter()


@router.get("/api/console_assistant/ping")
async def api_console_assistant_ping() -> JSONResponse:
    """task 48: 给前端探测后端是否就绪,200 = 真后端可用 (前端切走 mock)。"""
    return JSONResponse({"ok": True, "service": "console_assistant", "version": "1"})


@router.get("/api/console_assistant/conversations")
async def api_console_assistant_conversations(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 111: 列当前用户所有对话。"""
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return JSONResponse({"items": []})
    from console_assistant import list_conversations
    from console_assistant.conversations import list_conversations_pg
    items = list_conversations(user_id)
    seen = {it.get("id") for it in items}
    # 进程内 dict 在 worker 重启后为空 → 并入 PG 持久化的对话(刷新/换 worker 也能列出历史会话)。
    for it in list_conversations_pg(user_id):
        if it.get("id") not in seen:
            items.append(it)
            seen.add(it.get("id"))
    items.sort(key=lambda r: r.get("last_used", ""), reverse=True)
    return JSONResponse({"items": items})


@router.get("/api/console_assistant/conversations/{conversation_id}/messages")
async def api_console_assistant_conversation_messages(
    conversation_id: str,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """取某对话的可渲染历史(用于刷新后还原侧栏 agent 会话)。

    conv['messages'] 是 LLM 格式 [{role,content}],其中工具结果以 assistant 消息 `[tool …]`
    的形式夹在中间(给模型看的)。这里还原成前端可渲染的简洁会话:只保留用户消息 + 每轮最终回答,
    跳过工具结果中间态(避免一堆裸 tool 文本)。Redis 兜底,跨 worker / 重启(6h 内)可还原。"""
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return JSONResponse({"ok": False, "error": "需要登录"}, status_code=401)
    cid = str(conversation_id or "").strip()
    if not cid:
        return JSONResponse({"ok": False, "error": "conversation_id 必填"}, status_code=400)
    from console_assistant.conversations import _get_or_create_conversation
    _cid, conv = _get_or_create_conversation(user_id, cid)
    # 优先用 ui_turns(含每轮工具调用:名/参数/状态/结果)→ 刷新后工具历史不丢、可还原折叠块。
    ui_turns = conv.get("ui_turns")
    if isinstance(ui_turns, list) and ui_turns:
        out = []
        for tnu in ui_turns:
            if not isinstance(tnu, dict):
                continue
            role = tnu.get("role") or "assistant"
            text = str(tnu.get("text") or "")
            tools = tnu.get("tools") if isinstance(tnu.get("tools"), list) else []
            if not text.strip() and not tools:
                continue
            out.append({"role": role, "text": text, "tools": tools})
        return JSONResponse({"ok": True, "conversation_id": _cid, "messages": out})
    # 兜底(旧对话无 ui_turns):仅文本还原。
    out = []
    for m in (conv.get("messages") or []):
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "assistant" and content.startswith("[tool "):
            continue  # 工具结果中间态,不回灌
        if not content.strip():
            continue
        out.append({"role": role, "text": content, "tools": []})
    return JSONResponse({"ok": True, "conversation_id": _cid, "messages": out})


@router.post("/api/console_assistant/new_conversation", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_console_assistant_new_conversation(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 111: 开新对话, 返新 conversation_id。"""
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return JSONResponse({"ok": False, "error": "需要登录"}, status_code=401)
    from console_assistant import new_conversation
    new_id = new_conversation(user_id)
    return JSONResponse({"ok": True, "conversation_id": new_id})


@router.post("/api/console_assistant/delete_conversation", response_model=OkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_console_assistant_delete_conversation(
    body: ConsoleAssistantDeleteConversationRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 111: 删除某对话。"""
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return JSONResponse({"ok": False, "error": "需要登录"}, status_code=401)
    body_dict = body.model_dump(exclude_none=True)
    cid = str(body_dict.get("conversation_id") or "").strip()
    if not cid:
        return JSONResponse({"ok": False, "error": "conversation_id 必填"}, status_code=400)
    from console_assistant import delete_conversation
    ok = delete_conversation(user_id, cid)
    return JSONResponse({"ok": ok})


@router.post("/api/console_assistant/chat")
async def api_console_assistant_chat(
    body: ConsoleAssistantChatRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    """task 48: 侧栏助手主聊天 SSE endpoint。

    body: { message: str, conversation_id?: str, page_context?: dict }
    SSE: meta / token / tool_call / tool_result / confirmation_required / error / done
    """
    from app import _ensure_loaded, _resolve_console_assistant_backend
    body_dict = body.model_dump(exclude_none=True)
    message = str(body_dict.get("message") or "").strip()
    conversation_id = body_dict.get("conversation_id")
    if isinstance(conversation_id, str):
        conversation_id = conversation_id.strip() or None
    else:
        conversation_id = None
    page_context = body_dict.get("page_context") if isinstance(body_dict.get("page_context"), dict) else None

    if not message:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'空消息'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
        )

    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'需要登录'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
        )

    # 注意:这里 state_provider 用 _ensure_loaded — 只有 save scope 工具用得到。
    def _sp(env):
        try:
            if env.save_id is None:
                return None
            return _ensure_loaded(api_user)
        except Exception:
            return None

    # 解析 backend
    try:
        backend = _resolve_console_assistant_backend(api_user)
    except Exception as exc:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':f'backend 初始化失败: {exc}'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
        )

    from console_assistant import stream_chat as _stream_chat
    # 接入站点统一模型参数(与 GM 一致):max_tokens 走用户 settings.max_tokens 偏好,不再写死 1200。
    try:
        from app import _chat_max_tokens
        _mt = _chat_max_tokens(api_user)
    except Exception:
        _mt = 1200

    def _gen():
        yield from _stream_chat(
            user_id=user_id,
            message=message,
            conversation_id=conversation_id,
            page_context=page_context,
            backend=backend,
            state_provider=_sp,
            max_tokens=_mt,
        )

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _editor_panel_model(user_id: int) -> tuple[str | None, str | None]:
    """续写 / 改写 / 内联补全默认跟随「编辑器 AI 面板」选的模型
    (user_preferences.console_assistant_model_override = {api_id, model},与右栏聊天同源,见 app.py)。
    否则 editor.* 偏好从没有任何 UI 写入、永远落 resolve_api_and_model 的 fallback,跟用户在面板里选的
    模型不一致 → 续写可能用了未配置/不同的模型 → 返空「啥也没看到」(群反馈 耀月余辉)。无 → (None, None)。"""
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s", (int(user_id),),
            ).fetchone()
        prefs = (row and row.get("preferences")) or {}
        ov = prefs.get("console_assistant_model_override") if isinstance(prefs, dict) else None
        if isinstance(ov, dict):
            return (ov.get("api_id") or None, ov.get("model") or None)
    except Exception:
        pass
    return (None, None)


@router.post("/api/console_assistant/autocomplete")
async def api_console_assistant_autocomplete(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """编辑器内联续写(Copilot ghost text):据光标前文给【一句】短续写,作者按 Tab 采纳。

    body: { before, script_id?, chapter_index? }。owner-scoped + 用户自有模型/key(云端隔离)。
    非流式、短输出(max_tokens 小)、上下文精简(只 before + 写作规范)以求低延迟低成本。
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return JSONResponse({"ok": False, "error": "需要登录"}, status_code=401)
    before = str(body.get("before") or "")[-1500:]
    if not before.strip():
        return JSONResponse({"ok": True, "text": ""})

    rules = ""
    script_id = body.get("script_id")
    if script_id is not None:
        try:
            sid = int(script_id)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "script_id 无效"}, status_code=400)
        try:
            from platform_app.db import connect, init_db
            from platform_app.perms import script_owned
            init_db()
            with connect() as db:
                if not script_owned(db, sid, user_id):
                    return JSONResponse({"ok": False, "error": "无权操作该剧本:仅原作者可用 AI 续写。"}, status_code=403)
                row = db.execute("select writing_rules from scripts where id=%s", (sid,)).fetchone()
                rules = str((row.get("writing_rules") if row else "") or "").strip()[:1000]
        except Exception:
            return JSONResponse({"ok": False, "error": "剧本归属校验失败"}, status_code=200)
        script_id = sid
    else:
        script_id = None

    try:
        from agents._harness import resolve_api_and_model
        _ov_api, _ov_model = _editor_panel_model(user_id)
        api_id, model_real = resolve_api_and_model(
            user_id, api_pref_key="editor.api_id", model_pref_key="editor.model_real_name",
            api_id_override=_ov_api, model_override=_ov_model,
        )
    except Exception:
        return JSONResponse({"ok": False, "error": "未配置可用模型"}, status_code=200)
    if not api_id or not model_real:
        return JSONResponse({"ok": False, "error": "未配置可用模型"}, status_code=200)

    try:
        from agents.gm import GameMaster
        backend = GameMaster(api_id=str(api_id), model=str(model_real), user_id=user_id)._backend
    except Exception:
        return JSONResponse({"ok": False, "error": "模型后端初始化失败"}, status_code=200)

    sys_prompt = (
        "你是小说续写补全引擎。任务:顺着作者光标处的正文,给出紧接其后的【一小段】续写"
        "(约 10-40 字,最多一句话),供作者按 Tab 采纳。严格要求:"
        "① 只输出续写本身,绝不重复已有文字,不加引号/解释/标题/省略号前缀;"
        "② 语气、人称、时态、文风与上文完全一致;"
        "③ 若上文最后一句未写完,就把这句话自然补完;写完则起一句承接的新句。"
        "④ 叙事语言与上文既有语言一致,不要无故切换语言。"
    )
    if rules:
        sys_prompt += "\n\n【作者写作规范(务必遵守)】\n" + rules
    user_prompt = "【上文,请紧接最后一个字续写】\n" + before

    text = ""
    try:
        for chunk in backend.stream(sys_prompt, [{"role": "user", "content": user_prompt}], max_tokens=80):
            if chunk:
                text += chunk
            if len(text) > 240:
                break
    except Exception as exc:
        import logging
        logging.getLogger("console_assistant").info("autocomplete failed: %s", type(exc).__name__)
        from agents.provider_errors import classify_provider_error
        known = classify_provider_error(exc)
        return JSONResponse({"ok": False, "error": (known[1] if known else "续写失败")}, status_code=200)

    # 清洗:去首尾空白/引号/省略号前缀;去掉模型可能回声的上文尾巴。
    text = text.strip().strip('「」“”"\'').lstrip("….—- ").strip()
    text = text[:160]

    try:
        usage = getattr(backend, "last_usage", None) or {}
        if usage and (usage.get("input_tokens") or usage.get("output_tokens")):
            from platform_app.usage import record_usage
            record_usage(
                user_id=user_id, save_id=None, context_run_id=None,
                api_id=str(getattr(backend, "api_id", None) or api_id),
                model_real_name=str(getattr(backend, "model_name", None) or model_real),
                usage=usage, metadata={"kind": "editor_autocomplete", "script_id": script_id},
                scenario="assistant",
            )
    except Exception:
        pass

    return JSONResponse({"ok": True, "text": text})


@router.post("/api/console_assistant/confirm")
async def api_console_assistant_confirm(
    body: ConsoleAssistantConfirmRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    """task 58: 对一个 pending destructive 工具调用做决策, 返 SSE 流。

    body: { conversation_id: str, call_id: str, decision: 'approve'|'reject',
            page_context?: dict }
    SSE: 与 /chat endpoint 同款 (meta / tool_call / tool_result / token /
         confirmation_required / navigation_required / error / done)

    旧 JSON 协议已弃用 — 修复:用户点确认后 LLM 必须基于工具结果续写,
    否则对话直接断在工具结果。
    """
    from app import _ensure_loaded, _resolve_console_assistant_backend
    body_dict = body.model_dump(exclude_none=True)
    conversation_id = str(body_dict.get("conversation_id") or "").strip()
    call_id = str(body_dict.get("call_id") or "").strip()
    decision = str(body_dict.get("decision") or "").strip().lower()
    page_context = body_dict.get("page_context") if isinstance(body_dict.get("page_context"), dict) else None
    if not conversation_id or not call_id or decision not in {"approve", "reject"}:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'conversation_id / call_id / decision 必填; decision ∈ {approve,reject}'}, ensure_ascii=False)}\n\n",
                  "event: done\ndata: {}\n\n"]),
            media_type="text/event-stream",
            status_code=400,
        )

    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'需要登录'}, ensure_ascii=False)}\n\n",
                  "event: done\ndata: {}\n\n"]),
            media_type="text/event-stream",
            status_code=401,
        )

    def _sp(env):
        try:
            if env.save_id is None:
                return None
            return _ensure_loaded(api_user)
        except Exception:
            return None

    try:
        backend = _resolve_console_assistant_backend(api_user)
    except Exception as exc:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':f'backend 初始化失败: {exc}'}, ensure_ascii=False)}\n\n",
                  "event: done\ndata: {}\n\n"]),
            media_type="text/event-stream",
        )

    from console_assistant import apply_confirmation_stream as _apply_stream

    def _gen():
        yield from _apply_stream(
            user_id=user_id,
            conversation_id=conversation_id,
            call_id=call_id,
            decision=decision,
            page_context=page_context,
            backend=backend,
            state_provider=_sp,
        )

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ── MD 编辑器:AI 续写/改写正文(流式纯文本,无工具)──────────────────
# 像 Cursor 写代码:LLM 只产正文文本片段,前端决定插哪。本端点只负责流式产文本。

_CONTINUE_BEFORE_CAP = 4000   # before 截到末尾这么多字
_CONTINUE_AFTER_CAP = 1500    # after 截到开头这么多字
_CONTINUE_SELECTION_CAP = 4000

_CONTINUE_SYSTEM_PROMPT = (
    "你是中文小说正文写作助手。**只输出要插入/替换进正文的纯文本片段**,"
    "不要任何解释、不要 markdown 标记、不要把前后文重复出来、不要加引号外壳。\n"
    "\n"
    "【最高优先级】若给了『指令』,严格服从它,它凌驾一切默认做法"
    "(包括默认的「顺势往下续写」)——指令要求什么就写什么、怎么写。"
    "指令未覆盖到的方面,仍然遵守下面的【文风一致】。\n"
    "\n"
    "【文风一致】你的输出必须无缝融入上下文,沿用上下文已有的:\n"
    "  · 人称与时态(第一/第三人称、过去/现在时);\n"
    "  · 叙述视角与叙述距离(全知/限知、贴近内心还是冷静旁观);\n"
    "  · 语气与语域(雅俗、正式/口语、文白程度);\n"
    "  · 用词习惯与句子节奏(长短句搭配、标点密度、段落呼吸);\n"
    "  · 对话风格与标点(引号样式、对白与动作的穿插方式);\n"
    "  · 内容尺度(露骨或含蓄的程度与上下文保持一致,除非『指令』另有要求"
    "——本平台允许成人内容)。\n"
    "把 before/after/selection 仅当作可能被截断的上下文。\n"
    "\n"
    "【设定一致】若给了【相关设定】(本剧本既有的世界书/人物/词条/时点),续写须援引它保持忠实:"
    "人物言行合其设定、术语地名用既有的、不与之矛盾;但不要发明【相关设定】与前后文之外的全新人物、"
    "势力、地名或重大设定——需要的细节优先从给定设定取,未覆盖处做不冲突的合理推演而非凭空另起。"
    "没给【相关设定】时,基于前后文合理续写,别硬造与剧本冲突的重大新设定。\n"
    "\n"
    "- continue 模式:在『前文』与『后文』之间续写衔接的一段;承接前文、并与后文自然衔接;"
    "若给了『指令』按【最高优先级】执行,否则顺势往下续写,全程守【文风一致】。\n"
    "- rewrite 模式:把『选中原文』按『指令』改写成新版本,只输出新版本正文;"
    "改写后仍要与前后文【文风一致】。"
)


def _build_continue_user_prompt(
    *, before: str, after: str, selection: str, instruction: str, mode: str,
    environment: str = "",
) -> str:
    """把 before/after/selection/instruction 组织进 user_prompt,清楚标注每段。
    environment:阶段2 注入的「相关设定」环境块(可空),放最前供 LLM 援引保持忠实一致。"""
    parts: list[str] = []
    if environment:
        parts.append(environment)
    parts.append(f"【模式】{'改写(rewrite)' if mode == 'rewrite' else '续写(continue)'}")
    if before:
        parts.append(f"【前文】(正文光标之前,可能已截断)\n{before}")
    if mode == "rewrite" and selection:
        parts.append(f"【选中原文】(请把这段改写成新版本)\n{selection}")
    if after:
        parts.append(f"【后文】(正文光标之后,可能已截断)\n{after}")
    if instruction:
        parts.append(f"【指令(最高优先级,凌驾默认做法)】\n{instruction}")
    if mode == "rewrite":
        parts.append("请只输出『选中原文』改写后的新版本正文,不要解释、不要重复前后文。")
    else:
        parts.append("请在『前文』与『后文』之间续写衔接的一段正文,只输出新增的这段文字。")
    return "\n\n".join(parts)


@router.post("/api/console_assistant/continue")
async def api_console_assistant_continue(
    body: ConsoleAssistantContinueRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    """MD 编辑器「AI 续写/改写正文」流式端点(纯文本,无工具)。

    body: { before, after, instruction?, selection?, mode?='continue'|'rewrite',
            script_id?, api_id?, model? }
    SSE: token{text} / done{} / error{message}
    """
    body_dict = body.model_dump(exclude_none=True)
    before = str(body_dict.get("before") or "")
    after = str(body_dict.get("after") or "")
    instruction = str(body_dict.get("instruction") or "").strip()
    selection = str(body_dict.get("selection") or "")
    mode = str(body_dict.get("mode") or "continue").strip().lower()
    if mode not in ("continue", "rewrite"):
        mode = "continue"
    script_id = body_dict.get("script_id")
    api_id_in = (str(body_dict.get("api_id")).strip() or None) if body_dict.get("api_id") else None
    model_in = (str(body_dict.get("model")).strip() or None) if body_dict.get("model") else None
    _ci_in = body_dict.get("chapter_index")
    try:
        chapter_index = int(_ci_in) if _ci_in is not None else None
    except (TypeError, ValueError):
        chapter_index = None

    def _err(message: str):
        return StreamingResponse(
            iter([
                f"event: error\ndata: {json.dumps({'message': message}, ensure_ascii=False)}\n\n",
                "event: done\ndata: {}\n\n",
            ]),
            media_type="text/event-stream",
        )

    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return _err("需要登录")

    # 截断:before 取末尾,after 取开头(光标处上下文最相关)
    before = before[-_CONTINUE_BEFORE_CAP:]
    after = after[:_CONTINUE_AFTER_CAP]
    selection = selection[:_CONTINUE_SELECTION_CAP]

    # 严格 owner 鉴权(给了 script_id 才校验):既防滥用又给用量归属
    if script_id is not None:
        try:
            sid = int(script_id)
        except (TypeError, ValueError):
            return _err("script_id 无效")
        try:
            from platform_app.db import connect, init_db
            from platform_app.perms import script_owned
            init_db()
            with connect() as db:
                owned = script_owned(db, sid, user_id)
        except Exception:
            return _err("剧本归属校验失败,请稍后重试")
        if not owned:
            return _err("无权操作该剧本:仅原作者可用 AI 续写/改写。")
        script_id = sid
    else:
        script_id = None

    # 阶段2:装配「相关设定」环境块 —— 续写/改写忠于该剧本既有世界书/人物/词条/时点,
    # 按 chapter_index 防剧透截断(空白剧本/未关联 script → 跳过,退化为纯局部续写)。失败不影响续写。
    environment = ""
    if script_id is not None:
        try:
            from console_assistant.editor_context import build_editor_environment
            _scan = "\n".join([before, selection, after]).strip()
            environment = build_editor_environment(script_id, _scan, chapter_index)
        except Exception:
            environment = ""

    # 模型解析:body 给了 api_id+model 就用;否则跟随「编辑器 AI 面板」选的模型(与右栏聊天同源);
    # 再否则回退到 editor.* 偏好 / agent.* / 用户首个可用模型 / 默认。
    if not api_id_in or not model_in:
        _ov_api, _ov_model = _editor_panel_model(user_id)
        api_id_in = api_id_in or _ov_api
        model_in = model_in or _ov_model
    try:
        from agents._harness import resolve_api_and_model
        api_id, model_real = resolve_api_and_model(
            user_id,
            api_pref_key="editor.api_id",
            model_pref_key="editor.model_real_name",
            api_id_override=api_id_in,
            model_override=model_in,
        )
    except Exception:
        return _err(
            "未找到可用的模型。请到「设置 → API 设置」配置并测试一个模型后重试。"
        )
    if not api_id or not model_real:
        return _err(
            "未找到可用的模型。请到「设置 → API 设置」配置并测试一个模型后重试。"
        )

    # 构造 backend(复用 GM 的 backend 抽象);纯文本流式只用 backend.stream(无工具)
    try:
        from agents.gm import GameMaster
        backend = GameMaster(
            api_id=str(api_id), model=str(model_real), user_id=user_id,
        )._backend
    except Exception as exc:
        from agents.provider_errors import classify_provider_error
        known = classify_provider_error(exc)
        return _err(known[1] if known else "模型后端初始化失败,请检查 API 设置后重试。")

    system_prompt = _CONTINUE_SYSTEM_PROMPT
    user_prompt = _build_continue_user_prompt(
        before=before, after=after, selection=selection,
        instruction=instruction, mode=mode, environment=environment,
    )

    def _gen():
        try:
            for chunk in backend.stream(
                system_prompt,
                [{"role": "user", "content": user_prompt}],
                max_tokens=2000,
            ):
                if chunk:
                    yield f"event: token\ndata: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            import logging
            logging.getLogger("console_assistant").exception(
                "continue stream failed: %s", type(exc).__name__,
            )
            from agents.provider_errors import classify_provider_error
            known = classify_provider_error(exc)
            msg = known[1] if known else "AI 续写出错,请稍后重试。"
            yield f"event: error\ndata: {json.dumps({'message': msg}, ensure_ascii=False)}\n\n"
        else:
            # 用量归属(失败静默,不影响流):scenario=assistant,归到 script_id
            try:
                usage = getattr(backend, "last_usage", None) or {}
                if usage and (usage.get("input_tokens") or usage.get("output_tokens")):
                    from platform_app.usage import record_usage
                    record_usage(
                        user_id=user_id,
                        save_id=None,
                        context_run_id=None,
                        api_id=str(getattr(backend, "api_id", None) or api_id),
                        model_real_name=str(getattr(backend, "model_name", None) or model_real),
                        usage=usage,
                        metadata={"kind": "editor_continue", "mode": mode,
                                  "script_id": script_id},
                        scenario="assistant",
                    )
            except Exception:
                pass
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")
