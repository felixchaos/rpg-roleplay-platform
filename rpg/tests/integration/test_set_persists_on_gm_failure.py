"""
test_set_persists_on_gm_failure.py — task 27 回归

复现用户报告：
  /api/chat 收到 `/set ...` 后，原来流程是：
    1. apply_player_directives → 把 /set 改动写到 in-memory state
    2. run_context_agent + GM respond_stream_with_tools
    3. 全部跑完后才 _persist_chat_turn / runtime checkpoint 落盘
  上游 GM 504 / context_agent 抛异常 → 整轮 try 跳到 except → 第 3 步永远不
  执行 → /set 的 world.time / worldline.user_variables / player.current_location
  改动全部丢失。

修复：在 apply_player_directives 返回 non-empty 后立刻 _persist_runtime_checkpoint
+ 发 `updates` SSE 事件，让 /set 的硬改动先行落盘并通知 UI。后续 GM 失败也保
留这批改动。

测试做法：
  - 注册用户 → 拿基线 /api/state
  - monkeypatch ui.run_context_agent 让它 raise（模拟 504 / 上下文子代理崩溃）
  - POST /api/chat 发 `/set ...`，消费完 SSE（应见 error 事件）
  - GET /api/state，断言 /set 内容已落到 state
"""
from __future__ import annotations

import json
import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class SetPersistsBeforeGMCall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _consume_sse(self, resp) -> list[dict]:
        """把 SSE response 切成 [{event, data}, ...]，方便断言。"""
        events: list[dict] = []
        cur_event = "message"
        cur_data: list[str] = []
        for raw_line in resp.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if line == "":
                if cur_data:
                    try:
                        data = json.loads("\n".join(cur_data))
                    except Exception:
                        data = "\n".join(cur_data)
                    events.append({"event": cur_event, "data": data})
                cur_event = "message"
                cur_data = []
                continue
            if line.startswith("event:"):
                cur_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                cur_data.append(line[len("data:"):].strip())
        return events

    def test_set_persists_when_context_agent_raises(self):
        """
        关键回归：context_agent 在 /set 之后立刻抛异常（模拟 504），/set 的
        world.time / player.current_location / worldline.user_variables / memory
        全部应该已写回 state 并可通过 /api/state 看到。
        """
        u = register_user(self.client)
        cookies = u["cookies"]

        # 0) baseline
        r0 = self.client.get("/api/state", cookies=cookies)
        self.assertEqual(r0.status_code, 200, r0.text[:300])
        s0 = r0.json()
        original_time = (s0.get("world") or {}).get("time", "")
        original_loc = (s0.get("player") or {}).get("current_location", "")
        original_vars = ((s0.get("worldline") or {}).get("user_variables")) or {}

        # 1) monkeypatch ui.run_context_agent 让它 raise（模拟上游崩溃 / 504）
        import app as ui_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated upstream 504 / context_agent crash")
            yield  # pragma: no cover  让 Python 视它为 generator-compatible

        original_rca = ui_mod.run_context_agent
        ui_mod.run_context_agent = _boom
        try:
            # 2) POST /api/chat：/set 覆盖一组核心状态
            # 注意路径形态：
            #   - "当前时间=次日清晨"  → 走 _extract_set_time_targets → update_time
            #   - "当前位置改为雾港灯塔" → 走 _extract_location_override → update_location
            #   - "worldline.user_variables.身份暴露度=88%" → 走 apply_state_write，kind=user_variable
            #   - "world.weather=雾" → kind=scalar → _set_path 直接写
            payload = {
                "message": (
                    "/set 当前时间=次日清晨；"
                    "当前位置改为雾港灯塔；"
                    "worldline.user_variables.身份暴露度=88%；"
                    "world.weather=雾"
                ),
                "attachments": [],
            }
            with self.client.stream("POST", "/api/chat", json=payload, cookies=cookies) as resp:
                self.assertEqual(resp.status_code, 200, "chat 应回 200 SSE 流")
                events = self._consume_sse(resp)

            # 3) SSE 序列断言：必须先有 pre_llm 的 updates，再有 error
            event_names = [e["event"] for e in events]
            self.assertIn("updates", event_names, f"应包含 pre_llm 阶段的 updates 事件；got={event_names}")
            self.assertIn("error", event_names,
                f"context_agent 抛异常应触发 error 事件；got={event_names}")
            pre_llm_updates = [
                e for e in events
                if e["event"] == "updates" and isinstance(e["data"], dict)
                and e["data"].get("stage") == "pre_llm"
            ]
            self.assertTrue(pre_llm_updates,
                f"必须存在 stage=pre_llm 的 updates 事件；events={[(e['event'], e['data']) for e in events]}")
            items = pre_llm_updates[0]["data"].get("items") or []
            self.assertTrue(items, f"pre_llm updates.items 不应为空；got={pre_llm_updates[0]['data']}")
            # error 应该排在 pre_llm updates 之后
            self.assertGreater(event_names.index("error"), event_names.index("updates"),
                "error 应在 updates 之后；说明 /set 是先持久化再触发 LLM 的")
            # error.message 应包含我们模拟的崩溃信息（确认确实是 context_agent 抛了）
            err_events = [e for e in events if e["event"] == "error"]
            err_msg = ""
            if err_events and isinstance(err_events[0]["data"], dict):
                err_msg = err_events[0]["data"].get("message", "")
            self.assertIn("simulated upstream 504", err_msg,
                f"error.message 应反映 context_agent 抛的异常；实际 {err_msg!r}")

            # 4) GET /api/state：/set 的改动应该已落盘，跨进程内 state cache 重读也能看到
            r1 = self.client.get("/api/state", cookies=cookies)
            self.assertEqual(r1.status_code, 200, r1.text[:300])
            s1 = r1.json()

            new_time = (s1.get("world") or {}).get("time", "")
            new_loc = (s1.get("player") or {}).get("current_location", "")
            new_vars = ((s1.get("worldline") or {}).get("user_variables")) or {}
            new_weather = (s1.get("world") or {}).get("weather", "")

            # 时间应被 /set 覆盖
            self.assertEqual(new_time, "次日清晨",
                f"task 27：world.time 应被 /set 写为『次日清晨』；"
                f"实际 {new_time!r}（baseline={original_time!r}）")
            # 位置应被 /set 覆盖
            self.assertEqual(new_loc, "雾港灯塔",
                f"task 27：player.current_location 应被 /set 写为『雾港灯塔』；"
                f"实际 {new_loc!r}（baseline={original_loc!r}）")
            # weather 应被 /set 覆盖
            self.assertEqual(new_weather, "雾",
                f"task 27：world.weather 应被 /set 写为『雾』；实际 {new_weather!r}")
            # user_variables 应该多了一项 key=身份暴露度
            # （apply_state_write("worldline.user_variables.身份暴露度=88%") 走 user_variable 分支 → set_user_variable）
            self.assertIn("身份暴露度", new_vars,
                f"task 27：worldline.user_variables 应包含 key=身份暴露度；"
                f"before={list(original_vars.keys())} after={list(new_vars.keys())}")
            hit = new_vars.get("身份暴露度")
            # set_user_variable 把它包成 {turn, value, locked, source, updated_at}；兼容也允许纯字符串
            if isinstance(hit, dict):
                hit_value = str(hit.get("value", ""))
            else:
                hit_value = str(hit)
            self.assertEqual(hit_value, "88%",
                f"task 27：身份暴露度 value 应为 88%；实际 {hit!r}")
            # 还应该有一条 set_X_Y 总记录（apply_set_directive 把完整 directive 也存进 user_variables）
            self.assertTrue(any(str(k).startswith("set_") for k in new_vars.keys()),
                f"task 27：apply_set_directive 应额外存一条 set_X_Y 总记录；"
                f"keys={list(new_vars.keys())}")
        finally:
            ui_mod.run_context_agent = original_rca

    def test_set_without_failure_still_works(self):
        """
        对照：没人抛异常时，/set 也照旧应用（双路径都不该把状态弄丢）。
        这里仍 monkeypatch GM 流为一个空 streamer，避免真打 LLM；
        但 context_agent 用 stub 返回最小 result。
        """
        u = register_user(self.client)
        cookies = u["cookies"]

        import app as ui_mod

        # context_agent 返回一个最小可用的 result
        def _fake_ctx(*args, **kwargs):
            yield {"type": "step", "step": {"phase": "stub", "message": "stub", "status": "running"}}
            yield {
                "type": "result",
                "retrieved_context": "",
                "bundle": {"debug": {"cache_plan": {}}, "prompt": "stub prompt"},
                "steps": [],
                "agent_prompt": "stub",
                "curator_plan": {},
            }

        # GM 立刻 done，不流任何 token
        original_get_gm = ui_mod._get_gm

        class _StubGM:
            api_id = "stub"
            class _B:
                model_name = "stub"
                last_usage = {}
            _backend = _B()

            def respond_stream_with_tools(self, *args, **kwargs):
                if False:
                    yield {}
                return

            def curate_context(self, *args, **kwargs):
                # _get_sub_gm 会把这个方法当 llm_curator 传给 run_context_agent；
                # 我们已经 patch 掉 run_context_agent，所以它不会被调用，
                # 但 Python 在传参时会做 attribute lookup，所以必须存在。
                return ""

        def _fake_get_gm(api_user):
            return _StubGM()

        original_rca = ui_mod.run_context_agent
        ui_mod.run_context_agent = _fake_ctx
        ui_mod._get_gm = _fake_get_gm
        try:
            payload = {
                "message": "/set 当前位置改为试验台",
                "attachments": [],
            }
            with self.client.stream("POST", "/api/chat", json=payload, cookies=cookies) as resp:
                self.assertEqual(resp.status_code, 200)
                events = self._consume_sse(resp)
            event_names = [e["event"] for e in events]
            # 正常路径不应 error，应 done
            err_msg = ""
            for e in events:
                if e["event"] == "error" and isinstance(e["data"], dict):
                    err_msg = e["data"].get("message", "")
                    break
            self.assertNotIn("error", event_names,
                f"正常路径不应 error；got={event_names}; err={err_msg!r}")
            self.assertIn("done", event_names, f"正常路径应有 done；got={event_names}")
            r1 = self.client.get("/api/state", cookies=cookies)
            loc = (r1.json().get("player") or {}).get("current_location", "")
            self.assertEqual(loc, "试验台", f"正常路径下 /set 也应生效；loc={loc!r}")
        finally:
            ui_mod.run_context_agent = original_rca
            ui_mod._get_gm = original_get_gm


if __name__ == "__main__":
    unittest.main(verbosity=2)
