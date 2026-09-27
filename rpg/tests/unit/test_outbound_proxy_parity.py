"""
test_outbound_proxy_parity.py
=============================

凭据代理「修 A 漏 B」的奇偶守卫(反馈 #107)。

以前只有 GM 的 openai_compat 后端读凭据里的 proxy,拉模型列表 / anthropic 后端 / 子代理
harness / 生图全都不带,用户在「连接方式」里配了 HTTP 代理,只有聊天生效,保存 key 和同步
模型照样直连超时。收口后:

1. 静态守卫:全仓每一处 `safe_httpx_client(` 调用都必须**显式**写出 `proxy=` 关键字
   (不需要代理的写 `proxy=None`)。新出站点漏写会在这里红,逼着写的人想一下代理。
2. 行为守卫:拉模型列表(model_probe)从凭据解析出的代理经 core.outbound.credential_proxy
   传给出站 client —— 本地模式带上,服务器模式恒 None。
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

import httpx
import pytest

RPG = Path(__file__).resolve().parents[2]
_SKIP_PARTS = {"tests", ".venv", "venv", "node_modules", "__pycache__", "rpg_env"}


def _prod_py_files():
    for p in RPG.rglob("*.py"):
        rel = p.relative_to(RPG)
        if _SKIP_PARTS & set(rel.parts):
            continue
        yield p


def test_every_safe_httpx_client_call_names_proxy():
    missing: list[str] = []
    seen = 0
    for p in _prod_py_files():
        src = p.read_text(encoding="utf-8")
        if "safe_httpx_client(" not in src:
            continue
        tree = ast.parse(src, filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name != "safe_httpx_client":
                continue
            seen += 1
            if not any(kw.arg == "proxy" for kw in node.keywords):
                missing.append(f"{p.relative_to(RPG)}:{node.lineno}")
    assert seen >= 10, f"只扫到 {seen} 处调用,扫描范围可能错了"
    assert not missing, (
        "这些 safe_httpx_client(...) 调用没写 proxy=(凭据代理从 core.outbound.credential_proxy 取,"
        "确实不需要的写 proxy=None):\n" + "\n".join(missing)
    )


def _models_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [
        {"id": "m1", "object": "model", "created": 0, "owned_by": "x"},
    ]})


@pytest.fixture()
def probe_env(monkeypatch):
    """model_probe 拉模型的最小桩:凭据带 proxy、出站 client 换成 MockTransport 并记下 kwargs。"""
    import model_probe
    from core import outbound
    from platform_app import user_credentials

    monkeypatch.setattr(model_probe, "_require_user_credential", lambda: False)
    monkeypatch.setattr(user_credentials, "resolve_api_key", lambda *a, **k: {
        "key": "sk-test", "base_url_override": "", "proxy": " http://127.0.0.1:7890 ",
        "source": "user_db",
    })
    captured: list[dict] = []

    def _fake_client(**kwargs):
        captured.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(_models_response))

    monkeypatch.setattr(outbound, "safe_httpx_client", _fake_client)
    return captured


@pytest.mark.parametrize("server_mode,expected", [(False, "http://127.0.0.1:7890"), (True, None)])
def test_list_models_passes_credential_proxy(probe_env, server_mode, expected):
    import model_probe
    from core import outbound

    with mock.patch.object(outbound, "_ssrf_enforced", return_value=server_mode):
        models = model_probe._list_openai_compat_models(
            {"id": "openai", "kind": "openai_compat", "base_url": "https://api.openai.com/v1"},
            user_id=1,
        )
    assert [m["id"] for m in models] == ["m1"]
    assert probe_env and all("proxy" in kw for kw in probe_env)
    assert probe_env[0]["proxy"] == expected


@pytest.mark.parametrize("server_mode,expected", [(False, "http://127.0.0.1:7890"), (True, None)])
def test_anthropic_list_models_passes_credential_proxy(probe_env, server_mode, expected):
    import model_probe
    from core import outbound

    def _anth(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": "claude-x", "type": "model", "display_name": "X", "created_at": "2026-01-01T00:00:00Z"},
        ], "has_more": False, "first_id": "claude-x", "last_id": "claude-x"})

    with mock.patch.object(outbound, "_ssrf_enforced", return_value=server_mode), \
         mock.patch.object(outbound, "safe_httpx_client",
                           side_effect=lambda **kw: probe_env.append(kw) or httpx.Client(
                               transport=httpx.MockTransport(_anth))):
        models = model_probe._list_anthropic_models({"id": "anthropic", "kind": "anthropic"}, user_id=1)
    assert [m["id"] for m in models] == ["claude-x"]
    assert probe_env[-1]["proxy"] == expected


def test_resolve_provider_creds_carries_proxy(probe_env):
    import model_probe
    from core import outbound

    with mock.patch.object(outbound, "_ssrf_enforced", return_value=False):
        creds = model_probe._resolve_provider_creds({"id": "openai"}, 1)
    assert creds["proxy"] == "http://127.0.0.1:7890"
    with mock.patch.object(outbound, "_ssrf_enforced", return_value=True):
        creds = model_probe._resolve_provider_creds({"id": "openai"}, 1)
    assert creds["proxy"] is None
