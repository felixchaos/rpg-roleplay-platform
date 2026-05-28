"""
test_context_providers.py — ContextProvider 架构验收测试。

覆盖：
- Ash Mine context isolation：不包含 novel_timeline / novel_retrieval / ChapterFact / 柏林残渣
- Novel backward compatibility：legacy script_id 默认 novel_adaptation；providers 包含 novel_*
- Demand Resolver independence：Demand 输出本身不选数据源
- Manifest resolver：优先级正确（state.content_pack > scene.module_id > script_id > freeform）
- Provider registry：自动加载内置 provider；缺失 provider 不让 pipeline 崩
"""
from __future__ import annotations

import unittest

from agents.context_agent import _demand_from_curator_plan, run_context_agent
from context_providers import (
    DEFAULT_FREEFORM_MANIFEST,
    DEFAULT_MODULE_MANIFEST,
    DEFAULT_NOVEL_MANIFEST,
    Demand,
    ProviderServices,
    available_providers,
    resolve_content_pack,
    run_providers,
)
from context_providers.registry import _REGISTRY
from rules_bridge import enter_room, start_module
from state import GameState


class ProviderRegistryTests(unittest.TestCase):
    def test_builtin_providers_registered(self):
        ids = set(available_providers())
        for required in (
            "memory", "worldline", "recent_chat",
            "module_scene", "module_encounter", "module_worldbook",
            "novel_timeline", "novel_retrieval", "novel_characters", "novel_worldbook",
            "rules",
        ):
            self.assertIn(required, ids, f"内置 provider {required} 未注册")

    def test_missing_provider_warns_but_does_not_crash(self):
        g = GameState.new()
        manifest = {"context_providers": ["does_not_exist", "memory"]}
        contribs, used = run_providers(g, manifest, Demand.empty(), ProviderServices())
        self.assertEqual(used, ["memory"])
        missing = next(c for c in contribs if c.provider_id == "does_not_exist")
        self.assertFalse(missing.applied)
        self.assertTrue(any("未注册" in w for w in missing.warnings))


class ManifestResolutionTests(unittest.TestCase):
    def test_module_scene_wins_over_script(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        m = resolve_content_pack(g, script_id=42)
        self.assertEqual(m["kind"], "module_adventure")
        self.assertEqual(m["id"], "ash_mine")
        self.assertIn("module_scene", m["context_providers"])
        self.assertNotIn("novel_timeline", m["context_providers"])

    def test_script_id_implies_novel_adaptation(self):
        g = GameState.new()
        m = resolve_content_pack(g, script_id=42)
        self.assertEqual(m["kind"], "novel_adaptation")
        self.assertIn("novel_timeline", m["context_providers"])
        self.assertIn("novel_retrieval", m["context_providers"])

    def test_legacy_history_save_defaults_to_novel(self):
        g = GameState.new()
        g.data["history"] = [{"role": "user", "content": "继续"}]
        m = resolve_content_pack(g)
        self.assertEqual(m["kind"], "novel_adaptation")

    def test_empty_state_falls_back_to_freeform(self):
        g = GameState.new()
        m = resolve_content_pack(g)
        self.assertEqual(m["kind"], "freeform")
        # freeform 不含小说 / 模组 providers
        self.assertNotIn("novel_timeline", m["context_providers"])
        self.assertNotIn("module_scene", m["context_providers"])

    def test_module_manifest_uses_module_json_context_providers(self):
        # Ash Mine module.json 应声明完整 context_providers 列表
        g = GameState.new()
        start_module(g, "ash_mine")
        m = resolve_content_pack(g)
        self.assertEqual(
            sorted(m["context_providers"]),
            sorted(["module_scene", "module_encounter", "module_worldbook",
                    "rules", "memory", "worldline", "recent_chat"]),
        )
        # retrieval_policy 应禁用小说检索
        self.assertFalse(m["retrieval_policy"]["allow_script_retrieval"])
        self.assertFalse(m["retrieval_policy"]["allow_chapter_facts"])


class AshMineContextIsolationTests(unittest.TestCase):
    """核心验收：Ash Mine 模组 context 绝不能混入小说残渣。"""

    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")
        enter_room(self.g, "minecart_track")

    def _run(self, user_input: str) -> dict:
        result = None
        for ev in run_context_agent(self.g, user_input):
            if ev.get("type") == "result":
                result = ev
        self.assertIsNotNone(result, "context_agent 没产出 result")
        return result

    def test_ash_mine_does_not_use_novel_providers(self):
        result = self._run("我悄悄靠近矿车")
        used = result["providers_used"]
        self.assertNotIn("novel_timeline", used)
        self.assertNotIn("novel_retrieval", used)
        self.assertNotIn("novel_characters", used)
        self.assertNotIn("novel_worldbook", used)

    def test_ash_mine_uses_module_providers(self):
        result = self._run("我悄悄靠近矿车")
        used = result["providers_used"]
        self.assertIn("module_scene", used)
        # module_encounter 在无战斗时会 skip；不强制要求
        self.assertIn("module_worldbook", used)
        self.assertIn("rules", used)
        self.assertIn("memory", used)
        self.assertIn("worldline", used)

    def test_ash_mine_prompt_has_no_novel_residue(self):
        result = self._run("我悄悄靠近矿车")
        prompt = result["bundle"]["prompt"]
        banned = ["原著锚点", "ChapterFact", "柏林", "杭雁菱", "蕾穆丽娜",
                  "图卢兹", "扎兹巴鲁姆", "薇瑟帝国"]
        for word in banned:
            self.assertNotIn(word, prompt, f"Ash Mine prompt 混入了 {word!r}")

    def test_ash_mine_layers_have_no_novel_ids(self):
        result = self._run("我悄悄靠近矿车")
        layer_ids = {lyr["id"] for lyr in result["bundle"]["debug"]["layers"]}
        # novel_* layers 不应出现
        for forbidden in ("novel_timeline", "novel_retrieval", "novel_worldbook",
                          "novel_characters", "npc_cards", "player_card", "worldbook"):
            self.assertNotIn(forbidden, layer_ids, f"Ash Mine 层包含 {forbidden}")
        # module_scene 必须出现
        self.assertIn("module_scene", layer_ids)
        self.assertIn("rules", layer_ids)

    def test_ash_mine_active_content_pack_metadata(self):
        result = self._run("我悄悄靠近矿车")
        cp = result["active_content_pack"]
        self.assertEqual(cp["kind"], "module_adventure")
        self.assertEqual(cp["id"], "ash_mine")
        self.assertFalse(cp["retrieval_policy"]["allow_script_retrieval"])
        self.assertTrue(cp["gm_policy"]["must_obey_rules_result"])


class NovelBackwardCompatTests(unittest.TestCase):
    """旧柏林存档 / script_id 必须仍走 novel pipeline。"""

    def _build_legacy(self) -> GameState:
        g = GameState.new()
        g.data["history"] = [
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "你站在柏林街头。"},
        ]
        return g

    def _run(self, g, user_input: str, script_id: int = 42) -> dict:
        result = None
        for ev in run_context_agent(g, user_input, script_id=script_id):
            if ev.get("type") == "result":
                result = ev
        self.assertIsNotNone(result)
        return result

    def test_legacy_save_uses_novel_providers(self):
        g = self._build_legacy()
        result = self._run(g, "继续观察")
        used = result["providers_used"]
        self.assertIn("novel_timeline", used)
        # novel_retrieval 依赖外部 retrieve_fn；不强制 used，但至少在 manifest 里
        cp = result["active_content_pack"]
        self.assertIn("novel_retrieval", cp["context_providers"])
        self.assertIn("memory", used)

    def test_legacy_save_does_not_load_module_providers(self):
        g = self._build_legacy()
        result = self._run(g, "继续观察")
        used = result["providers_used"]
        for forbidden in ("module_scene", "module_encounter", "module_worldbook"):
            self.assertNotIn(forbidden, used)

    def test_legacy_save_manifest_kind(self):
        g = self._build_legacy()
        result = self._run(g, "继续观察")
        cp = result["active_content_pack"]
        self.assertEqual(cp["kind"], "novel_adaptation")


class DemandResolverIndependenceTests(unittest.TestCase):
    """Demand Resolver 输出 player_intent / rule_candidate_actions / retrieval_query
    本身不应该选择具体数据源（不带 'novel'/'module' 字样的来源决策）。"""

    def test_demand_dataclass_is_source_agnostic(self):
        # _demand_from_curator_plan 应产出 Demand，字段不含任何 provider id 决策
        plan = {
            "intent": "玩家想悄悄靠近矿车",
            "active_goal": "通过潜行检定",
            "rule_candidate_actions": [{"kind": "skill_check", "skill": "stealth", "dc": 13}],
            "retrieval_query": "潜行 矿车",
            "confidence": 0.8,
        }
        demand = _demand_from_curator_plan(plan, "我悄悄靠近矿车")
        # Demand 本身不知道哪个 provider 会启用
        self.assertEqual(demand.player_intent, "玩家想悄悄靠近矿车")
        self.assertEqual(len(demand.rule_candidate_actions), 1)
        # Demand 字段中不应出现 provider 名（说明它没绑定到具体数据源）
        keys = set(demand.to_dict().keys())
        self.assertNotIn("provider_id", keys)
        self.assertNotIn("uses_novel", keys)
        self.assertNotIn("uses_module", keys)


class ProviderContributionShapeTests(unittest.TestCase):
    """每个 provider 的 ContextContribution 输出契约。"""

    def test_module_scene_contribution_shape(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        manifest = resolve_content_pack(g)
        contribs, used = run_providers(g, manifest, Demand.empty(), ProviderServices())
        scene_c = next(c for c in contribs if c.provider_id == "module_scene")
        self.assertTrue(scene_c.applied)
        self.assertEqual(scene_c.kind, "module_scene")
        self.assertGreater(len(scene_c.layers), 0)
        # 至少一个 fact
        self.assertGreaterEqual(len(scene_c.facts), 1)
        # debug 含 module_id
        self.assertEqual(scene_c.debug.get("module_id"), "ash_mine")

    def test_skipped_provider_marks_applied_false(self):
        # 在没有 module 的 state 上跑 module manifest，module_scene 应 skip
        g = GameState.new()
        # 强制用 module manifest，但 scene 没设
        contribs, used = run_providers(
            g, DEFAULT_MODULE_MANIFEST, Demand.empty(), ProviderServices()
        )
        scene_c = next(c for c in contribs if c.provider_id == "module_scene")
        self.assertFalse(scene_c.applied)


if __name__ == "__main__":
    unittest.main(verbosity=2)
