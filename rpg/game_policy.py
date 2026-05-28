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
from typing import Any


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

    def preflight(self, user_input: str, state: Any) -> dict | None:
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

    def preflight(self, user_input: str, state: Any) -> dict | None:
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
        """只输出**事实数据快照**+**数据来源指南**。

        架构原则 (用户 2026-05 评审定调):
        - DnD 规则裁定 = deterministic 自动化规则 (preflight gate + RulesEngine)。
        - LLM agent 不参与规则判断,也不应该用 prompt "教" 它不要乱裁定。
        - 任何 5E 规则约束 (攻击命中 / HP / 借机攻击 / 武器可用性 / 卡住 / 投降是否被接受...)
          应该通过 `classify_combat_intent` 等 deterministic preflight 在 GM 被调用前
          就挡掉,或通过 `_apply_chat_rule_candidates` 跑出 dice_log + verdict,
          再以**事实**形式喂给 GM —— 不在这个 prompt 段里用 "不得 X" 教条堵 LLM。

        所以本方法只输出:
        1. 当前 encounter 状态 / 房间 enemies 名单 = 场景事实快照
        2. "state 是真相源,检索是参考" = 数据来源路由 (告诉 GM 用哪份数据)

        不输出任何 "GM 不得 X / 必须由 Y / 不能 Z" 的行为约束。
        """
        data = getattr(state, "data", state) or {}
        scene = data.get("scene") or {}
        enc = data.get("encounter") or {}
        current_room = scene.get("current_room") or {}

        lines: list[str] = []

        # 1) 场景事实快照 — 客观陈述,不带"不得 X"
        lines.append("【场景事实快照】")
        room_enemies = current_room.get("enemies") or []
        if room_enemies:
            names = "、".join(
                (e.get("name") or e.get("id") or "?") for e in room_enemies
            )
            lines.append(f"- 当前房间 enemies = [{names}]")
        else:
            lines.append("- 当前房间 enemies = 空")
        lines.append(
            "- encounter.active = " + ("是" if enc.get("active") else "否")
        )

        # 2) 数据层级 — 数据来源指南 (Codex #4 + #7)
        ref_block = [
            "【数据层级 — 真相源 vs 参考】",
            "- state / scene / encounter / dice_log / player_character / active_entities = **当前事实真相源**",
            "  这些是 RulesEngine / 模组数据写入的硬事实,GM 必须以此为准。",
            "- 知识检索 (retrieved_context / 章节摘要 / 角色卡库 / 世界书) = **风格与背景参考**",
            "  仅用于补叙事色彩,不能覆盖 state 当前位置 / 当前 HP / 当前敌人。",
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

    def preflight(self, user_input: str, state: Any) -> dict | None:
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

    def preflight(self, user_input: str, state: Any) -> dict | None:
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
