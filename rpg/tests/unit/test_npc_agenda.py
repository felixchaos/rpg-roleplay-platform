"""
test_npc_agenda.py — NPC 议程 v0 确定性脚手架测试。

设计文档: docs/design/npc_agenda_v0.md §6

覆盖范围:
  · 纯函数(state.npc_agenda):
      名字不在册拒绝 / 截断 / 部分更新合并 / 上限剪最旧 / injection 排序与上限
  · apply op 分支(apply_structured_updates 的 "agenda" JSON op):
      合法登记 / 缺 name 拒绝 / goal+stance 至少一个
  · context provider(npc_agenda):
      gate 关 = skip、无条目 = skip、渲染含「当下活状态」

recorder parity 守卫（prompt/tool-schema 同源门控）不在本文件范围内，属 GM 敏感
改动，留给设计文档 §2 指定的负责人补齐。

所有测试不依赖 DB。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import DEFAULT_STATE, GameState  # noqa: E402
from state.npc_agenda import (  # noqa: E402
    MAX_AGENDAS,
    MAX_FIELD_LEN,
    agendas_for_injection,
    upsert_agenda,
)


def _new_state(turn: int = 0) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


def _with_known_npc(data: dict, *names: str) -> dict:
    """把 names 塞进 relationships,使其成为「已知 NPC」(名字白名单口径)。"""
    rel = data.setdefault("relationships", {})
    for n in names:
        rel.setdefault(n, "未知")
    return data


# ────────────────────────────────────────────────────────────
# upsert_agenda
# ────────────────────────────────────────────────────────────

class UpsertAgendaTests(unittest.TestCase):
    def test_register_success_via_relationships(self):
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 15
        _with_known_npc(data, "雷纳德")
        ok, msg = upsert_agenda(
            data, name="雷纳德",
            goal="查清东林兽伤的真相，保住村子的猎场",
            stance="对玩家信任但保留观察",
        )
        self.assertTrue(ok, msg)
        entry = data["npc_agendas"]["雷纳德"]
        self.assertEqual(entry["goal"], "查清东林兽伤的真相，保住村子的猎场")
        self.assertEqual(entry["stance"], "对玩家信任但保留观察")
        self.assertEqual(entry["updated_turn"], 15)

    def test_register_success_via_active_entities(self):
        """名字白名单口径二:active_entities 里的 name 字段也算已知。"""
        data = copy.deepcopy(DEFAULT_STATE)
        data["active_entities"] = [{"name": "艾琳", "kind": "npc"}]
        ok, msg = upsert_agenda(data, name="艾琳", goal="想去石桥渡")
        self.assertTrue(ok, msg)
        self.assertIn("艾琳", data["npc_agendas"])

    def test_name_not_in_roster_rejected(self):
        """名字不在 relationships ∪ active_entities → 拒绝(防 LLM 发明路人)。"""
        data = copy.deepcopy(DEFAULT_STATE)
        ok, msg = upsert_agenda(data, name="路人甲", goal="随便逛逛")
        self.assertFalse(ok)
        self.assertNotIn("npc_agendas", data)

    def test_missing_name_rejected(self):
        data = copy.deepcopy(DEFAULT_STATE)
        ok, msg = upsert_agenda(data, name="", goal="X")
        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_missing_both_goal_and_stance_rejected(self):
        """goal / stance 都没给 → 拒绝(至少给一个)。"""
        data = copy.deepcopy(DEFAULT_STATE)
        _with_known_npc(data, "雷纳德")
        ok, msg = upsert_agenda(data, name="雷纳德")
        self.assertFalse(ok)
        self.assertNotIn("npc_agendas", data)

    def test_goal_only_accepted(self):
        data = copy.deepcopy(DEFAULT_STATE)
        _with_known_npc(data, "雷纳德")
        ok, msg = upsert_agenda(data, name="雷纳德", goal="只给目标")
        self.assertTrue(ok, msg)
        entry = data["npc_agendas"]["雷纳德"]
        self.assertEqual(entry["goal"], "只给目标")
        self.assertEqual(entry["stance"], "")

    def test_stance_only_accepted(self):
        data = copy.deepcopy(DEFAULT_STATE)
        _with_known_npc(data, "雷纳德")
        ok, msg = upsert_agenda(data, name="雷纳德", stance="只给态度")
        self.assertTrue(ok, msg)
        entry = data["npc_agendas"]["雷纳德"]
        self.assertEqual(entry["goal"], "")
        self.assertEqual(entry["stance"], "只给态度")

    def test_field_truncation(self):
        """单条 goal/stance 各 <=60 字截断(验收截断)。"""
        data = copy.deepcopy(DEFAULT_STATE)
        _with_known_npc(data, "雷纳德")
        long_goal = "查" * 100
        long_stance = "怒" * 100
        ok, msg = upsert_agenda(data, name="雷纳德", goal=long_goal, stance=long_stance)
        self.assertTrue(ok, msg)
        entry = data["npc_agendas"]["雷纳德"]
        self.assertLessEqual(len(entry["goal"]), MAX_FIELD_LEN)
        self.assertLessEqual(len(entry["stance"]), MAX_FIELD_LEN)

    def test_partial_update_merges_not_overwrites(self):
        """部分更新合并:只更新 goal 不应清空已有 stance,反之亦然。"""
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 5
        _with_known_npc(data, "雷纳德")
        upsert_agenda(data, name="雷纳德", goal="初始目标", stance="初始态度", turn=5)
        data["turn"] = 8
        ok, msg = upsert_agenda(data, name="雷纳德", goal="新目标", turn=8)
        self.assertTrue(ok, msg)
        entry = data["npc_agendas"]["雷纳德"]
        self.assertEqual(entry["goal"], "新目标")
        self.assertEqual(entry["stance"], "初始态度")  # 未被清空
        self.assertEqual(entry["updated_turn"], 8)

        # 反向:只更新 stance,goal 保留
        ok2, _ = upsert_agenda(data, name="雷纳德", stance="新态度", turn=9)
        self.assertTrue(ok2)
        entry2 = data["npc_agendas"]["雷纳德"]
        self.assertEqual(entry2["goal"], "新目标")
        self.assertEqual(entry2["stance"], "新态度")

    def test_upsert_uses_state_turn_when_turn_not_given(self):
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 22
        _with_known_npc(data, "雷纳德")
        ok, _ = upsert_agenda(data, name="雷纳德", goal="X")
        self.assertTrue(ok)
        self.assertEqual(data["npc_agendas"]["雷纳德"]["updated_turn"], 22)

    def test_max_agendas_prunes_oldest(self):
        """超出 12 个 NPC 上限 → 剪掉 updated_turn 最旧的(活跃度自然淘汰)。"""
        data = copy.deepcopy(DEFAULT_STATE)
        names = [f"NPC{i}" for i in range(MAX_AGENDAS)]
        _with_known_npc(data, *names)
        for i, n in enumerate(names):
            ok, msg = upsert_agenda(data, name=n, goal=f"目标{i}", turn=i)
            self.assertTrue(ok, msg)
        self.assertEqual(len(data["npc_agendas"]), MAX_AGENDAS)

        # 再加一个新 NPC,应挤掉 updated_turn 最小的 NPC0(turn=0)
        _with_known_npc(data, "NPC_NEW")
        ok, msg = upsert_agenda(data, name="NPC_NEW", goal="新目标", turn=100)
        self.assertTrue(ok, msg)
        self.assertEqual(len(data["npc_agendas"]), MAX_AGENDAS)
        self.assertNotIn("NPC0", data["npc_agendas"])
        self.assertIn("NPC_NEW", data["npc_agendas"])

    def test_updating_existing_entry_does_not_trigger_prune(self):
        """已在册 NPC 的更新（非新增）不应导致自己被误剪。"""
        data = copy.deepcopy(DEFAULT_STATE)
        names = [f"NPC{i}" for i in range(MAX_AGENDAS)]
        _with_known_npc(data, *names)
        for i, n in enumerate(names):
            upsert_agenda(data, name=n, goal=f"目标{i}", turn=i)
        # 更新最旧的 NPC0 本身(不新增 key)
        ok, msg = upsert_agenda(data, name="NPC0", goal="更新后的目标", turn=999)
        self.assertTrue(ok, msg)
        self.assertEqual(len(data["npc_agendas"]), MAX_AGENDAS)
        self.assertIn("NPC0", data["npc_agendas"])
        self.assertEqual(data["npc_agendas"]["NPC0"]["goal"], "更新后的目标")


# ────────────────────────────────────────────────────────────
# agendas_for_injection
# ────────────────────────────────────────────────────────────

class AgendasForInjectionTests(unittest.TestCase):
    def test_empty_when_no_agendas(self):
        data = copy.deepcopy(DEFAULT_STATE)
        self.assertEqual(agendas_for_injection(data), [])

    def test_no_crash_on_empty_state(self):
        data = copy.deepcopy(DEFAULT_STATE)
        self.assertEqual(agendas_for_injection(data, relationships_keys=[]), [])

    def test_in_scene_entries_prioritized_over_offscreen(self):
        """键在 relationships 里的（当前在场相关）优先于不在里面的。"""
        data = copy.deepcopy(DEFAULT_STATE)
        _with_known_npc(data, "在场甲", "离场乙")
        upsert_agenda(data, name="离场乙", goal="离场的议程", turn=50)  # 更新更晚但不在场
        upsert_agenda(data, name="在场甲", goal="在场的议程", turn=1)   # 更新更早但在场
        entries = agendas_for_injection(data, relationships_keys=["在场甲"])
        names = [e["name"] for e in entries]
        self.assertEqual(names[0], "在场甲")

    def test_sorted_by_updated_turn_desc_within_group(self):
        data = copy.deepcopy(DEFAULT_STATE)
        names = ["A", "B", "C"]
        _with_known_npc(data, *names)
        upsert_agenda(data, name="A", goal="a", turn=1)
        upsert_agenda(data, name="B", goal="b", turn=10)
        upsert_agenda(data, name="C", goal="c", turn=5)
        entries = agendas_for_injection(data, relationships_keys=names)
        self.assertEqual([e["name"] for e in entries], ["B", "C", "A"])

    def test_limit_respected(self):
        data = copy.deepcopy(DEFAULT_STATE)
        names = [f"N{i}" for i in range(8)]
        _with_known_npc(data, *names)
        for i, n in enumerate(names):
            upsert_agenda(data, name=n, goal=f"g{i}", turn=i)
        entries = agendas_for_injection(data, relationships_keys=names, limit=6)
        self.assertEqual(len(entries), 6)
        # 最近更新的 6 个应在结果里(降序取前 limit)
        self.assertEqual([e["name"] for e in entries], ["N7", "N6", "N5", "N4", "N3", "N2"])

    def test_default_relationships_keys_used_when_not_given(self):
        """relationships_keys 缺省时用 state_data["relationships"] 的键。"""
        data = copy.deepcopy(DEFAULT_STATE)
        _with_known_npc(data, "雷纳德", "艾琳")
        upsert_agenda(data, name="雷纳德", goal="X", turn=1)
        upsert_agenda(data, name="艾琳", goal="Y", turn=2)
        entries = agendas_for_injection(data)  # 不传 relationships_keys
        self.assertEqual(len(entries), 2)


# ────────────────────────────────────────────────────────────
# apply_structured_updates 的 "agenda" JSON op 分支
# ────────────────────────────────────────────────────────────

class ApplyOpBranchTests(unittest.TestCase):
    def test_json_op_registers_agenda(self):
        s = _new_state(turn=15)
        _with_known_npc(s.data, "雷纳德")
        gm_response = (
            "正文正文。\n"
            "```json\n"
            '{"op": "agenda", "name": "雷纳德", "goal": "查清东林兽伤的真相", "stance": "信任但保留观察"}\n'
            "```\n"
        )
        updates = s.apply_structured_updates(gm_response)
        self.assertIn("雷纳德", s.data["npc_agendas"])
        entry = s.data["npc_agendas"]["雷纳德"]
        self.assertEqual(entry["goal"], "查清东林兽伤的真相")
        self.assertEqual(entry["stance"], "信任但保留观察")
        self.assertTrue(any("状态写入" in u for u in updates), updates)

    def test_json_op_goal_only_variant(self):
        s = _new_state(turn=1)
        _with_known_npc(s.data, "艾琳")
        gm_response = (
            "```json\n"
            '{"op": "agenda", "name": "艾琳", "goal": "想去石桥渡"}\n'
            "```\n"
        )
        s.apply_structured_updates(gm_response)
        self.assertEqual(s.data["npc_agendas"]["艾琳"]["goal"], "想去石桥渡")
        self.assertEqual(s.data["npc_agendas"]["艾琳"]["stance"], "")

    def test_json_op_missing_name_rejected_no_crash(self):
        s = _new_state(turn=1)
        gm_response = (
            "```json\n"
            '{"op": "agenda", "goal": "缺名字"}\n'
            "```\n"
        )
        updates = s.apply_structured_updates(gm_response)
        self.assertEqual(s.data.get("npc_agendas", {}), {})
        self.assertTrue(any("缺 name" in u or "忽略" in u for u in updates), updates)

    def test_json_op_missing_goal_and_stance_rejected_no_crash(self):
        s = _new_state(turn=1)
        _with_known_npc(s.data, "雷纳德")
        gm_response = (
            "```json\n"
            '{"op": "agenda", "name": "雷纳德"}\n'
            "```\n"
        )
        updates = s.apply_structured_updates(gm_response)
        self.assertEqual(s.data.get("npc_agendas", {}), {})
        self.assertTrue(updates)  # 有说明,不静默

    def test_json_op_unknown_name_rejected_no_crash(self):
        """名字不在场上已知名单 → 纯函数层拒绝,apply 分支不崩、有说明。"""
        s = _new_state(turn=1)
        gm_response = (
            "```json\n"
            '{"op": "agenda", "name": "路人甲", "goal": "随便逛逛"}\n'
            "```\n"
        )
        updates = s.apply_structured_updates(gm_response)
        self.assertEqual(s.data.get("npc_agendas", {}), {})
        self.assertTrue(updates)

    def test_question_op_still_works_alongside(self):
        """回归:新分支不破坏既有 question op 行为。"""
        s = _new_state(turn=1)
        gm_response = (
            "```json\n"
            '{"op": "question", "question": "去哪?", "options": ["东", "西"]}\n'
            "```\n"
        )
        updates = s.apply_structured_updates(gm_response)
        self.assertTrue(any("等待玩家回答" in u for u in updates), updates)

    def test_consequence_op_still_works_alongside(self):
        """回归:新分支不破坏既有 consequence op 行为(同批柱子2/3 op 共存)。"""
        s = _new_state(turn=10)
        gm_response = (
            "```json\n"
            '{"op": "consequence", "text": "答应帮铁匠找矿石", "due_turns": 4}\n'
            "```\n"
        )
        updates = s.apply_structured_updates(gm_response)
        self.assertEqual(len(s.data["consequence_ledger"]), 1)
        self.assertTrue(any("状态写入" in u for u in updates), updates)


# ────────────────────────────────────────────────────────────
# context provider: npc_agenda
# ────────────────────────────────────────────────────────────

class NpcAgendaProviderTests(unittest.TestCase):
    def setUp(self):
        from context_providers import Demand, ProviderServices
        from context_providers.registry import get_provider
        self.provider = get_provider("npc_agenda")
        self.assertIsNotNone(self.provider, "npc_agenda provider 未注册")
        self.demand = Demand.empty()
        self.manifest = {"context_providers": ["npc_agenda"]}
        self.services_factory = ProviderServices

    def test_gate_off_skips(self):
        os.environ.pop("RPG_NPC_AGENDA", None)  # 确保走默认(关)
        s = _new_state(turn=10)
        _with_known_npc(s.data, "雷纳德")
        upsert_agenda(s.data, name="雷纳德", goal="X", turn=10)
        contrib = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        self.assertFalse(contrib.applied)

    def test_gate_on_no_agendas_returns_empty(self):
        os.environ["RPG_NPC_AGENDA"] = "1"
        try:
            s = _new_state(turn=1)
            contrib = self.provider.collect(
                s, self.manifest, self.demand, self.services_factory(user_id=None),
            )
            self.assertFalse(contrib.applied)
        finally:
            os.environ.pop("RPG_NPC_AGENDA", None)

    def test_gate_on_with_agenda_returns_active_state_text(self):
        os.environ["RPG_NPC_AGENDA"] = "1"
        try:
            s = _new_state(turn=15)
            _with_known_npc(s.data, "雷纳德")
            upsert_agenda(
                s.data, name="雷纳德",
                goal="查清东林兽伤真相", stance="信任但保留观察", turn=15,
            )
            contrib = self.provider.collect(
                s, self.manifest, self.demand, self.services_factory(user_id=None),
            )
            self.assertTrue(contrib.applied)
            self.assertEqual(len(contrib.layers), 1)
            content = contrib.layers[0]["content"]
            self.assertIn("当下活状态", content)
            self.assertIn("雷纳德", content)
            self.assertIn("查清东林兽伤真相", content)
        finally:
            os.environ.pop("RPG_NPC_AGENDA", None)

    def test_state_data_missing_skips_without_crash(self):
        os.environ["RPG_NPC_AGENDA"] = "1"
        try:
            class _Bare:
                pass
            contrib = self.provider.collect(
                _Bare(), self.manifest, self.demand, self.services_factory(user_id=None),
            )
            self.assertFalse(contrib.applied)
        finally:
            os.environ.pop("RPG_NPC_AGENDA", None)


if __name__ == "__main__":
    unittest.main()
