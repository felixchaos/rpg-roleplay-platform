"""console_assistant.llm_loop — LLM 主循环内核。"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterator

from tools_dsl.command_dispatcher import ToolCallEnvelope, ToolResult

from console_assistant.conversations import _trim_messages
from console_assistant.prompts import build_system_prompt
from console_assistant.tools import dispatch_assistant_tool, get_tool_spec, list_assistant_tools


def _sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_tool_result_for_llm(call_id: str, result: ToolResult) -> str:
    """ToolResult → LLM-facing 文本。"""
    head = "OK" if result.ok else "FAIL"
    body = result.result or result.error or ""
    return f"[tool {call_id} {head}]\n{body[:1500]}"


def _new_call_id() -> str:
    import secrets
    return f"cc-{secrets.token_urlsafe(6)}"


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
        extra_pending_note.append({"role": "system", "content": pending_summary})

    pending_for_this_turn: list[dict[str, Any]] = []

    page_save_id = (page_context or {}).get("save_id")
    page_script_id = (page_context or {}).get("script_id")
    try:
        page_save_id = int(page_save_id) if page_save_id is not None else None
    except (TypeError, ValueError):
        page_save_id = None
    try:
        page_script_id = int(page_script_id) if page_script_id is not None else None
    except (TypeError, ValueError):
        page_script_id = None

    def _router(server_id: str, tool_name: str, arguments: dict) -> dict[str, Any]:
        spec = get_tool_spec(tool_name)
        if spec is None:
            return {"ok": False, "error": f"未知工具 {tool_name}"}
        call_id = _new_call_id()
        if spec.destructive:
            pending = {
                "call_id": call_id,
                "tool": tool_name,
                "args": arguments or {},
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
            if m["role"] in ("user", "assistant")
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
                        payload = {"question": payload_str, "options": []}
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
    except Exception as exc:
        yield _sse_event("error", {"message": f"{type(exc).__name__}: {exc}"})
