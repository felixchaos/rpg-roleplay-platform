"""出正文前的自动重试 / 跨渠道切换要把失败那次的思考流丢掉。

流内错误常在思考流吐了一段之后才到(reasoning 不算提交,包装器照样重试)。失败那次的思考已经
append 进 state.data['_turn_reasoning'];retry_notice / fallback_notice 分支以前不清它:
  · 重试成功 → record_turn 把两次尝试的思考都落进这条 assistant 历史,重开聊天后思考流重复;
  · 重试那次上游什么都没给 → 空回合分诊靠 reasoning_chars>0 判成 reasoning_only(「只输出了
    思考过程,把推理强度调低」),真实情况应是 upstream_empty。
这两个事件都保证之前没有已提交事件(正文 / 工具),所以只清思考流即可。
"""
from __future__ import annotations

import asyncio
import threading
import unittest

import agents.gm.stream_retry as stream_retry
import chat_pipeline.gm as gm_phase
from chat_pipeline._common import PipelineContext
from chat_pipeline.persist import _empty_turn_diagnosis
from state import GameState


class _Backend:
    model_name = "stub-model"

    def __init__(self):
        self.last_usage: dict = {}


class _GM:
    api_id = "stub"

    def __init__(self):
        self._backend = _Backend()


async def _drain(agen):
    return [x async for x in agen]


class GmRetryResetsReasoning(unittest.TestCase):
    def setUp(self):
        self._saved = (stream_retry.stream_with_channel_fallback, gm_phase._narrator_slim)
        gm_phase._narrator_slim = lambda *_a, **_k: False

    def tearDown(self):
        stream_retry.stream_with_channel_fallback, gm_phase._narrator_slim = self._saved

    def _run(self, events):
        stream_retry.stream_with_channel_fallback = lambda *_a, **_k: iter(events)
        ctx = PipelineContext(
            api_user=None, state=GameState.new(), gm=_GM(), sub_gm=None,
            message_for_model="你好", run_id=1, stop_event=threading.Event(), chat_start_time=0.0,
        )
        ctx.bundle = {"prompt": "p", "debug": {"layers": []}}
        ctx.agent_result = {"curator_plan": {}}
        out = asyncio.run(_drain(gm_phase.run_gm_phase(
            ctx,
            payload_fn=lambda u: {},
            persist_chat_turn=lambda *a, **k: None,
            mark_context_run=lambda *a, **k: None,
            current_run_id_fn=lambda u: 1,
            is_stop_requested_global=lambda u, r: False,
            is_extractor_enabled=lambda u: False,
            acceptance_verifier_mode=lambda u: "off",
            verify_acceptance=lambda *a, **k: [],
            active_script_id=lambda u: None,
        )))
        return ctx, out

    def test_retry_notice_drops_failed_attempt_reasoning(self):
        ctx, out = self._run([
            {"type": "reasoning", "text": "思考1"},
            {"type": "retry_notice", "attempt": 1, "max_retries": 2, "category": "upstream"},
            {"type": "reasoning", "text": "思考2"},
        ])
        self.assertEqual(ctx.state.data.get("_turn_reasoning"), ["思考2"],
                         "失败那次的思考留着 → 落库重复 / 空回合误判")
        # 前端据 gm_retry 阶段清空思考框:事件本身照常转发
        self.assertTrue(any(e == "agent" and d.get("phase") == "gm_retry" for e, d in out))

    def test_fallback_notice_drops_failed_attempt_reasoning(self):
        ctx, _ = self._run([
            {"type": "reasoning", "text": "主渠道的思考"},
            {"type": "fallback_notice", "from_api_id": "a", "api_id": "b", "model": "m"},
            {"type": "reasoning", "text": "备用渠道的思考"},
        ])
        self.assertEqual(ctx.state.data.get("_turn_reasoning"), ["备用渠道的思考"])

    def test_retry_that_returns_nothing_is_upstream_empty(self):
        """重试那次上游什么都没给:应判 upstream_empty,不是 reasoning_only。"""
        ctx, _ = self._run([
            {"type": "reasoning", "text": "想了很久" * 20},
            {"type": "retry_notice", "attempt": 1, "max_retries": 2, "category": "upstream"},
        ])
        reason, _msg, facts = _empty_turn_diagnosis(ctx, 0)
        self.assertEqual(facts["reasoning_chars"], 0)
        self.assertEqual(reason, "upstream_empty")


if __name__ == "__main__":
    unittest.main()
