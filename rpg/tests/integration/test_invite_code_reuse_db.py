"""test_invite_code_reuse_db.py — 真库验证:邀请码的注册者被硬删后,这张码仍算已用。

invite_codes.used_by 外键 on delete set null。用户用码注册、之后账号被硬删,used_by 变回 NULL、
used_at 还在。以前注册闸、confirm 原子预占、管理页「未用」筛选与删除都只看 used_by is null,
单次码能再注册一次,管理员还能把它当未用码删掉;管理页却按 used_at 显示「已使用」。
这里在真实 PostgreSQL 上走一遍外键级联,确认四处都按 auth.invite_code_unused_sql 判定。

需要 DATABASE_URL 指向可写的测试库(用户名 integtest_ 前缀,邀请码 ITEST 前缀,用完删除)。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from tests.helpers import integtest_username, random_suffix


def _db_ok() -> bool:
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            db.execute("select 1").fetchone()
        return True
    except Exception:
        return False


@unittest.skipUnless(_db_ok(), "需要可用的 PostgreSQL(DATABASE_URL)")
class InviteCodeOfDeletedUser(unittest.TestCase):
    def setUp(self):
        from platform_app.db import connect
        self.used_code = f"ITEST{random_suffix(6).upper()}"
        self.fresh_code = f"ITEST{random_suffix(6).upper()}"
        with connect() as db:
            uid = db.execute(
                "insert into users(username, display_name) values (%s, 'integ') returning id",
                (integtest_username(),),
            ).fetchone()["id"]
            db.execute("insert into invite_codes(code) values (%s), (%s)", (self.used_code, self.fresh_code))
            db.execute("update invite_codes set used_by = %s, used_at = now() where code = %s",
                       (uid, self.used_code))
            # 注销后 cron 硬删:外键把 used_by 置回 NULL
            db.execute("delete from users where id = %s", (uid,))
            row = db.execute("select used_by, used_at from invite_codes where code = %s",
                             (self.used_code,)).fetchone()
        self.assertIsNone(row["used_by"])
        self.assertIsNotNone(row["used_at"])

    def tearDown(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from invite_codes where code in (%s, %s)", (self.used_code, self.fresh_code))

    def test_registration_gate(self):
        from platform_app import auth
        from platform_app.db import connect
        with connect() as db, patch.object(auth, "registration_mode", return_value="invite"):
            with self.assertRaisesRegex(ValueError, "邀请码无效或已使用"):
                auth._registration_gate(db, "nobody@example.test", self.used_code)
            self.assertEqual(auth._registration_gate(db, "nobody@example.test", self.fresh_code), self.fresh_code)

    def test_confirm_reservation_predicate(self):
        """confirm 的原子预占用的就是这个谓词:已用码命中 0 行。"""
        from platform_app.auth import invite_code_unused_sql
        from platform_app.db import connect
        with connect() as db:
            hit = db.execute(
                f"update invite_codes set note = note where code = %s and {invite_code_unused_sql()}",
                (self.used_code,),
            ).rowcount
            self.assertEqual(hit, 0)

    def _admin_codes(self, used):
        from platform_app.api.admin import registration as reg
        resp = asyncio.run(reg.admin_list_invite_codes(page=1, limit=200, used=used, admin={"id": 1}))
        return {c["code"] for c in json.loads(resp.body)["codes"]}

    def test_admin_list_filters(self):
        self.assertNotIn(self.used_code, self._admin_codes("unused"))
        self.assertIn(self.used_code, self._admin_codes("used"))
        self.assertIn(self.fresh_code, self._admin_codes("unused"))

    def test_admin_cannot_delete_used_code(self):
        from fastapi import HTTPException

        from platform_app.api.admin import registration as reg
        req = MagicMock()
        req.headers = {}
        req.client = MagicMock(host="127.0.0.1")
        with patch.object(reg, "_write_audit"):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(reg.admin_delete_invite_code(req, self.used_code, admin={"id": 1}))
            self.assertEqual(cm.exception.status_code, 404)
            asyncio.run(reg.admin_delete_invite_code(req, self.fresh_code, admin={"id": 1}))
        self.assertNotIn(self.fresh_code, self._admin_codes("all"))
