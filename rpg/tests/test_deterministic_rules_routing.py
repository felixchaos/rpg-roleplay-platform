"""
test_deterministic_rules_routing.py
====================================

架构原则锁 (2026-05 用户评审):

  DnD 规则裁定 = deterministic 自动化运行的规则,**不靠 prompt 教 agent**。
  Agent (GM LLM) 只负责叙事;玩家意图 → 规则裁定 → state 写入 全在系统层完成。

本测试覆盖最近落地的两条 deterministic 路径,确保它们不退化回 "agent 自觉":

  Layer A — pending_question 自动过期 (state.expire_stale_gm_questions)
            玩家进入新一轮 → 上一轮未答 GM 询问自动 expire,不依赖玩家手动 clear。
            来源:UI bug "2 项待确认" 同时挂两个,玩家无法继续。

  Layer B — 社交意图 (投降 / 求饶 / 挣脱) deterministic 路由
            INTENT_KEYWORDS regex → suggest_rule_actions → Persuasion / Athletics 检定。
            来源:用户反馈 "投降没有显式 ro 点判定,GM 直接接受"。
            纠正:不在 prompt 教 GM "投降是否被接受",直接走 deterministic skill_check
            写 dice_log,GM 只看 verdict 叙事。

  Layer C — 入口在 chat handler:expire 先于 apply_player_directives,
            INTENT_KEYWORDS 经 _chat_rule_candidates → _apply_chat_rule_candidates 落地。
"""
from __future__ import annotations

import copy as _copy
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ────────────────────────────────────────────────────────────────────
# Layer A: pending_question 自动过期
# ────────────────────────────────────────────────────────────────────


class ExpireStaleGmQuestionsUnit(unittest.TestCase):
    """state.expire_stale_gm_questions:玩家进入新一轮自动过期上轮系统询问。"""

    def _fresh_state(self):
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = 0
        g.data.setdefault("permissions", {})["pending_questions"] = []
        g.data["permissions"]["audit_log"] = []
        return g

    def test_old_gm_question_expires_on_next_turn(self):
        g = self._fresh_state()
        g.data["turn"] = 5
        # turn 5 GM 问了一个,玩家没答
        self.assertTrue(g.add_pending_question(
            "你打算如何离开?", source="gm", options=["A", "B"],
        ))
        # 玩家直接进入新一轮 (turn 6)
        g.data["turn"] = 6
        expired = g.expire_stale_gm_questions()
        self.assertEqual(expired, 1, "上一轮 GM 询问必须过期")
        self.assertEqual(g.data["permissions"]["pending_questions"], [])
        # audit_log 必须留痕 (deterministic 可追溯)
        last_audit = g.data["permissions"]["audit_log"][-1]
        self.assertEqual(last_audit["kind"], "pending_questions_expired")
        self.assertEqual(last_audit["expired_count"], 1)
        self.assertEqual(last_audit["current_turn"], 6)

    def test_current_turn_question_not_expired(self):
        """同一回合的询问不过期 (player 还在当前回合可能要回答)。"""
        g = self._fresh_state()
        g.data["turn"] = 5
        g.add_pending_question("你打算如何离开?", source="gm")
        # 同回合不进新一轮
        expired = g.expire_stale_gm_questions(current_turn=5)
        self.assertEqual(expired, 0)
        self.assertEqual(len(g.data["permissions"]["pending_questions"]), 1)

    def test_rules_engine_question_also_expires(self):
        """source=rules_engine 也算系统询问 → 新一轮过期。"""
        g = self._fresh_state()
        g.data["turn"] = 3
        g.add_pending_question(
            "想战斗但视野无敌人,要先做什么?",
            source="rules_engine",
            options=["观察", "撤退"],
        )
        g.data["turn"] = 4
        expired = g.expire_stale_gm_questions()
        self.assertEqual(expired, 1)

    def test_curator_question_also_expires(self):
        """source=curator:clarify (子代理澄清询问) 同样系统化过期。"""
        g = self._fresh_state()
        g.data["turn"] = 2
        g.add_pending_question("你说的『他』指谁?", source="curator:clarify")
        g.data["turn"] = 3
        self.assertEqual(g.expire_stale_gm_questions(), 1)

    def test_player_authored_question_not_expired(self):
        """玩家自己挂的 pending question (source 不是系统类) 不动 — 那是玩家笔记。"""
        g = self._fresh_state()
        g.data["turn"] = 5
        # 模拟玩家自己 add (source != gm/rules_engine/curator/extractor/set_parser)
        g.data["permissions"]["pending_questions"].append({
            "id": "player_note",
            "question": "记得问铁匠铺的事",
            "options": [],
            "source": "player",
            "turn": 5,
        })
        g.data["turn"] = 6
        expired = g.expire_stale_gm_questions()
        self.assertEqual(expired, 0)
        self.assertEqual(len(g.data["permissions"]["pending_questions"]), 1)

    def test_multiple_old_questions_all_expire(self):
        """多条堆积的旧 GM 询问全部过期 — 这是 bug 现场 "2 项待确认" 的修法。"""
        g = self._fresh_state()
        g.data["turn"] = 3
        g.add_pending_question("Q1?", source="gm")
        g.data["turn"] = 4
        g.add_pending_question("Q2?", source="gm")
        g.data["turn"] = 5
        expired = g.expire_stale_gm_questions()
        self.assertEqual(expired, 2, "两轮旧询问都要过期")
        self.assertEqual(g.data["permissions"]["pending_questions"], [])

    def test_audit_log_records_expired_metadata(self):
        """audit_log 必须留 deterministic 追溯字段:source / turn / question。"""
        g = self._fresh_state()
        g.data["turn"] = 7
        g.add_pending_question("追溯测试", source="gm")
        g.data["turn"] = 8
        g.expire_stale_gm_questions(reason="new_chat_turn")
        log = g.data["permissions"]["audit_log"][-1]
        self.assertEqual(log["source"], "expire_stale_gm_questions")
        self.assertEqual(log["reason"], "new_chat_turn")
        self.assertEqual(len(log["expired"]), 1)
        e = log["expired"][0]
        self.assertEqual(e["source"], "gm")
        self.assertEqual(e["turn"], 7)
        self.assertIn("追溯", e["question"])

    def test_empty_pending_questions_safe(self):
        g = self._fresh_state()
        g.data["turn"] = 10
        self.assertEqual(g.expire_stale_gm_questions(), 0)
        # 不该往 audit_log 写空记录
        self.assertEqual(g.data["permissions"]["audit_log"], [])


class ChatHandlerCallsExpire(unittest.TestCase):
    """app.py chat handler 必须在 apply_player_directives 之前调 expire_stale_gm_questions。
    用源码静态扫描确保入口正确接通 — bug 现场就是这步漏了。"""

    @classmethod
    def setUpClass(cls):
        cls.app_text = (PROJECT_ROOT / "rpg" / "app.py").read_text(encoding="utf-8")

    def test_chat_imports_or_calls_expire(self):
        self.assertIn("expire_stale_gm_questions", self.app_text,
            "app.py chat 流程必须调 state.expire_stale_gm_questions")

    def test_expire_is_before_apply_directives(self):
        """expire 必须在 apply_player_directives **之前** — 否则玩家这一轮的
        directive 还在用旧 pending_question 跟自己打架。"""
        idx_expire = self.app_text.find("expire_stale_gm_questions")
        idx_apply = self.app_text.find("apply_player_directives")
        self.assertGreater(idx_expire, 0)
        self.assertGreater(idx_apply, 0)
        self.assertLess(idx_expire, idx_apply,
            "expire 必须在 apply_player_directives 之前调用")


# ────────────────────────────────────────────────────────────────────
# Layer B: 社交意图 → deterministic 检定路由
# ────────────────────────────────────────────────────────────────────


class SocialIntentDeterministicRouting(unittest.TestCase):
    """投降 / 求饶 / 挣脱 等社交意图必须 deterministic 路由到对应检定,
    **不依赖 prompt 教 GM** "投降是否被接受"。

    流程:玩家文本 → INTENT_KEYWORDS regex 命中 → suggest_rule_actions 生成
    skill_check candidate → _execute_rules_action 跑 perform_skill_check →
    写 dice_log → _rule_results_prompt 给 GM 看 verdict。"""

    def _module_state(self):
        """开 Ash Mine 进 minecart_track,有 module_id 才会触发 module rules 路径。"""
        from rules_bridge import enter_room, start_module
        from state import GameState
        g = GameState.new()
        start_module(g, "ash_mine")
        enter_room(g, "minecart_track")
        return g

    def test_surrender_matches_persuasion_skill_check(self):
        from rules_bridge import suggest_rule_actions
        g = self._module_state()
        actions = suggest_rule_actions("我跪下投降", g)
        persuasion = [a for a in actions
                      if a.get("kind") == "skill_check" and a.get("skill") == "persuasion"]
        self.assertTrue(persuasion,
            f"'投降' 必须经 INTENT_KEYWORDS 命中 persuasion,实际 actions: {actions}")

    def test_surrender_uses_dc_hint_when_room_has_no_check(self):
        """当前房间 minecart_track 没有自定义 persuasion check → 应 fallback 到 dc_hint=14。"""
        from rules_bridge import suggest_rule_actions
        g = self._module_state()
        actions = suggest_rule_actions("我放下武器请降", g)
        persuasion = next(
            (a for a in actions if a.get("skill") == "persuasion"), None,
        )
        self.assertIsNotNone(persuasion)
        self.assertEqual(persuasion.get("dc"), 14,
            f"persuasion 应 fallback dc_hint=14,实际 {persuasion}")

    def test_struggle_matches_athletics_skill_check(self):
        """挣脱 / 摆脱抓握 → Athletics DC 13 (5E escape grapple)。"""
        from rules_bridge import suggest_rule_actions
        g = self._module_state()
        actions = suggest_rule_actions("我用力挣脱束缚", g)
        athletics = [a for a in actions
                     if a.get("kind") == "skill_check" and a.get("skill") == "athletics"]
        self.assertTrue(athletics, f"'挣脱' 必须命中 athletics,实际 actions: {actions}")
        self.assertEqual(athletics[0].get("dc"), 13)

    def test_persuasion_includes_negotiation_keywords(self):
        """多条 persuasion 触发词:说服 / 谈判 / 交涉 / 求和 都该走同一路径。"""
        from rules_bridge import suggest_rule_actions
        g = self._module_state()
        for text in ("我尝试说服他放下武器", "我和他谈判", "我求和", "我举起双手"):
            actions = suggest_rule_actions(text, g)
            self.assertTrue(
                any(a.get("skill") == "persuasion" for a in actions),
                f"{text!r} 应触发 persuasion,实际: {actions}",
            )

    def test_deception_keyword_routes_to_deception_check(self):
        """欺骗 / 撒谎 → Deception DC 13 (5E Charisma)。"""
        from rules_bridge import suggest_rule_actions
        g = self._module_state()
        actions = suggest_rule_actions("我撒谎说自己是矿工", g)
        deception = [a for a in actions if a.get("skill") == "deception"]
        self.assertTrue(deception, "撒谎应触发 deception 检定")

    def test_skill_check_actually_writes_dice_log(self):
        """完整路径:suggest_rule_actions → _execute_rules_action → dice_log 落地。"""
        from app import _execute_rules_action
        from rules_bridge import suggest_rule_actions
        g = self._module_state()
        # 清掉 start_module 可能写入的 dice_log,这样下面 assert 干净
        g.data["dice_log"] = []
        actions = suggest_rule_actions("我投降", g)
        persuasion = next((a for a in actions if a.get("skill") == "persuasion"), None)
        self.assertIsNotNone(persuasion)
        # seed=1 确保 deterministic
        persuasion["seed"] = 1
        out = _execute_rules_action(g, persuasion)
        self.assertTrue(out.get("ok"), f"persuasion 检定应成功执行,实际: {out}")
        # dice_log 必须有一条 skill_check / persuasion
        log_kinds = [(d.get("kind"), d.get("skill")) for d in g.data["dice_log"]]
        self.assertIn(("skill_check", "persuasion"), log_kinds,
            f"dice_log 必须含 persuasion 检定,实际 log: {log_kinds}")

    def test_rule_result_prompt_surfaces_verdict_to_gm(self):
        """_rule_results_prompt 把 verdict (成功/失败 + 骰点 + DC) 喂给 GM 当事实,
        不靠 prompt 教 GM "投降是否被接受"。"""
        from app import _execute_rules_action, _rule_results_prompt
        from rules_bridge import suggest_rule_actions
        g = self._module_state()
        g.data["dice_log"] = []
        actions = suggest_rule_actions("我投降", g)
        persuasion = next((a for a in actions if a.get("skill") == "persuasion"), None)
        persuasion["seed"] = 1
        out = _execute_rules_action(g, persuasion)
        text = _rule_results_prompt([{"action": persuasion, "out": out}], g)
        self.assertIn("persuasion", text.lower())
        self.assertIn("DC", text)
        # 应该明确给出 verdict
        self.assertTrue(
            ("成功" in text) or ("失败" in text),
            f"_rule_results_prompt 应明示 verdict,实际: {text}",
        )

    def test_non_module_state_does_not_run_module_rules(self):
        """没 module_id 时,_apply_chat_rule_candidates 直接跳过 — 小说模式不强制 5E 规则。
        这保证 deterministic rules 不污染小说叙事流程。"""
        from app import _apply_chat_rule_candidates, _chat_rule_candidates
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["scene"] = {"module_id": "", "location_id": "",
                           "current_room": {"id": "", "enemies": []}}
        candidates = _chat_rule_candidates(g, "我投降", [])
        # _chat_rule_candidates 无 module_id 返回 curator 给的 (这里空)
        # _apply_chat_rule_candidates 同样会在无 module_id 时返回 []
        results = _apply_chat_rule_candidates(g, candidates)
        self.assertEqual(results, [],
            "无 module_id 时不该跑 deterministic rules action")


# ────────────────────────────────────────────────────────────────────
# Layer C: chat handler 端到端接通点 (源码静态扫描)
# ────────────────────────────────────────────────────────────────────


class ChatHandlerWiresDeterministicRouting(unittest.TestCase):
    """端到端入口必须把 INTENT_KEYWORDS 路径接通到 GM bundle 上。
    这是协调层 — 不接通 GM 看不到 dice_log verdict,就会自己幻觉。"""

    @classmethod
    def setUpClass(cls):
        cls.app_text = (PROJECT_ROOT / "rpg" / "app.py").read_text(encoding="utf-8")

    def test_chat_calls_apply_chat_rule_candidates(self):
        self.assertIn("_apply_chat_rule_candidates", self.app_text)

    def test_chat_appends_rule_prompt_to_gm_bundle(self):
        """rule_results 之后必须把 _rule_results_prompt 接到 bundle["prompt"]。"""
        self.assertIn("_rule_results_prompt(rule_results", self.app_text)
        # rule_prompt 拼到 bundle prompt 上
        self.assertIn('bundle["prompt"]', self.app_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
