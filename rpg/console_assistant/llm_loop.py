"""console_assistant.llm_loop — LLM 主循环内核。"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from console_assistant.conversations import _new_call_id, _trim_messages
from console_assistant.prompts import build_system_prompt
from console_assistant.tools import dispatch_assistant_tool, get_tool_spec, list_assistant_tools
from tools_dsl.command_dispatcher import ToolCallEnvelope, ToolResult

# 安全: navigate_to_setting 的目标必须在白名单内, 避免任意字符串经 SSE 传到前端
# 后再被解析为 url/open_redirect。同步前端 console-assistant-navigation.jsx 的 MAP。
_NAV_TARGETS_WHITELIST = frozenset({
    "models", "models.add_api", "models.pricing",
    "rules", "rules.modules",
    "memory", "memory.add",
    "skills",
    "permissions",
    "library",
    "saves",
    "scripts",
    "tools", "mcp",
    "settings", "settings.profile", "settings.security",
    "platform.home", "platform.usage",
    "console.assistant",
})


def _validate_owned_save_id(user_id: int, save_id: Any) -> int | None:
    """归属校验: page_context.save_id 必须属于当前 user_id, 否则置 None。

    旧实现直接信前端传上来的 save_id, 攻击者可改 save_id 让 LLM 操作他人存档
    （`get_game_state` / 后续 destructive 工具）。
    """
    if save_id is None:
        return None
    try:
        sid = int(save_id)
    except (TypeError, ValueError):
        return None
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select 1 from game_saves where id = %s and user_id = %s",
                (sid, int(user_id)),
            ).fetchone()
        return sid if row else None
    except Exception:
        # DB 故障时保守: 不放行外部 save_id
        return None


def _validate_owned_script_id(user_id: int, script_id: Any) -> int | None:
    """同 _validate_owned_save_id, 校验 scripts.owner_id。"""
    if script_id is None:
        return None
    try:
        sid = int(script_id)
    except (TypeError, ValueError):
        return None
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select 1 from scripts where id = %s and owner_id = %s",
                (sid, int(user_id)),
            ).fetchone()
        return sid if row else None
    except Exception:
        return None


def _sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _fetch_save_details(user_id: int, save_ids: list[Any]) -> list[dict[str, Any]]:
    """查 DB 拿 save_id 列表的 title/turn, 供 destructive 确认弹窗展示。
    DB 故障或 id 无效时静默返回空列表, 不阻塞主流程。
    """
    ids = []
    for x in save_ids:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    if not ids:
        return []
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            placeholders = ", ".join(["%s"] * len(ids))
            rows = db.execute(
                f"SELECT id, title, coalesce((state_snapshot->>'turn')::int, 0) AS turn "
                f"FROM game_saves WHERE id IN ({placeholders}) AND user_id = %s",
                (*ids, int(user_id)),
            ).fetchall() or []
        return [
            {"id": int(r["id"]), "title": str(r.get("title") or ""), "turn": int(r.get("turn") or 0)}
            for r in rows
        ]
    except Exception:
        return []


def _format_tool_result_for_llm(call_id: str, result: ToolResult) -> str:
    """ToolResult → LLM-facing 文本。

    如果 body 含 "--- raw JSON ---" 分隔符, 优先保留分隔符前的 summary 部分
    (避免截断点落在 JSON 内产生乱码), 否则按原 1500 字符截。
    """
    head = "OK" if result.ok else "FAIL"
    body = result.result or result.error or ""
    JSON_MARKER = "--- raw JSON ---"
    if JSON_MARKER in body:
        summary, _, _ = body.partition(JSON_MARKER)
        return f"[tool {call_id} {head}]\n{summary.rstrip()[:1500]}"
    return f"[tool {call_id} {head}]\n{body[:1500]}"


def _to_backend_messages(messages: list[dict[str, Any]]) -> list[dict]:
    """conv["messages"] 用 {role, content:str} 简单形态, backend 直接吃。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, list):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        if not isinstance(content, str):
            content = str(content)
        out.append({"role": role, "content": content})
    return out


def _run_llm_loop(
    *,
    user_id: int,
    conv: dict[str, Any],
    page_context: dict[str, Any] | None,
    backend: Any,
    state_provider: Callable[[ToolCallEnvelope], Any] | None,
    trace_id: str,
    max_iterations: int,
    max_tokens: int,
) -> Iterator[str]:
    """task 58: 共享内核 — 跑 backend.stream_with_mcp_loop, yield SSE 字符串。"""

    system_prompt = build_system_prompt(page_context)
    tools = list_assistant_tools()

    extra_pending_note: list[dict[str, Any]] = []
    if conv.get("pending_confirmations"):
        pending_summary = "(等待用户对以下调用做出 approve/reject 决定:\n" + json.dumps(
            list(conv["pending_confirmations"].values())[:3], ensure_ascii=False, indent=2,
        ) + "\n)"
        extra_pending_note.append({"role": "user", "content": pending_summary})

    pending_for_this_turn: list[dict[str, Any]] = []

    # 安全: 不再信前端任意传入的 save_id/script_id, 必须经过归属校验
    page_save_id = _validate_owned_save_id(user_id, (page_context or {}).get("save_id"))
    page_script_id = _validate_owned_script_id(user_id, (page_context or {}).get("script_id"))

    def _router(server_id: str, tool_name: str, arguments: dict) -> dict[str, Any]:
        spec = get_tool_spec(tool_name)
        if spec is None:
            return {"ok": False, "error": f"未知工具 {tool_name}"}
        call_id = _new_call_id()
        if spec.destructive:
            args_for_pending = dict(arguments or {})
            # task 120 UX: 在确认弹窗里显示 title/turn, 不只是 save_id
            if tool_name == "delete_save":
                args_for_pending["save_details"] = _fetch_save_details(
                    user_id, [args_for_pending.get("save_id")],
                )
            elif tool_name == "delete_saves":
                args_for_pending["save_details"] = _fetch_save_details(
                    user_id, args_for_pending.get("save_ids") or [],
                )
            pending = {
                "call_id": call_id,
                "tool": tool_name,
                "args": args_for_pending,
                "save_id": page_save_id,
                "script_id": page_script_id,
                "description": spec.description,
            }
            conv["pending_confirmations"][call_id] = pending
            pending_for_this_turn.append(pending)
            return {
                "ok": False,
                "error": "DESTRUCTIVE_REQUIRES_CONFIRMATION",
                "result": json.dumps(pending, ensure_ascii=False),
            }
        result = dispatch_assistant_tool(
            user_id=user_id,
            tool=tool_name,
            args=arguments or {},
            save_id=page_save_id,
            script_id=page_script_id,
            trace_id=trace_id,
            call_id=call_id,
            state_provider=state_provider,
        )
        return {
            "ok": result.ok,
            "result": result.result,
            "error": result.error,
            "_call_id": call_id,
        }

    try:
        messages_for_backend = _to_backend_messages(conv["messages"]) + [
            {"role": m["role"], "content": m["content"]} for m in extra_pending_note
        ]
        assistant_text_acc = ""
        for ev in backend.stream_with_mcp_loop(
            system=system_prompt,
            messages=messages_for_backend,
            mcp_tools=tools,
            max_iterations=max_iterations,
            max_tokens=max_tokens,
            mcp_call=_router,
        ):
            etype = ev.get("type")
            if etype == "text":
                txt = ev.get("text") or ""
                if txt:
                    assistant_text_acc += txt
                    yield _sse_event("token", {"text": txt})
            elif etype == "tool_call":
                yield _sse_event("tool_call", {
                    "tool": ev.get("tool"),
                    "args": ev.get("arguments") or {},
                    "server_id": ev.get("server_id") or "dispatcher",
                })
            elif etype == "tool_result":
                err = ev.get("error") or ""
                if "DESTRUCTIVE_REQUIRES_CONFIRMATION" in err:
                    pend = pending_for_this_turn[-1] if pending_for_this_turn else None
                    if pend:
                        yield _sse_event("confirmation_required", {
                            "call_id": pend["call_id"],
                            "tool": pend["tool"],
                            "args": pend["args"],
                            "description": pend["description"],
                            "destructive": True,
                        })
                    break
                result_str = ev.get("result") or ""
                if isinstance(result_str, str) and result_str.startswith("NAVIGATE:"):
                    payload = result_str[len("NAVIGATE:"):]
                    try:
                        target, _, reason = payload.partition("|")
                        target = (target or "").strip()
                        reason = (reason or "").strip()
                    except Exception:
                        target, reason = payload.strip(), ""
                    # 白名单校验: 防止 LLM 被诱导发出任意 target 字符串
                    # (open_redirect / 前端 XSS / 路由欺骗)
                    if target not in _NAV_TARGETS_WHITELIST:
                        # 不在白名单的 target 静默丢弃, 不发 navigation_required 事件
                        target = ""
                    # reason 严格净化: 去控制字符 + 截断 80, 防止 SSE 数据被前端 innerHTML 时 XSS
                    if reason:
                        reason = "".join(ch for ch in reason if ch.isprintable())[:80]
                    if target:
                        yield _sse_event("navigation_required", {
                            "target": target,
                            "reason": reason,
                            "dirty_check": True,
                        })
                if isinstance(result_str, str) and result_str.startswith("USER_CHOICE:"):
                    payload_str = result_str[len("USER_CHOICE:"):]
                    try:
                        payload = json.loads(payload_str)
                    except Exception:
                        payload = {"question": payload_str, "options": []}  # type: ignore[assignment]
                    if not assistant_text_acc.strip():
                        intro = "好的,先确认一下:"
                        assistant_text_acc += intro
                        yield _sse_event("token", {"text": intro})
                    yield _sse_event("user_choice_required", {
                        "call_id": ev.get("_call_id") or _new_call_id(),
                        "tool": "ask_user_choice",
                        "question": payload.get("question", ""),
                        "options": payload.get("options", []),
                        "allow_free_text": payload.get("allow_free_text", True),
                        "context": payload.get("context", ""),
                    })
                    break
                _raw = ev.get("result")
                if isinstance(_raw, dict) and _raw.get("__ui_action__"):
                    yield _sse_event("ui_action", {
                        "kind": _raw.get("__ui_action__"),
                        "form_id": _raw.get("form_id"),
                        "field_key": _raw.get("field_key"),
                        "value": _raw.get("value"),
                        "action_label": _raw.get("action_label"),
                    })
                    yield _sse_event("tool_result", {
                        "call_id": ev.get("_call_id") or _new_call_id(),
                        "ok": True,
                        "result": _raw.get("ack") or "ui action 已转发前端",
                    })
                    continue
                yield _sse_event("tool_result", {
                    "call_id": ev.get("_call_id") or _new_call_id(),
                    "ok": bool(ev.get("ok")),
                    "result": ev.get("result"),
                    "error": ev.get("error"),
                })
            elif etype == "tool_error":
                yield _sse_event("error", {"message": ev.get("error") or "tool 调用错误"})
        if assistant_text_acc:
            conv["messages"].append({"role": "assistant", "content": assistant_text_acc})
            _trim_messages(conv)
        try:
            usage = getattr(backend, "last_usage", None) or {}
            in_tk = int(usage.get("input_tokens", 0) or 0)
            out_tk = int(usage.get("output_tokens", 0) or 0)
            conv["cum_input_tokens"] = int(conv.get("cum_input_tokens", 0)) + in_tk
            conv["cum_output_tokens"] = int(conv.get("cum_output_tokens", 0)) + out_tk
            limit = int(getattr(backend, "context_window", 0) or 0)
            if not limit:
                m = (getattr(backend, "model_name", "") or "").lower()
                if "gemini" in m and ("3" in m or "2.5" in m or "flash" in m):
                    limit = 1_048_576
                elif "claude" in m or "opus" in m or "sonnet" in m or "haiku" in m:
                    limit = 200_000
                elif "gpt-5" in m or "gpt5" in m or "gpt-4" in m:
                    limit = 128_000
                else:
                    limit = 128_000
            conv["context_limit"] = limit
            yield _sse_event("context_usage", {
                "input_tokens": in_tk,
                "output_tokens": out_tk,
                "cum_input_tokens": conv["cum_input_tokens"],
                "cum_output_tokens": conv["cum_output_tokens"],
                "context_limit": limit,
            })
        except Exception:
            pass

        # 写 token_usage 表（不影响主流程，异常静默）
        try:
            if user_id and usage and (in_tk or out_tk):
                from platform_app.usage import record_usage as _rec
                _backend_api_id = (
                    getattr(backend, "api_id", None)
                    or ("anthropic" if "anthropic" in type(backend).__module__ else
                        "vertex_ai" if "vertex" in type(backend).__module__ else
                        "openai")
                )
                _rec(
                    user_id=user_id,
                    save_id=None,
                    context_run_id=None,
                    api_id=_backend_api_id,
                    model_real_name=getattr(backend, "model_name", "unknown"),
                    usage=usage,
                    metadata={"kind": "console"},
                    scenario="assistant",
                )
        except Exception:
            pass
    except Exception as exc:
        # 错误脱敏: 完整 exception 写后台日志, 对前端只暴露通用 message + code
        # 防止泄漏 Python 异常类型 / 文件路径 / DB SQL 片段 / API key 片段
        import logging
        logging.getLogger("console_assistant").exception(
            "llm loop failed: %s", type(exc).__name__,
        )
        yield _sse_event("error", {
            "message": "助手内部错误，请稍后重试",
            "code": "E_LLM_LOOP",
        })
