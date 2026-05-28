"""suggest.py — 基于关键词的规则候选动作生成器。"""
from __future__ import annotations

import re

import modules as module_registry

from rules_bridge.intent import (
    _has_movement_intent,
    _direction_to_exit,
)


# ── 简易意图 → 候选规则动作 ─────────────────────────────────────

INTENT_KEYWORDS: list[tuple[str, dict]] = [
    # 潜行 / 隐蔽 / 悄悄
    (r"(悄悄|潜行|隐蔽|偷偷|不被发现|溜过去)", {"kind": "skill_check", "skill": "stealth", "dc_hint": 13}),
    # 调查 / 搜查 / 查看细节
    (r"(调查|搜查|查看|检查|搜索|翻找)", {"kind": "skill_check", "skill": "investigation", "dc_hint": 12}),
    # 察觉 / 倾听 / 留意
    (r"(察觉|留意|倾听|听一下|发现|观察)", {"kind": "skill_check", "skill": "perception", "dc_hint": 12}),
    # 攀爬 / 跳跃 / 强力
    (r"(攀爬|爬上|跳过|破门|撞开|蛮力)", {"kind": "skill_check", "skill": "athletics", "dc_hint": 12}),
    # 说服 / 谈判 / 投降 / 求饶 — 都走 Persuasion 检定 vs NPC disposition。
    # 投降不是"自动接受",而是要看敌人愿不愿放过 (敌对教派可能直接处决)。
    (r"(说服|谈判|交涉|劝说|投降|求饶|放下武器|举起?双?手|跪下投降|请降|求和)",
        {"kind": "skill_check", "skill": "persuasion", "dc_hint": 14}),
    # 欺骗
    (r"(欺骗|撒谎|装作|伪装|装成)", {"kind": "skill_check", "skill": "deception", "dc_hint": 13}),
    # 挣脱 / 反抗约束 / 摆脱抓握 — Athletics 检定 (escape grapple)
    (r"(挣脱|挣开|挣扎|甩开|摆脱抓握|脱困|逃脱束缚)",
        {"kind": "skill_check", "skill": "athletics", "dc_hint": 13}),
    # 威胁 / 恐吓
    (r"(威胁|恐吓|逼问)", {"kind": "skill_check", "skill": "intimidation", "dc_hint": 13}),
    # 攻击
    (r"(攻击|砍|射|刺|杀|出手|短弓|短剑|远程攻击|近战攻击)", {"kind": "attack", "weapon_hint": "shortsword"}),
    # 短休
    (r"(短休|休息|歇一下)", {"kind": "short_rest"}),
    # Bug 4：移动意图。匹配「沿/往/向 ... 探索/前进/走/去」等，落到当前房间的某个 exit。
    # 真实 exit 由 suggest_rule_actions 内的 _direction_to_exit() 解析；这里只是触发器。
    (r"(沿|往|向|去|前往|走向|前进|探索|进入)", {"kind": "move", "_direction_hint": True}),
]


def _triggered_encounter_id(state) -> str:
    scene = state.data.get("scene") or {}
    module_id = scene.get("module_id")
    if not module_id:
        return ""
    try:
        bundle = module_registry.load_module(module_id)
    except Exception:
        return ""
    flags = scene.get("flags") or {}
    active_flags = {k for k, v in flags.items() if v}
    location_id = scene.get("location_id")
    encounters = bundle.get("encounters") or []
    for enc in encounters:
        trigger = enc.get("trigger")
        if trigger and trigger in active_flags:
            return enc.get("id") or ""
    for enc in encounters:
        if enc.get("location_id") == location_id:
            return enc.get("id") or ""
    return ""


def _weapon_from_text(text: str) -> str:
    if any(token in text for token in ("短弓", "弓", "远程", "射", "箭")):
        return "shortbow"
    if any(token in text for token in ("短剑", "剑", "近战", "刺", "砍")):
        return "shortsword"
    return "shortsword"


def suggest_rule_actions(user_input: str, state) -> list[dict]:
    """根据用户输入文本和当前 scene 上下文，生成规则候选动作列表。

    这是简易的关键词匹配。真实场景由 LLM Demand Resolver 输出 rule_candidate_actions，
    但本函数提供 fallback 与基础线索（也方便测试）。
    """
    import re as _re
    out: list[dict] = []
    if not user_input:
        return out
    text = str(user_input)
    scene = state.data.get("scene") or {}
    current_room = scene.get("current_room") or {}
    location_id = scene.get("location_id")
    rooms_by_id: dict[str, dict] = {}
    module_id = scene.get("module_id")
    if module_id:
        try:
            rooms_by_id = {
                r.get("id"): r
                for r in (module_registry.load_module(module_id).get("rooms") or [])
                if r.get("id")
            }
        except Exception:
            rooms_by_id = {}
    for pattern, template in INTENT_KEYWORDS:
        if _re.search(pattern, text):
            action = dict(template)
            action["matched"] = pattern
            action["reason"] = f"匹配关键词「{pattern}」"
            # 如果当前房间有该 skill 的 check，借用 DC
            if action.get("kind") == "skill_check":
                target_skill = action["skill"]
                matched_check = False
                for chk in current_room.get("checks", []):
                    if chk.get("kind") == "skill_check" and chk.get("skill") == target_skill:
                        action["dc"] = chk.get("dc", action.get("dc_hint", 12))
                        action["target"] = location_id
                        action["sets_flag"] = chk.get("sets_flag")
                        action["fact"] = chk.get("fact")
                        matched_check = True
                        break
                # Bug 2 (retest)：只在玩家文本明确含『移动意图』时才跨房间扫描。
                # 之前"观察灌木"在 minecart_track（无 perception check）也触发跨房 fallback
                # → 错误地把玩家移回 mine_entrance 找 perception。
                # 现在原地无 check 时就让 GM 用默认 dc_hint 在当前房间做检定。
                if not matched_check and rooms_by_id and _has_movement_intent(text):
                    for ex in current_room.get("exits", []) or []:
                        room = rooms_by_id.get(ex.get("to"))
                        if not room:
                            continue
                        for chk in room.get("checks", []) or []:
                            if chk.get("kind") == "skill_check" and chk.get("skill") == target_skill:
                                action["dc"] = chk.get("dc", action.get("dc_hint", 12))
                                action["target"] = room.get("id")
                                action["move_to"] = room.get("id")
                                action["sets_flag"] = chk.get("sets_flag")
                                action["fact"] = chk.get("fact")
                                action["reason"] = f"{action['reason']}；目标在相邻房间「{room.get('name') or room.get('id')}」"
                                matched_check = True
                                break
                        if matched_check:
                            break
                action.setdefault("dc", action.get("dc_hint", 12))
                if not action.get("target"):
                    # 原地无 check 也要落地：target 设为当前房间，用 dc_hint 默认
                    action["target"] = location_id
            elif action.get("kind") == "attack":
                # 当前房间有敌人或战斗激活时才是合法的
                action["weapon"] = _weapon_from_text(text)
                enc = state.data.get("encounter") or {}
                if enc.get("active"):
                    enemies = [c for c in enc.get("combatants", []) if c.get("side") == "enemy" and not c.get("defeated")]
                    if enemies:
                        action["target"] = enemies[0].get("id")
                        action["target_name"] = enemies[0].get("name")
                else:
                    encounter_id = _triggered_encounter_id(state)
                    if encounter_id:
                        action["encounter_id"] = encounter_id
            elif action.get("kind") == "move":
                # Bug 4：把方向词解析到当前房间真实 exit id；无法解析就跳过。
                exit_id = _direction_to_exit(text, current_room)
                action.pop("_direction_hint", None)
                if not exit_id:
                    continue
                action["to"] = exit_id
                action["target"] = exit_id
                # 给 exit 名作为 reason 让 GM 知道这是规范化后的结果
                for ex in current_room.get("exits") or []:
                    if ex.get("to") == exit_id:
                        action["reason"] = f"方向词→出口『{ex.get('label') or exit_id}』"
                        break
            out.append(action)
    # 去重（按 kind+skill）
    seen = set()
    deduped: list[dict] = []
    for a in out:
        key = (a.get("kind"), a.get("skill"), a.get("target"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    return deduped
