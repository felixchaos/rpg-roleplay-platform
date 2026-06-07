"""
chat_tool_router.py — task 87 Phase 5: 统一工具路由 (GM tool_use)

GM 流式响应中调用工具时,需要识别:
  · dispatcher 工具 (server_id="" 或 magic "__dispatcher__"): 走 ToolDispatcher
  · MCP 工具 (server_id 是真实 server): 走 mcp_broker.call_tool

unified router 在 chat handler 内构造,带上当前 user_id / save_id / trace_id 上下文。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope,
    ToolDispatcher,
    get_registry,
)

# task 87 Phase 5: sentinel 必须不含 "__" (backend 用作 server_id__tool_name 分隔符),
# 否则 backend 把 full_name 拆错,server_id 解析失败 → router 回退到 mcp_broker 调用失败。
DISPATCHER_SENTINEL = "dispatcher"


# 酒馆 = 基于 harness 的完整 agent(用户决策):**允许「改写只读剧本 canon」以外的所有操作**。
# 不再像旧设计那样砍掉战斗/物品/模组/锚点/时间线 —— 那些写的是本存档自身状态,是合法的
# 「世界随对话推进写入 DB」的一部分。只有两类按需丢弃:
#   · canon 写(kb_*)= 改世界树 KB:绑定只读剧本时禁(不许改原著);无剧本时也无对象 → 丢。
#   · canon 读(search_canon 等):无绑定剧本时没有原著可读 → 丢;绑定后放开(贴合原著)。
_TAVERN_CANON_WRITE_SUBSTR = ("kb_",)
_TAVERN_CANON_READ_SUBSTR = (
    "search_canon", "lookup_entity", "lookup_timeline", "graph_neighbors",
    "get_chapter_facts", "get_worldbook",
)

# agent 自举工具(建/换角色、persona、列/绑剧本)永不被子串匹配误伤 ——
# tavern_list_scripts / tavern_bind_script 含 "script" 子串,否则可能被规则吞掉。
_TAVERN_KEEP_PREFIX = ("set_tavern_", "edit_tavern_", "tavern_")


def _tavern_drops_tool(name: str, *, bound_script_id: int | None = None) -> bool:
    n = (name or "").lower()
    # 酒馆自举工具永远保留
    if any(n.startswith(p) for p in _TAVERN_KEEP_PREFIX):
        return False
    if bound_script_id:
        # 绑定只读剧本:仅禁「改写 canon」,canon 读 + 其余所有写本档状态的工具全开
        return any(s in n for s in _TAVERN_CANON_WRITE_SUBSTR)
    # 无绑定剧本:没有 canon 对象 → canon 读/写工具都丢;其余(world/memory/关系/战斗/物品/模组…)全开
    return any(s in n for s in (_TAVERN_CANON_WRITE_SUBSTR + _TAVERN_CANON_READ_SUBSTR))


def build_unified_tool_list(
    mcp_tools: list[dict[str, Any]] | None,
    origin: str = "llm_chat",
    *,
    mode: str | None = None,
    bound_script_id: int | None = None,
) -> list[dict[str, Any]]:
    """合并 MCP 工具列表 + dispatcher 注册表中允许 origin 的工具。

    输出格式与 mcp_broker.discover_all_tools 一致:
        [{"server_id": str, "name": str, "description": str, "schema": dict}, ...]
    dispatcher 工具用 server_id="__dispatcher__" 标识。

    排序:KB 查询 / 信息最稀缺的工具排前 — backend 截断(Vertex 64 / Anthropic 128)
    时不会把 search_canon 等 GM 真正需要的查询砍掉。
    优先级(数字小靠前):
      0. KB 查询(search_canon / lookup_* / graph_neighbors)— 缺这个 GM 只能虚构
      1. 状态读(get_* / list_* / query_*)+ KB 写(kb_*)— 看清现状 / 提交世界树 delta
      2. 状态写(set_* / add_* / pin_* / clarify / confirm_*)— 改 state
      3. 其余(combat_* / skill_* / consume_*)— 战斗 / 检定专用
    """
    def _rank(name: str) -> int:
        n = (name or "").lower()
        # 酒馆自管理工具(建/换角色、persona、改卡、列/绑剧本)是酒馆 agent 的核心能力,必须排最前:
        # backend 有工具数上限(openai_compat 取前 N),排后面会被截断 → 模型拿不到 schema → 只能
        # 幻觉式叙述「已修改」而不真正调用。放 -1 保证它们永远落在窗口内。
        if n.startswith(("set_tavern_", "edit_tavern_", "tavern_")):
            return -1
        if n.startswith(("search_canon", "lookup_", "graph_neighbors")):
            return 0
        if n.startswith(("kb_", "get_", "list_", "query_")):
            return 1
        if n.startswith(("set_", "add_", "pin_", "clarify", "confirm_", "reject_", "dismiss_", "save_")):
            return 2
        return 3

    out: list[dict[str, Any]] = list(mcp_tools or [])
    disp: list[dict[str, Any]] = []
    for spec in get_registry().list_for_origin(origin):
        if mode == "tavern_gm":
            if _tavern_drops_tool(spec.name, bound_script_id=bound_script_id):
                continue
        # 非酒馆(游戏控制台 freeform/novel)模式:酒馆自管理工具(建/换角色、persona、列/绑剧本)
        # 在游戏里无意义且会因 _rank=-1 抢占窗口最前。这里丢掉,别污染游戏控制台工具表。
        elif spec.name.lower().startswith(("set_tavern_", "edit_tavern_", "tavern_")):
            continue
        disp.append({
            "server_id": DISPATCHER_SENTINEL,
            "name": spec.name,
            "description": spec.description,
            "schema": spec.input_schema,
        })
    disp.sort(key=lambda d: (_rank(d.get("name", "")), d.get("name", "")))
    out.extend(disp)
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
