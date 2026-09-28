"""只带 key 重存凭据,不能把别处配好的接口地址(base_url_override)清掉。

与代理同一个坑:手机端「API」、首配拦截弹窗以外的供应商卡片这些表单根本没有地址输入,请求体
只有 {api_id, api_key}。以前路由把缺失的键取成空串,upsert 又无条件
`base_url_override = excluded.base_url_override` —— 用户先在桌面设置页给内置 provider 配了
中转站地址和 key,再到手机端重填 key,地址被静默清掉,之后 GM 打官方端点,拿中转站的 key 撞 401。

约定(与 proxy 相同的三态):请求体**带了** base_url_override 键才改(空串 = 清空,回到目录
默认地址);没带 = 保留已存值。完整保存与 keep_key 两条路径同一语义。真库往返见
tests/integration/test_credential_proxy_persist_db.py。
"""
from __future__ import annotations

import asyncio
import json

import pytest


class _JsonRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _post(monkeypatch, body: dict, *, role="admin", stored=""):
    from platform_app import user_credentials
    from platform_app.api import me as me_api

    calls: list[dict] = []
    monkeypatch.setattr(user_credentials, "set_credential",
                        lambda *a, **k: calls.append(k) or {"ok": True})
    monkeypatch.setattr(user_credentials, "stored_base_url_override",
                        lambda uid, api_id: stored, raising=False)
    resp = asyncio.run(me_api.api_set_credential(_JsonRequest(body), user={"id": 19, "role": role}))
    return resp, calls


# ── 路由:三态派发 ───────────────────────────────────────────────────────


def test_body_without_base_url_key_keeps_existing(monkeypatch):
    resp, calls = _post(monkeypatch, {"api_id": "openai", "api_key": "sk-x"})
    assert resp.status_code == 200
    assert calls[0]["base_url_override"] is None, "没带 base_url_override 键 = 不动已存地址(不能冲成空)"


def test_body_with_empty_base_url_clears(monkeypatch):
    _, calls = _post(monkeypatch, {"api_id": "openai", "api_key": "sk-x", "base_url_override": ""})
    assert calls[0]["base_url_override"] == ""


def test_body_with_base_url_is_stripped(monkeypatch):
    _, calls = _post(monkeypatch, {"api_id": "openai", "api_key": "sk-x",
                                   "base_url_override": "  https://relay.example.com/v1  "})
    assert calls[0]["base_url_override"] == "https://relay.example.com/v1"


def test_custom_provider_key_resave_uses_stored_base_url(monkeypatch):
    """自定义中转站(目录里没有的 provider)在没有地址输入的表单里重填 key:已存地址算数,不报
    「必须填写 Base URL」,也不把地址冲掉。"""
    resp, calls = _post(monkeypatch, {"api_id": "my-relay", "api_key": "sk-x"},
                        role="user", stored="https://relay.example.com/v1")
    assert resp.status_code == 200, json.loads(resp.body)
    assert calls[0]["base_url_override"] is None


def test_custom_provider_without_any_base_url_still_rejected(monkeypatch):
    resp, calls = _post(monkeypatch, {"api_id": "my-relay", "api_key": "sk-x"}, role="user", stored="")
    assert resp.status_code == 400
    assert "必须填写 Base URL" in json.loads(resp.body)["error"]
    assert calls == []


# ── 数据层:None = 不动已存地址 ─────────────────────────────────────────


class _Cur:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _DB:
    def __init__(self, log, stored=""):
        self.log = log
        self.stored = stored

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.log.append((flat, params))
        if flat.startswith("select base_url_override from user_api_credentials"):
            return _Cur({"base_url_override": self.stored} if self.stored else None)
        if flat.startswith("insert into user_api_credentials") or flat.startswith("update user_api_credentials"):
            return _Cur({"id": 1, "api_id": "openai", "base_url_override": "", "enabled": True})
        raise AssertionError(f"假库没预料到的 SQL: {flat[:120]}")


class _Conn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *a):
        return False


@pytest.fixture()
def fake_db(monkeypatch):
    from platform_app import user_credentials as uc
    log: list = []
    state = {"stored": ""}
    monkeypatch.setattr(uc, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(uc, "connect", lambda *a, **k: _Conn(_DB(log, state["stored"])))
    monkeypatch.setattr(uc, "encrypt_api_key", lambda key, uid, api: b"cipher")
    import core.config
    monkeypatch.setattr(core.config, "require_auth", lambda: False)
    # 保存后的远程模型同步与本测试无关:让它在 import 处就失败(非致命,被吞掉)
    import model_probe
    monkeypatch.setattr(model_probe, "list_remote_models",
                        lambda *a, **k: {"ok": False, "models": []})
    import platform_app.user_models as um
    monkeypatch.setattr(um, "replace_synced_models", lambda *a, **k: None)
    return log, state


def _upsert(log):
    hits = [(s, p) for s, p in log if s.startswith("insert into user_api_credentials")]
    assert len(hits) == 1
    return hits[0]


def test_full_save_without_base_url_keeps_column(fake_db):
    from platform_app import user_credentials as uc
    log, _ = fake_db
    uc.set_credential(19, "openai", "sk-x", base_url_override=None, allow_base_url=True)
    sql, params = _upsert(log)
    assert "base_url_override = case when" in sql, "upsert 仍无条件覆盖 base_url_override"
    assert params[7] is True, "没提地址时 upsert 应保留已存值"


def test_full_save_with_empty_base_url_overwrites(fake_db):
    from platform_app import user_credentials as uc
    log, _ = fake_db
    uc.set_credential(19, "openai", "sk-x", base_url_override="", allow_base_url=True)
    _, params = _upsert(log)
    assert params[7] is False
    assert params[3] == ""


def test_keep_key_without_base_url_keeps_column(fake_db):
    """keep_key(只改代理 / 启用态,不重填 key)路径同一语义:没提地址 = 不动。"""
    from platform_app import user_credentials as uc
    log, _ = fake_db
    uc.set_credential(19, "openai", "", preserve_key_if_empty=True, allow_base_url=True,
                      base_url_override=None, proxy="")
    hits = [(s, p) for s, p in log if s.startswith("update user_api_credentials")]
    assert len(hits) == 1
    sql, params = hits[0]
    assert "base_url_override = case when" in sql, "keep_key 路径仍无条件覆盖 base_url_override"
    assert params[0] is True


def test_no_auth_without_base_url_uses_stored(fake_db):
    """免鉴权凭据必须有地址;没提地址时按已存地址判,不误报。"""
    from platform_app import user_credentials as uc
    log, state = fake_db
    state["stored"] = "http://127.0.0.1:11434/v1"
    uc.set_credential(19, "ollama", "", base_url_override=None, allow_base_url=True,
                      auth_mode="none")
    _upsert(log)
    state["stored"] = ""
    with pytest.raises(ValueError, match="免鉴权模式必须填接口地址"):
        uc.set_credential(19, "ollama", "", base_url_override=None, allow_base_url=True,
                          auth_mode="none")


# ── 空 key + keep_key:只改地址 / 代理,不删凭据(iOS「留空则保留现有」)───────────


def _route_real_set_credential(monkeypatch, fake_db, body, *, role="user"):
    """路由 → 真实 set_credential → 假库。delete_credential 换成记录器(它自己的 SQL 不在假库预期里)。"""
    from platform_app import user_credentials as uc
    from platform_app.api import me as me_api
    deleted: list = []
    monkeypatch.setattr(uc, "delete_credential",
                        lambda uid, api_id: deleted.append((uid, api_id)) or {"ok": True, "deleted": True})
    resp = asyncio.run(me_api.api_set_credential(_JsonRequest(body), user={"id": 19, "role": role}))
    return resp, deleted


def test_empty_key_with_keep_key_updates_meta_not_delete(monkeypatch, fake_db):
    """iOS 已配 key 的供应商只改 Base URL / 代理:空 key + keep_key → 只更新元数据,密钥不删。"""
    log, _ = fake_db
    resp, deleted = _route_real_set_credential(monkeypatch, fake_db, {
        "api_id": "openai", "api_key": "", "keep_key": True, "enabled": True,
        "base_url_override": "", "proxy": "",
    })
    assert resp.status_code == 200, json.loads(resp.body)
    assert deleted == [], "空 key + keep_key 把凭据删了"
    assert any(s.startswith("update user_api_credentials") for s, _ in log), "keep_key 应只更新元数据"
    assert not any(s.startswith("insert into user_api_credentials") for s, _ in log)


def test_empty_key_without_keep_key_still_deletes(monkeypatch, fake_db):
    """对照:不带 keep_key 的空 key 仍是删除语义(手机端 / 设置页「删除密钥」依赖它)。"""
    resp, deleted = _route_real_set_credential(monkeypatch, fake_db, {
        "api_id": "openai", "api_key": "", "enabled": True, "base_url_override": "", "proxy": "",
    })
    assert resp.status_code == 200
    assert deleted == [(19, "openai")]


def test_ios_blank_key_save_sends_keep_key():
    """iOS 供应商详情页的 Key 框写着「留空则保留现有」,只改地址 / 代理时请求体里 key 为空。
    以前不带 keep_key,后端按「空 key = 删除」把密钥删了,界面却显示「已保存」。
    iOS 没有单测工程,这里锁源码:setCredential 能带 keep_key,详情页的保存按已配置与否传它。"""
    import pathlib
    import re
    ios = pathlib.Path(__file__).resolve().parents[3] / "ios" / "Sources"
    api_src = (ios / "API.swift").read_text(encoding="utf-8")
    fn = api_src[api_src.index("func setCredential("):]
    fn = fn[:fn.index("\n    func ", 10)]
    assert re.search(r'body\["keep_key"\]\s*=\s*true', fn), "iOS setCredential 不会带 keep_key"
    view = (ios / "Views" / "ModelsView.swift").read_text(encoding="utf-8")
    save = view[view.index("private func save() async"):]
    save = save[:save.index("private func test() async")]
    assert "keepKey:" in save, "iOS 详情页保存没按「已配置 + 空 key」传 keepKey"
