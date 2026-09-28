"""停用的供应商重新存 key,不能把用户自己的模型清单清空。

模型页的供应商总开关切的是用户自己这条凭据的 enabled。关掉之后在「编辑供应商」里重填 key,
前端会把 enabled:false 一起发回来(不然会被悄悄重新打开)。set_credential 落库后照旧内联同步
远端模型:list_remote_models 里判断「用户有没有这个供应商的凭据」时跳过了停用的行,于是拉取失败,
走进「换 key 后新 key 列不出模型 → 清空旧 overlay」的分支,把这个供应商下用户改过名的模型、
隐藏过的模型整个清掉。重新打开开关后同步回来的是全开、远端原名。

停用的凭据本来就不该拿来拉模型,拉不到也不代表 key 换坏了。锁死:
  · enabled=False 落库后不做内联同步,既不调远端,也不动 overlay;
  · 启用态照旧(拉取失败仍清空,这是 issue #22 的修法,不能顺手改没)。
"""
from __future__ import annotations

import pytest


class _Cur:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _DB:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.log.append((flat, params))
        if flat.startswith("insert into user_api_credentials"):
            return _Cur({"id": 1, "api_id": "deepseek", "base_url_override": "",
                         "enabled": params[4], "auth_mode": "api_key"})
        raise AssertionError(f"假库没预料到的 SQL: {flat[:120]}")


class _Conn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *a):
        return False


@pytest.fixture()
def calls(monkeypatch):
    from platform_app import user_credentials as uc
    log: list = []
    rec = {"list": [], "replace": [], "invalidate": []}
    monkeypatch.setattr(uc, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(uc, "connect", lambda *a, **k: _Conn(_DB(log)))
    monkeypatch.setattr(uc, "encrypt_api_key", lambda key, uid, api: b"cipher")
    import model_probe
    # 与真实行为一致:停用的凭据 get_credential 取不到 → 拉取失败
    monkeypatch.setattr(model_probe, "list_remote_models",
                        lambda *a, **k: rec["list"].append((a, k)) or {"ok": False, "models": [],
                                                                     "error": "需要先配置"})
    monkeypatch.setattr(model_probe, "invalidate_user_api",
                        lambda uid, api: rec["invalidate"].append((uid, api)))
    import platform_app.user_models as um
    monkeypatch.setattr(um, "replace_synced_models",
                        lambda uid, api, models: rec["replace"].append((uid, api, list(models))))
    return rec


def test_disabled_resave_does_not_touch_overlay(calls):
    from platform_app import user_credentials as uc
    out = uc.set_credential(7, "deepseek", "sk-new", base_url_override=None,
                            enabled=False, allow_base_url=True)
    assert out["ok"] is True and out["enabled"] is False
    assert calls["replace"] == [], "停用态重填 key 把用户的模型清单清空了(改名 / 隐藏全丢)"
    assert calls["list"] == [], "停用的凭据不该拿去拉远端模型"
    # 换了 key,旧 key 的远端清单缓存仍要清掉(重新打开后别命中旧 key 的缓存)
    assert calls["invalidate"] == [(7, "deepseek")]


def test_enabled_resave_still_clears_overlay_when_remote_fails(calls):
    """启用态:新 key 列不出模型 → 仍清掉旧 key 同步来的 overlay(OSS issue #22 的修法)。"""
    from platform_app import user_credentials as uc
    uc.set_credential(7, "deepseek", "sk-new", base_url_override=None,
                      enabled=True, allow_base_url=True)
    assert calls["list"], "启用态保存后应内联同步一次"
    assert calls["replace"] == [(7, "deepseek", [])]
