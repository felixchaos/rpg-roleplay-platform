"""
test_model_probe_unreachable.py
===============================

反馈 #107:列模型探测没有上限。SDK 默认 max_retries=2、连接超时 10s,打不通的地址单轮约
31s;裸地址(不带 /vN)还会整轮补 /v1 再来一次,约 62s —— 比前端 15s 超时长,于是「假保存
失败」,还把单 worker 的桌面后端整个冻住。错误还被归成「base_url 可能缺 /v1」误导用户改地址。

锁死:
1. 连接类 / 超时类错误:不补 /v1、SDK 不重试、给「连不上这个地址」的准话(不提 /v1)。
2. 其余失败照旧补 /v1:401 / 403 / 400 / 405,以及裸 /models 返回 200 HTML(one-api 类中转站
   的前端页)导致的解析异常 —— #91 那一族不能因为收窄而从能用变成不能用。
3. SDK 自身不重试(max_retries=0),探测超时是有界的 httpx.Timeout(15, connect=5)。
4. 提示文案不带 emoji / markdown 星号(toast 是纯文本位)。
5. 连不上时按出站方式分开说:经用户配的代理(文案带脱敏后的代理地址)、系统代理拒绝、直连。
6. GM 路径探测的读超时夹紧到 60s(请求体传入的 timeout 不许无上限);本地模式打本机 / 局域网
   模型时至少 40s,冷加载不被误报成不可用;服务器模式不放宽。
7. 向量请求因凭据代理用不了(协议不对)失败时,原因写进 sticky 错误,不静默。
8. 出站闸拒绝时带闸门原因、不提代理;只有本地模式才给「连接方式 / 系统代理」建议。

全程 MockTransport,零真实网络。
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

_MODELS_JSON = {"object": "list", "data": [{"id": "m1", "object": "model", "created": 0, "owned_by": "x"}]}


@pytest.fixture()
def probe(monkeypatch):
    import model_probe
    from core import outbound
    from platform_app import user_credentials

    monkeypatch.setattr(model_probe, "_require_user_credential", lambda: False)
    monkeypatch.setattr(user_credentials, "resolve_api_key", lambda *a, **k: {
        "key": "sk-test", "base_url_override": "", "source": "user_db",
    })
    state = {"paths": [], "handler": None, "openai_kwargs": []}

    def _handler(request: httpx.Request) -> httpx.Response:
        state["paths"].append(request.url.path)
        return state["handler"](request)

    monkeypatch.setattr(outbound, "safe_httpx_client",
                        lambda **kw: httpx.Client(transport=httpx.MockTransport(_handler)))

    import openai
    _real = openai.OpenAI

    class _SpyOpenAI(_real):
        def __init__(self, *a, **kw):
            state["openai_kwargs"].append(kw)
            super().__init__(*a, **kw)

    monkeypatch.setattr(openai, "OpenAI", _SpyOpenAI)

    def _run(base_url: str):
        return model_probe._list_openai_compat_models(
            {"id": "relay", "kind": "openai_compat", "base_url": base_url}, user_id=1)

    state["run"] = _run
    return state


@pytest.mark.parametrize("exc_cls", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                                     httpx.ProxyError])
def test_unreachable_does_not_retry_v1_and_says_so(probe, exc_cls):
    def _boom(request):
        raise exc_cls("boom", request=request)

    probe["handler"] = _boom
    with pytest.raises(RuntimeError) as ei:
        probe["run"]("https://relay.example")  # 裸地址:以前会整轮补 /v1 再来一次
    msg = str(ei.value)
    assert probe["paths"] == ["/models"], f"连不上时不该重试或补 /v1,实际请求 {probe['paths']}"
    assert "连不上这个地址" in msg
    assert "缺 /v1" not in msg and "版本段" not in msg


def test_sdk_retries_disabled_and_timeout_bounded(probe):
    probe["handler"] = lambda request: httpx.Response(500, json={"error": {"message": "upstream"}})
    with pytest.raises(RuntimeError):
        probe["run"]("https://relay.example")
    # 裸 /models 一次 + 补 /v1 一次;SDK 默认重试 2 次的话会是 6 次
    assert probe["paths"] == ["/models", "/v1/models"]
    kw = probe["openai_kwargs"][0]
    assert kw["max_retries"] == 0
    assert kw["timeout"].connect == 5.0 and kw["timeout"].read == 15.0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405])
def test_http_errors_still_retry_v1(probe, status):
    def _h(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=_MODELS_JSON)
        return httpx.Response(status, json={"error": {"message": "nope"}})

    probe["handler"] = _h
    models = probe["run"]("https://relay.example")
    assert [m["id"] for m in models] == ["m1"]
    assert probe["paths"] == ["/models", "/v1/models"]


def test_bare_models_html_page_still_retries_v1(probe):
    """one-api / new-api 类中转站:裸 /models 命中前端 SPA,200 text/html。"""
    def _h(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=_MODELS_JSON)
        return httpx.Response(200, text="<!doctype html><html></html>",
                              headers={"content-type": "text/html"})

    probe["handler"] = _h
    models = probe["run"]("https://relay.example")
    assert [m["id"] for m in models] == ["m1"]
    assert probe["paths"] == ["/models", "/v1/models"]


def test_versioned_base_url_never_appends_v1(probe):
    probe["handler"] = lambda request: httpx.Response(403, json={"error": {"message": "nope"}})
    with pytest.raises(RuntimeError):
        probe["run"]("https://relay.example/v1")
    assert probe["paths"] == ["/v1/models"]


def test_anthropic_unreachable_message(monkeypatch):
    import model_probe
    from core import outbound
    from platform_app import user_credentials

    monkeypatch.setattr(model_probe, "_require_user_credential", lambda: False)
    monkeypatch.setattr(user_credentials, "resolve_api_key",
                        lambda *a, **k: {"key": "sk-ant", "source": "user_db"})
    calls = []

    def _boom(request):
        calls.append(request.url.path)
        raise httpx.ConnectTimeout("boom", request=request)

    monkeypatch.setattr(outbound, "safe_httpx_client",
                        lambda **kw: httpx.Client(transport=httpx.MockTransport(_boom)))
    with pytest.raises(RuntimeError) as ei:
        model_probe._list_anthropic_models({"id": "anthropic", "kind": "anthropic"}, user_id=1)
    assert "连不上这个地址" in str(ei.value)
    assert len(calls) == 1, "SDK 不该自己重试"


def test_probe_messages_have_no_emoji_or_markdown():
    import model_probe
    src = Path(model_probe.__file__).read_text(encoding="utf-8")
    # 抽出所有字符串字面量里给用户看的错误文案(RuntimeError 构造体)
    bodies = re.findall(r"RuntimeError\(\s*((?:f?\"[^\"]*\"\s*)+)", src)
    assert bodies, "没抽到任何 RuntimeError 文案,正则失效"
    # 与全局禁 emoji 规则同一组码位(写成转义,测试源码自己不含这些字符)
    emoji = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2665\u2726\u25B2\u25BC\u25B6\u25C6]")
    for b in bodies:
        assert not emoji.search(b), f"用户可见文案里有 emoji / 符号图标: {b[:80]}"
        assert "**" not in b, f"toast 是纯文本位,不能带 markdown 星号: {b[:80]}"


def test_bound_backend_for_probe_sets_limits():
    """GM 路径探测(校验连接 / 可用性嗅探)也有上限:不重试 + 有界 timeout,代理 client 不丢。"""
    import openai

    import model_probe

    http_client = httpx.Client()

    class _B:
        client = openai.OpenAI(api_key="k", base_url="https://relay.example/v1", http_client=http_client)

    b = _B()
    model_probe.bound_backend_for_probe(b, 20)
    assert b.client.max_retries == 0
    assert b.client.timeout.connect == 5.0 and b.client.timeout.read == 20.0
    assert b.client._client is http_client, "with_options 必须复用同一个 http_client(代理设置在它身上)"

    class _NoOptions:
        client = object()

    nb = _NoOptions()
    model_probe.bound_backend_for_probe(nb, 20)  # vertex(genai)没有 with_options:原样不动、不报错
    assert nb.client is _NoOptions.client


def test_unreachable_via_credential_proxy_says_proxy(probe, monkeypatch):
    from core import outbound
    from platform_app import user_credentials

    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: False)  # 本地模式才用凭据代理
    monkeypatch.setattr(user_credentials, "resolve_api_key", lambda *a, **k: {
        "key": "sk-test", "base_url_override": "", "source": "user_db",
        "proxy": "http://user:secret@127.0.0.1:7890",
    })

    def _boom(request):
        raise httpx.ConnectError("boom", request=request)

    probe["handler"] = _boom
    with pytest.raises(RuntimeError) as ei:
        probe["run"]("https://relay.example")
    msg = str(ei.value)
    assert "连不上这个地址" in msg and "代理" in msg
    assert "127.0.0.1:7890" in msg and "secret" not in msg, "代理地址要带上,但账号密码必须脱敏"
    assert probe["paths"] == ["/models"]


def test_unreachable_env_proxy_refusal_says_system_proxy(probe):
    def _refused(request):
        raise httpx.ProxyError("403 Forbidden from proxy", request=request)

    probe["handler"] = _refused
    with pytest.raises(RuntimeError) as ei:
        probe["run"]("https://relay.example")
    msg = str(ei.value)
    assert "系统代理" in msg and "拒绝了这次连接" in msg and "连不上这个地址" in msg


def test_unreachable_plain_direct_message_unchanged(probe):
    def _boom(request):
        raise httpx.ConnectTimeout("boom", request=request)

    probe["handler"] = _boom
    with pytest.raises(RuntimeError) as ei:
        probe["run"]("https://relay.example")
    msg = str(ei.value)
    assert msg.startswith("连不上这个地址(连接超时或连接失败)")
    assert "拒绝了这次连接" not in msg and "经连接方式里配的代理" not in msg


def test_unreachable_outbound_blocked_shows_gate_reason_not_proxy(probe, monkeypatch):
    """服务器模式:出站闸拒绝(域名解析不到 / 解析到内网)被 SDK 包成「Connection error.」。
    文案要带闸门的真实原因,且不提代理 —— 服务器模式恒不走用户代理,叫人去配代理是误导。"""
    from core import outbound

    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: True)
    reason = "出站目标解析到私有/本地/保留地址,已拒绝(防 SSRF/DNS rebinding):relay.example → 10.0.0.2"

    def _blocked(request):
        raise outbound.OutboundBlockedTransportError(reason, request=request)

    probe["handler"] = _blocked
    with pytest.raises(RuntimeError) as ei:
        probe["run"]("https://relay.example")
    msg = str(ei.value)
    assert "保留地址" in msg and "10.0.0.2" in msg
    assert "代理" not in msg and "Connection error" not in msg
    assert probe["paths"] == ["/models"]


def test_unreachable_server_mode_does_not_suggest_proxy(probe, monkeypatch):
    from core import outbound

    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: True)

    def _boom(request):
        raise httpx.ConnectTimeout("boom", request=request)

    probe["handler"] = _boom
    with pytest.raises(RuntimeError) as ei:
        probe["run"]("https://relay.example")
    msg = str(ei.value)
    assert msg.startswith("连不上这个地址(连接超时或连接失败)")
    assert "代理" not in msg and "连接方式" not in msg


def test_unreachable_local_mode_suggests_proxy(probe, monkeypatch):
    from core import outbound

    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: False)

    def _boom(request):
        raise httpx.ConnectTimeout("boom", request=request)

    probe["handler"] = _boom
    with pytest.raises(RuntimeError) as ei:
        probe["run"]("https://relay.example")
    msg = str(ei.value)
    assert msg.startswith("连不上这个地址(连接超时或连接失败)")
    assert "连接方式" in msg and "系统代理" in msg


def _openai_backend(base_url: str):
    import openai

    class _B:
        client = openai.OpenAI(api_key="k", base_url=base_url, http_client=httpx.Client())

    return _B()


@pytest.mark.parametrize("server_mode,base_url,asked,want", [
    # 请求体传进来的超大 timeout 夹到 60s(服务器 / 本地一样)
    (True, "https://relay.example/v1", 100000, 60.0),
    (False, "https://relay.example/v1", 100000, 60.0),
    # 本地模式打本机模型:至少 40s(冷加载)
    (False, "http://127.0.0.1:11434/v1", 8, 40.0),
    (False, "http://host.docker.internal:11434/v1", 20, 40.0),
    (False, "http://192.168.1.20:1234/v1", 50, 50.0),
    # 本地模式打公网:照请求值
    (False, "https://relay.example/v1", 8, 8.0),
    # 服务器模式不放宽(本来也打不了本机地址)
    (True, "http://127.0.0.1:11434/v1", 8, 8.0),
])
def test_bound_backend_for_probe_clamp_and_local_floor(monkeypatch, server_mode, base_url, asked, want):
    import model_probe
    from core import outbound

    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: server_mode)
    b = _openai_backend(base_url)
    model_probe.bound_backend_for_probe(b, asked)
    assert b.client.timeout.read == want
    assert b.client.timeout.connect == 5.0
    assert b.client.max_retries == 0


def test_embedding_unsupported_proxy_is_recorded(monkeypatch):
    """向量走 urllib,凭据代理这条出站用不了:要写进 sticky 错误(设置页 / 拆书预检能看到),不能静默。

    SOCKS 不再算「用不了」:safe_urlopen 对 SOCKS 凭据代理退回环境/系统代理(巡检整合审查,
    与子代理 / 状态抽取同一口径)。还会抛 UnsupportedProxy 的只剩写时闸不放行的协议(存量脏数据)。
    """
    from core import outbound
    from platform_app.knowledge import embedding

    from platform_app.knowledge.embedding import _breaker

    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: False)
    with _breaker.attempt() as att:
        out = embedding._embed_via_openai("text-embedding-3-small", "sk", ["hi"],
                                          base_url="https://relay.example/v1",
                                          proxy="ftp://127.0.0.1:21")
    assert out is None
    assert att.kind == _breaker.KIND_CONFIG
    assert "代理" in att.friendly
