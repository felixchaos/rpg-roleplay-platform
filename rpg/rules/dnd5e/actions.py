"""
rules.dnd5e.actions — 攻击/伤害/短休等动作。
"""
from __future__ import annotations

from ..base import RuleResult, StateOp
from ..dice import is_critical_hit, is_critical_miss, parse_expression, roll
from .character import heal, take_damage
from .ruleset import ability_modifier


def damage_roll(expression: str, seed: int | None = None, critical: bool = False) -> dict:
    """掷伤害骰。critical=True 时骰子数 x2（5E 兼容：双倍 dice，不双倍 mod）。"""
    if critical:
        count, sides, mod = parse_expression(expression)
        crit_expr = f"{count * 2}d{sides}{'+' if mod >= 0 else '-'}{abs(mod)}"
        rr = roll(crit_expr, seed=seed)
    else:
        rr = roll(expression, seed=seed)
    d = rr.to_dict()
    d["critical"] = critical
    return d


def attack_roll(
    attacker: dict,
    target: dict,
    attack_bonus: int,
    damage_expr: str,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: int | None = None,
    attacker_name: str | None = None,
    target_name: str | None = None,
    weapon_name: str = "",
) -> RuleResult:
    """完整攻击流程：d20+atk vs AC；命中则 damage_expr 扣 HP；自然 20 暴击。"""
    bonus = int(attack_bonus)
    expr = f"1d20{'+' if bonus >= 0 else '-'}{abs(bonus)}"
    atk = roll(expr, seed=seed, advantage=advantage, disadvantage=disadvantage)
    ac = int((target or {}).get("ac", 10))
    actor = attacker_name or (attacker or {}).get("name", "attacker")
    targ = target_name or (target or {}).get("name", "target")

    state_ops: list[StateOp] = []
    gm_facts: list[str] = []
    damage_info: dict | None = None
    success: bool = False
    critical = is_critical_hit(atk)
    critical_miss = is_critical_miss(atk)

    if critical_miss:
        success = False
        gm_facts.append(f"{actor} 攻击 {targ} 自然 1，彻底落空。")
    elif critical or atk.total >= ac:
        success = True
        # 伤害骰用一个独立 seed 序列（不复用 atk 的 seed，避免确定性骰子和攻击骰耦合）
        dmg_seed = (seed + 1) if isinstance(seed, int) else None
        damage_info = damage_roll(damage_expr, seed=dmg_seed, critical=critical)
        dmg_amount = int(damage_info.get("total", 0))
        if critical:
            gm_facts.append(
                f"{actor} 自然 20 暴击 {targ}：伤害 {dmg_amount}（{damage_expr} 暴击）。"
            )
        else:
            gm_facts.append(
                f"{actor} 用 {weapon_name or '近战攻击'} 命中 {targ}：{atk.total} ≥ AC {ac}，伤害 {dmg_amount}。"
            )
        # 状态变更：仅声明，不直接改 target 引用，由 RulesEngine.apply 在 game state 中落地
        state_ops.append(StateOp(
            op="subtract",
            path=f"_combatant.{(target or {}).get('id', '')}.hp",
            value=dmg_amount,
            reason=f"{actor} → {targ} 伤害",
        ))
    else:
        success = False
        gm_facts.append(f"{actor} 攻击 {targ} 未命中（{atk.total} < AC {ac}）。")

    return RuleResult(
        kind="attack",
        actor=actor,
        target=targ,
        success=success,
        dc=ac,
        roll=atk.to_dict(),
        damage=damage_info,
        state_ops=state_ops,
        gm_facts=gm_facts,
        extra={
            "weapon": weapon_name,
            "attack_bonus": bonus,
            "critical": critical,
            "critical_miss": critical_miss,
        },
    )


def apply_damage(target: dict, amount: int) -> RuleResult:
    """直接对一个 combatant/character dict 扣 HP（陷阱伤害等用）。"""
    name = (target or {}).get("name", "target")
    actual = take_damage(target, amount)
    return RuleResult(
        kind="damage",
        actor="",
        target=name,
        success=actual > 0,
        roll={},
        damage={"amount": actual, "raw": int(amount)},
        gm_facts=[f"{name} 受到 {actual} 点伤害（HP {target.get('hp', 0)}/{target.get('max_hp', 0)}）。"],
    )


def short_rest(character: dict, hit_die: str = "1d8", seed: int | None = None) -> RuleResult:
    """简化短休：花 1 个生命骰 + con 修正。"""
    con_mod = ability_modifier(int((character or {}).get("abilities", {}).get("con", 10)))
    rr = roll(hit_die, seed=seed)
    healed = max(1, rr.total + con_mod)
    actual = heal(character, healed)
    name = (character or {}).get("name", "player")
    return RuleResult(
        kind="short_rest",
        actor=name,
        roll=rr.to_dict(),
        gm_facts=[
            f"{name} 短休回复 {actual} HP（生命骰 {hit_die} + CON 修正 {con_mod}），当前 HP {character.get('hp', 0)}/{character.get('max_hp', 0)}。"
        ],
        extra={"healed": actual, "hit_die": hit_die, "con_mod": con_mod},
    )
