"""GM 三后端(openai_compat / vertex / anthropic)五项不对称修复回归。

审计发现三后端在「结构化 no_think / 截断信号 / 流式思考 / 阶梯化收敛 / 403 可分类性」上各有
不对称。本测试锁死每项修复:

  修1  vertex 结构化路径 no_think 生效(此前只 openai 分支消费 no_think,vertex call_structured
       硬编码 thinking_budget=high)。
  修2  截断信号 finish_reason 三后端都采(此前只 openai;anthropic/vertex 静默)。
  修3  见 stream 层(此处只覆盖归一 helper 的确定性部分,流式事件走人工验证)。
  修4  openai_compat 阶梯化收敛到 _tiered(load_tools 目录/解析单一真源)——真实酒馆工具表跑
       load→call 闭环。
  修5  vertex call() 403 转换附 status_code → classify_provider_error 归类 auth(此前裸
       RuntimeError,分类落空走「请重试」泛化兜底)。

mock 风格对齐 test_tiered_tools.py(__new__ 跳过需 API key 的 __init__ + 脚本化 client)。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RPG_DEPLOYMENT_MODE", "local")


# ── 假 Vertex genai 客户端(捕获发出的 GenerateContentConfig) ──────────────

class _FakeModels:
    def __init__(self):
        self.captured: dict = {}

    def generate_content(self, *, model, contents, config):
        self.captured["config"] = config
        self.captured["model"] = model

        class _Resp:
            text = "{}"
            usage_metadata = None
            candidates: list = []

        return _Resp()


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def _make_vertex(user_id=None, model="gemini-3.5-flash"):
    from agents.gm.backends.vertex import _VertexBackend
    be = _VertexBackend.__new__(_VertexBackend)  # 跳过 __init__(无需 SA)
    be.user_id = user_id
    be.model_name = model
    be.last_usage = {}
    be._unavailable_message = ""
    be.client = _FakeClient()
    return be


# ── 修1:vertex 结构化 no_think → thinking_budget=0 ────────────────────────

def test_call_structured_no_think_zero_budget():
    """call_structured(thinking_budget=0) → 发出的 config.thinking_config.thinking_budget == 0。"""
    be = _make_vertex()
    be.call_structured("sys", [{"role": "user", "content": "hi"}], max_tokens=400, thinking_budget=0)
    cfg = be.client.models.captured["config"]
    assert cfg.thinking_config.thinking_budget == 0


def test_call_structured_default_uses_resolved_budget(monkeypatch):
    """thinking_budget=None(默认)→ 沿用用户 effort 偏好(现行为不变)。"""
    import agents.gm.backends.vertex as V
    monkeypatch.setattr(V, "_resolve_thinking_budget", lambda uid, mid: 4096)
    be = _make_vertex()
    be.call_structured("sys", [{"role": "user", "content": "hi"}], max_tokens=400)
    cfg = be.client.models.captured["config"]
    assert cfg.thinking_config.thinking_budget == 4096


def test_harness_vertex_structured_threads_no_think(monkeypatch):
    """call_agent_json("vertex_ai", no_think=True) → call_structured 收到 thinking_budget=0;
    no_think=False → thinking_budget=None(沿用现行为)。与「call_agent_json 必须 no_think」对齐。"""
    import agents.gm as gm_mod
    from agents import _harness

    captured: dict = {}

    class _FakeVB:
        def __init__(self, model, user_id=None):
            self.client = object()  # 非 None → 通过可用性检查
            self.last_usage = {"input_tokens": 1, "output_tokens": 1}
            self.model_name = model

        def call_structured(self, system, messages, max_tokens, thinking_budget=None):
            captured["thinking_budget"] = thinking_budget
            return "{}"

    monkeypatch.setattr(gm_mod, "_VertexBackend", _FakeVB)

    _harness.call_agent_json("vertex_ai", "gemini-x", "sys", "user", None, no_think=True, max_tokens=400)
    assert captured["thinking_budget"] == 0

    _harness.call_agent_json("vertex_ai", "gemini-x", "sys", "user", None, no_think=False, max_tokens=400)
    assert captured["thinking_budget"] is None


# ── 修2:截断信号 finish_reason 归一 ───────────────────────────────────────

def test_vertex_finish_reason_normalized():
    import agents.gm.backends.vertex as V

    def _resp(fr):
        cand = type("C", (), {"finish_reason": fr})()
        return type("R", (), {"candidates": [cand]})()

    assert V._finish_reason_normalized(_resp("MAX_TOKENS")) == "length"
    assert V._finish_reason_normalized(_resp("STOP")) == "STOP"
    # enum 形态(带 .name)
    enum_like = type("E", (), {"name": "MAX_TOKENS", "__bool__": lambda self: True})()
    assert V._finish_reason_normalized(_resp(enum_like)) == "length"
    # "FinishReason.STOP" 字符串前缀剥离
    assert V._finish_reason_normalized(_resp("FinishReason.STOP")) == "STOP"
    # 无候选 → None
    assert V._finish_reason_normalized(type("R", (), {"candidates": []})()) is None


def test_vertex_capture_usage_writes_finish_reason():
    """_capture_usage 从 candidates[0].finish_reason 补 finish_reason(MAX_TOKENS→length)。"""
    be = _make_vertex()
    meta = type("M", (), {
        "prompt_token_count": 10, "candidates_token_count": 5,
        "cached_content_token_count": 0, "thoughts_token_count": 2,
        "total_token_count": 17,
    })()
    cand = type("C", (), {"finish_reason": "MAX_TOKENS"})()
    resp = type("R", (), {"usage_metadata": meta, "candidates": [cand]})()
    be._capture_usage(resp)
    assert be.last_usage["finish_reason"] == "length"
    assert be.last_usage["reasoning_tokens"] == 2


def test_anthropic_normalize_stop_reason():
    import agents.gm.backends.anthropic as A
    assert A._normalize_stop_reason("max_tokens") == "length"
    assert A._normalize_stop_reason("end_turn") == "end_turn"
    assert A._normalize_stop_reason("tool_use") == "tool_use"
    assert A._normalize_stop_reason("stop_sequence") == "stop_sequence"
    assert A._normalize_stop_reason(None) is None
    assert A._normalize_stop_reason("") is None


# ── 修5:vertex call() 403 → 附 status_code → classify 为 auth ─────────────

def test_vertex_call_403_attaches_status_code_and_classifies_auth():
    from agents.provider_errors import classify_provider_error
    be = _make_vertex(user_id=None)

    def _boom(*, model, contents, config):
        raise RuntimeError("403 PERMISSION_DENIED: caller does not have permission")

    be.client.models.generate_content = _boom
    with pytest.raises(RuntimeError) as ei:
        be.call("sys", [{"role": "user", "content": "hi"}], max_tokens=100)
    exc = ei.value
    assert getattr(exc, "status_code", None) == 403
    classified = classify_provider_error(exc)
    assert classified is not None and classified[0] == "auth"
    # 友好文案保留(可行动引导)
    assert "Vertex AI 调用被拒" in str(exc)


# ── 修4:openai_compat 阶梯化收敛到 _tiered(load→call 闭环) ────────────────

def _oai_backend(api_id="relay", model="m", user_id=None):
    from agents.gm.backends.openai_compat import _OpenAICompatBackend
    be = _OpenAICompatBackend.__new__(_OpenAICompatBackend)  # 跳过 __init__(无需 API key)
    be.api_id = api_id
    be.model_name = model
    be.user_id = user_id
    be.last_usage = {}
    be.kind = api_id
    return be


def _ns(**kw):
    return type("NS", (), kw)()


def _tc(index, tid, name, arguments):
    return _ns(index=index, id=tid, function=_ns(name=name, arguments=arguments))


def _chunk(*, tool_calls=None, content=None, finish_reason=None):
    delta = _ns(content=content, tool_calls=tool_calls, reasoning_content=None, reasoning=None)
    choice = _ns(delta=delta, finish_reason=finish_reason)
    return _ns(choices=[choice], usage=None)


def test_openai_compat_tiered_load_then_call_roundtrip(monkeypatch):
    """openai_compat.stream_with_mcp_loop 收敛到 _tiered 后,真实酒馆工具表跑 load→call 闭环:
    模型先 load 窗口外工具,下一轮即可调用它;load_tools 不进 dispatcher。"""
    from agents.gm.backends import _tiered
    from tools_dsl.chat_tool_router import build_unified_tool_list
    from tools_dsl.command_tools_register import ensure_registered
    ensure_registered()
    tav = build_unified_tool_list([], origin="llm_chat", mode="tavern_gm", bound_script_id=None)
    # 窗口大小取配置真值,别抄字面量(理由同 test_tiered_tools)。
    # v1.82.0:真值 = effective_window(配置值只是下限,「主回合闭环工具必须全部直发」
    # 这条不变量会把它撑大;酒馆自举 6 + 闭环 4 + 锚点族 6 → 20)。
    from core.config import tool_window_size
    _W = _tiered.effective_window(tav, tool_window_size())
    _win, ovf, _cat = _tiered.split_window(tav, _W, True)
    assert ovf, "应有窗口外工具"
    target = next(iter(ovf))  # 某个窗口外工具的 full name

    be = _oai_backend()

    tools_seen: list[list[str]] = []  # 每轮 _create 收到的工具名
    scripts = iter([
        # 轮1:模型调 tiered__load_tools 加载窗口外 target
        [_chunk(tool_calls=[_tc(0, "c1", _tiered.LOAD_TOOLS_FULL_NAME, '{"names": ["%s"]}' % target)],
                finish_reason="tool_calls")],
        # 轮2:模型调刚加载的 target
        [_chunk(tool_calls=[_tc(0, "c2", target, "{}")], finish_reason="tool_calls")],
        # 轮3:收尾文本,无 tool_calls
        [_chunk(content="好的。", finish_reason="stop")],
    ])

    def fake_create(**kwargs):
        tools_seen.append([t["function"]["name"] for t in kwargs.get("tools", [])])
        return iter(next(scripts))

    be._create = fake_create  # type: ignore[method-assign]

    dispatched: list[tuple[str, str]] = []

    def fake_mcp(server_id, tool_name, args):
        dispatched.append((server_id, tool_name))
        return {"ok": True, "result": "done"}

    list(be.stream_with_mcp_loop("sys", [{"role": "user", "content": "hi"}], tav, 4, 256, fake_mcp))

    # 轮1:窗口工具 + load_tools,target 还没在
    assert _tiered.LOAD_TOOLS_FULL_NAME in tools_seen[0]
    assert target not in tools_seen[0]
    assert len(tools_seen[0]) <= _W + 1  # 窗口 + load_tools
    # 轮2:load 之后 target 被 append 进工具数组
    assert target in tools_seen[1], "load 后 target 应出现在工具数组"
    # target 真的被 dispatch,load_tools 没走 dispatcher
    assert ("tiered", "load_tools") not in dispatched
    sid, _, tn = target.partition("__")
    assert (sid, tn) in dispatched


def test_openai_compat_tiered_disabled_discards_overflow(monkeypatch):
    """RPG_TIERED_TOOLS=0 → 窗口外丢弃(无 load_tools 元工具),与 _tiered.split_window(enabled=False) 一致。"""
    monkeypatch.setenv("RPG_TIERED_TOOLS", "0")
    from agents.gm.backends import _tiered
    from tools_dsl.chat_tool_router import build_unified_tool_list
    from tools_dsl.command_tools_register import ensure_registered
    ensure_registered()
    tav = build_unified_tool_list([], origin="llm_chat", mode="tavern_gm", bound_script_id=None)
    from core.config import tool_window_size
    _W = _tiered.effective_window(tav, tool_window_size())
    assert len(tav) > _W

    be = _oai_backend()
    tools_seen: list[list[str]] = []
    scripts = iter([[_chunk(content="hi", finish_reason="stop")]])

    def fake_create(**kwargs):
        tools_seen.append([t["function"]["name"] for t in kwargs.get("tools", [])])
        return iter(next(scripts))

    be._create = fake_create  # type: ignore[method-assign]
    list(be.stream_with_mcp_loop("sys", [{"role": "user", "content": "hi"}], tav, 2, 256, lambda *a: {"ok": True}))

    assert tools_seen, "至少发一次请求"
    # 禁用阶梯化:只发窗口内那些,无 load_tools 目录
    assert _tiered.LOAD_TOOLS_FULL_NAME not in tools_seen[0]
    assert len(tools_seen[0]) == _W
