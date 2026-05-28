"""
test_character_card_generator.py — task 87 / 49: 创意工具测试。

覆盖:
  · 基本生成 (mocked LLM) — 通过所有 validator
  · 姓名查重 — 注入冲突 NPC,期望 retry 后通过 / 最终失败
  · phase 一致 — LLM 返回错 phase,期望 retry
  · 跨 phase token 黑名单 — 月球剧情返回柏林词,期望 retry
  · critic 低分 reject
  · retry 2 次后放弃
  · refine 路径走同一 generate 管线
  · dispatcher origin=console_assistant 成功
  · dispatcher origin=llm_chat 被拒
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

import character_card_generator as ccg  # noqa: E402
from tools_dsl.command_dispatcher import ToolCallEnvelope, ToolDispatcher, get_registry  # noqa: E402
from tools_dsl.command_tools_register import force_reset_for_tests  # noqa: E402


# ────────────────────────────────────────────────────────────
# 辅助: 构造一个完整合规的 draft
# ────────────────────────────────────────────────────────────


def _good_draft(name="艾莉雅", phase="柏林暗流篇") -> dict:
    return {
        "name": name,
        "gender": "女",
        "age": "20",
        "appearance": "金发碧眼,身材纤细,穿着旧式贵族风衣裙。",
        "personality": "傲娇但善良,口是心非。",
        "background": "流亡贵族,家族被陷害后被迫隐姓埋名。",
        "motivation": "查清家族冤案,夺回应得的尊严。",
        "speaking_style": "语速偏快,常带不必要的反问。/ 『哼,谁,谁稀罕!』/ 『...不是我想的那种!』",
        "abilities": ["元素魔法", "古典剑术", "宫廷礼仪"],
        "relationship_hints": {},
        "phase_availability": [phase],
        "consistency_check_self": "性格与背景与该 phase 的政治压抑氛围相称。",
    }


def _bad_phase_draft(wrong_phase="火星篇") -> dict:
    d = _good_draft()
    d["phase_availability"] = [wrong_phase]
    return d


def _leaky_draft(leak_token="月球") -> dict:
    d = _good_draft()
    d["background"] = f"流亡贵族,曾在{leak_token}基地工作过。"
    return d


# 模拟一个最小可用 backend
class _FakeBackend:
    """模拟 _AnthropicBackend (因为 _select_backend 通过类名判定走 native tool_use 还是
    call_structured)。我们让它的类名同时不等于 _AnthropicBackend,这样会走
    call_structured 文本路径,便于测试时直接控制返回 JSON。
    """
    def __init__(self, responses):
        # responses: list of JSON dicts (按 call_structured 次序返回)
        self._responses = list(responses)
        self.model_name = "fake"

    def call_structured(self, system, messages, max_tokens):
        if not self._responses:
            return "{}"
        nxt = self._responses.pop(0)
        return json.dumps(nxt, ensure_ascii=False)


def _patch_backend(responses):
    """把 character_card_generator._select_backend 替换成返回 _FakeBackend。"""
    return patch.object(ccg, "_select_backend", return_value=_FakeBackend(responses))


def _empty_slice(phase="柏林暗流篇", blacklist=None):
    """patch _layer1_reality_slice 让测试不依赖 DB。"""
    s = {
        "target_phase": phase,
        "script_id": 1,
        "existing_npc_names": [],
        "user_card_names": [],
        "phase_reference_cards": [],
        "worldbook_keys": [],
        "other_phase_tokens": blacklist or [],
        "ruleset_id": "",
        "warnings": ["test: skipped DB"],
    }
    return s


# ────────────────────────────────────────────────────────────
# Layer-by-layer 单元测试
# ────────────────────────────────────────────────────────────


class GenerateBasic(unittest.TestCase):

    def test_empty_brief_rejects(self):
        r = ccg.generate_character_card_draft(brief="", user_id=1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["retries"], 0)
        self.assertTrue(any(v.get("layer") == "input" for v in r["validations"]))

    def test_happy_path_with_mocked_backend(self):
        # critic LLM 返回高分;生成器返回合规 draft
        good = _good_draft()
        critic_pass = {"score": 0.85, "reason": "ok"}
        with patch.object(ccg, "_layer1_reality_slice",
                          return_value=_empty_slice()), \
             _patch_backend([good, critic_pass]):
            r = ccg.generate_character_card_draft(
                brief="20 岁女法师,流亡贵族,傲娇但善良",
                user_id=1, script_id=1, phase="柏林暗流篇",
            )
        self.assertTrue(r["ok"], r["validations"])
        self.assertEqual(r["retries"], 0)
        self.assertEqual(r["draft"]["name"], "艾莉雅")


class NameUniqueness(unittest.TestCase):

    def test_name_collision_triggers_retry_then_passes(self):
        # 第一次返回与已有 NPC 同名 → reject
        # 第二次改名 → 通过
        slice_ = _empty_slice()
        slice_["existing_npc_names"] = ["艾莉雅"]
        d1 = _good_draft(name="艾莉雅")
        d2 = _good_draft(name="艾莉娅")
        critic = {"score": 0.9, "reason": "ok"}
        with patch.object(ccg, "_layer1_reality_slice", return_value=slice_), \
             _patch_backend([d1, critic, d2, critic]):
            r = ccg.generate_character_card_draft(
                brief="法师", user_id=1, script_id=1, phase="柏林暗流篇",
            )
        self.assertTrue(r["ok"], r["validations"])
        self.assertEqual(r["retries"], 1)
        self.assertEqual(r["draft"]["name"], "艾莉娅")

    def test_name_collision_three_times_gives_up(self):
        slice_ = _empty_slice()
        slice_["existing_npc_names"] = ["艾莉雅"]
        d = _good_draft(name="艾莉雅")
        critic = {"score": 0.9, "reason": "ok"}
        with patch.object(ccg, "_layer1_reality_slice", return_value=slice_), \
             _patch_backend([d, critic, d, critic, d, critic]):
            r = ccg.generate_character_card_draft(
                brief="法师", user_id=1, script_id=1, phase="柏林暗流篇",
            )
        self.assertFalse(r["ok"])
        self.assertEqual(r["retries"], ccg.MAX_RETRIES)
        # validations 里至少有一条 name_uniqueness=False
        layers = [v.get("layer") for v in r["validations"]]
        self.assertIn("name_uniqueness", layers)


class PhaseConsistency(unittest.TestCase):

    def test_wrong_phase_triggers_retry(self):
        slice_ = _empty_slice(phase="柏林暗流篇")
        bad = _bad_phase_draft("火星篇")
        good = _good_draft(phase="柏林暗流篇")
        critic = {"score": 0.9, "reason": "ok"}
        with patch.object(ccg, "_layer1_reality_slice", return_value=slice_), \
             _patch_backend([bad, critic, good, critic]):
            r = ccg.generate_character_card_draft(
                brief="法师", user_id=1, script_id=1, phase="柏林暗流篇",
            )
        self.assertTrue(r["ok"], r["validations"])
        self.assertEqual(r["retries"], 1)


class CrossPhaseTokenBlacklist(unittest.TestCase):

    def test_moon_setting_rejects_berlin_token(self):
        slice_ = _empty_slice(phase="月球", blacklist=["柏林"])
        # 第一次返回背景里含柏林,reject
        leaky = _leaky_draft(leak_token="柏林")
        leaky["phase_availability"] = ["月球"]
        clean = _good_draft(phase="月球")
        critic = {"score": 0.9, "reason": "ok"}
        with patch.object(ccg, "_layer1_reality_slice", return_value=slice_), \
             _patch_backend([leaky, critic, clean, critic]):
            r = ccg.generate_character_card_draft(
                brief="法师", user_id=1, script_id=1, phase="月球",
            )
        self.assertTrue(r["ok"], r["validations"])
        self.assertEqual(r["retries"], 1)


class CriticLowScore(unittest.TestCase):

    def test_critic_below_threshold_rejects(self):
        slice_ = _empty_slice()
        good = _good_draft()
        # critic 给低分两次 → reject;第三次仍低分 → 放弃
        low_score = {"score": 0.3, "reason": "风格不符"}
        with patch.object(ccg, "_layer1_reality_slice", return_value=slice_), \
             _patch_backend([good, low_score, good, low_score, good, low_score]):
            r = ccg.generate_character_card_draft(
                brief="法师", user_id=1, script_id=1, phase="柏林暗流篇",
            )
        self.assertFalse(r["ok"])
        layers = [v.get("layer") for v in r["validations"] if not v.get("ok")]
        self.assertIn("critic_score", layers)


class RefinePath(unittest.TestCase):

    def test_refine_uses_previous_draft_and_feedback(self):
        slice_ = _empty_slice()
        prev = _good_draft()
        # refine 后改了性格描述
        refined = dict(prev)
        refined["personality"] = "更内向,沉默寡言。"
        critic = {"score": 0.9, "reason": "ok"}
        with patch.object(ccg, "_layer1_reality_slice", return_value=slice_), \
             _patch_backend([refined, critic]):
            r = ccg.refine_character_card_draft(
                previous_draft=prev,
                feedback="性格再内向一点",
                user_id=1, script_id=1,
            )
        self.assertTrue(r["ok"], r["validations"])
        self.assertEqual(r["draft"]["personality"], "更内向,沉默寡言。")

    def test_refine_missing_previous_fails(self):
        r = ccg.refine_character_card_draft(
            previous_draft={}, feedback="改改", user_id=1,
        )
        self.assertFalse(r["ok"])

    def test_refine_missing_feedback_fails(self):
        r = ccg.refine_character_card_draft(
            previous_draft=_good_draft(), feedback="", user_id=1,
        )
        self.assertFalse(r["ok"])


# ────────────────────────────────────────────────────────────
# Dispatcher integration
# ────────────────────────────────────────────────────────────


class DispatcherIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: None,
        )

    def _call(self, tool, args, origin, trace_id=None, user_id=1):
        env = ToolCallEnvelope(
            user_id=user_id, save_id=None, tool=tool, args=args,
            origin=origin, trace_id=trace_id or f"t-{tool}-{origin}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_generate_via_console_assistant_origin(self):
        slice_ = _empty_slice()
        good = _good_draft()
        critic = {"score": 0.9, "reason": "ok"}
        with patch.object(ccg, "_layer1_reality_slice", return_value=slice_), \
             _patch_backend([good, critic]):
            r = self._call("generate_character_card_draft",
                           {"brief": "法师", "script_id": 1, "phase": "柏林暗流篇"},
                           origin="console_assistant")
        self.assertTrue(r.ok, r.error or r.result)
        payload = json.loads(r.result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["draft"]["name"], "艾莉雅")

    def test_generate_blocked_from_llm_chat(self):
        r = self._call("generate_character_card_draft",
                       {"brief": "法师"},
                       origin="llm_chat",
                       trace_id="t-gen-llm")
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_generate_blocked_from_llm_set(self):
        r = self._call("generate_character_card_draft",
                       {"brief": "法师"},
                       origin="llm_set",
                       trace_id="t-gen-llmset")
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_refine_via_console_assistant_origin(self):
        slice_ = _empty_slice()
        good = _good_draft()
        critic = {"score": 0.9, "reason": "ok"}
        with patch.object(ccg, "_layer1_reality_slice", return_value=slice_), \
             _patch_backend([good, critic]):
            r = self._call("refine_character_card_draft",
                           {"previous_draft": _good_draft(),
                            "feedback": "更内向一点",
                            "script_id": 1},
                           origin="console_assistant")
        self.assertTrue(r.ok, r.error or r.result)

    def test_refine_blocked_from_llm_chat(self):
        r = self._call("refine_character_card_draft",
                       {"previous_draft": _good_draft(), "feedback": "x"},
                       origin="llm_chat",
                       trace_id="t-ref-llm")
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_tool_registered(self):
        registry = get_registry()
        self.assertTrue(registry.has("generate_character_card_draft"))
        self.assertTrue(registry.has("refine_character_card_draft"))
        gen = registry.get("generate_character_card_draft")
        self.assertIn("console_assistant", gen.origins)
        self.assertNotIn("llm_chat", gen.origins)


if __name__ == "__main__":
    unittest.main()
