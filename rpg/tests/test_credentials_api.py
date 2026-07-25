from __future__ import annotations

import asyncio
import json

import pytest


class _JsonRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def test_non_admin_cannot_save_unknown_api_credential(monkeypatch):
    """当前安全模型(a0f6efa39 起,见 me/credentials.py 非 admin 闸):普通用户可添加
    自定义(未知)provider,但必须自带 base_url 指向中转站——【无 base_url 的未知
    provider】仍被 400 拒且不落库(拒绝面不降级);base_url 本身的 SSRF 闸见下个测试。"""
    from platform_app.api import me as me_api
    from platform_app import user_credentials

    calls: list[tuple] = []

    def fake_set_credential(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(user_credentials, "set_credential", fake_set_credential)

    # 拒绝面:未知 provider + 设 key + 无 base_url → 400,set_credential 不被调用
    response = asyncio.run(me_api.api_set_credential(
        _JsonRequest({"api_id": "gpt-5.5", "api_key": "sk-test"}),
        user={"id": 19, "role": "user"},
    ))
    assert response.status_code == 400
    assert "必须填写 Base URL" in json.loads(response.body)["error"]
    assert calls == []

    # 开放面:同一未知 provider 带中转站 base_url → 放行到 set_credential,
    # 且 allow_base_url=True + base_url 传递(SSRF 由 _validate_base_url 把关)
    response2 = asyncio.run(me_api.api_set_credential(
        _JsonRequest({"api_id": "gpt-5.5", "api_key": "sk-test",
                      "base_url_override": "https://relay.example.com/v1"}),
        user={"id": 19, "role": "user"},
    ))
    assert response2.status_code == 200
    assert len(calls) == 1
    _args, _kwargs = calls[0]
    assert _kwargs.get("allow_base_url") is True
    assert _kwargs.get("base_url_override") == "https://relay.example.com/v1"


def test_validate_base_url_rejects_internal_hosts_in_server_mode(monkeypatch):
    """未知 provider 开放后的真防线:服务器模式(require_auth)下 base_url 必须 https,
    且解析级拒绝本机/私网/保留地址(SSRF)。守护语义:普通用户带 base_url 能存,
    但存不进指向内网的地址。数字 IP 无需 DNS,测试离线可跑。"""
    import core.config
    from platform_app.user_credentials import _validate_base_url

    monkeypatch.setattr(core.config, "require_auth", lambda: True)

    for bad in (
        "https://localhost/v1",          # 字面量本地名
        "https://127.0.0.1/v1",          # loopback
        "https://192.168.1.10/v1",       # RFC1918 私网
        "http://relay.example.com/v1",   # 服务器模式禁 http(先于解析被拒)
    ):
        with pytest.raises(ValueError):
            _validate_base_url(bad)


def test_non_admin_builtin_api_credential_is_normalized(monkeypatch):
    from platform_app.api import me as me_api
    from platform_app import user_credentials

    calls: list[tuple] = []

    def fake_set_credential(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True, "api_id": args[1]}

    monkeypatch.setattr(user_credentials, "set_credential", fake_set_credential)

    response = asyncio.run(me_api.api_set_credential(
        _JsonRequest({"api_id": "AlibabaQwen", "api_key": "sk-test"}),
        user={"id": 19, "role": "user"},
    ))

    assert response.status_code == 200
    assert json.loads(response.body)["api_id"] == "dashscope"
    assert calls[0][0][1] == "dashscope"


def test_keep_key_forwards_preserve_flag(monkeypatch):
    """编辑弹窗只改接口地址、不重填 key(key 从不回显)时,前端发 keep_key=true。

    路由必须把它翻成 set_credential(preserve_key_if_empty=True),否则空 key 会被
    当成删除 —— 这正是「改 URL 保存后没变化、要删 key 重填」的上报 bug。
    """
    from platform_app.api import me as me_api
    from platform_app import user_credentials

    calls: list[tuple] = []

    def fake_set_credential(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True, "api_id": args[1], "base_url_override": kwargs.get("base_url_override", "")}

    monkeypatch.setattr(user_credentials, "set_credential", fake_set_credential)

    response = asyncio.run(me_api.api_set_credential(
        _JsonRequest({
            "api_id": "longcat",
            "api_key": "",  # key 未重填
            "base_url_override": "https://relay.example.com/v1",
            "keep_key": True,
        }),
        user={"id": 19, "role": "user"},
    ))

    assert response.status_code == 200
    assert calls, "set_credential 必须被调用"
    _args, kwargs = calls[0]
    assert kwargs.get("preserve_key_if_empty") is True
    assert kwargs.get("base_url_override") == "https://relay.example.com/v1"


def test_no_keep_key_defaults_to_delete_semantics(monkeypatch):
    """没有 keep_key 时,preserve_key_if_empty 必须为 False —— 保留「空 key = 删除」语义。"""
    from platform_app.api import me as me_api
    from platform_app import user_credentials

    calls: list[tuple] = []
    monkeypatch.setattr(user_credentials, "set_credential",
                        lambda *a, **k: calls.append((a, k)) or {"ok": True})

    asyncio.run(me_api.api_set_credential(
        _JsonRequest({"api_id": "openai", "api_key": ""}),
        user={"id": 19, "role": "admin"},
    ))

    assert calls[0][1].get("preserve_key_if_empty") is False


def test_set_credential_preserve_dispatch(monkeypatch):
    """set_credential 层:空 key + preserve → _update_credential_meta;否则 → delete。

    不碰 DB:桩掉 init_db 及两个落库 helper,只验证分支派发。
    """
    from platform_app import user_credentials as uc

    monkeypatch.setattr(uc, "init_db", lambda *a, **k: None)
    routed: list[str] = []
    monkeypatch.setattr(uc, "delete_credential",
                        lambda *a, **k: routed.append("delete") or {"ok": True, "deleted": True})
    monkeypatch.setattr(uc, "_update_credential_meta",
                        lambda *a, **k: routed.append("update") or {"ok": True})

    # 空 key + preserve → 更新元数据,保留密钥
    uc.set_credential(19, "openai", "", base_url_override="", allow_base_url=True,
                      preserve_key_if_empty=True)
    # 空 key,无 preserve → 删除(短路在 base_url 校验之前)
    uc.set_credential(19, "openai", "", base_url_override="", allow_base_url=True)

    assert routed == ["update", "delete"]
