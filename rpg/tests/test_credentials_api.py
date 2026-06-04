from __future__ import annotations

import asyncio
import json


class _JsonRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def test_non_admin_cannot_save_unknown_api_credential(monkeypatch):
    from platform_app import user_credentials
    from platform_app.api import me as me_api

    called = False

    def fake_set_credential(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(user_credentials, "set_credential", fake_set_credential)

    response = asyncio.run(me_api.api_set_credential(
        _JsonRequest({"api_id": "gpt-5.5", "api_key": "sk-test"}),
        user={"id": 19, "role": "user"},
    ))

    assert response.status_code == 400
    assert "自定义供应商需管理员先配置" in json.loads(response.body)["error"]
    assert called is False


def test_non_admin_builtin_api_credential_is_normalized(monkeypatch):
    from platform_app import user_credentials
    from platform_app.api import me as me_api

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
