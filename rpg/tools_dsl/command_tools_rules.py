"""
command_tools_rules.py — task 87 Phase 2.3: 5E 模组规则工具表。

把 /api/rules/* 系列端点改造成 LLM 可调工具:
  · module_load          模组加载到当前 save
  · module_launch        模组启动 (高层入口,会初始化角色卡 + 入场)
  · module_enter_room    切换房间
  · combat_start         开打 (按 encounter_id)
  · combat_next_turn     推进回合
  · combat_player_attack 玩家攻击
  · combat_enemy_attack  敌方回合攻击
  · skill_check          技能检定
  · saving_throw         豁免检定
  · short_rest           短休
  · consume_item         消耗物品

所有工具 save 级,执行规则函数后会:
  1. 把结果写回 state.permissions.audit_log 的 rule_receipt
  2. 清掉已过期的 pending_questions
  3. 触发 state.save() 持久化
这些与原 HTTP 端点一致。
"""
from __future__ import annotations

from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry


# 5E 模组工具默认 origin: UI / API / LLM 都可调,但战斗具体动作禁止 llm_chat
# (LLM 必须明确通过 /set 或 UI 显式按按钮才能动 HP/initiative)。
# task 62: 移除 console_assistant — 模组装载/战斗都是 save 内行为,
# 控制台助手是跨 save 资源管理,不该越界触发战斗 / move 房间。
# 用户想玩模组就去 Game Console 走 GM。
_RULES_FULL_ORIGINS = frozenset({"ui_button", "api_direct", "llm_set", "llm_chat_json_op"})
_RULES_LLM_CHAT_ALLOWED_ORIGINS = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat", "llm_chat_json_op",
})


# ── 工具执行器 ───────────────────────────────────────────


def _t_module_load(state: Any, args: dict) -> str:
    mod_id = (args.get("module_id") or "").strip()
    if not mod_id:
        return "失败: module_id 为空"
    overrides = args.get("character_overrides") or None
    try:
        from rules_bridge import start_module
        result = start_module(state, mod_id, character_overrides=overrides)
        if not result.get("ok"):
            return f"失败: {result.get('error') or '未知错误'}"
        return f"模组 {mod_id} 已加载 (起点房间: {result.get('start_location_id', '?')})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_module_enter_room(state: Any, args: dict) -> str:
    loc = (args.get("location_id") or "").strip()
    if not loc:
        return "失败: location_id 为空"
    try:
        from rules_bridge import enter_room
        res = enter_room(state, loc)
        if not res.get("ok"):
            return f"失败: {res.get('error') or '未知错误'}"
        return f"进入房间 {loc} ✓"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_combat_start(state: Any, args: dict) -> str:
    enc_id = (args.get("encounter_id") or "").strip()
    if not enc_id:
        return "失败: encounter_id 为空"
    seed = args.get("seed")
    seed_int = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    try:
        from rules_bridge import start_encounter_by_id
        res = start_encounter_by_id(state, enc_id, seed=seed_int)
        if not res.get("ok"):
            return f"失败: {res.get('error') or '未知错误'}"
        return f"战斗已开始: {enc_id} (先攻顺序已就绪)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_combat_next_turn(state: Any, args: dict) -> str:
    try:
        from rules_bridge import advance_turn
        res = advance_turn(state)
        if not res.get("ok"):
            return f"失败: {res.get('error') or '未知错误'}"
        enc = res.get("encounter") or {}
        return f"推进到下一回合 (round={enc.get('round','?')}, turn_index={enc.get('turn_index','?')})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_combat_player_attack(state: Any, args: dict) -> str:
    target = (args.get("target_id") or "").strip()
    weapon = (args.get("weapon_id") or "shortsword").strip()
    seed = args.get("seed")
    seed_int = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    if not target:
        return "失败: target_id 为空"
    try:
        from rules_bridge import player_attack
        res = player_attack(state, target_id=target, weapon_id=weapon, seed=seed_int)
        if not res.get("ok"):
            return f"失败: {res.get('error') or '未知错误'}"
        r = res.get("result") or {}
        return f"攻击 {target} 用 {weapon}: {r.get('summary') or r}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_combat_enemy_attack(state: Any, args: dict) -> str:
    attacker = (args.get("attacker_id") or "").strip()
    target = (args.get("target_id") or "player").strip()
    seed = args.get("seed")
    seed_int = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    if not attacker:
        return "失败: attacker_id 为空"
    try:
        from rules_bridge import enemy_attack
        res = enemy_attack(state, attacker_id=attacker, target_id=target, seed=seed_int)
        if not res.get("ok"):
            return f"失败: {res.get('error') or '未知错误'}"
        return f"敌方 {attacker} → {target}: {(res.get('result') or {}).get('summary') or '完成'}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_skill_check(state: Any, args: dict) -> str:
    skill = (args.get("skill") or "").strip()
    dc = args.get("dc")
    if not skill or not isinstance(dc, (int, float)):
        return "失败: skill / dc 缺失"
    seed = args.get("seed")
    seed_int = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    try:
        from rules_bridge import perform_skill_check
        res = perform_skill_check(state, skill_id=skill, dc=int(dc), seed=seed_int)
        return f"{skill} 检定 DC={dc}: {res.get('summary') or res}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_saving_throw(state: Any, args: dict) -> str:
    save = (args.get("save") or "").strip()
    dc = args.get("dc")
    if not save or not isinstance(dc, (int, float)):
        return "失败: save / dc 缺失"
    seed = args.get("seed")
    seed_int = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    try:
        from rules_bridge import perform_saving_throw
        res = perform_saving_throw(state, save_id=save, dc=int(dc), seed=seed_int)
        return f"{save} 豁免 DC={dc}: {res.get('summary') or res}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_short_rest(state: Any, args: dict) -> str:
    seed = args.get("seed")
    seed_int = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    try:
        from rules_bridge import short_rest
        res = short_rest(state, seed=seed_int)
        return f"短休完成: {res.get('summary') or '已恢复'}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_consume_item(state: Any, args: dict) -> str:
    item_id = (args.get("item_id") or "").strip()
    qty = args.get("qty") or 1
    if not item_id:
        return "失败: item_id 为空"
    seed = args.get("seed")
    seed_int = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    try:
        from rules_bridge import consume_item_action
        res = consume_item_action(state, item_id=item_id, qty=int(qty), seed=seed_int)
        if not res.get("ok"):
            return f"失败: {res.get('error') or '未知错误'}"
        return f"消耗 {item_id} ×{qty}: {res.get('summary') or '完成'}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ── 注册 ─────────────────────────────────────────────────


def register_rules_tools() -> None:
    registry = get_registry()
    specs: list[ToolSpec] = [
        ToolSpec(
            name="module_load",
            description="把指定 5E 模组加载到当前 save (初始化角色卡 + 入场房间)。",
            input_schema={
                "type": "object",
                "properties": {
                    "module_id": {"type": "string"},
                    "character_overrides": {"type": "object",
                                             "description": "可选: 覆盖默认角色卡属性"},
                },
                "required": ["module_id"],
            },
            executor=_t_module_load,
            scope="save",
            origins=_RULES_FULL_ORIGINS,
            destructive=True,  # 改写整张角色卡,LLM 不直接调
        ),
        ToolSpec(
            name="module_enter_room",
            description="把玩家移动到指定 location_id 的房间。location_id 必须是当前模组中存在的房间。",
            input_schema={
                "type": "object",
                "properties": {"location_id": {"type": "string"}},
                "required": ["location_id"],
            },
            executor=_t_module_enter_room,
            scope="save",
            origins=_RULES_LLM_CHAT_ALLOWED_ORIGINS,
        ),
        ToolSpec(
            name="combat_start",
            description="按 encounter_id 启动战斗,计算先攻顺序。",
            input_schema={
                "type": "object",
                "properties": {
                    "encounter_id": {"type": "string"},
                    "seed": {"type": "integer", "description": "可选 RNG seed,测试用"},
                },
                "required": ["encounter_id"],
            },
            executor=_t_combat_start,
            scope="save",
            origins=_RULES_LLM_CHAT_ALLOWED_ORIGINS,
        ),
        ToolSpec(
            name="combat_next_turn",
            description="把战斗推进到下一回合 (先攻顺序前进一位)。",
            input_schema={"type": "object", "properties": {}, "required": []},
            executor=_t_combat_next_turn,
            scope="save",
            origins=_RULES_LLM_CHAT_ALLOWED_ORIGINS,
        ),
        ToolSpec(
            name="combat_player_attack",
            description="玩家用指定武器攻击指定目标。HP/AC 等数值由 RulesEngine 决定。",
            input_schema={
                "type": "object",
                "properties": {
                    "target_id": {"type": "string"},
                    "weapon_id": {"type": "string", "default": "shortsword"},
                    "seed": {"type": "integer"},
                },
                "required": ["target_id"],
            },
            executor=_t_combat_player_attack,
            scope="save",
            origins=_RULES_FULL_ORIGINS,  # llm_chat 不直接调战斗,要走 chat 路径
        ),
        ToolSpec(
            name="combat_enemy_attack",
            description="敌方回合 — 指定敌人攻击玩家(用于推进战斗 demo)。",
            input_schema={
                "type": "object",
                "properties": {
                    "attacker_id": {"type": "string"},
                    "target_id": {"type": "string", "default": "player"},
                    "seed": {"type": "integer"},
                },
                "required": ["attacker_id"],
            },
            executor=_t_combat_enemy_attack,
            scope="save",
            origins=_RULES_FULL_ORIGINS,
        ),
        ToolSpec(
            name="skill_check",
            description="技能检定 (perform_skill_check)。",
            input_schema={
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "dc": {"type": "integer"},
                    "seed": {"type": "integer"},
                },
                "required": ["skill", "dc"],
            },
            executor=_t_skill_check,
            scope="save",
            origins=_RULES_LLM_CHAT_ALLOWED_ORIGINS,
        ),
        ToolSpec(
            name="saving_throw",
            description="豁免检定 (perform_saving_throw)。",
            input_schema={
                "type": "object",
                "properties": {
                    "save": {"type": "string"},
                    "dc": {"type": "integer"},
                    "seed": {"type": "integer"},
                },
                "required": ["save", "dc"],
            },
            executor=_t_saving_throw,
            scope="save",
            origins=_RULES_LLM_CHAT_ALLOWED_ORIGINS,
        ),
        ToolSpec(
            name="short_rest",
            description="短休: 恢复 HP / 资源(按 5E 规则)。",
            input_schema={
                "type": "object",
                "properties": {"seed": {"type": "integer"}},
                "required": [],
            },
            executor=_t_short_rest,
            scope="save",
            origins=_RULES_LLM_CHAT_ALLOWED_ORIGINS,
        ),
        ToolSpec(
            name="consume_item",
            description="消耗物品(药水/食物等)。",
            input_schema={
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "qty": {"type": "integer", "default": 1, "minimum": 1},
                    "seed": {"type": "integer"},
                },
                "required": ["item_id"],
            },
            executor=_t_consume_item,
            scope="save",
            origins=_RULES_LLM_CHAT_ALLOWED_ORIGINS,
        ),
    ]
    for spec in specs:
        if not registry.has(spec.name):
            registry.register(spec)


__all__ = ["register_rules_tools"]
