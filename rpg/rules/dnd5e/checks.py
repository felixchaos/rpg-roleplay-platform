"""
rules.dnd5e.checks — 技能检定与豁免。纯函数。
"""
from __future__ import annotations

from typing import Optional

from ..base import RuleResult, StateOp
from ..dice import roll
from .character import saving_throw_modifier, skill_modifier
from .ruleset import ABILITIES, normalize_skill


def skill_check(
    character: dict,
    skill: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: int | None = None,
    actor_name: str | None = None,
    reason: str = "",
) -> RuleResult:
    """对 `character` 用 `skill` 做 d20 检定，目标 DC。返回标准 RuleResult。"""
    skill = normalize_skill(skill)
    mod = skill_modifier(character, skill)
    expr = f"1d20{'+' if mod >= 0 else '-'}{abs(mod)}"
    rr = roll(expr, seed=seed, advantage=advantage, disadvantage=disadvantage)
    success = rr.total >= int(dc)

    actor = actor_name or (character or {}).get("name") or "player"
    fact_skill = skill.replace("_", " ")
    if success:
        gm_fact = f"{actor} 的 {fact_skill} 检定成功（{rr.total} ≥ DC {dc}）。"
    else:
        gm_fact = f"{actor} 的 {fact_skill} 检定失败（{rr.total} < DC {dc}）。"

    return RuleResult(
        kind="skill_check",
        actor=actor,
        target=reason or "",
        success=success,
        dc=int(dc),
        roll=rr.to_dict(),
        gm_facts=[gm_fact],
        extra={"skill": skill, "modifier": mod, "reason": reason},
    )


def saving_throw(
    character: dict,
    ability: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: int | None = None,
    actor_name: str | None = None,
    reason: str = "",
) -> RuleResult:
    """属性豁免。"""
    ability = (ability or "").lower()
    if ability not in ABILITIES:
        raise ValueError(f"未知属性：{ability}")
    mod = saving_throw_modifier(character, ability)
    expr = f"1d20{'+' if mod >= 0 else '-'}{abs(mod)}"
    rr = roll(expr, seed=seed, advantage=advantage, disadvantage=disadvantage)
    success = rr.total >= int(dc)

    actor = actor_name or (character or {}).get("name") or "player"
    if success:
        gm_fact = f"{actor} 通过了 {ability.upper()} 豁免（{rr.total} ≥ DC {dc}）。"
    else:
        gm_fact = f"{actor} 未能通过 {ability.upper()} 豁免（{rr.total} < DC {dc}）。"

    return RuleResult(
        kind="saving_throw",
        actor=actor,
        target=reason or "",
        success=success,
        dc=int(dc),
        roll=rr.to_dict(),
        gm_facts=[gm_fact],
        extra={"ability": ability, "modifier": mod, "reason": reason},
    )
