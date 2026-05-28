"""
RecentChatProvider — 通用最近对话注入。
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider
from .registry import register_provider


class RecentChatProvider(ContextProvider):
    id = "recent_chat"

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        try:
            history = state.history_messages()
        except Exception:
            history = (getattr(state, "data", state) or {}).get("history") or []
        if not history:
            return ContextContribution.skipped(self.id, "no history")

        formatted_lines: list[str] = []
        for msg in history[-6:]:
            role = msg.get("role") or "user"
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            prefix = "玩家" if role == "user" else "GM"
            formatted_lines.append(f"{prefix}：{content[:600]}")
        text = "\n\n".join(formatted_lines) or "（暂无对话）"
        layer = self.make_layer(
            "recent_chat", "最近对话", text,
            sticky=False, priority=20,  # 低 priority：放在 prompt 末尾
        )
        return ContextContribution(
            provider_id=self.id,
            kind="recent_chat",
            priority=20,
            layers=[layer],
            tokens_estimate=len(text) // 2,
            debug={"turns": len(history)},
        )


register_provider(RecentChatProvider())
