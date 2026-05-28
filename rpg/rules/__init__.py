"""
rpg.rules — 5E-compatible deterministic rules engine for the RPG roleplay engine.

只允许此目录下的代码或 RulesEngine 改动 player_character / encounter / dice_log
等硬状态。LLM/GM 只能描述结果，规则结果必须经 RulesEngine 计算。
"""
from .engine import RulesEngine, get_engine
from .dice import roll, RollResult
from .base import RuleResult, StateOp

__all__ = ["RulesEngine", "get_engine", "roll", "RollResult", "RuleResult", "StateOp"]
