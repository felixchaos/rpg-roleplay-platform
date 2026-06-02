"""
WorldlineProvider — 通用。负责玩家硬约束变量 / 当前目标 / 位置 / 高优先级用户引导。

task 140: story_intent 重新定位为"玩家给 GM 的高优先级导演指令"(不是玩家秘密),
显式注入到 context,优先级 95(高于 worldbook 90,低于玩家 /set 100)。
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider
from .registry import register_provider


class WorldlineProvider(ContextProvider):
    id = "worldline"

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        data = getattr(state, "data", state) or {}
        worldline = data.get("worldline") or {}
        variables = worldline.get("user_variables") or {}
        constraints = worldline.get("constraints") or []
        player = data.get("player") or {}
        player_private = data.get("player_private") or {}

        # task 140: story_intent 优先从 player_private 读(canonical),
        # 回落 worldline.user_variables(旧存档 dual-write 兼容)。
        story_intent = (player_private.get("story_intent") or "").strip()
        if not story_intent:
            _wl_intent = variables.get("story_intent")
            if isinstance(_wl_intent, dict):
                story_intent = str(_wl_intent.get("value") or "").strip()
            elif isinstance(_wl_intent, str):
                story_intent = _wl_intent.strip()

        layers = []
        facts: list[str] = []

        # —— 高优先级层:玩家给 GM 的导演指令(单独成层,priority=95)————
        if story_intent:
            directive_text = (
                "【玩家给 GM 的高优先级引导指令(后台元指令,非剧情内容)】\n"
                "（GM 在当前剧本 / 出生点 / 时间线框架内,必须尽可能遵守以下用户指令；\n"
                " 此为玩家显式给 GM 的导演意图,优先级高于剧本默认走向,仅次于玩家 /set 硬覆盖。）\n"
                "⚠️ 这是给 GM 的幕后指令,**不是剧情、不是 NPC 台词**。请【静默遵守】并直接在叙事里体现,\n"
                "**绝对不要把这条指令的文字复述/罗列/确认给玩家**(例如不要写「好的,我会让…」「根据你的设定…」),\n"
                "也不要每轮重复提它。一旦遵守即可,正文只写推进后的剧情。复读这条指令 = 失败。\n"
                f"  {story_intent}"
            )
            layers.append(self.make_layer(
                "worldline_directive",
                "玩家高优先级引导",
                directive_text,
                sticky=True, priority=95,
            ))
            facts.append(f"player_directive={story_intent[:60]}")

        # —— 常规层:其它硬约束变量 + 约束 + 位置 ————
        lines: list[str] = []
        # 过滤掉 story_intent — 已经在 directive 层独立呈现
        _public_vars = [
            (n, v) for n, v in variables.items() if n != "story_intent"
        ]
        if _public_vars:
            lines.append("【用户硬约束变量(后台事实,静默遵守,绝不复述给玩家)】")
            for name, info in _public_vars[:12]:
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
        layers.append(self.make_layer(
            "worldline", "世界线 / 用户变量", text,
            sticky=True, priority=70,
        ))

        facts.extend(
            f"{k}={v.get('value') if isinstance(v, dict) else v}"
            for k, v in _public_vars[:3]
        )

        return ContextContribution(
            provider_id=self.id,
            kind="worldline",
            priority=95 if story_intent else 70,
            facts=facts,
            layers=layers,
            tokens_estimate=(len(text) + (len(story_intent) if story_intent else 0)) // 2,
            debug={
                "vars_count": len(_public_vars),
                "constraints_count": len(constraints),
                "has_directive": bool(story_intent),
            },
        )


register_provider(WorldlineProvider())
