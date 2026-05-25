"""
rules.dice — 骰子表达式解析与掷骰。纯函数，可由 seed 控制。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, asdict
from typing import Optional


_EXPR_RE = re.compile(r"^\s*(\d+)?\s*d\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*$", re.IGNORECASE)


@dataclass
class RollResult:
    expression: str
    rolls: list[int] = field(default_factory=list)
    modifier: int = 0
    total: int = 0
    advantage: bool = False
    disadvantage: bool = False
    # d20 检定时记录两次原始骰，用于显示 / 审计
    d20_raw: Optional[list[int]] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("d20_raw") is None:
            d.pop("d20_raw", None)
        return d


def parse_expression(expression: str) -> tuple[int, int, int]:
    """解析 1d20+3 / 2d6 / d20-1 / 1d8 形态，返回 (count, sides, modifier)。"""
    if expression is None:
        raise ValueError("dice expression is None")
    match = _EXPR_RE.match(str(expression))
    if not match:
        raise ValueError(f"无法解析骰子表达式：{expression!r}")
    count_str, sides_str, sign, mod_str = match.groups()
    count = int(count_str) if count_str else 1
    sides = int(sides_str)
    modifier = int(mod_str) if mod_str else 0
    if sign == "-":
        modifier = -modifier
    if count <= 0 or sides <= 0:
        raise ValueError(f"骰子参数非法：{expression!r}")
    if count > 100 or sides > 1000:
        raise ValueError(f"骰子参数过大：{expression!r}")
    return count, sides, modifier


def _rng(seed: Optional[int]) -> random.Random:
    return random.Random(seed) if seed is not None else random.Random()


def roll(
    expression: str,
    seed: Optional[int] = None,
    advantage: bool = False,
    disadvantage: bool = False,
) -> RollResult:
    """掷骰。advantage/disadvantage 仅对 d20 单骰生效。两者同时为 True 互相抵消。"""
    count, sides, modifier = parse_expression(expression)
    rng = _rng(seed)

    if advantage and disadvantage:
        advantage = disadvantage = False

    d20_raw: Optional[list[int]] = None
    if sides == 20 and count == 1 and (advantage or disadvantage):
        a = rng.randint(1, 20)
        b = rng.randint(1, 20)
        d20_raw = [a, b]
        chosen = max(a, b) if advantage else min(a, b)
        rolls = [chosen]
    else:
        rolls = [rng.randint(1, sides) for _ in range(count)]

    total = sum(rolls) + modifier
    return RollResult(
        expression=str(expression),
        rolls=rolls,
        modifier=modifier,
        total=total,
        advantage=advantage,
        disadvantage=disadvantage,
        d20_raw=d20_raw,
    )


def is_critical_hit(result: RollResult) -> bool:
    """d20 自然 20 视为暴击。"""
    if result.d20_raw is not None:
        return result.rolls and result.rolls[0] == 20
    return result.rolls == [20] and "d20" in result.expression.lower()


def is_critical_miss(result: RollResult) -> bool:
    if result.d20_raw is not None:
        return result.rolls and result.rolls[0] == 1
    return result.rolls == [1] and "d20" in result.expression.lower()
