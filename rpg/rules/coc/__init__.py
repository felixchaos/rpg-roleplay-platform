"""rpg.rules.coc — CoC 7E deterministic rules engine."""

from .character import COC_CHARACTERISTICS, COC_DERIVED, COC_SKILLS
from .checks import coc_characteristic_roll, coc_skill_check
from .combat import (
    apply_sanity_loss,
    coc_attack_roll,
    coc_first_aid,
    coc_initiative,
    coc_start_encounter,
)
from .engine import CoCRulesEngine
from .monsters import COC_STAT_BLOCKS, coc_build_combatant, coc_get_stat_block, coc_list_stat_blocks

__all__ = [
    "CoCRulesEngine",
    "COC_SKILLS",
    "COC_CHARACTERISTICS",
    "COC_DERIVED",
    "coc_skill_check",
    "coc_characteristic_roll",
    "coc_initiative",
    "coc_start_encounter",
    "coc_attack_roll",
    "coc_first_aid",
    "apply_sanity_loss",
    "COC_STAT_BLOCKS",
    "coc_get_stat_block",
    "coc_build_combatant",
    "coc_list_stat_blocks",
]
