"""三个结构化 JSON 微任务子代理接 no_think + 空正文护栏回归测试(268 实锤族,2026-07-10)。

黑天鹅 / 阶段摘要 / 世界心跳都是「一次 LLM 出结构化 JSON」的微任务,思考模型会
无界思考吃光 max_tokens 正文恒空。三处已从 call_agent_json 换成 call_agent_json_guarded
并追加 no_think=True + log_tag。

拦截口径统一在 harness 边界:调用方走 guarded,guarded 内部引用模块全局 call_agent_json,
所以 monkeypatch.setattr(_harness, "call_agent_json", fake) 即可拦到真实调用链(顺带验证
护栏在真实调用方生效)。
"""

from agents import _harness

# ── 黑天鹅 black_swan ────────────────────────────────────────────

def test_black_swan_single_shot_passes_no_think_and_schema(monkeypatch):
    """_make_harness_caller(use_tool_loop=False) → _call_single_shot 走 guarded,
    第一跳请求体带 no_think=True 且强 tool_schema 原样透传;合法 JSON → 返回 dict。"""
    from agents import black_swan_agent as B

    monkeypatch.setattr(_harness, "resolve_api_and_model", lambda *a, **k: ("relay", "m"))
    captured = {}

    def fake(*args, **kw):
        captured.update(kw)
        return ('{"event_kind": "no_op", "summary": "x"}', {"output_tokens": 10})

    monkeypatch.setattr(_harness, "call_agent_json", fake)

    schema = {
        "name": "propose_black_swan_event",
        "description": "x",
        "input_schema": {"type": "object", "properties": {}},
    }
    caller = B._make_harness_caller(user_id=1, use_tool_loop=False)
    assert caller is not None
    out = caller({}, schema)

    assert captured.get("no_think") is True
    assert captured.get("tool_schema") == schema
    assert captured.get("max_tokens") == 600
    assert isinstance(out, dict) and out.get("event_kind") == "no_op"


# ── 阶段摘要 phase_digest ────────────────────────────────────────

def test_phase_digest_passes_no_think_and_parses(monkeypatch):
    """_call_llm_with_retry 首跳走 guarded,带 no_think=True;合法 JSON 正常解析。"""
    from agents import phase_digest_agent as P

    captured = []

    def fake(*args, **kw):
        captured.append(kw)
        return ('{"ok": 1}', {"output_tokens": 20})

    monkeypatch.setattr(_harness, "call_agent_json", fake)

    parsed, _usage = P._call_llm_with_retry(
        "sys", "user", api_id="relay", model="m", user_id=1,
    )
    assert parsed == {"ok": 1}
    assert len(captured) == 1
    assert captured[0].get("no_think") is True
    assert captured[0].get("max_tokens") == 2400


def test_phase_digest_empty_body_triggers_budget_retry(monkeypatch):
    """首跳空正文 → 护栏在真实调用方扩预算重试一次(2400→4800),第二跳合法 JSON → 解析成功。
    验证空正文护栏在 phase_digest 真实链路上生效(捕获 max_tokens 序列 [2400, 4800])。"""
    from agents import phase_digest_agent as P

    mt_seq = []
    outputs = iter([
        ("", {"reasoning_tokens": 2400}),        # 首跳:思考吃光,正文空
        ('{"phase": 1}', {"output_tokens": 30}),  # 扩预算重试:拿到正文
    ])

    def fake(*args, **kw):
        mt_seq.append(kw.get("max_tokens"))
        return next(outputs)

    monkeypatch.setattr(_harness, "call_agent_json", fake)

    parsed, _usage = P._call_llm_with_retry(
        "sys", "user", api_id="relay", model="m", user_id=1,
    )
    # 护栏在 guarded 内部完成 空→扩预算 重试,phase_digest 自身的第二次修复调用未被触及
    assert mt_seq == [2400, 4800], f"预期护栏扩预算序列 [2400, 4800],实得 {mt_seq}"
    assert parsed == {"phase": 1}


# ── 世界心跳 world_heartbeat ─────────────────────────────────────

class _State:
    def __init__(self, data):
        self.data = data


def test_world_heartbeat_passes_no_think(monkeypatch):
    """run_heartbeat_tick 走 guarded,带 no_think=True;合法 JSON 数组通过确定性验收后写入。"""
    from agents import recorder as R
    from agents import world_heartbeat as W

    monkeypatch.setattr(R, "_resolve_recorder_api_and_model", lambda *a, **k: ("relay", "m"))
    captured = {}

    def fake(*args, **kw):
        captured.update(kw)
        return ('["世界某处发生了一件八字以上的事件"]', {"output_tokens": 15})

    monkeypatch.setattr(_harness, "call_agent_json", fake)

    state = _State({"turn": 5, "player": {"name": "Alice"}})
    written = W.run_heartbeat_tick(state, user_id=1)

    assert captured.get("no_think") is True
    assert captured.get("max_tokens") == 400
    # 合法数组 → 验收通过 → 写入 background_events
    assert written == ["世界某处发生了一件八字以上的事件"]
    assert state.data["background_events"][0]["text"] == "世界某处发生了一件八字以上的事件"
