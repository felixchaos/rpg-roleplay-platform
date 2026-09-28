"""test_invite_code_expiry_contract.py — 邀请码「有效天数」前端发了、后端没收。

管理后台两端(web components/admin/registration-section.jsx 下拉 7/14/30/90/180/365 天、
手机 mobile/admin/registration.jsx 数字输入默认 30)都以 `expires_days` 发给
POST /api/admin/invite-codes;后端只读 `expires_in_days` → 管理员选了「7 天」,
生成的码照样永不过期(列表「到期」一栏为空)。

这里锁:两个键都认(expires_days 是两端客户端的实际契约,expires_in_days 是老名字);
非正数 / 非整数不写到期时间(= 永不过期),不 500。

DB 全用替身,不打真库。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from unittest.mock import MagicMock, patch

_RPG = pathlib.Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from platform_app.api.admin import registration as reg  # noqa: E402


class _Req:
    def __init__(self, body):
        self._body = body
        self.headers = {}
        self.client = None

    async def json(self):
        return self._body


class _FakeDb:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), tuple(params or ())))
        cur = MagicMock()
        cur.fetchone = lambda: {"code": "X", "expires_at": None, "created_at": None}
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _create(body):
    db = _FakeDb()
    with patch.object(reg, "connect", return_value=db), \
         patch.object(reg, "_client_ip", return_value="127.0.0.1"), \
         patch.object(reg, "_write_audit", return_value=None):
        asyncio.run(reg.admin_create_invite_codes(_Req(body), admin={"id": 1}))
    return [c for c in db.calls if c[0].startswith("insert into invite_codes")]


def test_expires_days_from_admin_ui_sets_expiry():
    inserts = _create({"count": 2, "expires_days": 7, "note": "内测"})
    assert len(inserts) == 2
    for sql, params in inserts:
        assert "interval" in sql, "前端选了有效天数,码必须带到期时间"
        assert "7" in params


def test_legacy_expires_in_days_still_works():
    inserts = _create({"count": 1, "expires_in_days": 30})
    sql, params = inserts[0]
    assert "interval" in sql and "30" in params


def test_missing_or_non_positive_days_means_no_expiry():
    for body in ({"count": 1}, {"count": 1, "expires_days": 0}, {"count": 1, "expires_days": ""},
                 {"count": 1, "expires_days": "abc"}):
        sql, _params = _create(body)[0]
        assert "interval" not in sql, body
