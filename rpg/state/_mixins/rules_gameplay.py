"""state._mixins.rules_gameplay — RPG 规则引擎 / 战斗 / 场景 / active_entities mixin。

承载:
- dice_log:           append_dice_log
- player character:   set_player_character / update_player_hp / damage_player
- inventory:          consume_inventory_item / sync_resources_from_inventory / _audit_rules_inventory
- encounter:          set_encounter / clear_encounter
- scene:              set_scene / mark_scene_visit / set_scene_flag
- active_entities:    _active_entities / set_active_entities / upsert_active_entity /
                      prune_active_entities / replace_active_entities_with_source
"""
from __future__ import annotations
import copy
from datetime import datetime

# DEFAULT_STATE 在 state.core,但循环 import 风险 — 用延迟 import (函数体内)


class RulesGameplayMixin:
    """规则引擎专用入口 + 场景/encounter/active_entities 管理。"""

    def append_dice_log(self, entry: dict, cap: int = 50) -> None:
        """RulesEngine 唯一允许的 dice_log 写入入口。"""
        log = self.data.setdefault("dice_log", [])
        log.append(entry)
        if len(log) > cap:
            del log[: len(log) - cap]

    def set_player_character(self, character: dict) -> None:
        """初始化或替换 player_character。仅在模组开局 / 新游戏使用。"""
        self.data["player_character"] = copy.deepcopy(character or {})

    def update_player_hp(self, new_hp: int, reason: str = "") -> int:
        """RulesEngine 专用：直接设定玩家 HP，不超过 max_hp。"""
        pc = self.data.setdefault("player_character", {})
        max_hp = int(pc.get("max_hp", 0) or 0)
        new_hp = max(0, min(int(new_hp), max_hp if max_hp > 0 else int(new_hp)))
        pc["hp"] = new_hp
        return new_hp

    def damage_player(self, amount: int, reason: str = "") -> int:
        pc = self.data.setdefault("player_character", {})
        cur = int(pc.get("hp", 0) or 0)
        actual = max(0, int(amount))
        pc["hp"] = max(0, cur - actual)
        return cur - pc["hp"]

    # ── Inventory (canonical) ──────────────────────────────────
    # Bug 5：player_character.inventory 是物品的唯一真相源；
    # memory.resources 是派生展示层，consume 之后必须同步。

    def consume_inventory_item(self, alias: str, qty: int = 1) -> dict:
        """Canonical inventory 消耗。返回 RulesEngine 标准 result dict。

        副作用：
          1. player_character.inventory[item].qty -= consumed（qty=0 时移除条目）
          2. memory.resources 派生重写为当前 inventory 列表
          3. audit_log 记一条 source=rules_engine 的同步记录
        """
        from rules.dnd5e.character import (
            consume_inventory_item as _consume,
            resources_from_inventory as _derive,
        )
        pc = self.data.setdefault("player_character", {})
        result = _consume(pc, alias, qty)
        if result.get("ok"):
            # 同步派生层
            self.data.setdefault("memory", {})["resources"] = _derive(pc)
            self._audit_rules_inventory(
                action="consume",
                alias=alias,
                detail=result,
            )
        return result

    def sync_resources_from_inventory(self) -> list[str]:
        """重写 memory.resources 派生层，保持与 player_character.inventory 一致。"""
        from rules.dnd5e.character import resources_from_inventory as _derive
        pc = self.data.get("player_character") or {}
        derived = _derive(pc)
        self.data.setdefault("memory", {})["resources"] = derived
        return derived

    def _audit_rules_inventory(self, *, action: str, alias: str, detail: dict) -> None:
        try:
            audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
            audit.append({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "source": "rules_engine",
                "kind": "inventory",
                "action": action,
                "alias": alias,
                "detail": detail,
                "turn": self.data.get("turn", 0),
            })
            self.data["permissions"]["audit_log"] = audit[-200:]
        except Exception:
            pass

    def set_encounter(self, encounter: dict) -> None:
        """初始化或替换 encounter 状态。RulesEngine 专用。"""
        self.data["encounter"] = copy.deepcopy(encounter or {})

    def clear_encounter(self) -> None:
        from state.core import DEFAULT_STATE  # 延迟 import 避免循环
        self.data["encounter"] = copy.deepcopy(DEFAULT_STATE["encounter"])

    def set_scene(self, scene: dict) -> None:
        self.data["scene"] = copy.deepcopy(scene or {})

    def mark_scene_visit(self, location_id: str) -> None:
        scene = self.data.setdefault("scene", {})
        visited = scene.setdefault("visited_rooms", [])
        if location_id and location_id not in visited:
            visited.append(location_id)

    def set_scene_flag(self, flag: str, value=True) -> None:
        scene = self.data.setdefault("scene", {})
        flags = scene.setdefault("flags", {})
        flags[flag] = value

    # ── active_entities: 轻量在场实体索引 ────────────────────────
    # 设计要求 (Codex 评审):
    # - 不是完整角色卡;角色卡是长期资产 (在 user_cards 表)。
    # - 这是运行时索引:当 GM 在场景里遇到 / 引入角色,先进这里。
    # - 真正重要才手动 promote 成 user_card (只在平台『角色卡』页操作)。
    # - 来源 source ∈ {"room_data", "encounter", "gm_provisional"}。
    # - 5E 模组:敌人必须来自 scene.current_room.enemies 或合法 encounter.combatants,
    #   不允许 GM 正文凭空进 active_entities (combat gate 已经拦了 GM,这里也守一道)。

    def _active_entities(self) -> list:
        return self.data.setdefault("active_entities", [])

    def set_active_entities(self, entities: list) -> None:
        """覆盖整个 active_entities 列表。RulesEngine / rules_bridge 专用。"""
        self.data["active_entities"] = [copy.deepcopy(e or {}) for e in (entities or [])]

    def upsert_active_entity(self, entity: dict) -> None:
        """按 id upsert。已有 id 命中就更新 last_seen_turn + 合并字段;否则追加。"""
        if not isinstance(entity, dict):
            return
        ent_id = str(entity.get("id") or "").strip()
        if not ent_id:
            return
        turn = int(self.data.get("turn", 0) or 0)
        active = self._active_entities()
        for i, e in enumerate(active):
            if str(e.get("id") or "") == ent_id:
                merged = dict(e)
                # 来源 / first_seen 不被覆盖
                preserved_source = e.get("source") or entity.get("source") or "unknown"
                preserved_first_seen = e.get("first_seen_turn") if e.get("first_seen_turn") is not None else turn
                for k, v in entity.items():
                    if v is not None:
                        merged[k] = v
                merged["source"] = preserved_source
                merged["first_seen_turn"] = preserved_first_seen
                merged["last_seen_turn"] = turn
                active[i] = merged
                return
        # 新增
        new_entity = dict(entity)
        new_entity.setdefault("source", "unknown")
        new_entity.setdefault("first_seen_turn", turn)
        new_entity["last_seen_turn"] = turn
        new_entity.setdefault("kind", "unknown")
        new_entity.setdefault("disposition", "unknown")
        new_entity.setdefault("confidence", 1.0)
        active.append(new_entity)

    def prune_active_entities(self, keep_ids: list[str] | set[str]) -> int:
        """删除不在 keep_ids 里的 active_entities。返回删了几个。"""
        keep = set(str(x) for x in (keep_ids or []))
        active = self._active_entities()
        before = len(active)
        self.data["active_entities"] = [e for e in active if str(e.get("id") or "") in keep]
        return before - len(self.data["active_entities"])

    def replace_active_entities_with_source(self, source: str, entities: list) -> None:
        """删除指定 source 的所有条目,然后追加新的。
        典型用法:enter_room 时把 source='room_data' 的旧实体清掉,从新房间重新填。
        不影响其他 source 的实体 (如 encounter / gm_provisional)。"""
        if not source:
            return
        active = self._active_entities()
        keep_other = [e for e in active if e.get("source") != source]
        self.data["active_entities"] = keep_other
        for e in (entities or []):
            self.upsert_active_entity(e)

