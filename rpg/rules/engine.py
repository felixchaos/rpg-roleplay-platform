"""
rules.engine — RulesEngine 统一入口。
封装 dnd5e 规则集，对外提供 stable signature。
"""
from __future__ import annotations

from datetime import datetime

from .base import RuleResult
from .dice import RollResult
from .dice import roll as _roll
from .dnd5e import ability_modifier, proficiency_bonus
from .dnd5e.actions import (
    apply_damage as _apply_damage,
)
from .dnd5e.actions import (
    attack_roll as _attack_roll,
)
from .dnd5e.actions import (
    damage_roll as _damage_roll,
)
from .dnd5e.actions import (
    short_rest as _short_rest,
)
from .dnd5e.character import (
    consume_inventory_item as _consume_inventory_item,
)
from .dnd5e.character import (
    find_inventory_item as _find_inventory_item,
)
from .dnd5e.character import (
    make_default_character,
)
from .dnd5e.character import (
    normalize_item_alias as _normalize_item_alias,
)
from .dnd5e.character import (
    resources_from_inventory as _resources_from_inventory,
)
from .dnd5e.checks import saving_throw as _saving_throw
from .dnd5e.checks import skill_check as _skill_check
from .dnd5e.combat import (
    initiative as _initiative,
)
from .dnd5e.combat import (
    is_encounter_resolved,
    mark_defeated_by_hp,
)
from .dnd5e.combat import (
    next_turn as _next_turn,
)
from .dnd5e.combat import (
    start_encounter as _start_encounter,
)
from .dnd5e.monsters import build_combatant, get_stat_block, list_stat_blocks


class RulesEngine:
    """5E-compatible 规则集 facade。

    所有方法都是确定性的纯函数（给 seed 就重现）。修改 character/encounter 是
    通过返回 RuleResult.state_ops 由调用方应用，或对传入 dict 直接 in-place 修改
    （战斗类操作）。
    """

    def __init__(self, ruleset_id: str = "dnd5e", mode: str = "5e_compatible"):
        if ruleset_id not in ("dnd5e", "coc"):
            raise ValueError(f"未支持的 ruleset: {ruleset_id}")
        self.ruleset_id = ruleset_id
        self.mode = mode

    # ── 元信息 ──────────────────────────────────────────────────
    def info(self) -> dict:
        return {
            "id": self.ruleset_id,
            "mode": self.mode,
            "label": "5E compatible / 五版规则兼容",
            "rules_version": "1.0",
        }

    # ── 数学 ────────────────────────────────────────────────────
    def ability_modifier(self, score: int) -> int:
        return ability_modifier(score)

    def proficiency_bonus(self, level: int) -> int:
        return proficiency_bonus(level)

    # ── 掷骰 ────────────────────────────────────────────────────
    def roll(
        self,
        expression: str,
        seed: int | None = None,
        advantage: bool = False,
        disadvantage: bool = False,
    ) -> RollResult:
        return _roll(expression, seed=seed, advantage=advantage, disadvantage=disadvantage)

    def damage_roll(self, expression: str, seed: int | None = None, critical: bool = False) -> dict:
        return _damage_roll(expression, seed=seed, critical=critical)

    # ── 角色 ────────────────────────────────────────────────────
    def make_default_character(self, name: str = "Drifter", level: int = 1) -> dict:
        return make_default_character(name=name, level=level)

    # ── Inventory (canonical) ───────────────────────────────────
    def consume_inventory_item(self, character: dict, alias: str, qty: int = 1) -> dict:
        return _consume_inventory_item(character, alias, qty)

    def find_inventory_item(self, character: dict, alias: str) -> dict | None:
        return _find_inventory_item(character, alias)

    def normalize_item_alias(self, alias: str) -> str:
        return _normalize_item_alias(alias)

    def resources_from_inventory(self, character: dict) -> list[str]:
        return _resources_from_inventory(character)

    # ── 检定 ────────────────────────────────────────────────────
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
        return _skill_check(character, skill, dc, advantage=advantage, disadvantage=disadvantage,
                            seed=seed, actor_name=actor_name, reason=reason)

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
        return _saving_throw(character, ability, dc, advantage=advantage, disadvantage=disadvantage,
                             seed=seed, actor_name=actor_name, reason=reason)

    # ── 战斗 ────────────────────────────────────────────────────
    def initiative(self, combatants: list[dict], seed: int | None = None) -> list[dict]:
        return _initiative(combatants, seed=seed)

    def start_encounter(self, party: list[dict], enemies: list[dict], seed: int | None = None,
                        encounter_id: str = "") -> dict:
        return _start_encounter(party, enemies, seed=seed, encounter_id=encounter_id)

    def next_turn(self, encounter: dict) -> dict:
        return _next_turn(encounter)

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
        return _attack_roll(
            attacker, target, attack_bonus, damage_expr,
            advantage=advantage, disadvantage=disadvantage, seed=seed,
            attacker_name=attacker_name, target_name=target_name, weapon_name=weapon_name,
        )

    def apply_damage(self, target: dict, amount: int) -> RuleResult:
        return _apply_damage(target, amount)

    def short_rest(self, character: dict, hit_die: str = "1d8", seed: int | None = None) -> RuleResult:
        return _short_rest(character, hit_die=hit_die, seed=seed)

    # ── encounter 工具 ──────────────────────────────────────────
    def is_encounter_resolved(self, encounter: dict) -> tuple[bool, str]:
        return is_encounter_resolved(encounter)

    def mark_defeated_by_hp(self, encounter: dict) -> list[str]:
        return mark_defeated_by_hp(encounter)

    # ── 怪物 ────────────────────────────────────────────────────
    def get_stat_block(self, stat_block_id: str) -> dict:
        return get_stat_block(stat_block_id)

    def build_combatant(self, stat_block_id: str, instance_id: str | None = None,
                        name: str | None = None) -> dict:
        return build_combatant(stat_block_id, instance_id=instance_id, name=name)

    def list_stat_blocks(self) -> list[str]:
        return list_stat_blocks()

    # ── dice_log 辅助 ──────────────────────────────────────────
    @staticmethod
    def make_dice_log_entry(result: RuleResult, reason: str = "") -> dict:
        """把 RuleResult 压扁成 dice_log 条目（前端 UI 显示用）。

        把 extra 里的 skill / ability / weapon 抬到顶层 — dice_log 是
        deterministic 审计源,应当自描述,不要让后续读者再从 reason 字符串
        里去猜"这是哪种检定"。
        """
        import secrets as _secrets
        roll_data = result.roll or {}
        entry = {
            "id": f"dl_{_secrets.token_urlsafe(6)}",
            "kind": result.kind,
            "actor": result.actor,
            "target": result.target,
            "expression": roll_data.get("expression", ""),
            "rolls": roll_data.get("rolls", []),
            "modifier": roll_data.get("modifier", 0),
            "total": roll_data.get("total"),
            "dc": result.dc,
            "success": result.success,
            "advantage": roll_data.get("advantage", False),
            "disadvantage": roll_data.get("disadvantage", False),
            "damage": result.damage,
            "reason": reason or result.extra.get("reason", ""),
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        # 抬升 extra 的关键标识字段,方便审计 / 测试 / 前端展示
        for key in ("skill", "ability", "weapon"):
            v = result.extra.get(key) if result.extra else None
            if v:
                entry[key] = v
        return entry


_DEFAULT_ENGINE: RulesEngine | None = None


def get_engine(ruleset_id: str = "dnd5e", mode: str = "5e_compatible") -> RulesEngine:
    """全局规则引擎单例。"""
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is not None and _DEFAULT_ENGINE.ruleset_id == ruleset_id:
        return _DEFAULT_ENGINE
    if ruleset_id == "coc":
        from .coc.engine import CoCRulesEngine
        _DEFAULT_ENGINE = CoCRulesEngine(ruleset_id=ruleset_id, mode=mode)
    else:
        _DEFAULT_ENGINE = RulesEngine(ruleset_id=ruleset_id, mode=mode)
    return _DEFAULT_ENGINE
