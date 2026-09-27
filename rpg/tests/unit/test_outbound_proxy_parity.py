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
3. urllib 这一侧同样上守卫(巡检整合审查):`safe_get_bytes(` / `download_url(` 必须显式写
   `proxy=`;`safe_urlopen(` / `_no_redirect_urlopen(` 写 `proxy=` 或 `**proxy_kwargs(...)`
   (没配代理时调用形态与改动前一致)。以前守卫只扫 safe_httpx_client,生图提交走了代理、
   下载图片的 safe_get_bytes 却没带,需要代理的图片域名下载超时,整单失败(已计费)。
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


# 名字 → 是否允许用 **kwargs(proxy_kwargs 约定)代替显式 proxy=
_PROXY_SEAMS = {
    "safe_httpx_client": False,
    "safe_get_bytes": False,
    "download_url": False,
    "safe_urlopen": True,
    "_no_redirect_urlopen": True,
}


def _seam_calls():
    """(文件:行, 函数名, 是否写了 proxy)—— 只看生产代码。"""
    for p in _prod_py_files():
        src = p.read_text(encoding="utf-8")
        if not any(f"{n}(" in src for n in _PROXY_SEAMS):
            continue
        tree = ast.parse(src, filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name not in _PROXY_SEAMS:
                continue
            named = any(kw.arg == "proxy" for kw in node.keywords)
            splat = any(kw.arg is None for kw in node.keywords)
            yield f"{p.relative_to(RPG)}:{node.lineno}", name, named or (splat and _PROXY_SEAMS[name])


def test_every_urllib_outbound_call_names_proxy():
    seen: dict[str, int] = {}
    missing: list[str] = []
    for where, name, ok in _seam_calls():
        if name == "safe_httpx_client":
            continue  # 下面那条单独锁
        seen[name] = seen.get(name, 0) + 1
        if not ok:
            missing.append(f"{where} {name}(...)")
    # 扫描范围自检:这几个出站缝在生产代码里都有调用点
    for name in ("safe_get_bytes", "download_url", "safe_urlopen", "_no_redirect_urlopen"):
        assert seen.get(name), f"没扫到任何 {name}( 调用,扫描范围可能错了"
    assert not missing, (
        "这些 urllib 出站调用没带代理参数(凭据代理从 core.outbound.credential_proxy 取;"
        "safe_get_bytes / download_url 必须显式写 proxy=,确实不需要的写 proxy=None):\n"
        + "\n".join(missing)
    )


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


# ── 生图三家:generate() 收到的凭据代理要一路传到下载图片那一跳 ────────────────────────
_IMG_URL = "https://img.example/x.png"


def _fake_client(payload):
    from unittest import mock as _m

    resp = _m.Mock(status_code=200, json=lambda: payload, text="")
    client = _m.MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = resp
    return client


@pytest.mark.parametrize("mod_name", ["openai_compat", "doubao"])
def test_image_download_uses_generate_proxy(mod_name, monkeypatch):
    import importlib

    mod = importlib.import_module(f"agents.image_gen.{mod_name}")
    seen: list[dict] = []
    monkeypatch.setattr(mod, "safe_httpx_client", lambda **kw: _fake_client({"data": [{"url": _IMG_URL}]}))
    monkeypatch.setattr(mod, "download_url", lambda url, **kw: seen.append(kw) or b"png")
    out = mod.generate("p", {}, api_id="x", model="m", api_key="k",
                       base_url="https://relay.example/v1", proxy="http://127.0.0.1:7890")
    assert out == [b"png"]
    assert seen and all(kw.get("proxy") == "http://127.0.0.1:7890" for kw in seen), seen


def test_dashscope_download_uses_generate_proxy(monkeypatch):
    from agents.image_gen import dashscope

    seen: list[dict] = []
    monkeypatch.setattr(dashscope, "_submit", lambda *a, **k: "task-1")
    monkeypatch.setattr(dashscope, "_poll", lambda *a, **k: {})
    monkeypatch.setattr(dashscope, "_extract_urls", lambda *a, **k: [_IMG_URL])
    monkeypatch.setattr(dashscope, "download_url", lambda url, **kw: seen.append(kw) or b"png")
    out = dashscope.generate("p", {}, api_id="dashscope", model="wan2.7-image-pro", api_key="k",
                             proxy="http://127.0.0.1:7890")
    assert out == [b"png"]
    assert seen == [{"proxy": "http://127.0.0.1:7890"}]
