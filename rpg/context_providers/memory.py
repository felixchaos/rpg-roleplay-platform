"""
MemoryProvider — 通用记忆层。所有 manifest 都应该启用。
不区分小说/模组：facts / pinned / abilities / resources / notes 等等都是会话级数据。
"""
from __future__ import annotations

from .base import ContextProvider, ContextContribution, Demand, ProviderServices
from .registry import register_provider


class MemoryProvider(ContextProvider):
    id = "memory"

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        m = (getattr(state, "data", state) or {}).get("memory") or {}
        lines: list[str] = []
        if m.get("main_quest"):
            lines.append(f"主线：{m['main_quest']}")
        if m.get("current_objective"):
            lines.append(f"当前目标：{m['current_objective']}")
        for key, label in (("pinned", "固定记忆"), ("abilities", "能力"),
                           ("resources", "资源"), ("facts", "事实"),
                           ("notes", "笔记")):
            for item in (m.get(key) or [])[:5]:
                lines.append(f"{label}：{item}")
        # hypotheses
        active_hypos = [it for it in (m.get("items") or [])
                        if isinstance(it, dict) and it.get("kind") == "hypothesis"
                        and it.get("status") == "active"]
        for h in active_hypos[:5]:
            lines.append(f"未确认推测：{h.get('text', '')}")
        text = "\n".join(lines) or "（暂无长期记忆）"
        layer = self.make_layer(
            "memory", "长期记忆", text,
            sticky=False, priority=60,
        )
        return ContextContribution(
            provider_id=self.id,
            kind="memory",
            priority=60,
            facts=lines[:3],
            layers=[layer],
            tokens_estimate=len(text) // 2,
            debug={"memory_mode": m.get("mode"), "items_count": len(m.get("items") or [])},
        )


register_provider(MemoryProvider())
