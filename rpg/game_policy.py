"""
game_policy.py — GM 行为边界协调层。

Codex 评审定调:不要做两套 GM。保留单 GM,加 GamePolicy 决定能不能说。

  Base GM
  + GamePolicy        ← 本文件
  + ContextProviders  ← 已有 (context_providers/)
  + RulesEngine       ← 已有 (rules/)

GamePolicy 根据当前 content_pack.kind / scene.module_id 切换边界:
  - module_adventure / 5E-compatible:
      GM 只能叙事;攻击 / 检定 / 资源 / 战斗移动必须由 RulesEngine 决定。
  - novel_adaptation / freeform:
      GM 可自由叙事,但状态写入要过 State Gate。

设计要点:
- 这是**协调层**,不是新实现。它把分散在多个地方的 5E 约束
  (rules_bridge.classify_combat_intent / RulesProvider 硬约束 prompt /
   module.json gm_policy) 汇总到一个入口。
- GM 调用前调一次 `policy.preflight(text, state)`,返回 None / 阻挡块。
- GM 调用时 prompt 由 `policy.gm_prompt_constraints(state)` 提供文本段。
- 任何新的 "5E 应该拦截的玩家意图" (e.g. 资源耗尽后还想施法) 加到对应
  Policy 子类的 preflight,不动 chat handler 主体。

类型:
- GamePolicy            — 基类
- ModuleAdventurePolicy — 5E 模组,最严格
- NovelAdaptationPolicy — 小说改编,宽松
- FreeformPolicy        — 通用,最宽松
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PreflightBlock:
    """policy.preflight 命中后返回的阻挡块。

    chat handler 收到非 None 时:写 pending_question + 跳过主 GM 调用。
    """
    kind: str                # "no_target_combat" | "combat_pending_question" | ...
    question: str
    options: list[str]
    source: str = "rules_engine"
    reason: str = ""
    signals: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "question": self.question,
            "options": list(self.options or []),
            "source": self.source,
            "reason": self.reason,
        }
        if self.signals is not None:
            out["signals"] = dict(self.signals)
        return out


# ────────────────────────────────────────────────────────────
# Base
# ────────────────────────────────────────────────────────────


class GamePolicy:
    """基类。子类按 content_pack.kind 实现具体边界。

    所有 policy 子类共享一个原则:**state 是事实真相源;知识检索是参考**。
    """

    id = "base"

    def preflight(self, user_input: str, state: Any) -> Optional[dict]:
        """玩家输入到 GM 之间的拦截点。返回:
        - None: 放行,正常 GM 流程
        - dict (PreflightBlock.to_dict()): 阻挡块,chat handler 直接 yield
          pending_question + 跳过 GM。
        """
        return None

    def gm_prompt_constraints(self, state: Any) -> list[str]:
        """返回 prompt 文本块列表 (每项一段),由 RulesProvider 或主 prompt 拼接。

        子类应包含:
        - "GM 不能编造 X / Y / Z 的硬约束清单"
        - "知识检索是历史参考,state 是事实真相源" 的明示
        """
        return []

    def knowledge_is_reference_only(self) -> bool:
        """检索 retrieval 是否仅作参考(不覆盖 state/scene/rules)。
        所有 policy 都返回 True — 这是 Codex #4 + #7 的全局原则。"""
        return True


# ────────────────────────────────────────────────────────────
# ModuleAdventurePolicy — 5E 模组
# ────────────────────────────────────────────────────────────


class ModuleAdventurePolicy(GamePolicy):
    """5E-compatible 模组 (Ash Mine 等)。最严格。

    所有规则结果必须经 RulesEngine。GM 只描述事实,不裁定。
    """

    id = "module_adventure"

    def preflight(self, user_input: str, state: Any) -> Optional[dict]:
        # 复用现有 classify_combat_intent (已在 rules_bridge.py 实现)。
        # 这里只做协调:任何返回非 None 的 classifier 都构成阻挡。
        try:
            from rules_bridge import classify_combat_intent
        except Exception:
            return None
        block = classify_combat_intent(user_input, state)
        if block:
            return block
        # 未来扩展点:加更多 5E preflight (检定意图歧义 / 资源耗尽 / 死亡豁免 等)
        return None

    def gm_prompt_constraints(self, state: Any) -> list[str]:
        data = getattr(state, "data", state) or {}
        scene = data.get("scene") or {}
        enc = data.get("encounter") or {}
        current_room = scene.get("current_room") or {}

        # 硬约束(belt-and-suspenders;preflight 是主防线,prompt 是兜底)
        lines: list[str] = []
        lines.append("【硬约束 — GM 不得自行裁定】")
        lines.append(
            "下列事项是 RulesEngine / 玩家选择 的专属裁定权。"
            "GM 在正文中**一律不得**宣称这些发生:"
        )
        lines.append("  · 攻击命中 / miss / 暴击 / 伤害数字 (必须 attack_roll 结果)")
        lines.append("  · HP / AC / 先攻 / 状态 / 死亡 变化 (必须 RulesEngine 写)")
        lines.append("  · 借机攻击是否触发 / 命中 (必须玩家选 Disengage / 承受)")
        lines.append("  · 武器是否可用 / disadvantage (e.g. 不得叙述「短弓施展不了」)")
        lines.append("  · 玩家是否被卡住 / 是否能后退 (必须先走 Disengage 流程)")
        # 当前房间 enemies 摘要 — 给 GM 明确"合法敌人清单"
        room_enemies = current_room.get("enemies") or []
        if room_enemies:
            names = "、".join(
                (e.get("name") or e.get("id") or "?") for e in room_enemies
            )
            lines.append(f"  · 当前房间 enemies = [{names}];GM 不得引入这之外的敌人。")
        else:
            lines.append(
                "  · 当前房间 enemies = 空;encounter.active = "
                + ("是" if enc.get("active") else "否") + "。"
            )
        if not enc.get("active") and not room_enemies:
            lines.append(
                "  ⚠️ 当前 encounter 未激活、本房间无 enemies。"
                "**GM 不得在本轮正文中引入任何敌方 NPC 或战斗事件**。"
                "若需要遭遇,必须由 hazard / flag / 玩家明确触发,再由 RulesEngine 启动。"
            )
        lines.append(
            "原则:**RulesEngine 没返回的事实,GM 不能叙述成已发生**。"
            "玩家意图模糊或战斗细节未定时,GM 只能写"
            "「你准备这么做」或「你看见敌人压上」(等敌人 attack 真发生再叙)"
            ",绝不写已经成功/失败/被卡住/失去优势。"
        )

        # Codex #4 + #7: Knowledge 仅作参考,不是真相源
        ref_block = [
            "【数据层级 — 真相源 vs 参考】",
            "- state / scene / encounter / dice_log / player_character / active_entities = **当前事实真相源**",
            "  这些是 RulesEngine / 模组数据写入的硬事实,GM 必须以此为准。",
            "- 知识检索 (retrieved_context / 章节摘要 / 角色卡库 / 世界书) = **风格与背景参考**",
            "  仅用于补叙事色彩,**不能覆盖 state 当前位置 / 当前 HP / 当前敌人**。",
            "  例:retrieval 提到玩家曾在矿坑深处遇敌,但 state.scene.location_id=mine_entrance —",
            "  GM 应按 state 写『在矿道入口』,retrieval 信息可作『你想起之前那次...』的回忆,",
            "  不可作『你正身处矿坑深处』的当前事实。",
        ]
        return lines + [""] + ref_block


# ────────────────────────────────────────────────────────────
# NovelAdaptationPolicy — 小说改编
# ────────────────────────────────────────────────────────────


class NovelAdaptationPolicy(GamePolicy):
    """小说改编 (柏林暗流 等)。
    GM 可自由叙事,但 State Gate 仍管控 _RULES_MANAGED_PATHS 字段。
    """

    id = "novel_adaptation"

    def preflight(self, user_input: str, state: Any) -> Optional[dict]:
        return None  # 小说不拦截战斗;叙事完全交给 GM

    def gm_prompt_constraints(self, state: Any) -> list[str]:
        # 小说也要明示 "state 是真相源,retrieval 是参考"
        return [
            "【数据层级 — 真相源 vs 参考】",
            "- state.player / state.world / state.relationships / state.memory = **当前事实**",
            "- 知识检索 (章节原文 / 角色卡 / 世界书) = **风格与背景参考**,",
            "  补充氛围 / 用词 / 设定细节,但不覆盖 state 当前时刻 / 地点 / 关系。",
        ]


# ────────────────────────────────────────────────────────────
# FreeformPolicy — 通用
# ────────────────────────────────────────────────────────────


class FreeformPolicy(GamePolicy):
    """通用 / freeform 剧本。最宽松。State Gate 仍兜底。"""

    id = "freeform"

    def preflight(self, user_input: str, state: Any) -> Optional[dict]:
        return None

    def gm_prompt_constraints(self, state: Any) -> list[str]:
        return [
            "【数据层级 — 真相源 vs 参考】",
            "- state.* = 当前事实;检索内容仅作参考,不覆盖 state。",
        ]


# ────────────────────────────────────────────────────────────
# 工厂
# ────────────────────────────────────────────────────────────


def get_game_policy(state: Any) -> GamePolicy:
    """根据 state 的 content_pack.kind / scene.module_id 选择对应 policy。

    用 status_payload 一致的判断逻辑:
    - content_pack.kind == "module_adventure" 或 scene.module_id → ModuleAdventurePolicy
    - content_pack.kind == "novel_adaptation" → NovelAdaptationPolicy
    - 其他 → FreeformPolicy
    """
    data = getattr(state, "data", state) or {}
    # 先看 content_pack (从 status_payload 解析过的 manifest)
    try:
        from context_providers import resolve_content_pack
        cp = resolve_content_pack(state) or {}
    except Exception:
        cp = {}
    kind = cp.get("kind") or ""
    scene = data.get("scene") or {}
    if kind == "module_adventure" or scene.get("module_id"):
        return ModuleAdventurePolicy()
    if kind == "novel_adaptation":
        return NovelAdaptationPolicy()
    return FreeformPolicy()


__all__ = [
    "GamePolicy",
    "ModuleAdventurePolicy",
    "NovelAdaptationPolicy",
    "FreeformPolicy",
    "PreflightBlock",
    "get_game_policy",
]
