"""
test_credentials_proxy_persist.py
=================================

凭据代理的持久化语义(反馈 #107 的出路是「在连接方式里选 HTTP 代理」,这条路以前走不通):

- 设置页编辑已有凭据、只改代理不重填 key:前端什么都没发,后端 keep_key 路径也不写 metadata;
- 手机端 / 供应商卡片 / 拦截弹窗这些没有代理输入的表单,重新存 key 会把 metadata 覆盖成 {},
  把别处配好的代理悄悄清掉。

现在的约定:请求体**带了** proxy 键才动代理(空串 = 清空),没带 = 保留;keep_key 路径同样
遵守,并沿用完整保存路径的格式校验。这里锁路由到数据层的参数派发;真库往返在
tests/integration/test_credential_proxy_persist_db.py。
"""
from __future__ import annotations

import asyncio

import pytest


class _JsonRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _post(monkeypatch, body: dict) -> dict:
    from platform_app import user_credentials
    from platform_app.api import me as me_api

    calls: list[dict] = []
    monkeypatch.setattr(user_credentials, "set_credential",
                        lambda *a, **k: calls.append(k) or {"ok": True})
    resp = asyncio.run(me_api.api_set_credential(_JsonRequest(body), user={"id": 19, "role": "admin"}))
    assert resp.status_code == 200
    assert len(calls) == 1
    return calls[0]


def test_body_without_proxy_key_keeps_existing(monkeypatch):
    kw = _post(monkeypatch, {"api_id": "openai", "api_key": "sk-x"})
    assert kw["proxy"] is None, "没带 proxy 键 = 不动已存代理(不能冲成空)"


def test_body_with_empty_proxy_clears(monkeypatch):
    kw = _post(monkeypatch, {"api_id": "openai", "api_key": "sk-x", "proxy": ""})
    assert kw["proxy"] == ""


def test_body_with_proxy_is_stripped(monkeypatch):
    kw = _post(monkeypatch, {"api_id": "openai", "api_key": "", "keep_key": True,
                             "proxy": "  http://127.0.0.1:7890  "})
    assert kw["proxy"] == "http://127.0.0.1:7890"
    assert kw["preserve_key_if_empty"] is True


@pytest.fixture()
def no_db(monkeypatch):
    from platform_app import user_credentials as uc
    monkeypatch.setattr(uc, "init_db", lambda *a, **k: None)
    routed: list[dict] = []
    monkeypatch.setattr(uc, "_update_credential_meta",
                        lambda *a, **k: routed.append(k) or {"ok": True})
    monkeypatch.setattr(uc, "delete_credential", lambda *a, **k: {"ok": True, "deleted": True})
    import core.config
    monkeypatch.setattr(core.config, "require_auth", lambda: False)  # 本地模式:允许 127.0.0.1 代理
    return routed


def test_keep_key_forwards_proxy_to_meta_update(no_db):
    from platform_app import user_credentials as uc
    uc.set_credential(19, "openai", "", preserve_key_if_empty=True, allow_base_url=True,
                      proxy="http://127.0.0.1:7890")
    uc.set_credential(19, "openai", "", preserve_key_if_empty=True, allow_base_url=True, proxy="")
    uc.set_credential(19, "openai", "", preserve_key_if_empty=True, allow_base_url=True)
    assert [r["proxy"] for r in no_db] == ["http://127.0.0.1:7890", "", None]


def test_keep_key_validates_proxy_format(no_db):
    from platform_app import user_credentials as uc
    with pytest.raises(ValueError, match="代理地址格式不对"):
        uc.set_credential(19, "openai", "", preserve_key_if_empty=True, allow_base_url=True,
                          proxy="127.0.0.1:7890")
    assert no_db == []


def test_keep_key_rejects_internal_proxy_in_server_mode(no_db, monkeypatch):
    import core.config
    from platform_app import user_credentials as uc
    monkeypatch.setattr(core.config, "require_auth", lambda: True)
    with pytest.raises(ValueError, match="服务器模式下代理不允许"):
        uc.set_credential(19, "openai", "", preserve_key_if_empty=True, allow_base_url=True,
                          proxy="http://127.0.0.1:7890")
    assert no_db == []
