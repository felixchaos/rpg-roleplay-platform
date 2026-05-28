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

        lines: list[str] = []
        lines.append(f"【规则集】{ruleset.get('public_label') or ruleset.get('id') or 'unknown'}")

        # Codex #1+#2:硬约束 prompt 由 GamePolicy 统一提供。
        # 一个 policy 类同时负责 preflight (GM 前拦截) + prompt 文本 (GM 真被调用时兜底),
        # 避免两处分别维护;新增约束只动 game_policy.py。
        try:
            from game_policy import get_game_policy as _get_policy
            policy = _get_policy(state)
            policy_constraints = policy.gm_prompt_constraints(state) or []
        except Exception:
            policy_constraints = []
        if policy_constraints:
            lines.append("")
            for ln in policy_constraints:
                lines.append(ln)
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
