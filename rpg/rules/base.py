"""
rules.base — RulesEngine 通用数据结构。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class StateOp:
    """规则结果产出的、要写入 game state 的硬状态变更。

    State Gate 看到 source='rules_engine' 才允许写 player_character.* / encounter.* /
    scene.flags.* / dice_log.* 等受保护路径。
    """
    op: str          # "set" / "add" / "append" / "subtract"
    path: str        # e.g. "player_character.hp"
    value: Any = None
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuleResult:
    """规则函数的标准输出。GM 只能基于 gm_facts 叙事，不得自行编造数值。"""
    kind: str                                 # "skill_check" / "saving_throw" / "attack" / ...
    actor: str = ""
    target: str = ""
    success: Optional[bool] = None
    dc: Optional[int] = None
    roll: dict = field(default_factory=dict)  # RollResult.to_dict() or composite
    damage: Optional[dict] = None
    state_ops: list[StateOp] = field(default_factory=list)
    gm_facts: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "kind": self.kind,
            "actor": self.actor,
            "target": self.target,
            "success": self.success,
            "dc": self.dc,
            "roll": self.roll,
            "damage": self.damage,
            "state_ops": [op.to_dict() for op in self.state_ops],
            "gm_facts": list(self.gm_facts),
            "extra": dict(self.extra),
        }
        return d
