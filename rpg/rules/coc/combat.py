"""coc.combat — CoC 战斗、先攻、攻击、急救、SAN 损失。"""

from __future__ import annotations

import random

from ..base import RuleResult
from ..dice import roll as _roll

SANITY_LOSS_TABLE: list[dict] = [
    {"trigger": "发现被撕碎的动物尸体", "loss_success": "0", "loss_fail": "1d2"},
    {"trigger": "发现尸体的一部分", "loss_success": "0", "loss_fail": "1d3"},
    {"trigger": "看到一地流淌的鲜血", "loss_success": "0", "loss_fail": "1d4"},
    {"trigger": "发现一具血肉模糊的恐怖尸体", "loss_success": "1", "loss_fail": "1d4+1"},
    {"trigger": "醒来时发现被困在棺材中", "loss_success": "0", "loss_fail": "1d6"},
    {"trigger": "目击朋友因暴力而死亡", "loss_success": "0", "loss_fail": "1d6"},
    {"trigger": "目击食尸鬼/深潜者等神话生物", "loss_success": "0", "loss_fail": "1d6"},
    {"trigger": "遇见已知已死之人", "loss_success": "1", "loss_fail": "1d6+1"},
    {"trigger": "遭受严刑拷打", "loss_success": "0", "loss_fail": "1d10"},
    {"trigger": "看到死尸复活", "loss_success": "1", "loss_fail": "1d10"},
    {"trigger": "目击从天而降的尸雨", "loss_success": "2", "loss_fail": "2d10+1"},
    {"trigger": "直视伟大的克苏鲁", "loss_success": "1d10", "loss_fail": "1d100"},
]


def coc_initiative(combatants: list[dict], seed: int | None = None) -> list[dict]:
    """按 DEX 降序排列。DEX 相同时掷 d100 决先后。"""
    if seed is not None:
        random.seed(seed)

    order = sorted(
        combatants,
        key=lambda c: (
            -int((c.get("characteristics") or {}).get("dex", c.get("dex", 50))),
            -_roll("1d100").total,
        ),
    )
    for i, comb in enumerate(order):
        comb["initiative_order"] = i
    return order


def coc_start_encounter(
    party: list[dict], enemies: list[dict], seed: int | None = None, encounter_id: str = ""
) -> dict:
    """创建 CoC 遭遇状态。"""
    for p in party:
        p.setdefault("side", "party")
    for e in enemies:
        e.setdefault("side", "enemy")
    combatants = party + enemies
    initiative_order = coc_initiative(combatants, seed=seed)
    return {
        "active": True,
        "round": 1,
        "turn_index": 0,
        "combatants": combatants,
        "initiative_order": initiative_order,
        "log": [],
        "encounter_id": encounter_id,
        "outcome": None,
    }


def coc_attack_roll(
    attacker: dict,
    target: dict,
    attack_bonus: int,
    damage_expr: str,
    advantage: bool = False,
    seed: int | None = None,
    attacker_name: str | None = None,
    target_name: str | None = None,
    weapon_name: str = "",
) -> RuleResult:
    """CoC 攻击判定：d100 ≤ 攻击技能值。

    attack_bonus: 在 CoC 中表示攻击技能%（如格斗 50% 则传 50）
    damage_expr: 伤害表达式（如 "1d6+1d4"）
    """
    if seed is not None:
        random.seed(seed)

    skill_pct = attack_bonus
    result = _roll("1d100", seed=seed, advantage=advantage)
    hit = result.total <= skill_pct

    damage_roll = None
    damage_total = 0
    if hit:
        damage_roll = _roll(damage_expr, seed=seed)
        damage_total = damage_roll.total
        db = attacker.get("db", "0")
        if db and db not in ("0", "-1", "-2"):
            db_val = db.lstrip("+")
            try:
                db_roll = _roll(db_val, seed=seed)
                damage_total += db_roll.total
            except ValueError:
                pass

    return RuleResult(
        kind="attack",
        actor=attacker_name or attacker.get("name", ""),
        target=target_name or target.get("name", ""),
        roll=result.to_dict(),
        dc=skill_pct,
        success=hit,
        damage={
            "expression": damage_expr,
            "total": damage_total,
        },
        extra={
            "weapon": weapon_name,
            "attack_skill": skill_pct,
        },
        gm_facts=[
            f"{attacker_name or attacker.get('name', '?')} 使用{weapon_name}，"
            + (f"命中！造成 {damage_total} 点伤害。" if hit else "未命中。")
        ],
        state_ops=[],
    )


def coc_apply_damage(target: dict, amount: int) -> RuleResult:
    """应用伤害，护甲减免。检测重伤。"""
    armor = target.get("armor", 0)
    actual = max(0, amount - armor)
    hp = target.get("hp", 0)
    target["hp"] = max(0, hp - actual)

    max_hp = target.get("max_hp", hp) or 1
    is_major = actual >= max_hp // 2

    return RuleResult(
        kind="damage",
        actor=None,
        target=target.get("name", ""),
        roll=None,
        dc=None,
        success=True,
        damage={"total": actual, "armor_blocked": min(armor, amount)},
        extra={
            "is_major_wound": is_major,
            "hp_after": target["hp"],
            "hp_max": max_hp,
        },
        gm_facts=[
            f"{target.get('name', '?')} 受 {actual} 伤害（护甲减 {min(armor, amount)}），"
            + ("重伤！" if is_major else f"HP {target['hp']}/{max_hp}。")
        ],
        state_ops=[],
    )


def coc_first_aid(character: dict, seed: int | None = None) -> RuleResult:
    """First Aid：医学检定成功后回 1 HP。"""
    if seed is not None:
        random.seed(seed)

    skill = character.get("skills", {}).get("first_aid", 30)
    check = _roll("1d100", seed=seed)
    success = check.total <= skill

    if success:
        heal = 1
        hp = character.get("hp", 0)
        character["hp"] = min(character.get("max_hp", hp), hp + heal)
    else:
        heal = 0

    return RuleResult(
        kind="short_rest",
        actor=character.get("name", ""),
        target=None,
        roll=check.to_dict(),
        dc=skill,
        success=success,
        extra={
            "heal_amount": heal,
            "check_skill": "first_aid",
        },
        gm_facts=[f"急救 {'成功' if success else '失败'}，回复 {heal} HP。"],
        state_ops=[],
    )


def coc_next_turn(encounter: dict) -> dict:
    """推进到下一回合。按先攻顺序轮流。"""
    combatants = encounter.get("combatants", [])
    alive = [c for c in combatants if not c.get("defeated")]
    if not alive:
        return encounter
    turn_idx = int(encounter.get("turn_index", 0))
    next_idx = (turn_idx + 1) % len(alive)
    encounter["turn_index"] = next_idx
    if next_idx == 0:
        encounter["round"] = int(encounter.get("round", 1)) + 1
    return encounter


def coc_is_encounter_resolved(encounter: dict) -> tuple[bool, str]:
    """检查遭遇是否已结束。"""
    combatants = encounter.get("combatants", [])
    party_alive = any(not c.get("defeated") and c.get("side") == "party" for c in combatants)
    enemies_alive = any(not c.get("defeated") and c.get("side") == "enemy" for c in combatants)
    if not enemies_alive:
        return True, "party_victory"
    if not party_alive:
        return True, "party_defeat"
    return False, "ongoing"


def coc_mark_defeated_by_hp(encounter: dict) -> list[str]:
    """标记 HP ≤ 0 的单位为 defeated。"""
    defeated_ids = []
    for c in encounter.get("combatants", []):
        if c.get("hp", 0) <= 0 and not c.get("defeated"):
            c["defeated"] = True
            c["conditions"] = list(c.get("conditions", [])) + ["dying"]
            defeated_ids.append(c.get("id", c.get("name", "?")))
    return defeated_ids


def _parse_sanity_loss(expr: str, success: bool) -> str:
    """解析 "0/1d6" 格式的 SAN 损失表达式。"""
    if not expr or expr == "0":
        return "0"
    if "/" in expr:
        parts = expr.split("/")
        return parts[0].strip() if success else parts[1].strip()
    return expr.strip()


def apply_sanity_loss(
    character: dict, loss_expr: str, success: bool, seed: int | None = None
) -> dict:
    """应用 SAN 损失。

    loss_expr: 格式如 "0/1d6"（成功时 / 失败时）。
    返回 {san_lost, san_after, indefinite, temporary, description}。
    """
    if seed is not None:
        random.seed(seed)

    expr = _parse_sanity_loss(loss_expr, success)
    if not expr or expr == "0":
        return {
            "san_lost": 0,
            "san_after": character.get("san", 0),
            "indefinite": False,
            "temporary": False,
            "description": "未损失 SAN",
        }

    loss = _roll(expr, seed=seed).total if "d" in expr else int(expr)
    san = character.get("san", 0)
    new_san = max(0, san - loss)
    character["san"] = new_san

    indefinite = loss >= (character.get("max_san", 99) // 5)
    temporary = loss >= 5

    desc_parts = [f"损失 {loss} SAN"]
    if indefinite:
        desc_parts.append("→ 不定期疯狂")
    elif temporary:
        desc_parts.append("→ 临时疯狂（需 INT 检定成功才触发）")

    return {
        "san_lost": loss,
        "san_after": new_san,
        "indefinite": indefinite,
        "temporary": temporary,
        "description": "；".join(desc_parts),
    }
