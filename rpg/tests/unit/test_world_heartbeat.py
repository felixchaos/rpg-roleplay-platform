"""
test_world_heartbeat.py — 世界心跳 v0 确定性脚手架测试。

设计文档: docs/design/world_heartbeat_v0.md §6

覆盖范围:
  · should_tick 纯函数: flag关 / 回合<4 / 间隔不足 / 积压>=8 各拒,全条件满足才 True
  · _validate_items 验收器: 超长 / 含「你」 / 含玩家名 / 重复指纹 逐条拒,合法条目过
  · 上限: 未浮出 12 条拒收;过期剪除: surfaced 超 5 回合被剪,未浮出不剪
  · run_heartbeat_tick: LLM 调用处 monkeypatch 假返回,绝不真调外网
  · context provider(world_pulse): gate 关 skip、无条目 skip、注入标 surfaced、
    一次最多 2 条、模板文案含「不强求」、注入后再 collect 不重复给同条
  · prompt 构造: 材料含 facts/active_entities、含「不能出现『你』」等玩家指称约束文本

所有测试不依赖 DB,不发真实网络请求。
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
from agents.world_heartbeat import (  # noqa: E402
    MAX_UNSURFACED_BACKLOG,
    MAX_UNSURFACED_STORED,
    SURFACED_RETENTION_TURNS,
    _build_materials,
    _build_prompts,
    _validate_items,
    run_heartbeat_tick,
    should_tick,
)


def _new_state(turn: int = 0) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


def _bg_event(text: str, created_turn: int, surfaced_turn: int | None = None) -> dict:
    return {
        "id": f"bg_{abs(hash(text)) % 100000}",
        "text": text,
        "created_turn": created_turn,
        "surfaced_turn": surfaced_turn,
    }


# ────────────────────────────────────────────────────────────
# should_tick
# ────────────────────────────────────────────────────────────

class ShouldTickTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("RPG_WORLD_HEARTBEAT", None)

    def tearDown(self):
        os.environ.pop("RPG_WORLD_HEARTBEAT", None)

    def test_flag_off_rejected(self):
        """flag 默认关(env 未设)→ 拒绝,即便其它条件都满足。"""
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 10
        self.assertFalse(should_tick(data, user_id=None))

    def test_turn_below_min_rejected(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 3  # < 4
        self.assertFalse(should_tick(data, user_id=None))

    def test_interval_not_met_rejected(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 6
        data["heartbeat_meta"] = {"last_tick_turn": 5}  # 6-5=1 < 3
        self.assertFalse(should_tick(data, user_id=None))

    def test_backlog_at_limit_rejected(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 20
        data["background_events"] = [
            _bg_event(f"事件{i}", created_turn=1) for i in range(MAX_UNSURFACED_BACKLOG)
        ]
        self.assertFalse(should_tick(data, user_id=None))

    def test_all_conditions_met_true(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 10
        data["heartbeat_meta"] = {"last_tick_turn": 4}  # 10-4=6 >= 3
        data["background_events"] = [_bg_event("旧事件", created_turn=1)]  # 1 < 8
        self.assertTrue(should_tick(data, user_id=None))

    def test_never_ticked_before_uses_default_last_tick(self):
        """从未 tick 过(无 heartbeat_meta)→ 视为很久以前,间隔天然满足。"""
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        data = copy.deepcopy(DEFAULT_STATE)
        data["turn"] = 4  # 恰好达到 MIN_TURN_TO_START
        self.assertTrue(should_tick(data, user_id=None))

    def test_flag_exception_safe(self):
        """feature_enabled 抛异常也不应崩,应视为拒绝。"""
        import core.feature_flags as ff

        def _boom(key, user_id=None):
            raise RuntimeError("boom")

        orig = ff.feature_enabled
        ff.feature_enabled = _boom
        try:
            data = copy.deepcopy(DEFAULT_STATE)
            data["turn"] = 10
            self.assertFalse(should_tick(data, user_id=None))
        finally:
            ff.feature_enabled = orig


# ────────────────────────────────────────────────────────────
# _validate_items
# ────────────────────────────────────────────────────────────

class ValidateItemsTests(unittest.TestCase):
    def test_valid_item_passes(self):
        data = copy.deepcopy(DEFAULT_STATE)
        out = _validate_items(["村东磨坊主的驴昨夜挣脱缰绳跑进了麦田"], state_data=data)
        self.assertEqual(out, ["村东磨坊主的驴昨夜挣脱缰绳跑进了麦田"])

    def test_too_long_rejected(self):
        data = copy.deepcopy(DEFAULT_STATE)
        long_text = "字" * 121
        out = _validate_items([long_text], state_data=data)
        self.assertEqual(out, [])

    def test_exactly_at_limit_passes(self):
        data = copy.deepcopy(DEFAULT_STATE)
        text_120 = "字" * 120
        out = _validate_items([text_120], state_data=data)
        self.assertEqual(out, [text_120])

    def test_contains_ni_rejected(self):
        data = copy.deepcopy(DEFAULT_STATE)
        out = _validate_items(["你昨晚做的事全村都知道了"], state_data=data)
        self.assertEqual(out, [])

    def test_contains_player_name_rejected(self):
        data = copy.deepcopy(DEFAULT_STATE)
        out = _validate_items(
            ["艾伦悄悄溜进了酒馆后巷"], state_data=data, player_name="艾伦",
        )
        self.assertEqual(out, [])

    def test_duplicate_fingerprint_rejected(self):
        data = copy.deepcopy(DEFAULT_STATE)
        data["background_events"] = [_bg_event("村东磨坊主的驴跑了", created_turn=1)]
        out = _validate_items(["村东磨坊主的驴跑了！"], state_data=data)  # 标点差异
        self.assertEqual(out, [])

    def test_duplicate_within_batch_rejected(self):
        """同一批次内部重复也应去重(只留第一条)。"""
        data = copy.deepcopy(DEFAULT_STATE)
        out = _validate_items(
            ["磨坊主的驴跑了", "磨坊主的驴跑了", "铁匠铺进了新矿石"], state_data=data,
        )
        self.assertEqual(out, ["磨坊主的驴跑了", "铁匠铺进了新矿石"])

    def test_empty_string_rejected(self):
        data = copy.deepcopy(DEFAULT_STATE)
        out = _validate_items(["", "   ", "合法事件文本"], state_data=data)
        self.assertEqual(out, ["合法事件文本"])

    def test_non_string_item_rejected_no_crash(self):
        data = copy.deepcopy(DEFAULT_STATE)
        out = _validate_items([123, None, {"a": 1}, "合法事件文本"], state_data=data)
        self.assertEqual(out, ["合法事件文本"])

    def test_non_list_input_returns_empty_no_crash(self):
        data = copy.deepcopy(DEFAULT_STATE)
        self.assertEqual(_validate_items("not a list", state_data=data), [])
        self.assertEqual(_validate_items(None, state_data=data), [])
        self.assertEqual(_validate_items({"a": 1}, state_data=data), [])

    def test_max_two_items_per_tick(self):
        data = copy.deepcopy(DEFAULT_STATE)
        out = _validate_items(
            ["事件甲一二三", "事件乙四五六", "事件丙七八九"], state_data=data,
        )
        self.assertEqual(len(out), 2)


# ────────────────────────────────────────────────────────────
# 上限 + 过期剪除(run_heartbeat_tick 的落地部分 + world_pulse provider 的剪除部分)
# ────────────────────────────────────────────────────────────

class LimitsAndExpiryTests(unittest.TestCase):
    def test_unsurfaced_limit_12_rejects_new_writes(self):
        """未浮出已达 12 条 → run_heartbeat_tick 不再写入新条目。"""
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        try:
            s = _new_state(turn=10)
            s.data["background_events"] = [
                _bg_event(f"事件{i}", created_turn=1) for i in range(MAX_UNSURFACED_STORED)
            ]

            def _fake_call_agent_json(**kwargs):
                return '["新事件一二三四五"]', {}

            import agents._harness as harness
            import agents.recorder as recorder

            orig_call = harness.call_agent_json
            orig_resolve = recorder._resolve_recorder_api_and_model
            harness.call_agent_json = _fake_call_agent_json
            recorder._resolve_recorder_api_and_model = lambda *a, **k: ("openai", "gpt-test")
            try:
                written = run_heartbeat_tick(s, user_id=None)
            finally:
                harness.call_agent_json = orig_call
                recorder._resolve_recorder_api_and_model = orig_resolve

            self.assertEqual(written, [])
            self.assertEqual(len(s.data["background_events"]), MAX_UNSURFACED_STORED)
        finally:
            os.environ.pop("RPG_WORLD_HEARTBEAT", None)

    def test_surfaced_beyond_window_is_pruned_by_provider(self):
        """已浮出超过 SURFACED_RETENTION_TURNS 回合 → world_pulse.collect 剪除。"""
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        try:
            from context_providers import Demand, ProviderServices
            from context_providers.registry import get_provider

            provider = get_provider("world_pulse")
            s = _new_state(turn=100)
            s.data["background_events"] = [
                _bg_event("很老的已浮出事件", created_turn=1,
                          surfaced_turn=100 - SURFACED_RETENTION_TURNS - 1),  # 超窗口
                _bg_event("刚浮出不久的事件", created_turn=90, surfaced_turn=99),  # 未超窗口
            ]
            manifest = {"context_providers": ["world_pulse"]}
            provider.collect(s, manifest, Demand.empty(), ProviderServices(user_id=None))
            remaining_texts = [e["text"] for e in s.data["background_events"]]
            self.assertNotIn("很老的已浮出事件", remaining_texts)
            self.assertIn("刚浮出不久的事件", remaining_texts)
        finally:
            os.environ.pop("RPG_WORLD_HEARTBEAT", None)

    def test_unsurfaced_not_pruned_regardless_of_age(self):
        """未浮出条目不受过期剪除影响,即便 created_turn 很老。"""
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        try:
            from context_providers import Demand, ProviderServices
            from context_providers.registry import get_provider

            provider = get_provider("world_pulse")
            s = _new_state(turn=200)
            s.data["background_events"] = [
                _bg_event("很老但未浮出", created_turn=1, surfaced_turn=None),
            ]
            manifest = {"context_providers": ["world_pulse"]}
            contrib = provider.collect(s, manifest, Demand.empty(), ProviderServices(user_id=None))
            self.assertTrue(contrib.applied)
            # 该条应被取出浮出(标记 surfaced_turn),而不是被剪除
            self.assertEqual(len(s.data["background_events"]), 1)
            self.assertEqual(s.data["background_events"][0]["surfaced_turn"], 200)
        finally:
            os.environ.pop("RPG_WORLD_HEARTBEAT", None)


# ────────────────────────────────────────────────────────────
# run_heartbeat_tick(monkeypatch LLM 调用,绝不真调外网)
# ────────────────────────────────────────────────────────────

class RunHeartbeatTickTests(unittest.TestCase):
    def _patch_llm(self, fake_text: str):
        import agents._harness as harness
        import agents.recorder as recorder

        orig_call = harness.call_agent_json
        orig_resolve = recorder._resolve_recorder_api_and_model
        harness.call_agent_json = lambda **kwargs: (fake_text, {})
        recorder._resolve_recorder_api_and_model = lambda *a, **k: ("openai", "gpt-test")
        return orig_call, orig_resolve

    def _unpatch_llm(self, orig_call, orig_resolve):
        import agents._harness as harness
        import agents.recorder as recorder
        harness.call_agent_json = orig_call
        recorder._resolve_recorder_api_and_model = orig_resolve

    def test_happy_path_writes_events_and_updates_meta(self):
        s = _new_state(turn=10)
        orig_call, orig_resolve = self._patch_llm(
            '["村东磨坊主的驴跑进了麦田", "铁匠铺新到了矿石"]'
        )
        try:
            written = run_heartbeat_tick(s, user_id=None)
        finally:
            self._unpatch_llm(orig_call, orig_resolve)

        self.assertEqual(len(written), 2)
        self.assertEqual(len(s.data["background_events"]), 2)
        for e in s.data["background_events"]:
            self.assertEqual(e["created_turn"], 10)
            self.assertIsNone(e["surfaced_turn"])
            self.assertTrue(e["id"].startswith("bg_"))
        self.assertEqual(s.data["heartbeat_meta"]["last_tick_turn"], 10)

    def test_no_model_available_returns_empty_no_crash(self):
        import agents.recorder as recorder
        orig_resolve = recorder._resolve_recorder_api_and_model
        recorder._resolve_recorder_api_and_model = lambda *a, **k: ("", "")
        try:
            s = _new_state(turn=10)
            written = run_heartbeat_tick(s, user_id=None)
            self.assertEqual(written, [])
            self.assertEqual(s.data.get("background_events", []), [])
        finally:
            recorder._resolve_recorder_api_and_model = orig_resolve

    def test_model_resolution_raises_no_crash(self):
        import agents.recorder as recorder
        orig_resolve = recorder._resolve_recorder_api_and_model

        def _boom(*a, **k):
            raise RuntimeError("boom")

        recorder._resolve_recorder_api_and_model = _boom
        try:
            s = _new_state(turn=10)
            written = run_heartbeat_tick(s, user_id=None)
            self.assertEqual(written, [])
        finally:
            recorder._resolve_recorder_api_and_model = orig_resolve

    def test_llm_call_raises_no_crash(self):
        import agents._harness as harness
        import agents.recorder as recorder
        orig_call = harness.call_agent_json
        orig_resolve = recorder._resolve_recorder_api_and_model

        def _boom(**kwargs):
            raise RuntimeError("network boom")

        harness.call_agent_json = _boom
        recorder._resolve_recorder_api_and_model = lambda *a, **k: ("openai", "gpt-test")
        try:
            s = _new_state(turn=10)
            written = run_heartbeat_tick(s, user_id=None)
            self.assertEqual(written, [])
        finally:
            harness.call_agent_json = orig_call
            recorder._resolve_recorder_api_and_model = orig_resolve

    def test_malformed_json_output_returns_empty_no_crash(self):
        s = _new_state(turn=10)
        orig_call, orig_resolve = self._patch_llm("这不是 JSON，是一段散文解释。")
        try:
            written = run_heartbeat_tick(s, user_id=None)
        finally:
            self._unpatch_llm(orig_call, orig_resolve)
        self.assertEqual(written, [])
        self.assertEqual(s.data.get("background_events", []), [])

    def test_json_object_instead_of_array_returns_empty_no_crash(self):
        """LLM 吐了个 JSON object 而非 array → want=list 类型过滤应拒绝。"""
        s = _new_state(turn=10)
        orig_call, orig_resolve = self._patch_llm('{"event": "不对的形状"}')
        try:
            written = run_heartbeat_tick(s, user_id=None)
        finally:
            self._unpatch_llm(orig_call, orig_resolve)
        self.assertEqual(written, [])

    def test_all_items_rejected_by_validator_returns_empty_but_updates_meta(self):
        """全部候选被验收器拒绝 → 空手而归,但仍更新 last_tick_turn(避免立刻重复调用)。"""
        s = _new_state(turn=10)
        orig_call, orig_resolve = self._patch_llm('["你昨晚做的事全村都知道了"]')
        try:
            written = run_heartbeat_tick(s, user_id=None)
        finally:
            self._unpatch_llm(orig_call, orig_resolve)
        self.assertEqual(written, [])
        self.assertEqual(s.data.get("background_events", []), [])
        self.assertEqual(s.data["heartbeat_meta"]["last_tick_turn"], 10)

    def test_state_data_not_dict_returns_empty_no_crash(self):
        class _FakeState:
            data = None

        written = run_heartbeat_tick(_FakeState(), user_id=None)
        self.assertEqual(written, [])

    def test_markdown_fenced_json_is_parsed(self):
        """LLM 输出被 ```json 围栏包裹时仍应正常解析(parse_llm_json 鲁棒解析)。"""
        s = _new_state(turn=10)
        orig_call, orig_resolve = self._patch_llm(
            '```json\n["村口新开了一家豆腐坊"]\n```'
        )
        try:
            written = run_heartbeat_tick(s, user_id=None)
        finally:
            self._unpatch_llm(orig_call, orig_resolve)
        self.assertEqual(written, ["村口新开了一家豆腐坊"])


# ────────────────────────────────────────────────────────────
# context provider: world_pulse
# ────────────────────────────────────────────────────────────

class WorldPulseProviderTests(unittest.TestCase):
    def setUp(self):
        from context_providers import Demand, ProviderServices
        from context_providers.registry import get_provider
        self.provider = get_provider("world_pulse")
        self.assertIsNotNone(self.provider, "world_pulse provider 未注册")
        self.demand = Demand.empty()
        self.manifest = {"context_providers": ["world_pulse"]}
        self.services_factory = ProviderServices
        os.environ.pop("RPG_WORLD_HEARTBEAT", None)

    def tearDown(self):
        os.environ.pop("RPG_WORLD_HEARTBEAT", None)

    def test_gate_off_skips(self):
        s = _new_state(turn=10)
        s.data["background_events"] = [_bg_event("X", created_turn=1)]
        contrib = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        self.assertFalse(contrib.applied)

    def test_gate_on_no_events_skips(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        s = _new_state(turn=10)
        contrib = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        self.assertFalse(contrib.applied)

    def test_gate_on_all_already_surfaced_skips(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        s = _new_state(turn=10)
        s.data["background_events"] = [_bg_event("已经浮出过的事", created_turn=1, surfaced_turn=9)]
        contrib = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        self.assertFalse(contrib.applied)

    def test_surfaces_and_marks_surfaced_turn(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        s = _new_state(turn=12)
        s.data["background_events"] = [_bg_event("村东磨坊主的驴跑了", created_turn=10)]
        contrib = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        self.assertTrue(contrib.applied)
        self.assertEqual(len(contrib.layers), 1)
        content = contrib.layers[0]["content"]
        self.assertIn("村东磨坊主的驴跑了", content)
        self.assertIn("世界脉动", content)
        self.assertIn("不强求", content)
        self.assertEqual(s.data["background_events"][0]["surfaced_turn"], 12)

    def test_at_most_two_per_collect(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        s = _new_state(turn=20)
        s.data["background_events"] = [
            _bg_event(f"事件{i}", created_turn=i) for i in range(1, 6)  # 5 条未浮出
        ]
        contrib = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        self.assertTrue(contrib.applied)
        self.assertEqual(contrib.debug.get("surfaced_count"), 2)
        surfaced = [e for e in s.data["background_events"] if e.get("surfaced_turn")]
        self.assertEqual(len(surfaced), 2)

    def test_oldest_surfaced_first(self):
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        s = _new_state(turn=20)
        s.data["background_events"] = [
            _bg_event("较新事件", created_turn=15),
            _bg_event("最早事件", created_turn=5),
            _bg_event("中间事件", created_turn=10),
        ]
        contrib = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        content = contrib.layers[0]["content"]
        self.assertIn("最早事件", content)
        self.assertIn("中间事件", content)
        self.assertNotIn("较新事件", content)

    def test_second_collect_does_not_repeat_same_entry(self):
        """注入后再 collect 不重复给同条(已 surfaced 的条目不再被选中)。"""
        os.environ["RPG_WORLD_HEARTBEAT"] = "1"
        s = _new_state(turn=20)
        s.data["background_events"] = [_bg_event("唯一事件", created_turn=10)]
        contrib1 = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        self.assertTrue(contrib1.applied)
        contrib2 = self.provider.collect(
            s, self.manifest, self.demand, self.services_factory(user_id=None),
        )
        self.assertFalse(contrib2.applied)  # 已浮出,无新条目可给


# ────────────────────────────────────────────────────────────
# prompt 构造(_build_materials / _build_prompts)
# ────────────────────────────────────────────────────────────

class PromptConstructionTests(unittest.TestCase):
    def test_materials_include_facts_and_active_entities(self):
        data = copy.deepcopy(DEFAULT_STATE)
        data["memory"]["facts"] = ["事实一", "事实二"]
        data["active_entities"] = [
            {"name": "雷纳德", "disposition": "friendly"},
        ]
        materials = _build_materials(data, None)
        self.assertIn("事实一", materials["facts_recent"])
        self.assertIn("事实二", materials["facts_recent"])
        self.assertEqual(materials["active_entities"], [{"name": "雷纳德", "disposition": "friendly"}])

    def test_materials_include_recent_background_events(self):
        data = copy.deepcopy(DEFAULT_STATE)
        data["background_events"] = [_bg_event("旧事件甲", created_turn=1)]
        materials = _build_materials(data, None)
        self.assertIn("旧事件甲", materials["recent_background_events"])

    def test_materials_include_pending_anchor_hints_capped_at_two(self):
        data = copy.deepcopy(DEFAULT_STATE)
        anchors = [
            {"summary": "锚点摘要一"},
            {"summary": "锚点摘要二"},
            {"summary": "锚点摘要三"},
        ]
        materials = _build_materials(data, anchors)
        self.assertEqual(len(materials["pending_anchor_hints"]), 2)
        self.assertIn("锚点摘要一", materials["pending_anchor_hints"])
        self.assertIn("锚点摘要二", materials["pending_anchor_hints"])
        self.assertNotIn("锚点摘要三", materials["pending_anchor_hints"])

    def test_materials_no_pending_anchors_ok(self):
        data = copy.deepcopy(DEFAULT_STATE)
        materials = _build_materials(data, None)
        self.assertEqual(materials["pending_anchor_hints"], [])

    def test_prompts_contain_player_reference_constraint(self):
        data = copy.deepcopy(DEFAULT_STATE)
        data["memory"]["facts"] = ["事实一"]
        materials = _build_materials(data, None)
        system_prompt, user_prompt = _build_prompts(materials)
        self.assertIn("你", system_prompt)  # 约束文本本身会提到"你"这个字用于说明禁止规则
        self.assertIn("禁止提到玩家本人", system_prompt)
        self.assertIn("JSON", system_prompt)

    def test_prompts_include_materials_content(self):
        data = copy.deepcopy(DEFAULT_STATE)
        data["memory"]["facts"] = ["事实一"]
        data["player"]["current_location"] = "阿托菲村"
        materials = _build_materials(data, None)
        _system_prompt, user_prompt = _build_prompts(materials)
        self.assertIn("事实一", user_prompt)
        self.assertIn("阿托菲村", user_prompt)


if __name__ == "__main__":
    unittest.main()


def test_heartbeat_wired_in_async_and_sync_paths():
    """源码守卫(free-gate 守卫同款):心跳必须同时接在 async 生产默认路径(史官三合一
    所在的早退分支)与 sync 路径(_run_post_gm_parallel),缺任一路=灰度双路径事故
    (v1.41.0 只接 sync 导致生产永不触发,勿回归)。"""
    src = (Path(__file__).resolve().parents[2] / "chat_pipeline.py").read_text(encoding="utf-8")
    sync_start = src.index("async def _run_post_gm_parallel")
    async_section = src[:sync_start]
    sync_section = src[sync_start:]
    # async 分支:启动 + 两个 return 前 await
    assert async_section.count("run_heartbeat_tick") >= 1, "async 路径丢失心跳接线"
    assert async_section.count("await _hb_task") >= 2, "async 两个早退点须都 await 心跳"
    # sync 路径 parity
    assert "run_heartbeat_tick" in sync_section, "sync 路径丢失心跳接线"
