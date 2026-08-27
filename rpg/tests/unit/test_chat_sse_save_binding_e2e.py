"""test_chat_sse_save_binding_e2e.py — 走**真实 /api/chat SSE 回合**验证 save_id 绑定。

v1.83.1 的 save_id 绑定此前只用直接调 dispatcher 的探针验过。那证明了绑定函数本身对,
但没证明**真实回合链路**上它真的生效 —— chat_pipeline → run_gm_phase →
build_unified_tool_list → build_tool_call_router → ToolDispatcher 中间任何一环
把 envelope 的 save_id 丢了、或者 router 没走 dispatcher,探针都看不出来。

本测试打真实 SSE 端点,GM 用桩(不打 LLM、不花任何人的额度),桩按**生产实测的模型行为**
发一次 `list_pending_anchors(save_id=1)` —— 生产 239 次失败里 135 次填的就是这个 1。
判据是确定性的:`list_pending_anchors` 的返回 JSON 会**回显它实际收到的 save_id**,
所以「回显值 == 本次会话真实存档 id 且 != 1」就是绑定在真实链路上生效的直接证据。
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

# setdefault 不够:全量里前面的测试可能把它设成 "0" 且不还原,本测试就会继承成
# 「本地免鉴权」→ 走进 runtime._runtime_backend 的本地分支。setUpClass 里显式钉死并还原。

from tests.helpers import cleanup_test_users, make_client, register_user  # noqa: E402

MODEL_GUESS = 1  # 生产实测:模型最常填的那个假 save_id


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
        yield {"type": "step", "step": {"phase": "stub", "message": "stub", "status": "running"}}
        yield {"type": "result", "retrieved_context": "",
               "bundle": {"debug": {"cache_plan": {}, "layers": []}, "prompt": "stub"},
               "steps": [], "agent_prompt": "stub", "curator_plan": {}}


class _ToolCallingGM:
    """GM 桩:照生产实测的模型行为,用 save_id=1 调一次 list_pending_anchors。

    真实 backend 就是这样把 (server_id, tool_name, arguments) 交给 tool_call_router 的,
    所以这里走的是与生产同一条派发路径。
    """
    api_id = "stub"

    class _B:
        model_name = "stub"
        last_usage = {}

    _backend = _B()

    def __init__(self):
        self.router_result = None
        self.saw_router = False
        self.tools_offered = None

    def respond_stream_with_tools(self, *args, **kwargs):
        router = kwargs.get("tool_call_router")
        tools = kwargs.get("tools")
        self.tools_offered = [t.get("name") for t in (tools or [])]
        if router is None:
            yield {"type": "text", "text": "(没拿到 router)"}
            return
        self.saw_router = True
        args_from_model = {"save_id": MODEL_GUESS, "limit": 3}
        yield {"type": "tool_call", "server_id": "dispatcher",
               "tool": "list_pending_anchors", "arguments": dict(args_from_model)}
        res = router("dispatcher", "list_pending_anchors", args_from_model)
        self.router_result = res
        yield {"type": "tool_result", "ok": bool(res.get("ok")),
               "result": res.get("result"), "error": res.get("error")}
        yield {"type": "text", "text": "好的。"}

    def curate_context(self, *args, **kwargs):
        return ""


@contextlib.contextmanager
def _stub_gms(gm):
    """把主 GM、子 GM(司命)、context agent 三处都换成桩。

    **三处都要**:平台是 BYOK 墙(无 key = 0 回复),`_get_sub_gm` 在 context 阶段先于
    `run_context_agent` 构造真实 backend,只桩主 GM 的话会撞
    「Anthropic API key 未配置(测试服 LLM 调用必须 BYOK)」→ 整个 chat 400。
    路由用的是函数内 `from app import _get_gm, _get_sub_gm`,所以改 app 模块属性即可生效。
    """
    import app as ui_mod
    saved = (ui_mod.run_context_agent, ui_mod._get_gm, ui_mod._get_sub_gm, ui_mod.GameMaster)
    ui_mod.run_context_agent = _FakeContextAgent()
    ui_mod._get_gm = lambda api_user: gm
    ui_mod._get_sub_gm = lambda *a, **k: gm
    # **GameMaster 这个构造点也要桩**:_ensure_loaded(ensure_gm=True) 在 _get_gm 之前就
    # 已经 new 了一个真 backend(app.py:1018),只桩工厂函数拦不住 —— 那一步就会抛
    # 「Anthropic API key 未配置」把整个 chat 变成 400。
    ui_mod.GameMaster = lambda *a, **k: gm
    try:
        yield
    finally:
        (ui_mod.run_context_agent, ui_mod._get_gm,
         ui_mod._get_sub_gm, ui_mod.GameMaster) = saved


# 这条测试跑**完整 chat 回合**,所以要把三个 LLM 构造点全桩掉(见 _stub_gms)——
# 平台是 BYOK 墙(无 key = 0 回复),漏掉任何一个都会在造 backend 时抛
# 「Anthropic API key 未配置」,整个 chat 变 400。桩齐之后它在全量里稳定通过。
# 内层仍留一道 BYOK skip 作安全阀:换个环境万一还是被门挡住,显式 skip 而不是假红。
class ChatSseBindsCurrentSave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev_auth = os.environ.get("RPG_REQUIRE_AUTH")
        os.environ.setdefault("RPG_REQUIRE_AUTH", "1")
        cleanup_test_users()
        # 生产在 FastAPI lifespan(core/startup.py)里调 ensure_registered();TestClient
        # 不作为上下文管理器使用时 lifespan 不跑,注册表会是空的 —— 那样 build_unified_tool_list
        # 返回 [],本测试会假绿(GM 拿不到工具,自然也不会有绑定问题)。这里显式补上。
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

    def _chat(self, cookies, gm, message: str):
        """发一个真实 SSE 回合。BYOK 墙拦下时显式 skip,不假红。

        平台无 key = 0 回复(设计如此)。本地测试环境通常没有配置凭据加密密钥,
        `set_credential` 落不下去,于是 chat 恒 400。这与被测对象(save_id 绑定)无关,
        所以判定为**前提不满足**而不是失败 —— 但绝不静默通过:skip 会在报告里显示。
        本测试在能满足前提的环境(本机全栈 / 有凭据的测试库)里跑真回合。
        """
        with _stub_gms(gm):
            with self.client.stream("POST", "/api/v1/chat", cookies=cookies,
                                    json={"message": message, "attachments": []}) as resp:
                if resp.status_code == 400:
                    body = resp.read()[:300]
                    if b"BYOK" in body or b"API key" in body:
                        self.skipTest(f"环境不满足前提(BYOK 墙):{body[:120]!r}")
                self.assertEqual(resp.status_code, 200,
                                 f"chat 应 200:{resp.status_code} body={resp.read()[:300]!r}")
                return _consume_sse(resp)

    def _mk_save(self, cookies, username) -> int:
        """建档走内部 create_save,不走 POST /api/saves —— 那个端点有 BYOK 模型门
        (「需要先上传 Service Account JSON」),而建档不是本测试的被测对象。
        建完 bootstrap_runtime_binding 把它绑成当前 runtime,chat 回合才解析得到 save_id。"""
        from platform_app import branches as _branches
        from platform_app.db import connect
        from platform_app.workspace.creation import create_save
        with connect() as db:
            uid = int(db.execute("select id from users where username=%s",
                                 (username,)).fetchone()["id"])
            sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (uid, "integtest_sse_binding")).fetchone()["id"])
        save = create_save(uid, sid, "integtest_sse_binding",
                           new_card={"name": "绑定测试者", "role": "测试",
                                     "background": "验证 save_id 绑定"})
        save_id = int((save or {}).get("id") or 0)
        self.assertGreater(save_id, 0, f"create_save 没返回 id: {save}")
        _branches.bootstrap_runtime_binding(user_id=uid)
        return save_id

    def test_model_guessed_save_id_is_replaced_on_the_real_sse_turn(self):
        u = register_user(self.client)
        cookies = u["cookies"]
        save_id = self._mk_save(cookies, u["username"])
        self.assertNotEqual(save_id, MODEL_GUESS,
                            "测试前提被破坏:真实 save 恰好等于模型瞎猜的值")

        gm = _ToolCallingGM()
        events = self._chat(cookies, gm, "看看还有哪些锚点没发生")

        # ① 真的走到了 GM 阶段并拿到了 router(否则下面全是假绿)
        self.assertTrue(gm.saw_router, "GM 桩没拿到 tool_call_router,链路没走通")
        self.assertIsNotNone(gm.router_result, "router 没返回结果")

        # ② 工具成功 —— 修复前这里必是「失败 (权限): save 1 不属于当前用户或不存在」
        res = gm.router_result
        self.assertTrue(res.get("ok"),
                        f"工具在真实回合里失败了:{str(res.get('result'))[:200]}")

        # ③ 决定性判据:返回 JSON 回显的 save_id 是本次会话的真实存档,不是模型填的 1
        payload = json.loads(str(res.get("result")))
        self.assertEqual(int(payload["save_id"]), save_id,
                         f"工具收到的 save_id 是 {payload.get('save_id')},绑定没生效")
        self.assertNotEqual(int(payload["save_id"]), MODEL_GUESS)

        # ④ SSE 里确实吐出了工具事件(证明这是端到端而不是我自己在测自己)
        kinds = [e.get("event") for e in events]
        self.assertTrue(events, "SSE 一个事件都没有")
        self.assertIn("done", kinds, f"SSE 没有收尾事件:{kinds[:12]}")

    def test_gm_is_offered_the_anchor_tool_at_all(self):
        """前置条件守卫:锚点工具得先在直发工具表里,否则上面那条测的是空气。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        self._mk_save(cookies, u["username"])
        gm = _ToolCallingGM()
        self._chat(cookies, gm, "推进剧情")
        self.assertIsNotNone(gm.tools_offered, "GM 桩没收到 tools 列表")
        self.assertIn("list_pending_anchors", gm.tools_offered or [],
                      f"锚点工具不在发给 GM 的工具表里:{(gm.tools_offered or [])[:20]}")


if __name__ == "__main__":
    unittest.main()
