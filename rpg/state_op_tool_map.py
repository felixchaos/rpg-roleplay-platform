"""
state_op_tool_map.py — task 87 Phase 6: GM JSON op path → dispatcher 工具映射

设计目的:
GM 通过老 JSON op 协议 (`{"op":"set","path":"world.time","value":"X"}`) 写状态时,
chat handler 在 state.apply_state_write 真正执行之前,通过这个映射表把 op 路由
到对应的 dispatcher 工具,获得统一审计 + destructive 检查。

返回值:
  None — 该 path 没有对应工具 (走老路径)
  (tool_name, args) — 转成 dispatcher 工具调用

设计原则:
  · 一个 path 对应至多一个工具
  · 路径前缀匹配 (relationships.X / worldline.user_variables.Y 等)
  · "append" 操作映射到 add_* / pin_* 工具
  · "set" 操作映射到 set_* 工具
"""
from __future__ import annotations

from typing import Any


def map_op_to_tool(path: str, value: Any, *, op_kind: str = "set",
                    append: bool = False) -> tuple[str, dict[str, Any]] | None:
    """把 GM JSON op 的 (path, value, op_kind/append) 映射到 dispatcher 工具调用。

    返回 (tool_name, args) 或 None (无对应工具,走老路径)。
    """
    if not path:
        return None

    # 是否是 append 操作 (op_kind="append" 或 append=True)
    is_append = (op_kind == "append") or append

    # ── world.* ─────────────────────────────────────────
    if path == "world.time":
        return "set_world_time", {"target": str(value or "")}
    if path == "world.known_events":
        # 总是 append 语义
        if isinstance(value, list):
            # 多条 → 只能逐条返回,这里只处理第一条 (调用方应展开)
            v = value[0] if value else ""
        else:
            v = value
        return "add_world_event", {"text": str(v or "")}
    if path.startswith("world.") and path not in {"world.timeline"} and not path.startswith("world.timeline."):
        # 其它 world.* 标量属性 (weather / atmosphere / season / region)
        key = path[len("world."):]
        # 排除嵌套结构
        if "." not in key:
            return "set_world_attribute", {"key": key, "value": str(value or "")}

    # ── player.* ────────────────────────────────────────
    if path == "player.name":
        return "set_player_name", {"name": str(value or "")}
    if path == "player.role":
        return "set_player_role", {"role": str(value or "")}
    if path == "player.background":
        return "set_player_background", {"background": str(value or "")}
    if path == "player.current_location":
        return "set_player_location", {"location": str(value or "")}

    # ── relationships.X ─────────────────────────────────
    if path.startswith("relationships."):
        character = path[len("relationships."):]
        return "set_relationship", {"character": character, "status": str(value or "")}

    # ── memory.* ────────────────────────────────────────
    if path == "memory.main_quest":
        return "set_main_quest", {"text": str(value or "")}
    if path == "memory.current_objective":
        return "set_current_objective", {"text": str(value or "")}
    if path == "memory.mode":
        return "set_memory_mode", {"mode": str(value or "")}
    # memory list-bucket appends
    bucket_to_tool = {
        "memory.facts": "add_memory_fact",
        "memory.resources": "add_memory_resource",
        "memory.abilities": "add_memory_ability",
        "memory.pinned": "pin_memory",
        "memory.notes": "add_memory_note",
    }
    if path in bucket_to_tool:
        # 单条值
        if isinstance(value, list):
            v = value[0] if value else ""
        else:
            v = value
        return bucket_to_tool[path], {"text": str(v or "")}

    # ── worldline.user_variables.X ─────────────────────
    if path.startswith("worldline.user_variables."):
        key = path[len("worldline.user_variables."):]
        return "set_user_variable", {"key": key, "value": str(value or "")}

    # 其它 (permissions.* / history.* / schema_version / encounter.* / dice_log 等)
    # 没有对应工具 — 应该被 hard_forbidden 或 rules_managed 拦下,这里返回 None
    # 让老路径走自己的检查
    return None


def expand_list_value_to_tool_calls(
    path: str, value: Any, *, op_kind: str = "set", append: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    """对于 list 值的 append 操作 (如 memory.facts=[A,B,C]),展开成多个工具调用。
    标量 value 返回单个调用。无映射时返回 []。
    """
    out: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, list) and value and (op_kind == "append" or append):
        for v in value:
            mapped = map_op_to_tool(path, v, op_kind="set" if path == "world.known_events" else "append",
                                     append=True)
            if mapped:
                out.append(mapped)
    else:
        mapped = map_op_to_tool(path, value, op_kind=op_kind, append=append)
        if mapped:
            out.append(mapped)
    return out


__all__ = ["map_op_to_tool", "expand_list_value_to_tool_calls"]
