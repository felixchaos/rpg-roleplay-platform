"""批次4 真机 e2e:async(生产默认)下 acceptance retry 真的会触发并落最终稿。

行者无疆流水线审计后 v1.32.9 把 verify+retry 抽成 _acceptance_gate 闭包,async 两处
early-return 前用 to_thread 调它。本测试用【真实 chat 端点 + 真实 async 路径 + 真实
rule 验收器】,只 stub 掉 curator(注入 acceptance 关键词)和 GM(第一稿不满足→触发
retry→第二稿满足),确定性验证:
  1. 回合 200、无 error(我的热路径改动没搞崩 turn loop)
  2. GM 被调 2 次(retry 真的 fire 了)
  3. 出现 acceptance_retry 事件
  4. 最终落库的是第二稿(含关键词)
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

    def test_async_acceptance_retry_fires_and_applies_second_draft(self):
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

        orig = (ui_mod.run_context_agent, ui_mod._get_gm, ui_mod._get_sub_gm, cp._recorder_unified)
        ui_mod.run_context_agent = fake_ca
        ui_mod._get_gm = lambda u: StubGM()
        ui_mod._get_sub_gm = lambda u: StubGM()
        cp._recorder_unified = lambda *a, **k: False  # 走非史官三合一 async 路径(site 2),不触发 recorder LLM
        try:
            with self.client.stream(
                "POST", "/api/v1/chat",
                json={"message": "我进屋歇着", "attachments": []}, cookies=cookies,
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                events = self._consume(resp)
        finally:
            (ui_mod.run_context_agent, ui_mod._get_gm, ui_mod._get_sub_gm, cp._recorder_unified) = orig

        ev_names = [e["event"] for e in events]
        self.assertNotIn("error", ev_names, events)
        # 1. retry 真的 fire:GM 被调 2 次
        self.assertEqual(calls["n"], 2, f"GM 调用次数={calls['n']},应为 2(retry 未触发?)")
        # 2. 出现 acceptance_retry agent 事件
        agent_phases = [e["data"].get("phase") for e in events
                        if e["event"] == "agent" and isinstance(e["data"], dict)]
        self.assertIn("acceptance_retry", agent_phases, f"无 acceptance_retry 事件;phases={agent_phases}")
        # 3. 最终 state 落的是第二稿(含红茶)
        state = self.client.get("/api/v1/state", cookies=cookies).json()
        hist = json.dumps(state.get("history") or state, ensure_ascii=False)
        self.assertIn("红茶", hist, "最终落库的不是 retry 第二稿")


if __name__ == "__main__":
    unittest.main()
