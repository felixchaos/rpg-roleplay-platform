"""
test_panel_rules_isolation.py — 5E rules panel 必须根据 ContentPack manifest 自适应。

回归 case：用户在小说存档（柏林暗流篇）里看到 5E 规则 tab 显示一套不属于
该剧本的默认 5E 角色卡 + "开始：灰烬矿坑" 按钮。模组加载入口应该只在
Platform 冒险模组页（创建新存档），不在当前剧本里污染。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from state import GameState
from rules_bridge import start_module


class ContentPackPayloadTests(unittest.TestCase):
    """/api/state 应暴露 content_pack manifest 供 FE 自适应。"""

    def test_brand_new_state_content_pack_is_freeform(self):
        g = GameState.new()
        p = g.status_payload()
        self.assertIn("content_pack", p)
        self.assertEqual(p["content_pack"]["kind"], "freeform")
        self.assertNotEqual(p["content_pack"]["kind"], "module_adventure")

    def test_legacy_history_save_content_pack_is_novel(self):
        """有 history 但无 module/script_id 的旧存档应归为 novel_adaptation。"""
        g = GameState.new()
        g.data["history"] = [
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "夜色压在哈布斯堡街道上。"},
        ]
        p = g.status_payload()
        self.assertEqual(p["content_pack"]["kind"], "novel_adaptation")

    def test_module_save_content_pack_is_module_adventure(self):
        g = GameState.new()
        start_module(g, "ash_mine")
        p = g.status_payload()
        self.assertEqual(p["content_pack"]["kind"], "module_adventure")
        self.assertEqual(p["content_pack"]["id"], "ash_mine")


class DefaultStateNotPollutedTests(unittest.TestCase):
    """DEFAULT_STATE.player_character 必须是空骨架。
    否则新建小说存档会带上 hp=9/ac=13/默认属性，PanelRules 在小说剧本里就
    会误显示一套不属于该剧本的 5E 角色卡。"""

    def test_brand_new_player_character_is_empty(self):
        g = GameState.new()
        pc = g.data["player_character"]
        self.assertEqual(pc["hp"], 0, "新存档 hp 必须为 0；非 0 会污染小说存档")
        self.assertEqual(pc["max_hp"], 0)
        self.assertEqual(pc["ac"], 0)
        self.assertEqual(pc["level"], 0)
        self.assertEqual(pc["abilities"], {})
        self.assertEqual(pc["class_name"], "")

    def test_status_payload_player_character_empty_for_novel(self):
        g = GameState.new()
        g.data["history"] = [{"role": "user", "content": "继续"}]
        p = g.status_payload()
        pc = p["player_character"]
        # FE PanelRules 的早期 return 会处理 manifest.kind != module_adventure，
        # 但即便它不返回，hp=0/abilities={} 也不会被误显示成完整角色卡。
        self.assertEqual(pc["hp"], 0)
        self.assertEqual(pc["ac"], 0)
        self.assertEqual(pc["abilities"], {})

    def test_start_module_populates_player_character(self):
        """模组开启时才填入默认 5E 角色——保证模组模式 UI 正常。"""
        g = GameState.new()
        start_module(g, "ash_mine")
        pc = g.data["player_character"]
        self.assertGreater(pc["hp"], 0)
        self.assertGreater(pc["max_hp"], 0)
        self.assertGreater(pc["ac"], 0)
        self.assertGreater(pc["level"], 0)
        self.assertGreater(len(pc["abilities"]), 0)


class PanelRulesFrontendBranchTests(unittest.TestCase):
    """game-panels.jsx PanelRules 必须根据 content_pack.kind 自适应。"""

    @classmethod
    def setUpClass(cls):
        panel = Path(__file__).resolve().parents[2] / "frontend" / "src" / "game-panels.jsx"
        cls.text = panel.read_text(encoding="utf-8")

    def test_panel_reads_content_pack(self):
        self.assertIn("state.content_pack", self.text,
            "PanelRules 必须读取 state.content_pack 才能根据 kind 切换 UI")

    def test_panel_short_circuits_for_non_module_kind(self):
        self.assertIn("module_adventure", self.text,
            "PanelRules 必须判断 module_adventure kind 来决定显示规则 UI")
        self.assertIn("5E 规则不适用", self.text,
            "非 module_adventure 剧本必须显示明确的『不适用』提示，"
            "不能默默渲染一套空角色卡")

    def test_panel_no_inline_start_module_button(self):
        """PanelRules 不应该内嵌『开始：xxx 模组』按钮——
        模组加载必须从 Platform 冒险模组页发起（建新存档），
        不能在当前剧本会话里污染存档。"""
        self.assertNotIn('disabled={busy} onClick={() => startModule(m.id)}', self.text,
            "PanelRules 内不能有 startModule 调用按钮；应只在 Platform ModulesPage 提供入口")

    def test_panel_keeps_module_runtime_actions(self):
        """模组进行中仍需要 move/doAction/encounter 控制按钮（不被本次重构误删）。"""
        for keep in ("move(", "doAction(", "startEncounter(", "nextTurn(", "enemyAttack("):
            self.assertIn(keep, self.text, f"PanelRules 缺少 {keep} 控制函数")


class StateGateStillProtectsAfterDefaultChangeTests(unittest.TestCase):
    """清空 DEFAULT_STATE.player_character 默认值后，State Gate 仍要拦截 GM 直写。"""

    def test_gm_cannot_overwrite_player_hp_after_default_changes(self):
        g = GameState.new()
        result = g.apply_state_write("player_character.hp=5", source="gm")
        self.assertIn("rules_managed", result)
        # 仍然是 0（无变化）
        self.assertEqual(g.data["player_character"]["hp"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
