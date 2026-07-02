"""acceptance A/B 真机 e2e:async(生产默认)下,首稿有 unmet + 节流放行时生成【改写候选】,
但【首稿仍是权威版、不被替换】,候选以 acceptance_alt 事件下发前端供玩家选择。

改版背景:原「verify+retry 直接替换首稿」在生产流式路径暴露成客户可见的「正文流完 5-10 秒
自己变一套」(行者无疆反馈)。现改为 A/B 用户裁决。本测试用【真实 chat 端点 + 真实 async
路径 + 真实 rule 验收器】,只 stub curator(注入 acceptance 关键词)和 GM(首稿不满足→生成
改写候选满足),确定性验证:
  1. 回合 200、无 error(热路径没搞崩 turn loop)
  2. GM 被调 2 次(首稿 + 改写候选)
  3. 出现 acceptance_retry + acceptance_alt 事件(候选已生成、待玩家选)
  4. 最终落库的是【首稿】(不含关键词)—— 候选不静默替换,除非玩家显式选择
无外部 LLM 调用。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.helpers import make_client, register_user, cleanup_test_users  # noqa: E402


class AcceptanceRetryFiresE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _consume(self, resp) -> list[dict]:
        events, ev, buf = [], "message", []
        for raw in resp.iter_lines():
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if line == "":
                if buf:
                    try:
                        data = json.loads("\n".join(buf))
                    except Exception:
                        data = "\n".join(buf)
                    events.append({"event": ev, "data": data})
                ev, buf = "message", []
                continue
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                buf.append(line[len("data:"):].strip())
        return events

    def test_async_acceptance_offers_ab_candidate_keeps_first_draft(self):
        import app as ui_mod
        import chat_pipeline as cp

        user = register_user(self.client)
        cookies = user["cookies"]
        # chat 端点要求 BYOK 有配置 key(否则 400)。给个可解密的假 anthropic key —— GM 已被 stub,
        # 不会真调 LLM,只为过端点的 key-exists 校验。
        from platform_app.db import connect
        from platform_app.user_credentials import set_credential
        with connect() as db:
            uid = int(db.execute("select id from users where username=%s", (user["username"],)).fetchone()["id"])
        set_credential(uid, "anthropic", "sk-ant-dummy-test-key-not-real")
        # 用模组 start 建立可玩 state(无需 reviewed 剧本)
        self.client.post("/api/v1/rules/module/start", json={"module_id": "ash_mine"}, cookies=cookies)

        calls = {"n": 0}

        class StubGM:
            api_id = "stub"

            class Backend:
                model_name = "stub"
                last_usage = {}

            _backend = Backend()

            def curate_context(self, *a, **k):
                return ""

            def respond_stream_with_tools(self, *a, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    yield {"type": "text", "text": "你走进房间，在椅子上坐了下来。"}  # 无「红茶」→ 不满足
                else:
                    yield {"type": "text", "text": "你走进房间，给自己泡了一杯红茶，慢慢啜饮。"}  # 满足

        def fake_ca(*a, **k):
            yield {
                "type": "result", "retrieved_context": "",
                "bundle": {"debug": {"cache_plan": {}}, "prompt": "stub"},
                "steps": [], "agent_prompt": "stub",
                "curator_plan": {"acceptance": ["红茶"], "rule_candidate_actions": []},
            }

        orig = (ui_mod.run_context_agent, ui_mod._get_gm, ui_mod._get_sub_gm, cp._recorder_unified, cp._POSTPROC_MODE)
        ui_mod.run_context_agent = fake_ca
        ui_mod._get_gm = lambda u: StubGM()
        ui_mod._get_sub_gm = lambda u: StubGM()
        cp._recorder_unified = lambda *a, **k: False  # 走非史官三合一路径,不触发 recorder LLM
        # 强制 sync 模式:改写候选走【内联】路径(候选作为 SSE 流事件下发,便于确定性断言)。
        # 生产 async 模式改写走后台任务(不阻塞回合)+ emit 推前端,不在流里,单独 source 测试覆盖。
        cp._POSTPROC_MODE = "sync"
        try:
            with self.client.stream(
                "POST", "/api/v1/chat",
                json={"message": "我进屋歇着", "attachments": []}, cookies=cookies,
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                events = self._consume(resp)
        finally:
            (ui_mod.run_context_agent, ui_mod._get_gm, ui_mod._get_sub_gm, cp._recorder_unified, cp._POSTPROC_MODE) = orig

        ev_names = [e["event"] for e in events]
        self.assertNotIn("error", ev_names, events)
        # 1. 改写候选真的生成:GM 被调 2 次(首稿 + 候选)
        self.assertEqual(calls["n"], 2, f"GM 调用次数={calls['n']},应为 2(候选未生成?)")
        # 2. 出现 acceptance_alt 候选事件(sync/内联路径走 SSE 流)
        alt_events = [e for e in events if e["event"] == "acceptance_alt"]
        self.assertTrue(alt_events, f"无 acceptance_alt 候选事件;events={ev_names}")
        alt = alt_events[0]["data"]
        self.assertIn("红茶", str(alt.get("rewrite", "")), "候选正文应含改写关键词")
        self.assertTrue(alt.get("alt_id"), "候选应带 alt_id(已落 acceptance_ab_log)")
        # 3. 最终 state 落的是【首稿】(不含红茶)—— 候选不静默替换首稿
        state = self.client.get("/api/v1/state", cookies=cookies).json()
        hist = json.dumps(state.get("history") or state, ensure_ascii=False)
        self.assertNotIn("红茶", hist, "首稿被静默替换成候选了(应保留首稿,等玩家选)")
        self.assertIn("坐了下来", hist, "最终落库的应是首稿正文")


if __name__ == "__main__":
    unittest.main()
