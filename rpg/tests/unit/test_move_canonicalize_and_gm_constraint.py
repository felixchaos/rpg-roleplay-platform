"""
test_move_canonicalize_and_gm_constraint.py — Bug 4 回归

人工 QA 报告：
- 玩家输入「我仔细调查脚印，然后沿外侧锈轨往东探索」
- Investigation 跑了，但 move 没触发：scene.location_id 仍是 mine_entrance
- audit_log 显示 GM 试图写 player.current_location=「东侧旧铁轨（minecart_track）」被 module_managed 拒绝
- GM 正文却继续叙事「沿着锈蚀的轨道无声地向东摸索过去」
- context_agent 给的 move target 是虚构的 east_rust_track，不是真实 exit minecart_track

修复要求：
1. exit id 规范化：LLM 给出的 east_rust_track / east / east_track 等模糊词
   应该被映射到当前房间的真实 exit id（minecart_track）。
2. 同回合允许多种 kind（skill_check + move 同时跑），不是只跑第一条。
3. 移动失败/规范化失败要在 GM prompt 里显式标记，让 GM 不要叙事成已移动。
"""
from __future__ import annotations

import unittest

from app import (
    _apply_chat_rule_candidates,
    _canonicalize_exit_target,
    _rule_results_prompt,
)
from rules_bridge import start_module, suggest_rule_actions
from state import GameState


class CanonicalizeExitTarget(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")
        # mine_entrance 出口：shaft_lift（推开木桩，进入主井）+ minecart_track（沿外侧锈轨往东）

    def test_exact_match_returns_as_is(self):
        canonical, _ = _canonicalize_exit_target(self.g, "minecart_track")
        self.assertEqual(canonical, "minecart_track")

    def test_llm_hallucinated_id_maps_to_real_exit(self):
        # LLM 经常给 east_rust_track / east_track 之类的虚构 id
        for fake in ("east_rust_track", "east_track", "rust_track_east"):
            canonical, reason = _canonicalize_exit_target(self.g, fake)
            self.assertEqual(canonical, "minecart_track",
                f"Bug 4：{fake!r} 应规范化为 minecart_track；实际 {canonical!r}（reason={reason}）")

    def test_unrelated_id_does_not_match(self):
        # 完全不相关的词不该假命中
        canonical, reason = _canonicalize_exit_target(self.g, "completely_unrelated_name")
        self.assertEqual(canonical, "", f"不相关 id 不该 match：reason={reason}")

    def test_canonicalize_with_no_exits_returns_target(self):
        # 边界：当前房间没有出口（理论上不会发生）
        g = GameState.new()
        # 不 start_module → scene.current_room 无 exits
        canonical, _ = _canonicalize_exit_target(g, "anywhere")
        self.assertEqual(canonical, "anywhere")  # 没有出口约束就 pass-through


class MultipleKindsPerTurn(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")

    def test_skill_check_and_move_can_both_run(self):
        """Bug 4：同回合 skill_check + move 应都触发。
        之前只跑第一条成功，move 就被吞掉。"""
        actions = [
            {"kind": "skill_check", "skill": "investigation", "dc": 10, "reason": "查脚印"},
            {"kind": "move", "to": "minecart_track"},
        ]
        results = _apply_chat_rule_candidates(self.g, actions)
        kinds = [r.get("action", {}).get("kind") for r in results if r.get("out", {}).get("ok")]
        self.assertIn("skill_check", kinds, f"investigation 应被执行：{results}")
        self.assertIn("move", kinds, f"move 应同回合也执行：{results}")
        self.assertEqual(self.g.data["scene"]["location_id"], "minecart_track",
            "scene.location_id 应已切到 minecart_track")

    def test_duplicate_kind_only_runs_once(self):
        """同回合两个 skill_check 只跑第一个，避免重复掷骰。"""
        actions = [
            {"kind": "skill_check", "skill": "investigation", "dc": 10},
            {"kind": "skill_check", "skill": "perception", "dc": 12},
        ]
        results = _apply_chat_rule_candidates(self.g, actions)
        ok_kinds = [r["action"]["kind"] for r in results if r.get("out", {}).get("ok")]
        self.assertEqual(ok_kinds.count("skill_check"), 1,
            f"同回合 skill_check 应只跑一次；实际跑了 {ok_kinds.count('skill_check')} 次")

    def test_failed_action_still_recorded(self):
        """规范化失败 / 不可达的 action 也要进 results，让 GM 看到失败。"""
        actions = [{"kind": "move", "to": "definitely_not_an_exit"}]
        results = _apply_chat_rule_candidates(self.g, actions)
        self.assertEqual(len(results), 1, "失败 action 也要被记录传给 GM")
        self.assertFalse(results[0]["out"].get("ok"), "应是失败")
        self.assertIn("无法前往", results[0]["out"].get("error") or "")


class GmPromptSurfacesFailedMove(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")

    def test_failed_move_appears_in_gm_prompt(self):
        # 用一个真正与所有 exit 完全无关的 id（不含「东/西/track/shaft/lift」等 token），
        # 让规范化无法命中，触发失败路径。
        results = _apply_chat_rule_candidates(self.g, [
            {"kind": "move", "to": "completely_unrelated_xyz"},
        ])
        prompt = _rule_results_prompt(results, self.g)
        self.assertIn("❌", prompt, f"失败 prompt 应含明显失败标记；prompt={prompt}")
        self.assertIn("不要把玩家描述成已经移动", prompt,
            "GM 必须被告知不要叙事成已移动")

    def test_llm_hallucinated_id_is_canonicalized_not_failed(self):
        """LLM 给的 east_rust_track 这种 hallucination 应该被自动救回，不算失败。"""
        results = _apply_chat_rule_candidates(self.g, [
            {"kind": "move", "to": "east_rust_track"},
        ])
        self.assertTrue(results[0]["out"].get("ok"),
            f"east_rust_track 应被规范化为真实 exit，不该失败：{results}")
        self.assertEqual(self.g.data["scene"]["location_id"], "minecart_track")
        # canonicalize 信息应留在 out 里
        canon = results[0]["out"].get("canonicalize") or {}
        self.assertEqual(canon.get("requested"), "east_rust_track")
        self.assertEqual(canon.get("resolved"), "minecart_track")

    def test_canonicalized_move_notes_original_request(self):
        results = _apply_chat_rule_candidates(self.g, [
            {"kind": "skill_check", "skill": "stealth", "dc": 13, "move_to": "east_track"},
        ])
        prompt = _rule_results_prompt(results, self.g)
        # canonicalize 后 prompt 应该提及"系统规范化"
        self.assertIn("规范化", prompt, f"prompt 应记录规范化过程；prompt={prompt}")


class DirectionToExitHelper(unittest.TestCase):
    def setUp(self):
        self.g = GameState.new()
        start_module(self.g, "ash_mine")

    def test_chinese_direction_word_maps_to_exit(self):
        """suggest_rule_actions 解析「沿外侧锈轨往东」应产出 move → minecart_track。"""
        actions = suggest_rule_actions("我沿外侧锈轨往东探索", self.g)
        moves = [a for a in actions if a.get("kind") == "move"]
        self.assertGreater(len(moves), 0, f"应至少一个 move 候选：{actions}")
        self.assertEqual(moves[0].get("to"), "minecart_track",
            f"方向解析应 → minecart_track；实际 {moves[0]}")

    def test_investigation_and_move_both_suggested(self):
        actions = suggest_rule_actions("我仔细调查脚印，然后沿外侧锈轨往东探索", self.g)
        kinds = {a.get("kind") for a in actions}
        self.assertIn("skill_check", kinds, f"应建议 skill_check：{actions}")
        self.assertIn("move", kinds, f"应建议 move：{actions}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
