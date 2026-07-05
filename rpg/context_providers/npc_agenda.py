"""
npc_agenda.py — NPC 议程 v0（活世界·柱子3）context provider。

设计文档: docs/design/npc_agenda_v0.md §4

每回合把「当下活跃 NPC 的议程（想要什么/对玩家什么态度）」渲染成一段提示，
优先于人物卡的静态设定，加入 novel + freeform 两份 manifest。不做扫描/触发，
只读 state.data["npc_agendas"] 当前快照（写入在 apply_structured_updates 的
"agenda" JSON op 分支完成，见 state._mixins.apply_ops）。

feature gate: core.feature_flags.feature_enabled("npc_agenda", user_id)，
默认关（与 consequence_ledger / world_heartbeat 同口径）。

priority 76：紧贴 novel.py 里 npc_cards layer（78）之下——议程是「当下活状态」，
排在静态人物卡之后但仍处于高优先级区间。
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider
from .registry import register_provider

# 与设计文档 §4 一致。
PRIORITY = 76


class NpcAgendaProvider(ContextProvider):
    """NPC 议程 — 注入当下活跃 NPC 的意图/态度，优先于人物卡静态设定。"""
    id = "npc_agenda"

    def applies(self, state, manifest, demand) -> bool:
        return super().applies(state, manifest, demand)

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        from core.feature_flags import feature_enabled
        user_id = getattr(services, "user_id", None)
        if not feature_enabled("npc_agenda", user_id):
            return ContextContribution.skipped(self.id, "npc_agenda flag 关闭")

        state_data = getattr(state, "data", None)
        if not isinstance(state_data, dict):
            return ContextContribution.skipped(self.id, "state.data 不可用")

        from state.npc_agenda import agendas_for_injection

        entries = agendas_for_injection(state_data)
        if not entries:
            return ContextContribution.skipped(self.id, "无 NPC 议程")

        text = _render(entries)
        layer = self.make_layer(
            "npc_agenda",
            "NPC 议程",
            text,
            sticky=False,
            priority=PRIORITY,
        )
        return ContextContribution(
            provider_id=self.id,
            kind="npc_agenda",
            priority=PRIORITY,
            layers=[layer],
            facts=[
                f"NPC 议程（{e.get('name', '')}，第{e.get('updated_turn')}回合更新）："
                f"想要「{e.get('goal', '')}」；对玩家「{e.get('stance', '')}」"
                for e in entries
            ],
            tokens_estimate=len(text) // 2,
            debug={"agenda_count": len(entries)},
        )


def _render(entries: list[dict]) -> str:
    lines = [
        "【NPC 议程（当下活状态，优先于人物卡的静态设定）】",
    ]
    for e in entries:
        name = e.get("name", "")
        goal = e.get("goal", "")
        stance = e.get("stance", "")
        parts = []
        if goal:
            parts.append(f"想要「{goal}」")
        if stance:
            parts.append(f"对玩家「{stance}」")
        detail = "；".join(parts)
        lines.append(f"- {name}：{detail}（第{e.get('updated_turn')}回合更新）")
    return "\n".join(lines)


register_provider(NpcAgendaProvider())
