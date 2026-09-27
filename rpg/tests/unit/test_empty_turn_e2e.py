"""test_empty_turn_e2e.py — 真实 /api/chat SSE 回合上的空回合处理(巡检 F5)。

GM 用桩(不打 LLM),桩照生产最常见的形态:思考流吃光 4096 的单轮输出上限、finish_reason=length、
正文为空。断言整条链路:
  · 玩家拿到的 error 带 reason=reasoning_exhausted,文案不再是「安全过滤 / 换个说法」;
  · token_usage 落了一条 chat 行,metadata 带 empty_response / reason(生产可按 reason 统计);
  · 空回合不入队后处理、不跑史官(不白花玩家 token);对照组:有正文的回合照常入队。
  · 酒馆存档同样落库(身份修复后),但不发 usage SSE。
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.helpers import cleanup_test_users, make_client, register_user  # noqa: E402


def _consume_sse(resp) -> list[dict]:
    events, cur = [], {"event": None, "data": ""}
    for raw in resp.iter_lines():
        line = raw if isinstance(raw, str) else raw.decode("utf-8")
        if not line:
            if cur["event"]:
                try:
                    cur["data"] = json.loads(cur["data"]) if cur["data"] else None
                except json.JSONDecodeError:
                    pass
                events.append(dict(cur))
            cur = {"event": None, "data": ""}
            continue
        if line.startswith("event:"):
            cur["event"] = line[6:].strip()
        elif line.startswith("data:"):
            cur["data"] += line[5:].strip()
    if cur["event"]:
        events.append(cur)
    return events


class _FakeContextAgent:
    def __call__(self, *args, **kwargs):
        yield {"type": "result", "retrieved_context": "",
               "bundle": {"debug": {"cache_plan": {}, "layers": []}, "prompt": "stub"},
               "steps": [], "agent_prompt": "stub", "curator_plan": {}}


class _GM:
    """桩:text 为空时模拟「思考吃光上限」;非空时正常出正文。"""
    api_id = "stub"

    def __init__(self, text: str):
        self.text = text

        class _B:
            model_name = "stub-thinking"
            last_usage: dict = {}
        self._backend = _B()

    def respond_stream_with_tools(self, *args, **kwargs):
        self._backend.last_usage = {"input_tokens": 1500, "output_tokens": 4096, "reasoning_tokens": 4000,
                                    "total_tokens": 5596, "finish_reason": "length" if not self.text else "stop",
                                    "max_tokens": 4096}
        yield {"type": "reasoning", "text": "让我想想这一段该怎么写……"}
        if self.text:
            yield {"type": "text", "text": self.text}


@contextlib.contextmanager
def _stubs(gm, enqueued: list, recorded: list):
    import app as ui_mod
    import gm_serving.recorder_bridge as rb
    import platform_app.postproc_queue as pq
    saved = (ui_mod.run_context_agent, ui_mod._get_gm, ui_mod._get_sub_gm, ui_mod.GameMaster,
             pq.enqueue_postproc, rb.run_unified_recorder)
    ui_mod.run_context_agent = _FakeContextAgent()
    ui_mod._get_gm = lambda api_user: gm
    ui_mod._get_sub_gm = lambda *a, **k: gm
    ui_mod.GameMaster = lambda *a, **k: gm
    pq.enqueue_postproc = lambda *a, **k: enqueued.append(k.get("gm_output")) or 0
    rb.run_unified_recorder = lambda *a, **k: recorded.append(a[1] if len(a) > 1 else "") or {"ops": []}
    try:
        yield
    finally:
        (ui_mod.run_context_agent, ui_mod._get_gm, ui_mod._get_sub_gm, ui_mod.GameMaster,
         pq.enqueue_postproc, rb.run_unified_recorder) = saved


class EmptyTurnOnRealSse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev_auth = os.environ.get("RPG_REQUIRE_AUTH")
        os.environ["RPG_REQUIRE_AUTH"] = "1"
        cleanup_test_users()
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()
        if cls._prev_auth is None:
            os.environ.pop("RPG_REQUIRE_AUTH", None)
        else:
            os.environ["RPG_REQUIRE_AUTH"] = cls._prev_auth

    def _uid(self, username) -> int:
        from platform_app.db import connect
        with connect() as db:
            return int(db.execute("select id from users where username=%s", (username,)).fetchone()["id"])

    def _game_save(self, username) -> int:
        from platform_app import branches as _branches
        from platform_app.db import connect
        from platform_app.workspace.creation import create_save
        uid = self._uid(username)
        with connect() as db:
            sid = int(db.execute("insert into scripts(owner_id, title) values (%s,%s) returning id",
                                 (uid, "integtest_empty_turn")).fetchone()["id"])
        save = create_save(uid, sid, "integtest_empty_turn",
                           new_card={"name": "测试者", "role": "测试", "background": "空回合"})
        _branches.bootstrap_runtime_binding(user_id=uid)
        return int(save["id"])

    def _tavern_save(self, username) -> int:
        from platform_app import branches as _branches
        from platform_app.workspace.creation import create_tavern_save
        uid = self._uid(username)
        sid = int(create_tavern_save(uid, None)["id"])
        _branches.activate_save(uid, sid)
        return sid

    def _chat(self, cookies, gm, enqueued, recorded, message="继续推进剧情"):
        with _stubs(gm, enqueued, recorded):
            with self.client.stream("POST", "/api/v1/chat", cookies=cookies,
                                    json={"message": message, "attachments": []}) as resp:
                if resp.status_code == 400:
                    body = resp.read()[:300]
                    if b"BYOK" in body or b"API key" in body:
                        self.skipTest(f"环境不满足前提(BYOK 墙):{body[:120]!r}")
                self.assertEqual(resp.status_code, 200, resp.read()[:300])
                return _consume_sse(resp)

    def _usage_rows(self, sid):
        from platform_app.db import connect
        with connect() as db:
            return db.execute("select output_tokens, reasoning_tokens, metadata from token_usage "
                              "where save_id=%s and scenario='chat' order by id", (sid,)).fetchall()

    def test_game_empty_turn(self):
        u = register_user(self.client)
        sid = self._game_save(u["username"])
        enqueued, recorded = [], []
        events = self._chat(u["cookies"], _GM(""), enqueued, recorded)
        kinds = [e["event"] for e in events]
        errs = [e["data"] for e in events if e["event"] == "error"]
        self.assertEqual(len(errs), 1, kinds)
        self.assertEqual(errs[0]["kind"], "empty_response")
        self.assertEqual(errs[0]["reason"], "reasoning_exhausted")
        self.assertIn("4096", errs[0]["message"])
        self.assertNotIn("安全过滤", errs[0]["message"])
        # 空回合不再先弹「截断了,可以说继续」
        self.assertFalse([e for e in events if e["event"] == "agent"
                          and (e["data"] or {}).get("phase") == "stop_reason"])
        self.assertIn("usage", kinds)
        self.assertLess(kinds.index("usage"), kinds.index("error"))
        self.assertEqual(enqueued, [], "空回合不该入队后处理")
        self.assertEqual(recorded, [], "空回合不该跑史官")
        rows = self._usage_rows(sid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["reasoning_tokens"]), 4000)
        self.assertEqual(rows[0]["metadata"].get("empty_response"), True)
        self.assertEqual(rows[0]["metadata"].get("reason"), "reasoning_exhausted")

        # 对照组:有正文的回合照常入队(证明上面的「没入队」不是桩没接上)
        events2 = self._chat(u["cookies"], _GM("夜色渐深,远处传来钟声。"), enqueued, recorded, message="走吧")
        self.assertNotIn("error", [e["event"] for e in events2])
        self.assertTrue(enqueued, "非空回合应入队后处理")

    def test_tavern_empty_turn(self):
        u = register_user(self.client)
        sid = self._tavern_save(u["username"])
        enqueued, recorded = [], []
        events = self._chat(u["cookies"], _GM(""), enqueued, recorded, message="你好呀")
        kinds = [e["event"] for e in events]
        errs = [e["data"] for e in events if e["event"] == "error"]
        self.assertEqual([x["reason"] for x in errs], ["reasoning_exhausted"])
        self.assertNotIn("usage", kinds, "酒馆页会把这条用量挂到上一条 GM 回复下面")
        self.assertEqual(enqueued, [])
        rows = self._usage_rows(sid)
        self.assertEqual(len(rows), 1, "酒馆空回合也要落库(修复前酒馆 chat 一条不记)")
        self.assertEqual(rows[0]["metadata"].get("reason"), "reasoning_exhausted")


if __name__ == "__main__":
    unittest.main()
