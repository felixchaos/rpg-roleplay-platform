"""coc.checks — CoC 技能检定与属性检定。"""

from __future__ import annotations

import random

from ..base import RuleResult
from ..dice import roll as _roll


def _d100_with_advantage(
    advantage: bool = False, disadvantage: bool = False, seed: int | None = None
) -> dict:
    """Roll d100 with CoC-style bonus/penalty dice.

    Bonus die: roll an extra tens die, pick the lower total (better for roll-under).
    Penalty die: roll an extra tens die, pick the higher total (worse for roll-under).
    Returns a dict compatible with RollResult.to_dict().
    """
    rng = random.Random(seed) if seed is not None else random.Random()

    tens_1 = rng.randint(0, 9)
    ones = rng.randint(0, 9)
    base = (tens_1 * 10 + ones) or 100

    if advantage and not disadvantage:
        tens_2 = rng.randint(0, 9)
        alt = (tens_2 * 10 + ones) or 100
        total = min(base, alt)
    elif disadvantage and not advantage:
        tens_2 = rng.randint(0, 9)
        alt = (tens_2 * 10 + ones) or 100
        total = max(base, alt)
    else:
        tens_2 = None
        total = base

    result = {
        "expression": "1d100",
        "rolls": [total],
        "modifier": 0,
        "total": total,
        "advantage": advantage,
        "disadvantage": disadvantage,
    }
    return result


def _resolve_skill_level(total: int, skill_pct: int) -> str:
    """Determine success level for a d100 roll against a skill percentage."""
    if total == 1:
        return "critical_success"
    if total <= skill_pct // 5:
        return "extreme_success"
    if total <= skill_pct // 2:
        return "hard_success"
    if total <= skill_pct:
        return "regular_success"

    fumble_threshold = 96 if skill_pct < 50 else 100
    if total >= fumble_threshold:
        return "fumble"
    return "failure"


def _build_gm_facts(name: str, threshold: int, total: int, level: str, reason: str) -> list[str]:
    level_labels = {
        "critical_success": "大成功",
        "extreme_success": "极难成功",
        "hard_success": "困难成功",
        "regular_success": "常规成功",
        "failure": "失败",
        "fumble": "大失败",
    }
    label = level_labels.get(level, level)
    facts = [f"{name} 检定：d100={total}，阈值={threshold} → {label}"]
    if reason:
        facts.append(f"原因：{reason}")
    return facts


def coc_skill_check(
    character: dict,
    skill: str,
    dc: int,
    advantage: bool = False,
    seed: int | None = None,
    actor_name: str | None = None,
    reason: str = "",
) -> RuleResult:
    """CoC 技能检定：d100 ≤ skill% 为成功。

    dc 的含义：
      - 若 dc > 0，代表目标成功率（直接用作阈值）
      - 若 dc == 0，从角色 skills 中读取技能值
    """
    skills = character.get("skills", {})
    from .character import COC_SKILL_ALIASES
    from .character import COC_SKILLS as _SKILLS

    skill = COC_SKILL_ALIASES.get(skill, skill)
    skill_pct = dc if dc > 0 else skills.get(skill, _SKILLS.get(skill, {}).get("base", 20))

    if advantage:
        roll_result = _d100_with_advantage(advantage=True, seed=seed)
    else:
        r = _roll("1d100", seed=seed)
        roll_result = r.to_dict()
        roll_result["advantage"] = False

    total = roll_result["total"]
    level = _resolve_skill_level(total, skill_pct)
    success = level in ("critical_success", "extreme_success", "hard_success", "regular_success")

    return RuleResult(
        kind="skill_check",
        actor=actor_name or character.get("name", ""),
        target=None,
        roll=roll_result,
        dc=skill_pct,
        success=success,
        extra={
            "skill": skill,
            "level": level,
            "threshold": skill_pct,
            "reason": reason,
        },
        gm_facts=_build_gm_facts(skill, skill_pct, total, level, reason),
        state_ops=[],
    )


def coc_characteristic_roll(
    character: dict,
    ability: str,
    dc: int = 5,
    advantage: bool = False,
    seed: int | None = None,
    actor_name: str | None = None,
    reason: str = "",
) -> RuleResult:
    """CoC 属性检定：d100 ≤ 属性值 × 难度乘数。

    dc 含义（映射自 JSON 3 characteristic_roll.levels）：
      - dc = 5  → 常规难度：d100 ≤ 属性 × 5
      - dc = 2  → 困难难度：d100 ≤ 属性 × 2.5（向下取整）
      - dc = 1  → 极难难度：d100 ≤ 属性 × 1
    """
    characteristics = character.get("characteristics", {})
    stat = characteristics.get(ability.lower(), 50)

    if dc <= 1:
        multiplier = 1
    elif dc <= 2:
        multiplier = 2.5
    else:
        multiplier = 5

    threshold = int(stat * multiplier)

    if advantage:
        roll_result = _d100_with_advantage(advantage=True, seed=seed)
    else:
        r = _roll("1d100", seed=seed)
        roll_result = r.to_dict()
        roll_result["advantage"] = False

    total = roll_result["total"]
    success = total <= threshold

    if total == 1:
        level = "critical_success"
    elif total == 100:
        level = "fumble"
    elif success:
        level = "regular_success"
    else:
        level = "failure"

    return RuleResult(
        kind="saving_throw",
        actor=actor_name or character.get("name", ""),
        target=None,
        roll=roll_result,
        dc=threshold,
        success=success,
        extra={
            "ability": ability,
            "level": level,
            "threshold": threshold,
            "reason": reason,
        },
        gm_facts=_build_gm_facts(ability.upper(), threshold, total, level, reason),
        state_ops=[],
    )
