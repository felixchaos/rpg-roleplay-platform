"""/set 解析、状态抽取、验收判定的 Anthropic 分支遵守调用方的 timeout_sec,SDK 不重试(巡检整合审查)。

聊天 directives 阶段调 parse_set_command(timeout_sec=15):OpenAI 兼容分支(urllib)守这 15s、
不重试;Anthropic 分支却用 llm_timeout_seconds(桌面版 1800s)+ SDK 默认 max_retries=2。
请求一旦挂住(比如凭据代理不回包),/set 这一步最坏把整个回合卡 3 x 1800s。extractor /
acceptance_verifier 的 Anthropic 分支同样无视各自的 timeout_sec。

锁死:三处都经 _harness 的同一个构造缝,读超时 = 调用方 timeout_sec、连接 10s、max_retries=0,
凭据代理照常带上;挂住时只发一次请求、按调用方约定返回空结果。
"""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture()
def anth(monkeypatch):
    import anthropic

    from core import outbound
    from platform_app import user_credentials

    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: False)
    monkeypatch.setattr(user_credentials, "resolve_api_key", lambda *a, **k: {
        "key": "sk-ant-test", "source": "user_db", "proxy": "http://127.0.0.1:7890",
    })
    state: dict = {"requests": 0, "client_kw": [], "sdk_kw": []}

    def _hang(request):
        state["requests"] += 1
        raise httpx.ReadTimeout("hung", request=request)

    def _client(**kw):
        state["client_kw"].append(kw)
        return httpx.Client(transport=httpx.MockTransport(_hang))

    monkeypatch.setattr(outbound, "safe_httpx_client", _client)
    _real = anthropic.Anthropic

    class _Spy(_real):
        def __init__(self, *a, **kw):
            state["sdk_kw"].append(kw)
            super().__init__(*a, **kw)

    monkeypatch.setattr(anthropic, "Anthropic", _Spy)
    return state


def _assert_bounded(state, timeout_sec):
    assert state["requests"] == 1, f"SDK 不该自己重试,实际发了 {state['requests']} 次"
    assert state["client_kw"][-1]["timeout"] == timeout_sec
    assert state["client_kw"][-1]["proxy"] == "http://127.0.0.1:7890"
    sdk = state["sdk_kw"][-1]
    assert sdk["max_retries"] == 0
    assert sdk["timeout"].read == timeout_sec and sdk["timeout"].connect == 10.0


def test_parse_set_command_anthropic_honors_timeout(anth):
    from agents.command_agent import parse_set_command

    out = parse_set_command("/set 时间=第二天清晨", {}, user_id=None,
                            model_override="claude-x", api_id_override="anthropic", timeout_sec=15)
    assert out == []
    _assert_bounded(anth, 15)


def test_extract_state_ops_anthropic_honors_timeout(anth):
    from agents.extractor import extract_state_ops

    out = extract_state_ops("她推门走了出去。", {}, user_id=None,
                            model_override="claude-x", api_id_override="anthropic", timeout_sec=20)
    assert out == []
    _assert_bounded(anth, 20)


def test_verify_acceptance_anthropic_honors_timeout(anth):
    from agents.acceptance_verifier import verify_acceptance_llm

    out = verify_acceptance_llm(["主角离开房间"], "她推门走了出去。", [], user_id=None,
                                model_override="claude-x", api_id_override="anthropic", timeout_sec=12)
    assert out is None  # 调用失败 → None,上层降级回规则判定
    _assert_bounded(anth, 12)
