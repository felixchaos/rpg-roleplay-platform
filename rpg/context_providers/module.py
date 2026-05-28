"""
Module providers — 只在 manifest.kind == 'module_adventure' 时启用。

绝不引用 ChapterFact / 小说锚点 / 小说检索。
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider, Demand, ProviderServices
from .registry import register_provider


def _module_id(state) -> str | None:
    scene = (getattr(state, "data", state) or {}).get("scene") or {}
    return scene.get("module_id") or None


def _load_bundle(module_id: str) -> dict | None:
    try:
        import modules as _module_registry
        return _module_registry.load_module(module_id)
    except Exception:
        return None


class ModuleSceneProvider(ContextProvider):
    """注入当前房间 description / exits / clues / hazards / checks。"""
    id = "module_scene"

    def applies(self, state, manifest, demand) -> bool:
        if not super().applies(state, manifest, demand):
            return False
        return bool(_module_id(state))

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        scene = (getattr(state, "data", state) or {}).get("scene") or {}
        module_id = scene.get("module_id")
        bundle = _load_bundle(module_id) if module_id else None
        if not bundle:
            return ContextContribution.skipped(self.id, f"无法加载模组 {module_id}")
        rooms = bundle.get("rooms") or []
        current_room_id = scene.get("location_id")
        current_room = next((r for r in rooms if r.get("id") == current_room_id), None) or {}

        lines: list[str] = []
        manifest_meta = bundle.get("manifest") or {}
        title = manifest_meta.get("name_cn") or manifest_meta.get("name") or module_id
        lines.append(f"【模组】{title}（{module_id}）")
        if manifest_meta.get("tagline"):
            lines.append(f"基调：{manifest_meta['tagline']}")

        lines.append(f"\n【当前房间】{current_room.get('name', current_room_id or '未知')}")
        if current_room.get("description"):
            lines.append(current_room["description"])

        exits = current_room.get("exits") or []
        if exits:
            lines.append("\n【可用出口】")
            for ex in exits:
                req = f"（需要：{ex.get('requires')}）" if ex.get("requires") else ""
                lines.append(f"  · → {ex.get('to')}：{ex.get('label', '')}{req}")
        clues = current_room.get("visible_clues") or []
        if clues:
            lines.append("\n【可见线索】")
            for c in clues:
                lines.append(f"  · {c.get('text') if isinstance(c, dict) else c}")
        checks = current_room.get("checks") or []
        if checks:
            lines.append("\n【可发起检定（玩家若主动尝试，GM 不可自行掷骰，必须经规则引擎）】")
            for chk in checks:
                kind = chk.get("kind", "skill_check")
                skill = chk.get("skill") or chk.get("ability") or ""
                dc = chk.get("dc")
                lines.append(f"  · {kind} {skill} DC {dc} — {chk.get('fact') or chk.get('reveals') or ''}")
        hazards = current_room.get("hazards") or []
        if hazards:
            lines.append("\n【环境危险】")
            for h in hazards:
                lines.append(f"  · {h.get('description', h.get('id'))}")
        flags = scene.get("flags") or {}
        if flags:
            on_flags = [k for k, v in flags.items() if v]
            if on_flags:
                lines.append(f"\n【场景标记】{', '.join(on_flags)}")
        visited = scene.get("visited_rooms") or []
        if visited:
            lines.append(f"\n【已访问房间】{', '.join(visited)}")

        text = "\n".join(lines)
        layer = self.make_layer(
            "module_scene", "当前模组场景", text,
            sticky=False, priority=90,
        )
        facts = [
            f"模组『{title}』当前房间：{current_room.get('name', current_room_id)}",
        ]
        if exits:
            facts.append(f"出口：{', '.join(e.get('to') for e in exits)}")
        return ContextContribution(
            provider_id=self.id,
            kind="module_scene",
            priority=90,
            facts=facts,
            layers=[layer],
            tokens_estimate=len(text) // 2,
            debug={
                "module_id": module_id,
                "current_room": current_room_id,
                "exits": [e.get("to") for e in exits],
                "checks_count": len(checks),
            },
        )


class ModuleEncounterProvider(ContextProvider):
    """注入战斗状态 + encounter 定义 + 当前房间敌人。"""
    id = "module_encounter"

    def applies(self, state, manifest, demand) -> bool:
        if not super().applies(state, manifest, demand):
            return False
        return bool(_module_id(state))

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        data = getattr(state, "data", state) or {}
        scene = data.get("scene") or {}
        encounter = data.get("encounter") or {}
        module_id = scene.get("module_id")
        bundle = _load_bundle(module_id) if module_id else None
        if not bundle:
            return ContextContribution.skipped(self.id, f"无法加载模组 {module_id}")

        lines: list[str] = []
        # 当前战斗
        if encounter.get("active"):
            lines.append("【战斗进行中】")
            lines.append(f"  · 第 {encounter.get('round')} 回合，turn_index={encounter.get('turn_index')}")
            for c in encounter.get("combatants", []):
                state_mark = "已倒下" if c.get("defeated") else f"HP {c.get('hp')}/{c.get('max_hp')}"
                lines.append(f"  · {c.get('name')} [{c.get('side')}] AC {c.get('ac')} · {state_mark}")
            init = encounter.get("initiative_order") or []
            if init:
                lines.append("  · 先攻：" + " > ".join(f"{i.get('name')}({i.get('init')})" for i in init))
        # 当前房间预设遭遇
        encs = bundle.get("encounters") or []
        rel = [e for e in encs if e.get("location_id") == scene.get("location_id")]
        if rel:
            lines.append("\n【本房间可能的预设遭遇】")
            for e in rel:
                lines.append(f"  · id={e.get('id')} — {e.get('name')} — {e.get('description', '')}")
        if not lines:
            return ContextContribution.skipped(self.id, "无战斗 / 无预设遭遇")

        text = "\n".join(lines)
        layer = self.make_layer(
            "module_encounter", "战斗 / 遭遇", text,
            sticky=False, priority=85,
        )
        facts = []
        if encounter.get("active"):
            facts.append("战斗进行中 — GM 必须遵守规则引擎结果，禁止编造伤害/命中/HP。")
        return ContextContribution(
            provider_id=self.id,
            kind="module_encounter",
            priority=85,
            facts=facts,
            layers=[layer],
            tokens_estimate=len(text) // 2,
            debug={
                "active": bool(encounter.get("active")),
                "preset_encounters": [e.get("id") for e in rel],
            },
        )


class ModuleWorldbookProvider(ContextProvider):
    """模组级世界设定 / 派系 / 主题。"""
    id = "module_worldbook"

    def applies(self, state, manifest, demand) -> bool:
        if not super().applies(state, manifest, demand):
            return False
        return bool(_module_id(state))

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        module_id = _module_id(state)
        bundle = _load_bundle(module_id) if module_id else None
        if not bundle:
            return ContextContribution.skipped(self.id, "no module")
        wb = bundle.get("worldbook") or {}
        if not wb:
            return ContextContribution.skipped(self.id, "no worldbook")

        lines: list[str] = []
        if wb.get("setting"):
            lines.append(f"【世界设定】{wb['setting']}")
        for fac in (wb.get("factions") or []):
            lines.append(f"\n【派系】{fac.get('name')} — {fac.get('summary', '')}")
        if wb.get("themes"):
            lines.append("\n【主题】" + " / ".join(wb["themes"]))
        if wb.get("tone_guide"):
            lines.append("\n【GM 风格指引】")
            for g in wb["tone_guide"]:
                lines.append(f"  · {g}")
        if wb.get("rules_notice"):
            lines.append(f"\n【规则边界】{wb['rules_notice']}")
        text = "\n".join(lines)
        layer = self.make_layer(
            "module_worldbook", "模组世界书", text,
            sticky=True, priority=75,
        )
        return ContextContribution(
            provider_id=self.id,
            kind="module_worldbook",
            priority=75,
            layers=[layer],
            tokens_estimate=len(text) // 2,
            debug={"factions": [f.get('id') for f in (wb.get('factions') or [])]},
        )


register_provider(ModuleSceneProvider())
register_provider(ModuleEncounterProvider())
register_provider(ModuleWorldbookProvider())
