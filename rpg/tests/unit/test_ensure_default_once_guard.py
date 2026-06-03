"""热路径优化回归锁定:ensure_default 每用户每进程只 backfill 一次。

current_user 原本每个认证请求都跑 workspace.ensure_default(JOIN + 有存档时 seed_tree
+ 读 runtime 文件)。新存档在 create_save 时已自 seed,ensure_default 只为存量旧存档兜底,
故加进程内幂等守卫。本测试锁定:多次 current_user 同一 user → ensure_default 只调一次。
"""
import types

from platform_app.api import _deps


def _make_request(token="tok"):
    return types.SimpleNamespace(cookies={_deps.SESSION_COOKIE: token})


def test_ensure_default_called_once_per_user(monkeypatch):
    _deps._ENSURED_DEFAULT_USERS.clear()
    calls = []

    monkeypatch.setattr(_deps, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(_deps.auth, "user_from_token", lambda _t: {"id": 7, "role": "user"})
    monkeypatch.setattr(_deps.workspace, "ensure_default", lambda uid: calls.append(uid))

    req = _make_request()
    for _ in range(5):
        u = _deps.current_user(req)
        assert u and u["id"] == 7
    assert calls == [7], f"ensure_default 应只调一次,实际 {calls}"


def test_distinct_users_each_ensured_once(monkeypatch):
    _deps._ENSURED_DEFAULT_USERS.clear()
    calls = []
    seq = iter([{"id": 1, "role": "user"}, {"id": 2, "role": "user"}, {"id": 1, "role": "user"}])

    monkeypatch.setattr(_deps, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(_deps.auth, "user_from_token", lambda _t: next(seq))
    monkeypatch.setattr(_deps.workspace, "ensure_default", lambda uid: calls.append(uid))

    for _ in range(3):
        _deps.current_user(_make_request())
    assert calls == [1, 2], f"两个不同用户各一次,user1 第二次跳过,实际 {calls}"
