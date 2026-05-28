"""
chat_tool_router.py — task 87 Phase 5: 统一工具路由 (GM tool_use)

GM 流式响应中调用工具时,需要识别:
  · dispatcher 工具 (server_id="" 或 magic "__dispatcher__"): 走 ToolDispatcher
  · MCP 工具 (server_id 是真实 server): 走 mcp_broker.call_tool

unified router 在 chat handler 内构造,带上当前 user_id / save_id / trace_id 上下文。
"""
from __future__ import annotations

from typing import Any, Callable

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope, ToolDispatcher, get_registry,
)


# task 87 Phase 5: sentinel 必须不含 "__" (backend 用作 server_id__tool_name 分隔符),
# 否则 backend 把 full_name 拆错,server_id 解析失败 → router 回退到 mcp_broker 调用失败。
DISPATCHER_SENTINEL = "dispatcher"


def build_unified_tool_list(
    mcp_tools: list[dict[str, Any]] | None,
    origin: str = "llm_chat",
) -> list[dict[str, Any]]:
    """合并 MCP 工具列表 + dispatcher 注册表中允许 origin 的工具。

    输出格式与 mcp_broker.discover_all_tools 一致:
        [{"server_id": str, "name": str, "description": str, "schema": dict}, ...]
    dispatcher 工具用 server_id="__dispatcher__" 标识。
    """
    out: list[dict[str, Any]] = list(mcp_tools or [])
    for spec in get_registry().list_for_origin(origin):
        out.append({
            "server_id": DISPATCHER_SENTINEL,
            "name": spec.name,
            "description": spec.description,
            "schema": spec.input_schema,
        })
    return out


def build_tool_call_router(
    *,
    user_id: int,
    save_id: int | None,
    script_id: int | None,
    trace_id: str,
    state_provider: Callable[[ToolCallEnvelope], Any],
    fallback_mcp_call: Callable[[str, str, dict], dict] | None = None,
) -> Callable[[str, str, dict], dict[str, Any]]:
    """构造给 backend.stream_with_mcp_loop 用的 unified mcp_call。

    backend 调 router(server_id, tool_name, arguments) 时:
      · server_id == DISPATCHER_SENTINEL → 走 dispatcher (origin=llm_chat)
      · 否则 → fallback_mcp_call (默认 mcp_broker.call_tool)

    返回 dict {"ok":bool, "result":Any, "error":str|None} 与 mcp_broker 兼容。
    """
    if fallback_mcp_call is None:
        from mcp_broker import call_tool as _default_mcp
        fallback_mcp_call = _default_mcp

    dispatcher = ToolDispatcher(
        registry=get_registry(),
        state_provider=state_provider,
    )

    def _router(server_id: str, tool_name: str, arguments: dict) -> dict[str, Any]:
        if (server_id or "") == DISPATCHER_SENTINEL or get_registry().has(tool_name):
            env = ToolCallEnvelope(
                user_id=user_id,
                save_id=save_id,
                script_id=script_id,
                tool=tool_name,
                args=arguments or {},
                origin="llm_chat",
                trace_id=trace_id,
                depth=1,  # GM 响应路径已经在一个 trace 内,标记 depth=1
            )
            result = dispatcher.dispatch_sync(env)
            return {
                "ok": result.ok,
                "result": result.result,
                "error": result.error,
            }
        # MCP 工具
        try:
            return fallback_mcp_call(server_id, tool_name, arguments)
        except Exception as exc:
            return {"ok": False, "error": f"MCP 工具调用异常: {type(exc).__name__}: {exc}"}

    return _router


__all__ = [
    "DISPATCHER_SENTINEL",
    "build_unified_tool_list",
    "build_tool_call_router",
]
