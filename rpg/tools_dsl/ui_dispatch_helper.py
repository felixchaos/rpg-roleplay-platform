"""
ui_dispatch_helper.py — task 87 Phase 6 余下: UI HTTP endpoint 接入 dispatcher

设计:
  · 给 UI 按钮直打的 endpoint 提供统一 helper,内部构造 ToolCallEnvelope,
    origin="ui_button" 通过 dispatcher,获得统一审计 + destructive 检查。
  · LLM 影响不到 (它的 origin 是 llm_chat/llm_set,跟 UI 的 ui_button 独立)
  · 用户跨用户操作仍由 SQL where user_id=? 强保护 + dispatcher 鉴权双重防御

用法 (在 chat handler 等同步路径):
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="set_memory_mode", args={"mode": "deep"},
        user_id=user_id, save_id=save_id, state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
"""
from __future__ import annotations

import secrets
from typing import Any

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope,
    ToolDispatcher,
    ToolResult,
    get_registry,
)


def dispatch_ui_tool(
    *,
    tool_name: str,
    args: dict[str, Any],
    user_id: int,
    save_id: int | None = None,
    script_id: int | None = None,
    state: Any = None,
    trace_id: str | None = None,
) -> ToolResult:
    """从 UI endpoint 触发 dispatcher 工具调用。

    state 是当前 user 的 GameState (save 级工具需要); user 级工具不需要。
    """
    if trace_id is None:
        trace_id = f"ui-{secrets.token_urlsafe(6)}"
    dispatcher = ToolDispatcher(
        registry=get_registry(),
        state_provider=(lambda env: state) if state is not None else (lambda env: None),
    )
    env = ToolCallEnvelope(
        user_id=user_id,
        save_id=save_id,
        script_id=script_id,
        tool=tool_name,
        args=args,
        origin="ui_button",
        trace_id=trace_id,
    )
    return dispatcher.dispatch_sync(env)


__all__ = ["dispatch_ui_tool"]
