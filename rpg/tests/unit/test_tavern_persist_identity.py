"""test_tavern_persist_identity.py — 酒馆回合的落库身份不能是 None。

背景:ctx.persist_user_id / ctx.active_save_id 原本只在 run_rules_phase(Phase 3)末尾赋值,
而酒馆(tavern_gm)整段跳过 Phase 3 → 酒馆回合两者恒为 None。后果:
  · chat 的 token_usage 一条不记(_build_usage_payload 要 persist_user_id);
  · 打断落库 / 断连落库拿不到身份;
  · 空响应日志打出 save_id=None(巡检里那几条就是这么来的)。

修法:Phase 2(所有模式都经过)复用 Phase 1 已解析的 early_*。同时 _persist_chat_turn 对
酒馆确定性豁免 messages 表(酒馆 script_id 恒 NULL,ensure_game_session 必抛)与锚点/阶段块
(酒馆 manifest 不消费 phase digests,开阶段会改 prompt 前缀并按回合调 LLM)。
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import sys
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from chat_pipeline._common import PipelineContext  # noqa: E402
from chat_pipeline.context import run_context_phase  # noqa: E402
from state import GameState  # noqa: E402


def _stub_context_agent(*args, **kwargs):
    yield {"type": "result", "retrieved_context": "",
           "bundle": {"debug": {"cache_plan": {}, "layers": []}, "prompt": "stub"},
           "steps": [], "agent_prompt": "stub", "curator_plan": {}}


class _StubGM:
    api_id = "stub"

    class _B:
        model_name = "stub"
        last_usage: dict = {}

    _backend = _B()


def _mk_ctx() -> PipelineContext:
    return PipelineContext(
        api_user={"id": 7}, state=GameState.new(), gm=_StubGM(), sub_gm=_StubGM(),
        message_for_model="你好", run_id=1, stop_event=threading.Event(), chat_start_time=0.0,
    )


async def _drain(agen):
    return [x async for x in agen]


class ContextPhaseSetsPersistIdentity(unittest.TestCase):
    def _run(self, ctx):
        asyncio.run(_drain(run_context_phase(
            ctx,
            resolve_persist_target=lambda u: (_ for _ in ()).throw(AssertionError("不该再解析一次")),
            payload_fn=lambda u: {},
            active_script_id=lambda u: None,
            clarify_threshold=lambda u: 0.0,
            persist_chat_turn=lambda *a, **k: None,
            mark_context_run=lambda *a, **k: None,
            apply_chat_rule_candidates=lambda *a, **k: [],
            chat_rule_candidates=lambda *a, **k: [],
            rule_results_prompt=lambda *a, **k: "",
            persist_runtime_checkpoint=lambda *a, **k: None,
            platform_knowledge_mod=None,
            run_context_agent_fn=_stub_context_agent,
        )))

    def test_identity_copied_from_phase1_without_extra_io(self):
        ctx = _mk_ctx()
        ctx.early_persist_user_id, ctx.early_active_save_id = 7, 42
        self._run(ctx)
        self.assertFalse(ctx.early_return)
        self.assertEqual(ctx.persist_user_id, 7)
        self.assertEqual(ctx.active_save_id, 42)

    def test_unresolved_early_identity_stays_none(self):
        """Phase 1 没解析到(未登录的服务器模式)→ 仍是 None,不凭空造身份。"""
        ctx = _mk_ctx()
        self._run(ctx)
        self.assertIsNone(ctx.persist_user_id)
        self.assertIsNone(ctx.active_save_id)


class _FakeState:
    """_persist_chat_turn 只用到 data / record_turn / save。"""

    def __init__(self, data):
        self.data = data

    def record_turn(self, *a, **k):
        pass

    def save(self):
        pass


class PersistChatTurnTavernExemption(unittest.TestCase):
    def setUp(self):
        import app as ui_mod
        import save_phase_manager as spm
        self.app = ui_mod
        self.calls: list[str] = []
        self._saved_app = (ui_mod.platform_branches.record_runtime_turn,
                           ui_mod.platform_knowledge.record_turn_messages)
        ui_mod.platform_branches.record_runtime_turn = lambda *a, **k: {"ok": True}
        ui_mod.platform_knowledge.record_turn_messages = lambda *a, **k: self.calls.append("messages")
        self._spm = spm
        self._saved_spm = {n: getattr(spm, n) for n in (
            "upsert_timeline_anchor", "ensure_active_phase", "update_phase_turn_end",
            "detect_phase_boundary", "open_new_phase")}
        spm.upsert_timeline_anchor = lambda **k: self.calls.append("anchor")
        spm.ensure_active_phase = lambda *a, **k: self.calls.append("ensure_phase")
        spm.update_phase_turn_end = lambda *a, **k: self.calls.append("phase_end")
        spm.detect_phase_boundary = lambda *a, **k: True
        spm.open_new_phase = lambda **k: self.calls.append("open_phase")

    def tearDown(self):
        (self.app.platform_branches.record_runtime_turn,
         self.app.platform_knowledge.record_turn_messages) = self._saved_app
        for n, fn in self._saved_spm.items():
            setattr(self._spm, n, fn)

    def _persist(self, data):
        self.app._persist_chat_turn(
            {"id": 7}, _FakeState(data), "你好", "回复",
            persist_user_id=7, active_save_id=42,
        )

    def test_tavern_turn_skips_messages_and_phase_block(self):
        from context_providers.registry import DEFAULT_TAVERN_MANIFEST
        self._persist({"turn": 3, "content_pack": copy.deepcopy(DEFAULT_TAVERN_MANIFEST), "tavern": {}})
        self.assertEqual(self.calls, [], f"酒馆回合不该写 messages / 锚点 / 阶段:{self.calls}")

    def test_non_tavern_turn_unchanged(self):
        self._persist({"turn": 3, "world": {"timeline": {"current_label": "第1章"}}})
        self.assertEqual(self.calls, ["messages", "anchor", "ensure_phase", "phase_end", "open_phase"])


# ── 真实 /api/chat SSE 回合:酒馆 chat 开始写 token_usage ────────────────────────
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


class _TextGM:
    api_id = "stub"

    class _B:
        model_name = "stub"
        last_usage: dict = {}

    _backend = _B()

    def __init__(self, chunks):
        self.chunks = list(chunks)

    def respond_stream_with_tools(self, *args, **kwargs):
        for c in self.chunks:
            yield {"type": "text", "text": c}


class _FakeContextAgent:
    def __call__(self, *args, **kwargs):
        yield from _stub_context_agent()


@contextlib.contextmanager
def _stub_gms(gm):
    """主 GM / 子 GM / GameMaster 构造点 / context agent 四处都桩(BYOK 墙,漏一处就 400)。"""
    import app as ui_mod
    saved = (ui_mod.run_context_agent, ui_mod._get_gm, ui_mod._get_sub_gm, ui_mod.GameMaster)
    ui_mod.run_context_agent = _FakeContextAgent()
    ui_mod._get_gm = lambda api_user: gm
    ui_mod._get_sub_gm = lambda *a, **k: gm
    ui_mod.GameMaster = lambda *a, **k: gm
    try:
        yield
    finally:
        (ui_mod.run_context_agent, ui_mod._get_gm,
         ui_mod._get_sub_gm, ui_mod.GameMaster) = saved


class TavernChatTurnRecordsUsage(unittest.TestCase):
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

    def _tavern_save(self, username) -> tuple[int, int]:
        from platform_app import branches as _branches
        from platform_app.db import connect
        from platform_app.workspace.creation import create_tavern_save
        with connect() as db:
            uid = int(db.execute("select id from users where username=%s", (username,)).fetchone()["id"])
        save = create_tavern_save(uid, None)
        sid = int(save["id"])
        _branches.activate_save(uid, sid)
        return uid, sid

    def _chat(self, cookies, gm, message):
        with _stub_gms(gm):
            with self.client.stream("POST", "/api/v1/chat", cookies=cookies,
                                    json={"message": message, "attachments": []}) as resp:
                if resp.status_code == 400:
                    body = resp.read()[:300]
                    if b"BYOK" in body or b"API key" in body:
                        self.skipTest(f"环境不满足前提(BYOK 墙):{body[:120]!r}")
                self.assertEqual(resp.status_code, 200, resp.read()[:300])
                return _consume_sse(resp)

    def _chat_usage_rows(self, sid):
        from platform_app.db import connect
        with connect() as db:
            return db.execute(
                "select user_id, save_id, metadata from token_usage "
                "where save_id=%s and scenario='chat' order by id", (sid,)).fetchall()

    def test_tavern_turn_writes_chat_token_usage(self):
        u = register_user(self.client)
        uid, sid = self._tavern_save(u["username"])
        events = self._chat(u["cookies"], _TextGM(["她抬起头,笑了一下。"]), "你好呀")
        kinds = [e.get("event") for e in events]
        self.assertIn("done", kinds)
        self.assertNotIn("error", kinds, [e for e in events if e.get("event") == "error"])
        rows = self._chat_usage_rows(sid)
        self.assertEqual(len(rows), 1, "酒馆回合应写一条 chat token_usage(修复前恒 0 条)")
        self.assertEqual(int(rows[0]["user_id"]), uid)


if __name__ == "__main__":
    unittest.main()
