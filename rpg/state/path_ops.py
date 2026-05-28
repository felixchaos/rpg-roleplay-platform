"""state/path_ops.py — 路径操作 helpers (_clean_path, _write_path_*, _set_path, _get_path, _module_scene_active)"""
from __future__ import annotations

from typing import Any


def _clean_path(path: str) -> str:
    import re
    path = re.sub(r"\s+", "", str(path).strip())
    aliases = {
        "姓名": "player.name",
        "角色": "player.role",
        "定位": "player.role",
        "背景": "player.background",
        "当前位置": "player.current_location",
        "位置": "player.current_location",
        "当前时间线": "world.time",
        "时间线": "world.time",
        "当前目标": "memory.current_objective",
        "目标": "memory.current_objective",
        "主线": "memory.main_quest",
        "记忆模式": "memory.mode",
        "权限": "permissions.mode",
    }
    return aliases.get(path, path)


_HARD_FORBIDDEN_PATHS = {"schema_version", "history", "created_at", "is_new"}
_HARD_FORBIDDEN_PREFIXES = ("history.", "permissions.")

# 5E-compatible 规则受控字段。这些路径只能由 RulesEngine（source="rules_engine"
# 或 source 以 "rules_engine" 开头）改写。GM 自由写入 / 用户 /set 都被拒绝并 audit，
# 防止 LLM 自行编造 HP/AC/initiative 等硬数值。
_RULES_MANAGED_PATHS = {
    "player_character.hp",
    "player_character.max_hp",
    "player_character.ac",
    "player_character.inventory",  # Bug 5：canonical inventory，只允许 RulesEngine 写
    "encounter.active",
    "encounter.round",
    "encounter.turn_index",
    "encounter.initiative_order",
    "encounter.combatants",
    "encounter.encounter_id",
    "encounter.log",
    "dice_log",
}
_RULES_MANAGED_PREFIXES = (
    "encounter.combatants.",
    "encounter.initiative_order.",
    "dice_log.",
    "player_character.conditions",  # 条件由 rules 触发（中毒等）
    "player_character.inventory.",  # Bug 5：inventory 子路径也锁住
)

_MODULE_MANAGED_PATHS = {
    "player.current_location",
}


def _write_path_hard_forbidden(path: str) -> bool:
    """绝对不能写的路径，无论权限模式或 force 标志。

    permissions.* — 用户/GM 自己改权限模式 = 整套审批失效（自我提权）
    history.*     — 改对话历史 = 篡改可见证据
    schema_version / created_at / is_new — 元数据，破坏会让 state 反序列化崩
    """
    return path in _HARD_FORBIDDEN_PATHS or path.startswith(_HARD_FORBIDDEN_PREFIXES)


def _write_path_rules_managed(path: str) -> bool:
    """5E 规则受控路径。任何非 rules_engine 来源写入都会被 State Gate 拒绝。"""
    if path in _RULES_MANAGED_PATHS:
        return True
    return any(path == prefix.rstrip(".") or path.startswith(prefix) for prefix in _RULES_MANAGED_PREFIXES)


def _write_path_module_managed(path: str) -> bool:
    return path in _MODULE_MANAGED_PATHS


def _module_scene_active(data: dict) -> bool:
    try:
        return bool((data.get("scene") or {}).get("module_id"))
    except Exception:
        return False


def _write_path_allowed(path: str, mode: str) -> bool:
    from state.permissions import _normalize_permission_mode
    mode = _normalize_permission_mode(mode)
    if _write_path_hard_forbidden(path):
        return False
    # task 53：新增 read_only 模式 — 对齐 codex 的 suggest 模式。
    # 任何 LLM 自动写入都入 pending，不立即应用；玩家完全掌控。
    # /set（force=True）仍能通过，让玩家维护自己的状态。
    if mode == "read_only":
        return False
    if mode == "full_access":
        return True
    if path.startswith("worldline.custom_ui.") or path.startswith("ui."):
        return mode == "full_access"
    allowed = {
        "player.name",
        "player.role",
        "player.background",
        "player.current_location",
        "world.time",
        "world.timeline.current_phase",
        "world.timeline.anchor_state",
        "world.known_events",
        "memory.mode",
        "memory.main_quest",
        "memory.current_objective",
        "memory.resources",
        "memory.abilities",
        "memory.facts",
        "memory.pinned",
        "memory.notes",
    }
    if mode == "auto_review":
        return path in allowed or path.startswith("relationships.") or path.startswith("worldline.user_variables.")
    if mode == "default":
        return path in {
            "player.current_location",
            "world.time",
            "memory.main_quest",
            "memory.current_objective",
            "memory.resources",
            "memory.abilities",
            "memory.facts",
            "world.known_events",
        } or path.startswith("relationships.")
    return False


def _write_path_kind(path: str) -> str:
    if path == "player.current_location":
        return "location"
    if path == "world.time":
        return "time"
    if path in {"world.known_events", "memory.resources", "memory.abilities", "memory.facts", "memory.pinned", "memory.notes"}:
        return "list"
    if path.startswith("relationships."):
        return "relationship"
    if path.startswith("worldline.user_variables."):
        return "user_variable"
    if path.startswith("worldline.custom_ui.") or path.startswith("ui."):
        return "custom_ui"
    return "scalar"


def _set_path(root: dict, path: str, value: Any):
    parts = path.split(".")
    target = root
    for part in parts[:-1]:
        if not isinstance(target.get(part), dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value


def _get_path(root: dict, path: str) -> Any:
    target: Any = root
    for part in path.split("."):
        if not isinstance(target, dict):
            return None
        target = target.get(part)
    return target
