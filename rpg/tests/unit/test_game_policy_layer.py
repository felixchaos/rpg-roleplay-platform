"""
test_game_policy_layer.py
=========================

Codex 评审 #1+#2:单 GM + GamePolicy + ContextProviders + RulesEngine。

  Base GM
  + GamePolicy        ← 本文件锁
  + ContextProviders  ← 已有
  + RulesEngine       ← 已有

GamePolicy 是协调层,把分散的 5E 约束(combat gate / RulesProvider 硬约束 prompt /
module.json gm_policy)汇总到一个入口。chat handler 用统一接口 `policy.preflight(text, state)`
代替散落各处的 `_rb_classify_combat_intent`,以后扩展只动 policy。

Codex 评审 #4+#7:Knowledge 仅作参考,不覆盖 state/scene/rules。policy 通过
`gm_prompt_constraints(state)` 注入"state=真相源 / retrieval=参考"明示。

Codex 评审 #8:UI 不再宣称"已建立向量库",改为"基础知识库 (关键字 + 章节摘要)"。

测试结构:
  Layer A — GamePolicy 类层 (单元)
  Layer B — chat handler 用 policy 替换 classify_combat_intent (代码引用)
  Layer C — RulesProvider 由 policy 提供 constraints (代码引用)
  Layer D — knowledge 仅作参考 (policy 提供 prompt 文本)
  Layer E — UI 文案不再宣称向量库
"""
from __future__ import annotations

import copy as _copy
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ────────────────────────────────────────────────────────────
# Layer A: GamePolicy 类层 (单元)
# ────────────────────────────────────────────────────────────


class GamePolicyClassUnit(unittest.TestCase):
    """game_policy.py 公共 API:get_game_policy / preflight / gm_prompt_constraints。"""

    def _state(self, *, module_id="ash_mine", encounter_active=False, live_enemies=None):
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["scene"] = {
            "module_id": module_id,
            "location_id": "minecart_track" if module_id else "",
            "current_room": {
                "id": "minecart_track" if module_id else "",
                "enemies": [], "exits": [], "checks": [], "hazards": [],
                "visible_clues": [], "npcs": [], "loot": [], "flags": {},
            },
        }
        g.data["encounter"] = {
            "active": encounter_active,
            "combatants": [
                {"id": e.get("id"), "name": e.get("name"),
                 "side": "enemy", "defeated": False}
                for e in (live_enemies or [])
            ],
        }
        return g

    def test_module_policy_selected_for_ash_mine(self):
        from game_policy import ModuleAdventurePolicy, get_game_policy
        g = self._state(module_id="ash_mine")
        policy = get_game_policy(g)
        self.assertIsInstance(policy, ModuleAdventurePolicy)
        self.assertEqual(policy.id, "module_adventure")

    def test_freeform_policy_for_empty_module(self):
        from game_policy import FreeformPolicy, get_game_policy
        g = self._state(module_id="")
        # 同时清掉 content_pack 让 resolve 返回 freeform
        policy = get_game_policy(g)
        self.assertIsInstance(policy, FreeformPolicy)

    def test_module_preflight_blocks_no_target_combat(self):
        """ModuleAdventurePolicy.preflight 应该等价于现有的 classify_combat_intent。"""
        from game_policy import get_game_policy
        g = self._state(module_id="ash_mine")  # 无敌人 + 无 encounter
        policy = get_game_policy(g)
        block = policy.preflight("我用短弓射", g)
        self.assertIsNotNone(block, "想战斗但无敌人 → 必须阻挡")
        self.assertEqual(block["kind"], "no_target_combat")

    def test_module_preflight_blocks_combat_question(self):
        from game_policy import get_game_policy
        g = self._state(module_id="ash_mine",
                        encounter_active=True,
                        live_enemies=[{"id": "x", "name": "X"}])
        policy = get_game_policy(g)
        block = policy.preflight("向后拉开距离继续放箭", g)
        self.assertIsNotNone(block)
        self.assertEqual(block["kind"], "combat_pending_question")

    def test_freeform_preflight_never_blocks(self):
        from game_policy import get_game_policy
        g = self._state(module_id="")
        policy = get_game_policy(g)
        # 任何输入 freeform 都不拦
        self.assertIsNone(policy.preflight("我用短弓射敌人", g))
        self.assertIsNone(policy.preflight("观察四周", g))

    def test_module_constraints_are_data_only_no_behavior_directives(self):
        """架构原则 (2026-05 用户评审):DnD 规则裁定 = deterministic 自动化,
        agent 不参与。gm_prompt_constraints **不得**用 "GM 不得 X / 必须 Y" 这种
        prompt 教条堵 LLM。任何 5E 规则约束都靠 preflight + RulesEngine + State Gate
        在系统层挡掉,不靠 prompt。

        本测试断言 ModuleAdventurePolicy.gm_prompt_constraints 只输出:
        1. 场景事实快照 (enemies / encounter.active) — 客观陈述
        2. 数据来源指南 (state 是真相源 / retrieval 是参考) — 上下文路由
        不输出任何 "GM 不得攻击命中 / HP / 借机攻击 / 武器可用性 / 卡住..." 行为指令。
        """
        from game_policy import get_game_policy
        g = self._state(module_id="ash_mine")
        policy = get_game_policy(g)
        text = "\n".join(policy.gm_prompt_constraints(g))

        # 应当含事实快照 + 数据来源
        self.assertIn("场景事实快照", text)
        self.assertIn("enemies", text)
        self.assertIn("encounter.active", text)
        self.assertIn("数据层级", text)
        self.assertIn("真相源", text)
        self.assertIn("参考", text)

        # 不得含 5E 规则行为指令 — 这些都改由 deterministic 后端管
        forbidden_directives = (
            "GM 不得自行裁定",
            "GM 在正文中**一律不得**",
            "攻击命中 / miss / 暴击 / 伤害数字",
            "HP / AC / 先攻 / 状态 / 死亡 变化",
            "借机攻击是否触发",
            "武器是否可用 / disadvantage",
            "玩家是否被卡住",
            "不得引入这之外的敌人",
            "不得在本轮正文中引入任何敌方 NPC",
            "RulesEngine 没返回的事实",
            "绝不写已经成功",
        )
        for token in forbidden_directives:
            self.assertNotIn(token, text,
                f"gm_prompt_constraints 不应再有 prompt 行为指令: {token!r}")

    def test_module_constraints_show_enemy_snapshot_as_fact_not_directive(self):
        """无敌人 + 无 encounter 时,只该陈述"enemies = 空 / encounter.active = 否",
        不该再写"GM 不得在本轮引入敌人"这种行为约束 — 后者属于 prompt 教条。"""
        from game_policy import get_game_policy
        g = self._state(module_id="ash_mine",
                        encounter_active=False, live_enemies=[])
        text = "\n".join(get_game_policy(g).gm_prompt_constraints(g))
        # 事实数据应在
        self.assertIn("enemies", text)
        self.assertIn("encounter.active", text)
        # 行为指令不该再出现
        self.assertNotIn("不得引入", text)
        self.assertNotIn("**GM 不得在本轮正文中引入任何敌方 NPC**", text)

    def test_novel_constraints_include_knowledge_reference_disclaimer(self):
        # 即使是小说模式也要明示 state=真相源 / retrieval=参考
        from game_policy import NovelAdaptationPolicy
        policy = NovelAdaptationPolicy()
        text = "\n".join(policy.gm_prompt_constraints({}))
        self.assertIn("真相源", text)
        self.assertIn("参考", text)


# ────────────────────────────────────────────────────────────
# Layer B: chat handler 用 policy 入口
# ────────────────────────────────────────────────────────────


class ChatHandlerUsesGamePolicy(unittest.TestCase):
    """app.py chat 流程应通过 get_game_policy().preflight 而非直接
    classify_combat_intent。这是协调层抽象,以后扩展只动 policy。"""

    @classmethod
    def setUpClass(cls):
        cls.app_text = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((PROJECT_ROOT / "rpg" / "chat_pipeline").glob("*.py")))

    def test_chat_handler_calls_get_game_policy(self):
        self.assertIn("get_game_policy", self.app_text,
            "chat handler 应 import get_game_policy")

    def test_chat_handler_uses_policy_preflight(self):
        self.assertIn(".preflight(message_for_model", self.app_text,
            "chat handler 应该调 policy.preflight(message_for_model, state)")


# ────────────────────────────────────────────────────────────
# Layer C: RulesProvider 由 policy 提供 prompt
# ────────────────────────────────────────────────────────────


class RulesProviderDelegatesToPolicy(unittest.TestCase):
    """RulesProvider 不再硬编码"硬约束"清单,改为调 policy.gm_prompt_constraints。
    避免两处分别维护;新增约束只动 game_policy.py。"""

    @classmethod
    def setUpClass(cls):
        cls.rules_text = (PROJECT_ROOT / "rpg" / "context_providers" / "rules.py").read_text(encoding="utf-8")

    def test_rules_provider_imports_game_policy(self):
        self.assertIn("game_policy", self.rules_text,
            "RulesProvider 应引用 game_policy 模块拿 constraints")

    def test_rules_provider_no_longer_hardcodes_constraints(self):
        # 旧版直接 lines.append("【硬约束 — GM 不得自行裁定】") 改成由 policy 提供
        # 我们允许 lines 里仍有这串(policy 注入的),但不应该有"if is_module_adventure"
        # 这种独立分支逻辑(那已经在 policy 里做了)。
        self.assertNotIn("is_module_adventure = (", self.rules_text,
            "RulesProvider 不应再判断 is_module_adventure;由 policy 切换")


# ────────────────────────────────────────────────────────────
# Layer D: knowledge 仅作参考 — 系统级断言
# ────────────────────────────────────────────────────────────


class KnowledgeIsReferenceOnly(unittest.TestCase):
    """所有 GamePolicy 子类必须声明 knowledge_is_reference_only() = True。
    这是全局原则:state 是真相源,retrieval 是参考。"""

    def test_all_policies_say_knowledge_is_reference_only(self):
        from game_policy import FreeformPolicy, ModuleAdventurePolicy, NovelAdaptationPolicy
        for cls in (ModuleAdventurePolicy, NovelAdaptationPolicy, FreeformPolicy):
            p = cls()
            self.assertTrue(p.knowledge_is_reference_only(),
                f"{cls.__name__}.knowledge_is_reference_only 应为 True")

    def test_module_policy_explicit_layer_block_in_prompt(self):
        """module 模式的 constraint 必须包含"state vs retrieval"分层说明。"""
        from game_policy import ModuleAdventurePolicy
        # 不需要真 state — constraint 文本本身就有那段
        p = ModuleAdventurePolicy()
        text = "\n".join(p.gm_prompt_constraints({}))
        self.assertIn("数据层级", text)
        self.assertIn("state", text)
        self.assertIn("retrieval", text.lower())


# ────────────────────────────────────────────────────────────
# Layer E: UI 文案不再宣称向量库 (Codex #8)
# ────────────────────────────────────────────────────────────


class UiKnowledgeBaseWording(unittest.TestCase):
    """前端不该假装"已建立向量库" — 实际 _embed_query() 是 stub,
    pgvector 余弦查询自动退化到 ILIKE。文案需如实表达。"""

    @classmethod
    def setUpClass(cls):
        cls.platform_text = (PROJECT_ROOT / "frontend" / "src" / "platform-app.jsx").read_text(encoding="utf-8")

    def test_no_false_vector_db_claim(self):
        # 不再有"向量库已建立"这种宣称
        # (允许出现在注释里作历史说明 — 用 regex 找非注释行)
        for line in self.platform_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                continue
            self.assertNotIn("已建立向量库", line,
                f"非注释行不应有'已建立向量库'宣称: {line[:120]}")
            self.assertNotIn("向量索引已建立", line,
                f"非注释行不应有'向量索引已建立': {line[:120]}")

    def test_import_toast_has_honest_wording(self):
        # 导入成功 toast 应明确说"基础知识库 (关键字 + 章节摘要)" 或类似
        # 旧文案: "知识库后台同步中" — 太模糊容易被误解为向量库
        self.assertIn("基础知识库", self.platform_text,
            "导入成功 toast 应使用'基础知识库'明示当前实现层级")
        self.assertTrue(
            "关键字" in self.platform_text or "章节摘要" in self.platform_text,
            "应说明检索基于'关键字'或'章节摘要',而非向量",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
