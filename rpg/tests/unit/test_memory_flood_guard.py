"""test_memory_flood_guard.py — 记忆桶洪泛闸(07-30 群反馈)。

反馈原文:「修功法要点亮体内星辰,写了几段话点亮了 36 个,应该是每点一个就给我写一个
能力,直接污染了大概二十多条」。GM 每点亮一处星窍就 add_memory 一条能力,玩家面板被
同一门功法刷满,只能一条条点 × 删。

本测试锁四件事:

1. **每回合每桶预算** —— 单回合追加超过 N 条即拒。
2. **同族上限** —— abilities/resources 里同前缀超过 M 条即拒;facts 不设族闸
   (同一个 NPC 名下攒七八条事实是正常的,给 facts 上族闸就是误伤)。
3. **拒绝不是丢弃** —— 拒绝要有理由、要进 audit_log、要能被 write_results 讲给 GM 听。
   没有最后这一步,闸就只是「悄悄拒绝」,GM 下轮原样再写一遍。
4. **方向:fail-open** —— 只拦认得出的 GM 来源;玩家来源和认不出的来源一律放行。
   前科是「玩家笔记/固定记忆被自动归档悄悄丢」,玩家可见资产绝不能被系统吃掉。
"""
from __future__ import annotations

import copy
import os
import pathlib
import sys
import unittest

_RPG = pathlib.Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from core.config import memory_append_per_turn_max, memory_family_max  # noqa: E402
from state import DEFAULT_STATE, GameState  # noqa: E402
from state.memory_budget import family_head, is_gated_origin  # noqa: E402

GM = "llm_chat"          # GM 回合工具调用
GM_JSON = "llm_chat_json_op"  # 后处理 JSON op


def _state(turn: int = 1) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


class TestFamilyHead(unittest.TestCase):
    def test_splits_on_structural_separator(self):
        self.assertEqual(family_head("周天命星炼窍法·神庭·星宿(神光凝聚)"), "周天命星炼窍法")
        self.assertEqual(family_head("周天命星炼窍法·六合星官(第三重)"), "周天命星炼窍法")
        self.assertEqual(family_head("玩家强制设定：不要写我的名字"), "玩家强制设定")

    def test_no_separator_means_no_family(self):
        """整串相同的条目已被精确去重挡掉,不需要族闸;别把无分隔符的整句当族名。"""
        self.assertEqual(family_head("指尖点火"), "")
        self.assertEqual(family_head("一段没有任何结构分隔符的长句子描述"), "")

    def test_short_head_is_not_a_family(self):
        """前缀太短(<3 字)判不出族,宁漏勿误。"""
        self.assertEqual(family_head("A·B"), "")
        self.assertEqual(family_head("剑·残"), "")


class TestOriginDirection(unittest.TestCase):
    def test_gm_origins_are_gated(self):
        self.assertTrue(is_gated_origin(GM))
        self.assertTrue(is_gated_origin(GM_JSON))
        self.assertTrue(is_gated_origin("gm"))
        self.assertTrue(is_gated_origin("gm:json"))

    def test_player_and_unknown_origins_are_never_gated(self):
        """fail-open:漏拦一次洪泛只是面板多几条,误拦一次玩家写入就是资产被吃掉。"""
        for o in ("ui_button", "llm_set", "api_direct", "user:/set", "player", "", None, "什么鬼"):
            self.assertFalse(is_gated_origin(o), o)


class TestPerTurnBudget(unittest.TestCase):
    def test_gm_cannot_flood_one_bucket_in_one_turn(self):
        s = _state()
        cap = memory_append_per_turn_max()
        for i in range(cap):
            ok, why = s.add_memory_ex("abilities", f"能力{i}", origin=GM)
            self.assertTrue(ok, why)
        ok, why = s.add_memory_ex("abilities", "第 N+1 条", origin=GM)
        self.assertFalse(ok)
        self.assertIn("上限", why)
        self.assertEqual(len(s.data["memory"]["abilities"]), cap)

    def test_budget_resets_next_turn(self):
        """预算是每回合的,不是永久上限 —— 长局照样能慢慢积累。"""
        s = _state()
        cap = memory_append_per_turn_max()
        for i in range(cap):
            s.add_memory_ex("facts", f"事实{i}", origin=GM)
        self.assertFalse(s.add_memory_ex("facts", "溢出", origin=GM)[0])
        s.data["turn"] = 2
        self.assertTrue(s.add_memory_ex("facts", "下一回合的事实", origin=GM)[0])

    def test_budget_is_per_bucket_not_global(self):
        s = _state()
        cap = memory_append_per_turn_max()
        for i in range(cap):
            s.add_memory_ex("facts", f"事实{i}", origin=GM)
        self.assertFalse(s.add_memory_ex("facts", "溢出", origin=GM)[0])
        self.assertTrue(s.add_memory_ex("resources", "一把钥匙", origin=GM)[0])

    def test_player_writes_are_never_blocked_by_budget(self):
        s = _state()
        for i in range(memory_append_per_turn_max() + 5):
            ok, why = s.add_memory_ex("notes", f"玩家笔记{i}", origin="ui_button")
            self.assertTrue(ok, why)


class TestFamilyCeiling(unittest.TestCase):
    def _fill_family(self, s: GameState, bucket: str, n: int, *, turn_step: bool = True):
        """跨回合灌同族条目(每条一个回合,避开每回合预算,单独验族闸)。"""
        for i in range(n):
            if turn_step:
                s.data["turn"] = i + 1
            s.add_memory_ex(bucket, f"周天命星炼窍法·第{i}窍(细节)", origin=GM)

    def test_same_family_capped_across_turns(self):
        """这正是反馈现场:同一门功法逐窍拆写,跨回合累计十几条。"""
        s = _state()
        cap = memory_family_max()
        self._fill_family(s, "abilities", cap)
        self.assertEqual(len(s.data["memory"]["abilities"]), cap)
        s.data["turn"] += 1
        ok, why = s.add_memory_ex("abilities", "周天命星炼窍法·又一窍(细节)", origin=GM)
        self.assertFalse(ok)
        self.assertIn("周天命星炼窍法", why)
        self.assertEqual(len(s.data["memory"]["abilities"]), cap)

    def test_other_families_unaffected(self):
        s = _state()
        self._fill_family(s, "abilities", memory_family_max())
        s.data["turn"] += 1
        self.assertTrue(s.add_memory_ex("abilities", "指尖点火·初阶(引火)", origin=GM)[0])

    def test_facts_has_no_family_ceiling(self):
        """facts 天然按人名聚族(同一个 NPC 名下攒七八条事实正常),上族闸=误伤。"""
        s = _state()
        for i in range(memory_family_max() + 4):
            s.data["turn"] = i + 1
            ok, why = s.add_memory_ex("facts", f"路西恩·线索{i}", origin=GM)
            self.assertTrue(ok, why)

    def test_player_writes_ignore_family_ceiling(self):
        s = _state()
        self._fill_family(s, "resources", memory_family_max())
        s.data["turn"] += 1
        self.assertTrue(s.add_memory_ex("resources", "周天命星炼窍法·玩家自己记的", origin="ui_button")[0])


class TestRejectionIsVisible(unittest.TestCase):
    def test_block_is_audited(self):
        s = _state()
        for i in range(memory_append_per_turn_max() + 1):
            s.add_memory_ex("abilities", f"能力{i}", origin=GM)
        kinds = [a.get("kind") for a in s.data["permissions"]["audit_log"]]
        self.assertIn("memory_flood_blocked", kinds)

    def test_write_results_tells_the_gm(self):
        """没有这一段,闸只是「悄悄拒绝」,GM 下轮原样再写一遍 —— UI 存在≠生效同理。"""
        from context_engine.layers import _write_results_layer  # noqa: PLC0415
        s = _state()
        for i in range(memory_append_per_turn_max() + 1):
            s.add_memory_ex("abilities", f"能力{i}", origin=GM)
        text = _write_results_layer(s)
        self.assertIn("洪泛闸", text)
        self.assertIn("memory.abilities", text)
        self.assertIn("合并", text)

    def test_tool_returns_a_failure_string(self):
        """dispatcher 惯例:失败结果串以「失败:」开头,GM 才认得出这不是成功。"""
        from tools_dsl.command_tools import execute_tool  # noqa: PLC0415
        s = _state()
        for i in range(memory_append_per_turn_max()):
            execute_tool(s, "add_memory_ability", {"text": f"能力{i}", "_origin": GM})
        out = execute_tool(s, "add_memory_ability", {"text": "溢出的一条", "_origin": GM})
        self.assertTrue(out.startswith("失败:"), out)

    def test_duplicate_is_reported_as_duplicate_not_as_flood(self):
        """精确去重要排在闸前面:重写一条已有的什么都没改,回「超额」会误导 GM。"""
        from tools_dsl.command_tools import execute_tool  # noqa: PLC0415
        s = _state()
        for i in range(memory_append_per_turn_max()):
            execute_tool(s, "add_memory_ability", {"text": f"能力{i}", "_origin": GM})
        out_dup = execute_tool(s, "add_memory_ability", {"text": "能力0", "_origin": GM})
        self.assertIn("去重", out_dup)

    def test_tool_path_respects_player_origin(self):
        from tools_dsl.command_tools import execute_tool  # noqa: PLC0415
        s = _state()
        for i in range(memory_append_per_turn_max() + 3):
            out = execute_tool(s, "add_memory_ability", {"text": f"能力{i}", "_origin": "ui_button"})
            self.assertFalse(out.startswith("失败:"), out)


class TestParallelPathClosed(unittest.TestCase):
    """老路径(dispatcher 路由失败 fall-through)曾手写 append,绕过去重 + dual-write + 闸。"""

    def test_old_path_routes_memory_buckets_through_add_memory(self):
        s = _state()
        for i in range(memory_append_per_turn_max()):
            s.apply_state_write_typed("memory.abilities", f"能力{i}", source="gm", append=True)
        out = s.apply_state_write_typed("memory.abilities", "溢出的一条", source="gm", append=True)
        self.assertIn("拒绝", out)
        self.assertIn("abilities", out)
        self.assertEqual(len(s.data["memory"]["abilities"]), memory_append_per_turn_max())

    def test_old_path_also_dual_writes_memory_items(self):
        """收口的附带修复:老路径此前不写 memory.items,结构化记忆会漏条。"""
        s = _state()
        s.apply_state_write_typed("memory.resources", "一把铜钥匙", source="gm", append=True)
        items = [it for it in s.data["memory"]["items"] if it.get("legacy_bucket") == "resources"]
        self.assertEqual(len(items), 1)

    def test_old_path_blocking_one_item_does_not_drop_the_others(self):
        """同批次里一条被族闸拦下,不该连累另一条合法条目。"""
        s = _state()
        for i in range(memory_family_max()):
            s.data["turn"] = i + 1
            s.apply_state_write_typed("memory.resources", f"星辰石·第{i}块(碎)", source="gm", append=True)
        s.data["turn"] = 99
        s.apply_state_write_typed("memory.resources", ["星辰石·再一块(碎)", "一把铜钥匙"],
                                  source="gm", append=True)
        self.assertIn("一把铜钥匙", s.data["memory"]["resources"])
        self.assertEqual(
            sum(1 for t in s.data["memory"]["resources"] if t.startswith("星辰石")),
            memory_family_max())

    def test_old_path_leaves_non_memory_lists_alone(self):
        s = _state()
        s.apply_state_write_typed("world.known_events", "城门失火", source="gm", append=True)
        self.assertIn("城门失火", s.data["world"]["known_events"])


class TestConfigKnobs(unittest.TestCase):
    def test_zero_disables_each_gate(self):
        from state.memory_budget import check_append  # noqa: PLC0415
        s = _state()
        s.data["memory"]["abilities"] = [f"周天命星炼窍法·第{i}窍(x)" for i in range(50)]
        os.environ["RPG_MEMORY_FAMILY_MAX"] = "0"
        os.environ["RPG_MEMORY_APPEND_PER_TURN"] = "0"
        try:
            self.assertEqual(check_append(s.data, "abilities", "周天命星炼窍法·再一窍(x)", GM), "")
        finally:
            del os.environ["RPG_MEMORY_FAMILY_MAX"]
            del os.environ["RPG_MEMORY_APPEND_PER_TURN"]


if __name__ == "__main__":
    unittest.main()
