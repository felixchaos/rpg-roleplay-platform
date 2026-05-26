"""
RulesProvider — 在 manifest.ruleset 非 none 时启用。
注入 player_character 摘要、dice_log、rule_candidate_actions。
"""
from __future__ import annotations

from .base import ContextProvider, ContextContribution, Demand, ProviderServices
from .registry import register_provider


def _has_ruleset(state, manifest) -> bool:
    rs = manifest.get("ruleset")
    if rs and rs != "none":
        return True
    data = getattr(state, "data", state) or {}
    rs_state = (data.get("ruleset") or {}).get("id")
    return bool(rs_state)


class RulesProvider(ContextProvider):
    id = "rules"

    def applies(self, state, manifest, demand) -> bool:
        if not super().applies(state, manifest, demand):
            return False
        return _has_ruleset(state, manifest)

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        data = getattr(state, "data", state) or {}
        ruleset = data.get("ruleset") or {}
        pc = data.get("player_character") or {}
        dice_log = list(data.get("dice_log") or [])[-8:]
        scene = data.get("scene") or {}
        enc = data.get("encounter") or {}
        current_room = scene.get("current_room") or {}

        lines: list[str] = []
        lines.append(f"【规则集】{ruleset.get('public_label') or ruleset.get('id') or 'unknown'}")

        # task XX:module_adventure (5E-compatible) 必须的 GM 硬约束块。
        # 文本约束只是 belt-and-suspenders;主要防线是 app.py 的 classify_combat_intent
        # 在 GM 调用前就把含糊战斗/无目标战斗拦截成 pending_question。
        # 但 GM 调用确实发生时,以下约束帮它别越界。
        is_module_adventure = (
            manifest.get("kind") == "module_adventure"
            or bool(scene.get("module_id"))
        )
        if is_module_adventure:
            lines.append("")
            lines.append("【硬约束 — GM 不得自行裁定】")
            lines.append(
                "下列事项是 RulesEngine / 玩家选择 的专属裁定权。"
                "GM 在正文中**一律不得**宣称这些发生:"
            )
            lines.append("  · 攻击命中 / miss / 暴击 / 伤害数字 (必须 attack_roll 结果)")
            lines.append("  · HP / AC / 先攻 / 状态 / 死亡 变化 (必须 RulesEngine 写)")
            lines.append("  · 借机攻击是否触发、是否命中 (必须玩家选 Disengage / 承受)")
            lines.append("  · 武器是否可用 / 是否 disadvantage (e.g. 不得叙述「短弓施展不了」)")
            lines.append("  · 玩家是否被卡住 / 是否能后退 (必须先走 Disengage 流程)")
            lines.append(
                "  · 敌人是否凭空出现 (必须有 encounter.active 或当前房间 enemies 数据;"
                "本轮 enemies=" + (
                    "[" + "、".join(
                        (e.get("name") or e.get("id") or "?")
                        for e in (current_room.get("enemies") or [])
                    ) + "]"
                    if current_room.get("enemies") else "空"
                )
                + (
                    f";encounter.active={'是' if enc.get('active') else '否'}"
                )
                + ")"
            )
            lines.append(
                "  · 敌人移动 / 动作 / 攻击结果 (必须 RulesEngine enemy_attack 写)"
            )
            lines.append(
                "原则:**RulesEngine 没返回的事实,GM 不能叙述成已发生**。"
                "玩家意图模糊或战斗细节未定时,GM 只能写"
                "「你准备这么做」或「你看见敌人压上」(等敌人 attack 真发生再叙)"
                ",绝不写已经成功/失败/被卡住/失去优势。"
            )
            if not enc.get("active") and not (current_room.get("enemies") or []):
                lines.append(
                    "  ⚠️ 当前 encounter 未激活、本房间无 enemies。"
                    "**GM 不得在本轮正文中引入任何敌方 NPC 或战斗事件**。"
                    "若需要遭遇,必须由 hazard / flag / 玩家明确触发,再由 RulesEngine 启动。"
                )
        if pc:
            lines.append(
                f"【角色】{pc.get('name')} · Lv {pc.get('level')} {pc.get('class_name', '')} · "
                f"HP {pc.get('hp')}/{pc.get('max_hp')} · AC {pc.get('ac')} · "
                f"熟练 +{pc.get('proficiency_bonus', 0)}"
            )
            abilities = pc.get("abilities") or {}
            if abilities:
                lines.append("  · 属性：" + " ".join(
                    f"{a.upper()} {abilities.get(a, 10)}" for a in ("str", "dex", "con", "int", "wis", "cha")
                ))
            if pc.get("conditions"):
                lines.append(f"  · 状态：{', '.join(pc['conditions'])}")
        # rule_candidate_actions（Demand Resolver 产出）
        rcas = (demand.rule_candidate_actions or []) if demand else []
        if rcas:
            lines.append("\n【本轮规则候选动作】")
            for a in rcas[:6]:
                desc = f"{a.get('kind')} {a.get('skill') or a.get('ability') or a.get('target') or ''}"
                if a.get("dc") is not None:
                    desc += f" DC {a['dc']}"
                if a.get("reason"):
                    desc += f" — {a['reason']}"
                lines.append(f"  · {desc}")
            lines.append("⚠️ GM 不能自己掷骰；必须经 RulesEngine。")
        if dice_log:
            lines.append("\n【最近骰子日志】")
            for d in dice_log:
                summary = (
                    f"{d.get('kind')} · {d.get('actor', '')} · "
                    f"{d.get('expression', '')}={d.get('total')}"
                )
                if d.get("dc") is not None:
                    summary += f" vs DC {d['dc']}"
                if d.get("success") is True:
                    summary += " ✓"
                elif d.get("success") is False:
                    summary += " ✗"
                lines.append(f"  · {summary}")
        text = "\n".join(lines)
        layer = self.make_layer(
            "rules", "规则集状态", text,
            sticky=False, priority=80,
        )
        facts: list[str] = []
        if pc:
            facts.append(f"角色 HP {pc.get('hp')}/{pc.get('max_hp')}, AC {pc.get('ac')}")
        if rcas:
            facts.append(f"本轮候选规则动作 {len(rcas)} 条")
        return ContextContribution(
            provider_id=self.id,
            kind="rules",
            priority=80,
            facts=facts,
            layers=[layer],
            tokens_estimate=len(text) // 2,
            debug={
                "ruleset": ruleset.get("id"),
                "pc_hp": pc.get("hp"),
                "dice_log_count": len(data.get("dice_log") or []),
                "candidate_actions_count": len(rcas),
            },
        )


register_provider(RulesProvider())
