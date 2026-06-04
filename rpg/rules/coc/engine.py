"""coc.engine — CoCRulesEngine，继承 RulesEngine 并覆写 CoC 特定方法。"""

from __future__ import annotations

from ..base import RuleResult
from ..engine import RulesEngine
from .character import make_investigator
from .checks import coc_characteristic_roll, coc_skill_check
from .combat import (
    coc_apply_damage,
    coc_attack_roll,
    coc_first_aid,
    coc_initiative,
    coc_is_encounter_resolved,
    coc_mark_defeated_by_hp,
    coc_next_turn,
    coc_start_encounter,
)
from .monsters import coc_build_combatant, coc_get_stat_block, coc_list_stat_blocks


class CoCRulesEngine(RulesEngine):
    """CoC 7E 规则引擎 —— 继承 RulesEngine，仅覆写检定/战斗/角色/怪物方法。"""

    def info(self) -> dict:
        return {
            "id": self.ruleset_id,
            "mode": self.mode,
            "label": "Call of Cthulhu 7E",
            "rules_version": "1.0",
        }

    def ability_modifier(self, score: int) -> int:
        return 0

    def proficiency_bonus(self, level: int) -> int:
        return 0

    def make_default_character(self, name: str = "Investigator", level: int = 1) -> dict:
        return make_investigator(name=name)

    def skill_check(
        self,
        character: dict,
        skill: str,
        dc: int,
        advantage: bool = False,
        disadvantage: bool = False,
        seed: int | None = None,
        actor_name: str | None = None,
        reason: str = "",
    ) -> RuleResult:
        return coc_skill_check(
            character,
            skill,
            dc,
            advantage=advantage,
            seed=seed,
            actor_name=actor_name,
            reason=reason,
        )

    def saving_throw(
        self,
        character: dict,
        ability: str,
        dc: int,
        advantage: bool = False,
        disadvantage: bool = False,
        seed: int | None = None,
        actor_name: str | None = None,
        reason: str = "",
    ) -> RuleResult:
        return coc_characteristic_roll(
            character,
            ability,
            dc,
            advantage=advantage,
            seed=seed,
            actor_name=actor_name,
            reason=reason,
        )

    def initiative(self, combatants: list[dict], seed: int | None = None) -> list[dict]:
        return coc_initiative(combatants, seed=seed)

    def start_encounter(
        self,
        party: list[dict],
        enemies: list[dict],
        seed: int | None = None,
        encounter_id: str = "",
    ) -> dict:
        return coc_start_encounter(party, enemies, seed=seed, encounter_id=encounter_id)

    def attack_roll(
        self,
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
        return coc_attack_roll(
            attacker,
            target,
            attack_bonus,
            damage_expr,
            advantage=advantage,
            seed=seed,
            attacker_name=attacker_name,
            target_name=target_name,
            weapon_name=weapon_name,
        )

    def apply_damage(self, target: dict, amount: int) -> RuleResult:
        return coc_apply_damage(target, amount)

    def short_rest(
        self, character: dict, hit_die: str = "1d8", seed: int | None = None
    ) -> RuleResult:
        return coc_first_aid(character, seed=seed)

    def next_turn(self, encounter: dict) -> dict:
        return coc_next_turn(encounter)

    def is_encounter_resolved(self, encounter: dict) -> tuple[bool, str]:
        return coc_is_encounter_resolved(encounter)

    def mark_defeated_by_hp(self, encounter: dict) -> list[str]:
        return coc_mark_defeated_by_hp(encounter)

    def get_stat_block(self, stat_block_id: str) -> dict:
        return coc_get_stat_block(stat_block_id)

    def build_combatant(
        self, stat_block_id: str, instance_id: str | None = None, name: str | None = None
    ) -> dict:
        return coc_build_combatant(stat_block_id, instance_id=instance_id, name=name)

    def list_stat_blocks(self) -> list[str]:
        return coc_list_stat_blocks()
