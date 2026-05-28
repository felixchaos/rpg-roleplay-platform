"""
WorldlineProvider — 通用。负责玩家硬约束变量 / 当前目标 / 位置。
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider, Demand, ProviderServices
from .registry import register_provider


class WorldlineProvider(ContextProvider):
    id = "worldline"

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        data = getattr(state, "data", state) or {}
        worldline = data.get("worldline") or {}
        variables = worldline.get("user_variables") or {}
        constraints = worldline.get("constraints") or []
        player = data.get("player") or {}

        lines: list[str] = []
        if variables:
            lines.append("【用户硬约束变量】")
            for name, info in list(variables.items())[:12]:
                val = info.get("value") if isinstance(info, dict) else info
                lines.append(f"  · {name}={val}")
        else:
            lines.append("（暂无用户变量）")
        if constraints:
            lines.append("\n【世界线推演约束】")
            for c in constraints[:8]:
                lines.append(f"  · {c}")
        if player.get("current_location"):
            lines.append(f"\n【玩家当前位置】{player['current_location']}")

        text = "\n".join(lines)
        layer = self.make_layer(
            "worldline", "世界线 / 用户变量", text,
            sticky=True, priority=70,
        )
        return ContextContribution(
            provider_id=self.id,
            kind="worldline",
            priority=70,
            facts=[f"{k}={v.get('value') if isinstance(v, dict) else v}"
                   for k, v in list(variables.items())[:3]],
            layers=[layer],
            tokens_estimate=len(text) // 2,
            debug={"vars_count": len(variables), "constraints_count": len(constraints)},
        )


register_provider(WorldlineProvider())
