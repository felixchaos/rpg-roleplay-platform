"""
test_combat_no_gm_hallucination.py
==================================
5E-compatible 模组里,GM 不得自行裁定战斗/移动结果。

现场 bug:
玩家在 Ash Mine `minecart_track` (room.enemies=[]、encounter.active=False)
输入 "借着矿车阻挡,向后拉开距离继续放箭",GM 直接叙事:矿车变阻挡 /
玩家被卡住 / 两名敌人贴身 / 短弓施展不了 / 陷入近战威胁 —— 全是 GM 口胡,
违反 "RulesEngine 是规则唯一真相源" 的设计要求。

修法 (deterministic, 不依赖模型训练或微调):
1. rules_bridge.classify_combat_intent — 纯函数,在 GM 被调用前拦截:
     * 想战斗但无敌人 + 无 encounter → no_target_combat 阻挡块
     * encounter 中 move_away + ranged → combat_pending_question 阻挡块
     * encounter 中含糊离场 → combat_pending_question 阻挡块
2. app.py chat SSE handler 收到阻挡块就写 pending_question + emit token + done,
   **不调主 GM**,所以 GM 无机会写坏正文。
3. context_providers/rules.py 加 "硬约束 — GM 不得自行裁定" 块作为 belt-and-suspenders。

本测试三层全覆盖:
  Layer A: classifier 单元 (rules_bridge.classify_combat_intent)
  Layer B: 集成 (Ash Mine 实际场景,/api/chat 端到端 — stub LLM)
  Layer C: RulesProvider prompt 包含硬约束块
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import make_client, register_user


# ────────────────────────────────────────────────────────────────────
# Layer A: classify_combat_intent 单元
# ────────────────────────────────────────────────────────────────────


class ClassifyCombatIntentUnit(unittest.TestCase):
    """rules_bridge.classify_combat_intent 是纯函数 — 不调 LLM,不调网络。"""

    def _make_state(self, *, module_id="ash_mine", room_enemies=None,
                    encounter_active=False, live_enemies=None):
        class _S:
            def __init__(self):
                self.data = {
                    "scene": {
                        "module_id": module_id,
                        "location_id": "minecart_track",
                        "current_room": {
                            "id": "minecart_track",
                            "enemies": list(room_enemies or []),
                            "exits": [], "checks": [], "hazards": [],
                            "visible_clues": [], "npcs": [], "loot": [],
                            "flags": {},
                        },
                    },
                    "encounter": {
                        "active": encounter_active,
                        "combatants": [
                            {"id": e.get("id"), "name": e.get("name"),
                             "side": "enemy", "defeated": False}
                            for e in (live_enemies or [])
                        ],
                    },
                }
        return _S()

    def test_no_module_id_returns_none(self):
        from rules_bridge import classify_combat_intent
        s = self._make_state(module_id="")
        self.assertIsNone(classify_combat_intent("我用短弓射击", s))

    def test_empty_text_returns_none(self):
        from rules_bridge import classify_combat_intent
        s = self._make_state()
        self.assertIsNone(classify_combat_intent("", s))
        self.assertIsNone(classify_combat_intent(None, s))

    def test_pure_narration_returns_none(self):
        from rules_bridge import classify_combat_intent
        s = self._make_state()
        # 纯观察 / 探索 — 没攻击/武器/离场信号
        self.assertIsNone(classify_combat_intent("我仔细观察矿车上的灰痕", s))
        self.assertIsNone(classify_combat_intent("调查地面脚印的方向", s))

    def test_attack_intent_without_enemies_returns_no_target_combat(self):
        """现场 bug 的核心:玩家想战斗,但当前没合法目标 → 必须强制问询。"""
        from rules_bridge import classify_combat_intent
        s = self._make_state(room_enemies=[], encounter_active=False)
        block = classify_combat_intent("我拔出短剑准备砍", s)
        self.assertIsNotNone(block, "想战斗但无敌人 — 应返回 no_target_combat 阻挡块")
        self.assertEqual(block["kind"], "no_target_combat")
        self.assertTrue(block["question"])
        self.assertGreaterEqual(len(block["options"]), 3)

    def test_move_away_and_ranged_in_active_encounter_returns_pending(self):
        """用户报告的复现:'向后拉开距离继续放箭' + encounter 中 + 敌人在场。"""
        from rules_bridge import classify_combat_intent
        s = self._make_state(
            encounter_active=True,
            live_enemies=[{"id": "ash_skulker_1", "name": "灰布教徒·甲"}],
        )
        block = classify_combat_intent("借着矿车阻挡,向后拉开距离继续放箭", s)
        self.assertIsNotNone(block)
        self.assertEqual(block["kind"], "combat_pending_question")
        # 4 个选项必须覆盖:Disengage / 直接后退 / 切近战 / 原地不利
        opts_text = " ".join(block["options"])
        self.assertIn("Disengage", opts_text)
        self.assertIn("借机", opts_text)
        self.assertIn("近战", opts_text)
        self.assertIn("不利", opts_text)

    def test_clear_single_attack_in_encounter_not_gated(self):
        """单一明确的攻击不应被拦截,让 suggest_rule_actions 走 attack。"""
        from rules_bridge import classify_combat_intent
        s = self._make_state(
            encounter_active=True,
            live_enemies=[{"id": "x", "name": "X"}],
        )
        # 没 move_away → 不是含糊战斗
        self.assertIsNone(classify_combat_intent("我用短弓射击灰布教徒", s))
        self.assertIsNone(classify_combat_intent("我拔短剑刺向敌人", s))

    def test_move_away_only_in_encounter_returns_pending(self):
        """encounter 中含糊离场也要拦截 — 不让 GM 说 '你被卡住' 或 '你顺利后退'。"""
        from rules_bridge import classify_combat_intent
        s = self._make_state(
            encounter_active=True,
            live_enemies=[{"id": "x", "name": "X"}],
        )
        block = classify_combat_intent("我后退几步", s)
        self.assertIsNotNone(block)
        self.assertEqual(block["kind"], "combat_pending_question")
        opts_text = " ".join(block["options"])
        self.assertIn("Disengage", opts_text)

    def test_disengage_intent_not_gated(self):
        """玩家明确说 Disengage / 脱离 — 已经选定动作,不需要再问。"""
        from rules_bridge import classify_combat_intent
        s = self._make_state(
            encounter_active=True,
            live_enemies=[{"id": "x", "name": "X"}],
        )
        self.assertIsNone(classify_combat_intent("我用 Disengage 后撤", s))
        self.assertIsNone(classify_combat_intent("我脱离接触,退出战斗距离", s))

    def test_signals_present_in_block(self):
        """返回的 signals 字段必须能复现决策路径,便于调试。"""
        from rules_bridge import classify_combat_intent
        s = self._make_state(
            encounter_active=True,
            live_enemies=[{"id": "x", "name": "X"}],
        )
        block = classify_combat_intent("拉开距离用短弓继续射", s)
        self.assertIn("signals", block)
        sig = block["signals"]
        self.assertTrue(sig.get("has_move_away"))
        self.assertTrue(sig.get("has_ranged"))
        self.assertTrue(sig.get("encounter_active"))


# ────────────────────────────────────────────────────────────────────
# Layer B: 集成 — /api/chat 端到端 (经 _rb_classify_combat_intent gate)
# ────────────────────────────────────────────────────────────────────


def _launch_ash_mine(client, cookies) -> dict:
    """启动 Ash Mine 模组到独立 save,返回 launch payload。"""
    r = client.post(
        "/api/rules/module/launch",
        json={"module_id": "ash_mine"},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text[:200]
    return r.json()


def _move_to(client, cookies, room_id: str):
    r = client.post(
        "/api/rules/move",
        json={"to": room_id},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text[:200]


def _get_state(client, cookies) -> dict:
    return client.get("/api/state", cookies=cookies).json()


def _consume_sse(client, cookies, text: str) -> tuple[str, list[dict]]:
    """跑一轮 chat;收集 token / done event,返回 (final_text, all_events)。"""
    import json as _json
    events: list[dict] = []
    text_out = ""
    with client.stream(
        "POST",
        "/api/chat",
        json={"text": text},
        cookies=cookies,
        headers={"Accept": "text/event-stream"},
        timeout=30,
    ) as r:
        assert r.status_code == 200, r.text[:300]
        cur_event = "message"
        buf_data = []
        for line in r.iter_lines():
            if line == "":
                if buf_data:
                    raw = "\n".join(buf_data)
                    try:
                        parsed = _json.loads(raw) if raw else None
                    except Exception:
                        parsed = raw
                    events.append({"event": cur_event, "data": parsed})
                    if cur_event == "token" and isinstance(parsed, dict):
                        text_out += parsed.get("text") or ""
                cur_event = "message"
                buf_data = []
                continue
            if line.startswith("event:"):
                cur_event = line[6:].strip()
            elif line.startswith("data:"):
                buf_data.append(line[5:].strip())
        if buf_data:
            raw = "\n".join(buf_data)
            try:
                parsed = _json.loads(raw) if raw else None
            except Exception:
                parsed = raw
            events.append({"event": cur_event, "data": parsed})
    return text_out, events


class CombatGateIntegration(unittest.TestCase):
    """端到端:在 minecart_track 里发"拉开距离放箭",GM 必须不被调用。"""

    @classmethod
    def setUpClass(cls):
        cls.client = make_client()
        cls.user = register_user(cls.client)
        _launch_ash_mine(cls.client, cls.user["cookies"])
        _move_to(cls.client, cls.user["cookies"], "minecart_track")

    def test_no_encounter_no_enemies_blocks_combat_narration(self):
        """minecart_track 没敌人没 encounter — 'continue 放箭' 必须被拦截。"""
        text, events = _consume_sse(
            self.client, self.user["cookies"],
            "借着矿车阻挡,向后拉开距离继续放箭",
        )
        # 必须有 rules_gate 事件 — 证明走了 gate,没调 GM
        gate_events = [e for e in events
                       if e["event"] == "agent"
                       and isinstance(e.get("data"), dict)
                       and e["data"].get("phase") == "rules_gate"]
        self.assertTrue(gate_events,
            f"应有 rules_gate agent 事件,实际 events={[e['event'] for e in events][:30]}")
        # done event 应带 rules_gated=True
        done = [e for e in events if e["event"] == "done"]
        self.assertTrue(done)
        self.assertTrue(done[-1]["data"].get("rules_gated"),
            "done event 必须带 rules_gated=True")
        # token 文本中应有"【规则要求先确认】"或"【需要先确认】"标记,且
        # 必须**不含**未经裁定的战斗结果短语
        forbidden = [
            "你被卡住", "无法后退", "短弓施展不了", "短弓难以施展",
            "陷入近战威胁", "敌人贴身",
        ]
        for word in forbidden:
            self.assertNotIn(word, text,
                f"GM 不应写未经裁定的战斗结果 '{word}',实际文本: {text[:200]}")
        # 应有问询标记
        self.assertTrue(
            "规则要求先确认" in text or "需要先确认" in text or "确认" in text,
            f"应有确认标记,实际:{text[:200]}",
        )

    def test_pending_question_persisted_after_gate(self):
        """gate 触发后 state.permissions.pending_questions 必须有新条目。"""
        # 用新 client 避免被上一个 test 影响
        client = make_client()
        u = register_user(client)
        _launch_ash_mine(client, u["cookies"])
        _move_to(client, u["cookies"], "minecart_track")
        _consume_sse(client, u["cookies"], "我拔出短弓准备射击")
        state = _get_state(client, u["cookies"])
        pqs = ((state.get("permissions") or {}).get("pending_questions") or [])
        self.assertTrue(pqs, "gate 触发后 pending_questions 应有新条目")
        last = pqs[-1]
        self.assertEqual(last.get("source"), "rules_engine")
        self.assertGreaterEqual(len(last.get("options") or []), 3)


# ────────────────────────────────────────────────────────────────────
# Layer C: RulesProvider prompt 包含硬约束块
# ────────────────────────────────────────────────────────────────────


class RulesProviderHardConstraintPrompt(unittest.TestCase):
    """module_adventure 场景下 RulesProvider 注入"硬约束 — GM 不得自行裁定"块。"""

    def test_prompt_contains_no_hallucination_clause(self):
        from context_providers.rules import RulesProvider
        from context_providers.base import Demand, ProviderServices

        class _S:
            data = {
                "ruleset": {"id": "dnd5e", "public_label": "5E compatible"},
                "player_character": {"name": "X", "level": 1, "hp": 10, "max_hp": 10, "ac": 12,
                                     "proficiency_bonus": 2, "abilities": {}},
                "scene": {
                    "module_id": "ash_mine",
                    "location_id": "minecart_track",
                    "current_room": {"id": "minecart_track", "enemies": []},
                },
                "encounter": {"active": False, "combatants": []},
                "dice_log": [],
            }
        manifest = {"kind": "module_adventure", "ruleset": "dnd5e"}
        demand = Demand(player_intent="explore")
        services = ProviderServices()
        prov = RulesProvider()
        contrib = prov.collect(_S(), manifest, demand, services)
        text = "\n".join(layer["content"] for layer in contrib.layers)
        # 硬约束块的标志短语
        self.assertIn("硬约束", text)
        self.assertIn("GM 不得自行裁定", text)
        self.assertIn("攻击命中", text)
        self.assertIn("HP", text)
        self.assertIn("借机攻击", text)
        self.assertIn("Disengage", text)
        self.assertIn("disadvantage", text)
        self.assertIn("RulesEngine 没返回的事实", text)

    def test_prompt_warns_when_no_enemies_and_no_encounter(self):
        """房间无 enemies + encounter 未激活 → 必须有额外警告"不得引入敌方 NPC"。"""
        from context_providers.rules import RulesProvider
        from context_providers.base import Demand, ProviderServices

        class _S:
            data = {
                "ruleset": {"id": "dnd5e", "public_label": "5E compatible"},
                "player_character": {"name": "X", "level": 1, "hp": 10, "max_hp": 10, "ac": 12,
                                     "proficiency_bonus": 2, "abilities": {}},
                "scene": {
                    "module_id": "ash_mine",
                    "location_id": "minecart_track",
                    "current_room": {"id": "minecart_track", "enemies": []},
                },
                "encounter": {"active": False, "combatants": []},
                "dice_log": [],
            }
        manifest = {"kind": "module_adventure", "ruleset": "dnd5e"}
        prov = RulesProvider()
        contrib = prov.collect(_S(), manifest, Demand(player_intent="explore"), ProviderServices())
        text = "\n".join(layer["content"] for layer in contrib.layers)
        self.assertIn("不得在本轮正文中引入任何敌方 NPC", text)


# ────────────────────────────────────────────────────────────────────
# Layer D: Ash Mine 数据完整性 — minecart_track 仍无敌人
# ────────────────────────────────────────────────────────────────────


class AshMineDataIntegrity(unittest.TestCase):
    """如果 Ash Mine 改了 minecart_track 加上敌人,以上集成测试会失效 —
    所以这里 lock 住 minecart_track 的 enemies=[]。"""

    def test_minecart_track_has_no_enemies(self):
        import json
        rooms = json.loads(
            (Path(__file__).resolve().parents[1] / "modules" / "ash_mine" / "rooms.json")
            .read_text(encoding="utf-8")
        )
        room = next((r for r in rooms if r.get("id") == "minecart_track"), None)
        self.assertIsNotNone(room, "minecart_track 房间必须存在")
        self.assertEqual(room.get("enemies") or [], [],
            "minecart_track 不能直接放敌人;敌人通过 camp_alert flag 触发 ash_camp_combat 遭遇。")
        self.assertEqual(room.get("npcs") or [], [],
            "minecart_track 也不放 NPC。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
