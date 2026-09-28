"""test_registration_gate.py —— 注册闸(管理后台「注册与邀请」页)的读方契约。

现场(巡检):
  1. `_check_invite_code` 对 `app_config.value` 调 `json.loads`。这一列是 jsonb,psycopg
     读出来已经是 dict → `json.loads(dict)` 抛 TypeError,被 `except: pass` 吞掉 → mode 恒为
     open → 「仅邀请」模式下邀请码从来没校验过。旧测试拿 JSON **字符串**当桩,正好绕开了真实
     形状,所以一直是绿的。
  2. 同页「关闭注册」按钮:没有任何读方,点了以后照样人人可注册。
  3. 同页「邮箱验证」「自动审批」两个开关:前端写 `email_verification`(后端白名单里没有,
     直接丢掉),后端存的是 `require_email_verify` / `auto_approve`,注册流程一个都不读。
  4. 登录页 schema 的 `invite_only` 写死 False → 网页注册表单从不出邀请码输入框。

本文件锁:
  - 注册模式的读取兼容 dict / JSON 串两种形状(单一读方 `registration_mode`);
  - 仅邀请 = 白名单邮箱直接放行,否则必须带有效邀请码;无码 / 错码拒;
  - 开放模式不校验也不消费邀请码(填了也忽略,不会因为选填框里的笔误卡在验证码那一步);
  - 关闭注册 = 密码注册与 Apple 新建账号都拒;
  - 登录页 schema 按模式透出邀请码字段 / 关闭提示;
  - 管理接口只收发真正有读方的 `mode`,并拒绝未知模式;两端管理页不再展示没有读方的开关。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

_RPG = pathlib.Path(__file__).resolve().parents[2]
REPO = _RPG.parent
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from platform_app import auth  # noqa: E402

_EMAIL = "newbie@example.com"
_GOOD_CODE = "GOODCODE1"


class _Db:
    """按 SQL 关键字回结果的假连接。cfg 原样当作 app_config.value 返回(dict = psycopg 真实形状)。"""

    def __init__(self, cfg, *, allowlist=(), codes=(), users=0):
        self.cfg = cfg
        self.allowlist = set(allowlist)
        self.codes = set(codes)
        self.users = users
        self.sql: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        self.sql.append((s, tuple(params or ())))
        cur = MagicMock()
        row = None
        if "from app_config" in s:
            row = {"value": self.cfg} if self.cfg is not None else None
        elif "from registration_allowlist" in s:
            row = {"x": 1} if params and params[0] in self.allowlist else None
        elif "from invite_codes" in s:
            row = {"x": 1} if params and params[0] in self.codes else None
        elif "count(*)" in s and "from users" in s:
            row = {"n": self.users}
        cur.fetchone = lambda row=row: row
        cur.fetchall = lambda: []
        cur.rowcount = 1
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _register(db: _Db, *, invite_code=None, email=_EMAIL):
    """server 模式跑一遍注册 Phase 1,返回 (结果, 暂存的 pending payload)。"""
    stored: dict[str, str] = {}
    with patch.object(auth, "connect", return_value=db), \
         patch.object(auth, "init_db"), \
         patch.object(auth, "_ip_budget_exceeded", return_value=False), \
         patch.object(auth, "_pending_store_set", side_effect=lambda k, v: stored.__setitem__(k, v)), \
         patch("core.config.require_auth", return_value=True), \
         patch("platform_app.email.send_verification_email", lambda *a, **k: None):
        out = auth.register(
            "newbie", "SecurePass123!", "",
            email=email, birthday=date(1990, 1, 1), invite_code=invite_code,
            terms_accepted=True, age_confirmed=True, ip="1.2.3.4", ua="ua",
        )
    pending = auth._decode_pending_register(stored.get(email))
    return out, pending


class TestRegistrationModeReader(unittest.TestCase):
    def _mode(self, cfg):
        return auth.registration_mode(_Db(cfg))

    def test_jsonb_dict_shape(self):
        """psycopg 把 jsonb 解成 dict —— 这是生产上的真实形状,旧实现在这里恒判 open。"""
        self.assertEqual(self._mode({"mode": "invite"}), "invite")
        self.assertEqual(self._mode({"mode": "closed"}), "closed")

    def test_legacy_json_string_shape(self):
        self.assertEqual(self._mode(json.dumps({"mode": "invite"})), "invite")

    def test_missing_or_broken_is_open(self):
        for cfg in (None, {}, "not json", {"mode": ""}, {"mode": None}, 42):
            with self.subTest(cfg=cfg):
                self.assertEqual(self._mode(cfg), "open")

    def test_case_and_whitespace(self):
        self.assertEqual(self._mode({"mode": " Invite "}), "invite")


class TestInviteMode(unittest.TestCase):
    CFG = {"mode": "invite", "require_email_verify": True, "auto_approve": True}

    def test_no_code_rejected(self):
        with self.assertRaisesRegex(ValueError, "邀请码"):
            _register(_Db(self.CFG, codes={_GOOD_CODE}))

    def test_wrong_code_rejected(self):
        with self.assertRaisesRegex(ValueError, "邀请码无效"):
            _register(_Db(self.CFG, codes={_GOOD_CODE}), invite_code="WRONG")

    def test_valid_code_passes_and_is_reserved_for_confirm(self):
        out, pending = _register(_Db(self.CFG, codes={_GOOD_CODE}), invite_code=_GOOD_CODE)
        self.assertTrue(out["pending_verify"])
        self.assertEqual(pending["invite_code"], _GOOD_CODE)

    def test_legacy_string_config_still_enforced(self):
        with self.assertRaisesRegex(ValueError, "邀请码"):
            _register(_Db(json.dumps(self.CFG)))

    def test_allowlisted_email_needs_no_code(self):
        """「仅邀请」原本就放行内测白名单(06-01 定的语义),邀请码是另一条受邀通道,不是额外门槛。"""
        out, pending = _register(_Db(self.CFG, allowlist={_EMAIL}))
        self.assertTrue(out["ok"])
        self.assertIsNone(pending["invite_code"])

    def test_allowlisted_email_with_stray_code_does_not_consume_it(self):
        """白名单用户顺手填了个错码:不该因为 confirm 时抢码失败把整个注册回滚。"""
        _out, pending = _register(_Db(self.CFG, allowlist={_EMAIL}), invite_code="WRONG")
        self.assertIsNone(pending["invite_code"])


class TestAllowlistAlias(unittest.TestCase):
    def test_allowlist_mode_ignores_codes(self):
        with self.assertRaisesRegex(ValueError, "白名单"):
            _register(_Db({"mode": "allowlist"}, codes={_GOOD_CODE}), invite_code=_GOOD_CODE)
        _out, pending = _register(_Db({"mode": "allowlist"}, allowlist={_EMAIL}))
        self.assertIsNone(pending["invite_code"])


class TestOpenMode(unittest.TestCase):
    CFG = {"mode": "open", "require_email_verify": True, "auto_approve": True}

    def test_open_passes_without_code(self):
        out, pending = _register(_Db(self.CFG))
        self.assertTrue(out["ok"])
        self.assertTrue(out["pending_verify"], "server 模式照旧要邮箱验证码")
        self.assertIsNone(pending["invite_code"])

    def test_open_ignores_code(self):
        """开放模式填了邀请码(移动端表单是选填框):不校验、不消费。"""
        _out, pending = _register(_Db(self.CFG), invite_code="TYPO")
        self.assertIsNone(pending["invite_code"])

    def test_no_config_row_is_open(self):
        out, _pending = _register(_Db(None))
        self.assertTrue(out["ok"])


class TestClosedMode(unittest.TestCase):
    def test_password_register_rejected(self):
        with self.assertRaisesRegex(ValueError, "关闭"):
            _register(_Db({"mode": "closed"}, allowlist={_EMAIL}, codes={_GOOD_CODE}), invite_code=_GOOD_CODE)

    def test_apple_new_account_rejected(self):
        with self.assertRaisesRegex(ValueError, "关闭"):
            auth._assert_registration_allowed(_Db({"mode": "closed"}, allowlist={_EMAIL}), _EMAIL)


class _PasswordlessDb(_Db):
    """免密登录 / 魔法链接要用到的额外几张表:users(按邮箱查)、建号、验证码、白名单消费。"""

    def __init__(self, cfg, *, existing_user=None, **kw):
        super().__init__(cfg, **kw)
        self.existing_user = existing_user

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        if s.startswith("select * from users") or (s.startswith("select") and "from users" in s and "lower(email)" in s):
            self.sql.append((s, tuple(params or ())))
            cur = MagicMock()
            cur.fetchone = lambda: self.existing_user
            return cur
        if s.startswith("insert into users"):
            self.sql.append((s, tuple(params or ())))
            cur = MagicMock()
            cur.fetchone = lambda: {"id": 11, "username": _EMAIL, "email": _EMAIL}
            return cur
        if "from email_verifications" in s:
            from platform_app.security import hash_email_code
            self.sql.append((s, tuple(params or ())))
            cur = MagicMock()
            cur.fetchone = lambda: {"id": 3, "code_hash": hash_email_code("123456")}
            return cur
        if s.startswith("update email_verifications") or s.startswith("update registration_allowlist"):
            self.sql.append((s, tuple(params or ())))
            cur = MagicMock()
            cur.fetchone = lambda: {"id": 3, "email_norm": _EMAIL}
            cur.rowcount = 1
            return cur
        return super().execute(sql, params)

    def created_user(self) -> bool:
        return any(q.startswith("insert into users") for q, _ in self.sql)


def _passwordless_patches(db):
    return [
        patch.object(auth, "connect", return_value=db),
        patch.object(auth, "init_db"),
        patch.object(auth, "_check_rate_limit"),
        patch.object(auth, "_verify_locked", return_value=False),
        patch.object(auth, "_record_login_fail"),
        patch.object(auth, "_record_login_success"),
        patch.object(auth, "_record_verify_fail"),
        patch.object(auth, "_issue_session", return_value="tok"),
    ]


def _run_magic(db):
    import contextlib
    with contextlib.ExitStack() as st:
        for p in _passwordless_patches(db):
            st.enter_context(p)
        return auth.login_via_magic_token(_EMAIL, ip="1.2.3.4", magic_token="mt")


def _run_passwordless(db):
    import contextlib
    with contextlib.ExitStack() as st:
        for p in _passwordless_patches(db):
            st.enter_context(p)
        return auth.verify_passwordless_and_login(_EMAIL, "123456", ip="1.2.3.4")


class TestPasswordlessNewAccountFollowsGate(unittest.TestCase):
    """魔法链接 / 免密验证码新建账号也要过注册闸(巡检第二轮整合审查)。

    关闭注册后,密码注册与 Apple 新建账号都拒,可 login_via_magic_token /
    verify_passwordless_and_login 在「账号不存在」分支只查白名单、不读注册模式:30 天内没用过的
    内测魔法链接照样建号登录。同一个邮箱走密码注册被拒、点邀请邮件却能进来。
    已有账号的登录不受注册模式影响。
    """

    RUNNERS = (("magic", _run_magic), ("passwordless", _run_passwordless))

    def test_closed_rejects_new_account(self):
        for label, run in self.RUNNERS:
            with self.subTest(path=label):
                db = _PasswordlessDb({"mode": "closed"}, allowlist={_EMAIL})
                with self.assertRaisesRegex(ValueError, "关闭"):
                    run(db)
                self.assertFalse(db.created_user(), "关闭注册时不该建号")

    def test_closed_still_logs_in_existing_account(self):
        for label, run in self.RUNNERS:
            with self.subTest(path=label):
                db = _PasswordlessDb({"mode": "closed"}, allowlist={_EMAIL},
                                     existing_user={"id": 7, "username": "old", "email": _EMAIL})
                out = run(db)
                self.assertEqual(out["user_id"], 7)
                self.assertFalse(db.created_user())

    def test_open_and_invite_allowlisted_email_creates_account(self):
        for mode in ("open", "invite", "allowlist"):
            for label, run in self.RUNNERS:
                with self.subTest(mode=mode, path=label):
                    db = _PasswordlessDb({"mode": mode}, allowlist={_EMAIL})
                    out = run(db)
                    self.assertEqual(out["user_id"], 11)
                    self.assertTrue(db.created_user())

    def test_not_allowlisted_still_rejected(self):
        for label, run in self.RUNNERS:
            with self.subTest(path=label):
                db = _PasswordlessDb({"mode": "open"})
                with self.assertRaisesRegex(ValueError, "白名单"):
                    run(db)
                self.assertFalse(db.created_user())


class TestAppleGate(unittest.TestCase):
    def test_open_allows(self):
        auth._assert_registration_allowed(_Db({"mode": "open"}), _EMAIL)

    def test_invite_requires_allowlist(self):
        """Apple 登录没有邀请码输入,仅邀请模式下只认白名单(与改动前一致)。"""
        with self.assertRaisesRegex(ValueError, "白名单"):
            auth._assert_registration_allowed(_Db({"mode": "invite"}), _EMAIL)
        auth._assert_registration_allowed(_Db({"mode": "invite"}, allowlist={_EMAIL}), _EMAIL)


class TestConfirmConsumesOnlyReservedCode(unittest.TestCase):
    """confirm 阶段只消费 Phase 1 放进 pending 的码(原子预占逻辑不变)。"""

    def _confirm(self, invite_code):
        from platform_app.security import hash_email_code
        db = MagicMock()
        executed: list[str] = []

        def _exec(sql, params=None):
            s = " ".join(sql.split()).lower()
            executed.append(s)
            cur = MagicMock()
            if "from email_verifications" in s:
                cur.fetchone.return_value = {"id": 9, "code_hash": hash_email_code("123456")}
            elif "insert into users" in s:
                cur.fetchone.return_value = {"id": 5, "username": "newbie"}
            elif "count(*)" in s:
                cur.fetchone.return_value = {"n": 0}
            else:
                cur.fetchone.return_value = None
            cur.rowcount = 1
            return cur

        db.execute.side_effect = _exec
        db.__enter__ = lambda s: db
        db.__exit__ = lambda s, *a: False
        pending = auth._encode_pending_register({
            "username": "newbie", "password_hash": "h", "display_name": "newbie",
            "birthday": "1990-01-01", "terms_accepted": True, "age_confirmed": True,
            "invite_code": invite_code, "allow_admin": False,
        })
        with patch.object(auth, "connect", return_value=db), \
             patch.object(auth, "init_db"), \
             patch.object(auth, "_verify_locked", return_value=False), \
             patch.object(auth, "_clear_verify_fail"), \
             patch.object(auth, "_pending_store_get", return_value=pending):
            auth.confirm_email_verification(_EMAIL, "123456")
        return [s for s in executed if s.startswith("update invite_codes")]

    def test_reserved_code_consumed(self):
        self.assertEqual(len(self._confirm(_GOOD_CODE)), 1)

    def test_no_code_no_consume(self):
        self.assertEqual(self._confirm(None), [])


_USED_AT = "2026-09-01T00:00:00+00:00"


def _invite_row_visible(sql: str, row: dict) -> bool:
    """按 SQL 里写了的「未使用」谓词过滤一行码(只认 used_by / used_at 两列的 is null)。"""
    import re as _re
    for col in ("used_by", "used_at"):
        if _re.search(rf"\b(?:ic\.)?{col} is null\b", sql) and row.get(col) is not None:
            return False
    return True


class _InviteDb(_Db):
    """invite_codes 按行存状态:{code: {used_by, used_at}}。"""

    def __init__(self, cfg, *, rows, **kw):
        super().__init__(cfg, **kw)
        self.rows = rows

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        if "invite_codes" in s:
            self.sql.append((s, tuple(params or ())))
            code = (params or (None,))[-1] if s.startswith("update") else (params or (None,))[0]
            row = self.rows.get(code)
            hit = row is not None and _invite_row_visible(s, row)
            cur = MagicMock()
            cur.fetchone = lambda: ({"x": 1} if hit else None)
            cur.rowcount = 1 if hit else 0
            return cur
        return super().execute(sql, params)


class TestInviteCodeUsedJudgedByUsedAt(unittest.TestCase):
    """用过的码,账号被硬删后不能再用(巡检第二轮整合审查)。

    invite_codes.used_by 外键 on delete set null:用户 A 用码 X 注册,之后注销、cron 硬删 users 行,
    X 的 used_by 变回 NULL,used_at 还在。注册闸与 confirm 的原子预占都只看 used_by is null,
    单次码就能再用一次;管理页按 used_at 显示「已使用」、不给删除,与后端判定对不上。
    """

    ORPHANED = {"used_by": None, "used_at": _USED_AT}   # 注册者已被硬删
    FRESH = {"used_by": None, "used_at": None}

    def test_gate_rejects_code_of_deleted_user(self):
        db = _InviteDb({"mode": "invite"}, rows={_GOOD_CODE: dict(self.ORPHANED)})
        with self.assertRaisesRegex(ValueError, "邀请码无效或已使用"):
            _register(db, invite_code=_GOOD_CODE)

    def test_gate_still_accepts_fresh_code(self):
        db = _InviteDb({"mode": "invite"}, rows={_GOOD_CODE: dict(self.FRESH)})
        _out, pending = _register(db, invite_code=_GOOD_CODE)
        self.assertEqual(pending["invite_code"], _GOOD_CODE)

    def _confirm_with(self, row):
        from platform_app.security import hash_email_code
        db = _InviteDb({"mode": "invite"}, rows={_GOOD_CODE: row})
        base = db.execute

        def _exec(sql, params=None):
            s = " ".join(sql.split()).lower()
            cur = MagicMock()
            if "from email_verifications" in s:
                cur.fetchone.return_value = {"id": 9, "code_hash": hash_email_code("123456")}
                return cur
            if "insert into users" in s:
                cur.fetchone.return_value = {"id": 5, "username": "newbie"}
                return cur
            if s.startswith("update email_verifications"):
                return cur
            return base(sql, params)

        db.execute = _exec
        pending = auth._encode_pending_register({
            "username": "newbie", "password_hash": "h", "display_name": "newbie",
            "birthday": "1990-01-01", "terms_accepted": True, "age_confirmed": True,
            "invite_code": _GOOD_CODE, "allow_admin": False,
        })
        with patch.object(auth, "connect", return_value=db), \
             patch.object(auth, "init_db"), \
             patch.object(auth, "_verify_locked", return_value=False), \
             patch.object(auth, "_clear_verify_fail"), \
             patch.object(auth, "_issue_session", return_value="tok"), \
             patch.object(auth, "_pending_store_get", return_value=pending):
            return auth.confirm_email_verification(_EMAIL, "123456")

    def test_confirm_reservation_refuses_code_of_deleted_user(self):
        with self.assertRaisesRegex(ValueError, "邀请码已被使用"):
            self._confirm_with(dict(self.ORPHANED))

    def test_confirm_reservation_takes_fresh_code(self):
        user, _tok = self._confirm_with(dict(self.FRESH))
        self.assertEqual(user["id"], 5)


class TestLoginSchemaFollowsMode(unittest.TestCase):
    def _schema(self, cfg):
        from platform_app.api import auth as api_auth
        db = _Db(cfg, users=3)
        with patch("platform_app.db.connect", return_value=db), \
             patch("platform_app.db.init_db"), \
             patch("core.config.effective_auth_required", return_value=True):
            resp = asyncio.run(api_auth.api_auth_schema())
        return json.loads(resp.body)

    def test_invite_mode_exposes_optional_invite_field(self):
        body = self._schema({"mode": "invite"})
        self.assertTrue(body["notes"]["invite_only"])
        self.assertFalse(body["notes"].get("registration_closed"))
        field = next((f for f in body["register"] if f["key"] == "invite_code"), None)
        self.assertIsNotNone(field, "仅邀请模式下网页注册表单必须能填邀请码")
        self.assertFalse(field["required"], "白名单邮箱不需要邀请码,前端不能把它当必填拦下")

    def test_open_mode_has_no_invite_field(self):
        body = self._schema({"mode": "open"})
        self.assertFalse(body["notes"]["invite_only"])
        self.assertFalse(body["notes"].get("registration_closed"))
        self.assertNotIn("invite_code", [f["key"] for f in body["register"]])

    def test_closed_mode_flags_closed(self):
        body = self._schema({"mode": "closed"})
        self.assertTrue(body["notes"]["registration_closed"])
        self.assertFalse(body["notes"]["invite_only"])


class TestAdminRegistrationApi(unittest.TestCase):
    def _req(self, body):
        req = MagicMock()

        async def _json():
            return body
        req.json = _json
        req.headers = {}
        req.client = MagicMock(host="1.2.3.4")
        return req

    def test_get_returns_only_mode(self):
        """生产库里存着 require_email_verify / auto_approve,但它们没有读方,不该再透给管理页。"""
        from platform_app.api.admin import registration as reg
        db = MagicMock()
        db.__enter__ = lambda s: db
        db.__exit__ = lambda s, *a: False
        with patch.object(reg, "connect", return_value=db), \
             patch.object(reg, "_get_app_config",
                          return_value={"mode": "open", "require_email_verify": True, "auto_approve": True}):
            resp = asyncio.run(reg.admin_get_registration(admin={"id": 1}))
        body = json.loads(resp.body)
        body.pop("meta", None)   # json_response 统一信封
        self.assertEqual(body, {"mode": "open"})

    def test_post_only_writes_mode(self):
        from platform_app.api.admin import registration as reg
        written: list[dict] = []
        db = MagicMock()
        db.__enter__ = lambda s: db
        db.__exit__ = lambda s, *a: False
        with patch.object(reg, "connect", return_value=db), \
             patch.object(reg, "_set_app_config", side_effect=lambda _db, _k, d: written.append(d)), \
             patch.object(reg, "_write_audit"):
            asyncio.run(reg.admin_set_registration(
                self._req({"mode": "invite", "email_verification": False,
                           "require_email_verify": False, "auto_approve": False}),
                admin={"id": 1}))
        self.assertEqual(written, [{"mode": "invite"}])

    def test_post_rejects_unknown_mode(self):
        from fastapi import HTTPException

        from platform_app.api.admin import registration as reg
        db = MagicMock()
        db.__enter__ = lambda s: db
        db.__exit__ = lambda s, *a: False
        with patch.object(reg, "connect", return_value=db), \
             patch.object(reg, "_set_app_config") as setter, \
             patch.object(reg, "_write_audit"):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(reg.admin_set_registration(self._req({"mode": "everyone"}), admin={"id": 1}))
        self.assertEqual(cm.exception.status_code, 400)
        setter.assert_not_called()


class TestAdminPagesSendOnlyReadKeys(unittest.TestCase):
    """两端管理页 saveReg 发出去的每个键,后端都必须真的读;不许再挂没有读方的开关。"""

    FILES = (
        REPO / "frontend/src/components/admin/registration-section.jsx",
        REPO / "frontend/src/mobile/admin/registration.jsx",
    )

    def test_save_keys_subset_of_backend(self):
        from platform_app.api.admin import registration as reg
        for path in self.FILES:
            src = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertIsNone(re.search(r"saveReg\(\{\s*\[", src), "动态键无法核对,别用 [key] 形式")
                keys = set(re.findall(r"saveReg\(\{\s*(\w+)\s*:", src))
                self.assertTrue(keys, "至少应有模式切换")
                self.assertLessEqual(keys, set(reg.REGISTRATION_WRITABLE_KEYS))
                for dead in ("email_verification", "require_email_verify", "auto_approve"):
                    self.assertFalse(re.search(rf"\b{dead}\b", src), f"管理页还在用没有读方的 {dead}")


class TestInviteCodeListContract(unittest.TestCase):
    """邀请码列表:两端管理页读的每个字段,后端 GET /api/admin/invite-codes 都得真的返回。

    以前网页读 used_by / expired_at、手机读 used,后端返回的是 used_at / used_by_username /
    expires_at → 用过的码永远显示「可用」,还挂着删不掉的「删除」按钮。邀请码真正开始校验后,
    管理员得看得清哪些码已经用掉。
    """

    def _backend_columns(self) -> set[str]:
        from platform_app.api.admin import registration as reg
        seen: list[str] = []

        class _Db:
            def execute(self, sql, params=None):
                seen.append(" ".join(sql.split()).lower())
                cur = MagicMock()
                cur.fetchone.return_value = {"total": 0}
                cur.fetchall.return_value = []
                return cur

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(reg, "connect", return_value=_Db()):
            asyncio.run(reg.admin_list_invite_codes(admin={"id": 1}))
        listing = next(q for q in seen if "from invite_codes ic" in q)
        select_list = listing.split("select", 1)[1].split(" from invite_codes", 1)[0]
        cols = set()
        for part in select_list.split(","):
            cols.add(part.strip().split(" as ")[-1].split(".")[-1].strip())
        return cols

    def test_fields_read_by_admin_pages_exist(self):
        cols = self._backend_columns()
        self.assertIn("used_at", cols)
        for path in TestAdminPagesSendOnlyReadKeys.FILES:
            src = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                read = set(re.findall(r"\bc\.(\w+)", src))
                self.assertTrue(read)
                self.assertLessEqual(read, cols, f"管理页读了后端不返回的字段: {sorted(read - cols)}")


if __name__ == "__main__":
    unittest.main()
