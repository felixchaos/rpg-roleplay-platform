"""
test_chat_field_contract.py — task 31 回归

复现：
  - Game Console.html 给 /api/chat 发 {"text": "/set ..."}（历史字段名）
  - 后端 ui.api_chat 只读 body.message → message="" → 立即 yield error{"message":"空消息"}
  - /set 完全没进 state；UI 因为 error event 又硬编码显示『请求中断：上游 504』，
    把字段契约错误误报成网络超时。

修复：
  - 后端 /api/chat 同时接受 message 与 text（message 优先）
  - 前端两字段都发（已在 Game Console.html 修）
  - 前端 on_error 拿 data.message（不是 data.detail），banner 显示真因
"""
from __future__ import annotations

import json
import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class ChatAcceptsBothMessageAndText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _consume(self, resp) -> list[dict]:
        events: list[dict] = []
        ev = "message"
        data_lines: list[str] = []
        for raw_line in resp.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if line == "":
                if data_lines:
                    try:
                        d = json.loads("\n".join(data_lines))
                    except Exception:
                        d = "\n".join(data_lines)
                    events.append({"event": ev, "data": d})
                ev = "message"; data_lines = []
                continue
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        return events

    def _patch_no_llm(self):
        """让 chat 不真打 LLM；只测 /set 写入 + SSE 序列"""
        import app as ui_mod

        def _fake_ctx(*a, **kw):
            yield {
                "type": "result",
                "retrieved_context": "",
                "bundle": {"debug": {"cache_plan": {}}, "prompt": "stub"},
                "steps": [],
                "agent_prompt": "stub",
                "curator_plan": {},
            }

        class _Stub:
            api_id = "stub"
            class _B:
                model_name = "stub"
                last_usage = {}
            _backend = _B()
            def respond_stream_with_tools(self, *a, **kw):
                if False:
                    yield {}
                return
            def curate_context(self, *a, **kw):
                return ""

        orig_rca = ui_mod.run_context_agent
        orig_get = ui_mod._get_gm
        ui_mod.run_context_agent = _fake_ctx
        ui_mod._get_gm = lambda u: _Stub()
        return ui_mod, orig_rca, orig_get

    def _restore(self, ui_mod, orig_rca, orig_get):
        ui_mod.run_context_agent = orig_rca
        ui_mod._get_gm = orig_get

    def test_text_field_is_accepted_like_message(self):
        """旧前端发 text 字段：后端必须当成 message 处理，不再吐'空消息'"""
        u = register_user(self.client)
        cookies = u["cookies"]
        ui_mod, orig_rca, orig_get = self._patch_no_llm()
        try:
            payload = {
                # 用历史 Game Console.html 的形状
                "text": "/set 当前位置改为雾港码头",
                "attachments": [],
                "model": "gpt-4o-mini-rpg",
                "command": None,
            }
            with self.client.stream("POST", "/api/chat", json=payload, cookies=cookies) as resp:
                self.assertEqual(resp.status_code, 200)
                events = self._consume(resp)
            names = [e["event"] for e in events]
            # 关键：不应有 error，应有 updates(stage=pre_llm)
            for ev in events:
                if ev["event"] == "error":
                    self.fail(f"task 31：text 字段应该被识别，不应 error；events={names} err={ev['data']}")
            pre = [e for e in events if e["event"] == "updates"
                   and isinstance(e["data"], dict) and e["data"].get("stage") == "pre_llm"]
            self.assertTrue(pre, f"应有 pre_llm updates；events={names}")

            # state 应该被写入
            s = self.client.get("/api/state", cookies=cookies).json() or {}
            loc = (s.get("player") or {}).get("current_location", "")
            self.assertEqual(loc, "雾港码头",
                f"task 31：text 字段下的 /set 应生效；loc={loc!r}")
        finally:
            self._restore(ui_mod, orig_rca, orig_get)

    def test_message_field_still_works(self):
        """对照：发 message 字段仍然工作（不应被 text fallback 破坏）"""
        u = register_user(self.client)
        cookies = u["cookies"]
        ui_mod, orig_rca, orig_get = self._patch_no_llm()
        try:
            payload = {"message": "/set 当前位置改为雾港灯塔", "attachments": []}
            with self.client.stream("POST", "/api/chat", json=payload, cookies=cookies) as resp:
                self.assertEqual(resp.status_code, 200)
                events = self._consume(resp)
            names = [e["event"] for e in events]
            for ev in events:
                if ev["event"] == "error":
                    self.fail(f"task 31：message 字段不应回归出错；events={names} err={ev['data']}")
            s = self.client.get("/api/state", cookies=cookies).json() or {}
            self.assertEqual((s.get("player") or {}).get("current_location"), "雾港灯塔")
        finally:
            self._restore(ui_mod, orig_rca, orig_get)

    def test_both_fields_message_takes_priority(self):
        """两个字段都发时 message 优先（避免歧义；前端兼容兜底也是这种形状）"""
        u = register_user(self.client)
        cookies = u["cookies"]
        ui_mod, orig_rca, orig_get = self._patch_no_llm()
        try:
            payload = {
                "message": "/set 当前位置改为优先位置",
                "text": "/set 当前位置改为兜底位置",
                "attachments": [],
            }
            with self.client.stream("POST", "/api/chat", json=payload, cookies=cookies) as resp:
                self.assertEqual(resp.status_code, 200)
                self._consume(resp)
            s = self.client.get("/api/state", cookies=cookies).json() or {}
            self.assertEqual((s.get("player") or {}).get("current_location"), "优先位置",
                "task 31：message 应优先于 text")
        finally:
            self._restore(ui_mod, orig_rca, orig_get)

    def test_truly_empty_still_returns_clean_error(self):
        """对照：两个字段都空时仍应返 '空消息'（行为不破坏）"""
        u = register_user(self.client)
        cookies = u["cookies"]
        payload = {"attachments": []}
        with self.client.stream("POST", "/api/chat", json=payload, cookies=cookies) as resp:
            events = self._consume(resp)
        err_events = [e for e in events if e["event"] == "error"]
        self.assertTrue(err_events, "真正空消息应返 error event")
        self.assertEqual(err_events[0]["data"].get("message"), "空消息")


if __name__ == "__main__":
    unittest.main(verbosity=2)
