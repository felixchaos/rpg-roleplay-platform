"""Phase 3 不再拿 sub_gm._backend.last_usage 记「子代理」账。

没配 sub_agent_model_override 时 _get_sub_gm 直接复用主 GM 实例(sub_gm is gm)。curator 早已改走
_harness.call_agent_json(agent_kind=curator,自己记账),根本不碰这个 backend;last_usage 的清零点
在 Phase 4 的 GameMaster.respond_stream_with_tools 入口,Phase 3 读在它之前。于是第 N+1 回合
Phase 3 读到的是第 N 回合主 GM 的用量,又按 kind=sub_agent 记一遍 token_usage —— 每个非酒馆
回合的主 GM 花费被算两次。独立子代理实例同理:它的 backend 也从来不被 curator 用来发请求,
读到的只可能是陈旧值。
"""
from __future__ import annotations

import asyncio
import threading
import unittest

from chat_pipeline._common import PipelineContext
from chat_pipeline.rules import run_rules_phase
from state import GameState


class _Backend:
    def __init__(self, usage):
        self.model_name = "stub-model"
        self.last_usage = dict(usage)


class _GM:
    api_id = "stub"

    def __init__(self, usage):
        self._backend = _Backend(usage)


class _NoPolicy:
    def preflight(self, *_a, **_k):
        return None


async def _drain(agen):
    return [x async for x in agen]


class RulesPhaseDoesNotReRecordMainGmUsage(unittest.TestCase):
    def setUp(self):
        import game_policy
        import platform_app.usage as usage_mod
        self.recorded: list[dict] = []
        self._saved = (game_policy.get_game_policy, usage_mod.record_usage)
        game_policy.get_game_policy = lambda _state: _NoPolicy()
        usage_mod.record_usage = lambda **kw: self.recorded.append(kw) or {"id": 1}

    def tearDown(self):
        import game_policy
        import platform_app.usage as usage_mod
        game_policy.get_game_policy, usage_mod.record_usage = self._saved

    def _run(self, gm, sub_gm):
        ctx = PipelineContext(
            api_user={"id": 7}, state=GameState.new(), gm=gm, sub_gm=sub_gm,
            message_for_model="你好", run_id=1, stop_event=threading.Event(), chat_start_time=0.0,
        )
        ctx.agent_result = {"steps": [], "agent_prompt": "", "curator_plan": {}}
        ctx.bundle = {"debug": {"cache_plan": {}, "layers": []}, "prompt": "p"}
        ctx.ctx_text = ""
        asyncio.run(_drain(run_rules_phase(
            ctx,
            payload_fn=lambda u: {},
            persist_chat_turn=lambda *a, **k: None,
            persist_runtime_checkpoint=lambda *a, **k: None,
            resolve_persist_target=lambda u: (None, None),
            mark_context_run=lambda *a, **k: None,
            clarify_threshold=lambda u: 0.0,
            apply_chat_rule_candidates=lambda *a, **k: [],
            chat_rule_candidates=lambda *a, **k: [],
            rule_results_prompt=lambda *a, **k: "",
            platform_knowledge_mod=None,
        )))
        return ctx

    def test_shared_instance_leftover_usage_not_recorded(self):
        """sub_gm is gm,backend 里留着上一回合主 GM 的用量 → Phase 3 不写 token_usage。"""
        gm = _GM({"input_tokens": 12000, "output_tokens": 800, "total_tokens": 12800})
        self._run(gm, gm)
        self.assertEqual(self.recorded, [], "上一回合主 GM 的用量被按 sub_agent 又记了一遍")

    def test_separate_instance_stale_usage_not_recorded(self):
        """独立子代理实例的 backend 从不被 curator 用来发请求,读到的只能是陈旧值,同样不记。"""
        gm = _GM({})
        sub = _GM({"input_tokens": 500, "output_tokens": 50, "total_tokens": 550})
        self._run(gm, sub)
        self.assertEqual(self.recorded, [])


if __name__ == "__main__":
    unittest.main()
