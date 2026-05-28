"""rules_bridge — RulesEngine bridge,按职责拆分子模块。"""
from rules_bridge.module_ops import start_module, enter_room
from rules_bridge.checks import perform_skill_check, perform_saving_throw, trap_check
from rules_bridge.combat import (
    start_encounter_by_id, player_attack, enemy_attack, advance_turn,
)
from rules_bridge.consume import parse_consume_intent, consume_item_action, short_rest
from rules_bridge.intent import classify_combat_intent
from rules_bridge.suggest import suggest_rule_actions

__all__ = [
    "start_module", "enter_room",
    "perform_skill_check", "perform_saving_throw", "trap_check",
    "start_encounter_by_id", "player_attack", "enemy_attack", "advance_turn",
    "parse_consume_intent", "consume_item_action", "short_rest",
    "classify_combat_intent",
    "suggest_rule_actions",
]
