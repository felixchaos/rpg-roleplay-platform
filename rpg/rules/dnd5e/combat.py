"""
rules.dnd5e.combat — 战斗遭遇状态。
"""
from __future__ import annotations

from typing import Optional

from ..base import RuleResult, StateOp
from ..dice import roll
from .ruleset import ability_modifier


def initiative(combatants: list[dict], seed: int | None = None) -> list[dict]:
    """对每个 combatant 掷 1d20+DEX mod 决定先攻顺序，返回排序后的 [{id, name, init, side}] 列表。"""
    rolls: list[dict] = []
    for idx, c in enumerate(combatants or []):
        dex = int((c.get("abilities") or {}).get("dex", 10))
        mod = ability_modifier(dex)
        sub_seed = (seed + idx) if isinstance(seed, int) else None
        rr = roll(f"1d20{'+' if mod >= 0 else '-'}{abs(mod)}", seed=sub_seed)
        rolls.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "side": c.get("side", "enemy"),
            "init": rr.total,
            "dex_mod": mod,
            "roll": rr.to_dict(),
        })
    # init 大 → 小；同分用 dex_mod；再同分保持原序
    rolls.sort(key=lambda r: (-(r["init"]), -(r["dex_mod"])))
    return rolls


def start_encounter(
    party: list[dict],
    enemies: list[dict],
    seed: int | None = None,
    encounter_id: str = "",
) -> dict:
    """生成 encounter 状态字典。party 通常只有玩家。"""
    combatants: list[dict] = []
    for p in party or []:
        combatants.append({
            "id": p.get("id") or "player",
            "name": p.get("name") or "Player",
            "side": "party",
            "hp": int(p.get("hp", 0)),
            "max_hp": int(p.get("max_hp", 0)),
            "ac": int(p.get("ac", 10)),
            "abilities": dict(p.get("abilities", {})),
            "conditions": list(p.get("conditions", [])),
            "defeated": False,
            "stat_block_id": "player",
        })
    for e in enemies or []:
        combatants.append({
            "id": e.get("id"),
            "name": e.get("name"),
            "side": "enemy",
            "hp": int(e.get("hp", e.get("max_hp", 1))),
            "max_hp": int(e.get("max_hp", e.get("hp", 1))),
            "ac": int(e.get("ac", 10)),
            "abilities": dict(e.get("abilities", {})),
            "attacks": list(e.get("attacks", [])),
            "conditions": list(e.get("conditions", [])),
            "defeated": False,
            "stat_block_id": e.get("stat_block_id", ""),
        })

    init_order = initiative(combatants, seed=seed)
    return {
        "active": True,
        "round": 1,
        "turn_index": 0,
        "initiative_order": init_order,
        "combatants": combatants,
        "encounter_id": encounter_id,
        "log": [],
    }


def next_turn(encounter: dict) -> dict:
    """推进到下一个未阵亡战斗员的回合。返回更新后的 encounter dict。"""
    if not encounter or not encounter.get("active"):
        return encounter
    order = encounter.get("initiative_order") or []
    combatants_by_id = {c["id"]: c for c in encounter.get("combatants", [])}
    n = len(order)
    if n == 0:
        encounter["active"] = False
        return encounter

    turn_index = int(encounter.get("turn_index", 0))
    round_no = int(encounter.get("round", 1))

    # 找下一个 alive 战斗员
    for _ in range(n + 1):
        turn_index += 1
        if turn_index >= n:
            turn_index = 0
            round_no += 1
            if round_no > 50:
                # 防御性兜底
                encounter["active"] = False
                encounter["round"] = round_no
                encounter["turn_index"] = turn_index
                return encounter
        cur = order[turn_index]
        comb = combatants_by_id.get(cur["id"])
        if comb and not comb.get("defeated") and int(comb.get("hp", 0)) > 0:
            break
    encounter["turn_index"] = turn_index
    encounter["round"] = round_no
    return encounter


def is_encounter_resolved(encounter: dict) -> tuple[bool, str]:
    """判断战斗是否结束。返回 (resolved, outcome)。outcome ∈ "victory"/"defeat"/"ongoing"。"""
    if not encounter or not encounter.get("active"):
        return True, "ongoing"
    combs = encounter.get("combatants") or []
    party_alive = any(c.get("side") == "party" and int(c.get("hp", 0)) > 0 and not c.get("defeated") for c in combs)
    enemies_alive = any(c.get("side") == "enemy" and int(c.get("hp", 0)) > 0 and not c.get("defeated") for c in combs)
    if not enemies_alive and party_alive:
        return True, "victory"
    if not party_alive:
        return True, "defeat"
    return False, "ongoing"


def mark_defeated_by_hp(encounter: dict) -> list[str]:
    """扫描 combatants，把 hp<=0 的标 defeated。返回新被标记的 id 列表。"""
    newly: list[str] = []
    for c in encounter.get("combatants", []):
        if int(c.get("hp", 0)) <= 0 and not c.get("defeated"):
            c["defeated"] = True
            newly.append(c.get("id", ""))
    return newly
