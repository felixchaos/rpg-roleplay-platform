"""test_login_code_unregistered.py — 未注册邮箱请求登录验证码时的去向(08-01 反馈)。

现场:有人用邮箱登录,一直收不到验证码。查库发现他根本没注册 —— 而
`request_login_code` 对未知邮箱**刻意静默返回 ok 且不发信**(防账号枚举),前端照样
显示「验证码已发送」,于是人对着输入框等一封永远不会到的邮件。这条分支**不打日志、
不写库、不计数**,所以服务器上连一行证据都没有。

为什么改成明说:那个防枚举是**装饰性的** —— 同一套 API 的 `register` 对已占用邮箱
直接回「该邮箱已被注册」,枚举面本来就敞开。沉默一个攻击者都没挡住,只挡住了真实用户。
(真要防枚举得两边一起改,那是独立的一件事;本测试的 `test_register_still_leaks_...`
就钉着这个前提 —— 哪天注册侧改成不透露了,它会红,提醒回来重新权衡这里。)

锁四件:未知邮箱报 registered=False 且不发信、已注册报 True 且发信、
per-IP 预算超限**不给** registered(那条是防滥发,不能变成探测信道)、三端都读这个字段。
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

_RPG = pathlib.Path(__file__).resolve().parents[2]
REPO = _RPG.parent
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from platform_app import auth  # noqa: E402

_EMAIL = "nobody@example.com"


class _Db:
    """按 SQL 关键字回不同结果的假连接。user_row=None 表示查无此人。"""

    def __init__(self, user_row):
        self.user_row = user_row
        self.sql: list[str] = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        cur = MagicMock()
        s = " ".join(sql.split())
        if "select id from users" in s:
            cur.fetchone = lambda: dict(self.user_row) if self.user_row else None
        elif "select created_at from email_verifications" in s:
            cur.fetchone = lambda: None          # 无近期发码 → 不触发 60s 节流
        else:
            cur.fetchone = lambda: {"id": 1}
        cur.fetchall = lambda: []
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def inserted_code(self) -> bool:
        return any("insert into email_verifications" in " ".join(q.split()) for q in self.sql)


def _request(user_row, *, budget_exceeded: bool = False):
    db = _Db(user_row)
    sent: list[str] = []
    with patch.object(auth, "connect", return_value=db), \
         patch.object(auth, "init_db"), \
         patch.object(auth, "_check_rate_limit"), \
         patch.object(auth, "_ip_budget_exceeded", return_value=budget_exceeded), \
         patch("platform_app.email.send_login_code_email",
               side_effect=lambda e, c: sent.append(e)):
        out = auth.request_login_code(_EMAIL, ip="1.2.3.4", ua="ua")
    return out, db, sent


class TestUnregisteredEmail(unittest.TestCase):
    def test_reports_not_registered(self):
        out, _db, _sent = _request(None)
        self.assertTrue(out["ok"])
        self.assertIs(out["registered"], False)

    def test_does_not_claim_a_code_was_sent(self):
        """旧行为返回 pending_verify=True 假装发了信 —— 前端据此显示「已发送」。"""
        out, _db, _sent = _request(None)
        self.assertNotEqual(out.get("pending_verify"), True)

    def test_writes_no_code_and_sends_no_mail(self):
        _out, db, sent = _request(None)
        self.assertFalse(db.inserted_code)
        self.assertEqual(sent, [])


class TestRegisteredEmail(unittest.TestCase):
    def test_reports_registered_and_sends(self):
        out, db, sent = _request({"id": 7})
        self.assertIs(out["registered"], True)
        self.assertTrue(out["pending_verify"])
        self.assertTrue(db.inserted_code)
        self.assertEqual(sent, [_EMAIL])

    def test_email_is_masked_in_both_cases(self):
        """掩码是给前端回显用的,两条路都得有,否则「已发送到 ***」渲染成空。"""
        for row in (None, {"id": 7}):
            self.assertIn("@", _request(row)[0]["email_mask"])


class TestAbuseBudgetStaysSilent(unittest.TestCase):
    def test_budget_exceeded_gives_no_registration_signal(self):
        """per-IP 预算超限是**防滥发**,不能顺手变成一个探测账号是否存在的信道。"""
        out, db, sent = _request(None, budget_exceeded=True)
        self.assertTrue(out["ok"])
        self.assertNotIn("registered", out)
        self.assertFalse(db.inserted_code)
        self.assertEqual(sent, [])

    def test_budget_branch_is_identical_for_existing_users(self):
        out_known, _, _ = _request({"id": 7}, budget_exceeded=True)
        out_unknown, _, _ = _request(None, budget_exceeded=True)
        self.assertEqual(out_known, out_unknown)


class TestPremiseThisTradeoffRestsOn(unittest.TestCase):
    def test_register_still_leaks_email_existence(self):
        """本改动的前提:枚举面本来就敞开(注册直接回「该邮箱已被注册」)。

        哪天注册侧改成不透露了,这条会红 —— 那时请回来重新权衡登录码这边要不要跟着闭嘴,
        别让两边悄悄退化成「一边防、一边漏」的假防护。
        """
        src = (REPO / "rpg/platform_app/auth.py").read_text(encoding="utf-8")
        self.assertIn("该邮箱已被注册", src)


class TestAllClientsReadTheFlag(unittest.TestCase):
    """同面横扫:三端都消费 login-code/request,漏一端就还有人卡在死路上。"""

    def test_web_routes_to_register(self):
        src = (REPO / "frontend/src/login-app.jsx").read_text(encoding="utf-8")
        i = src.index("const j = await window.api.auth.loginCodeRequest")
        body = src[i: i + 900]
        self.assertIn("j.registered === false", body)
        self.assertIn("setMode('register')", body)
        self.assertIn("email: cleanEmail", body)   # 邮箱预填,别让人重打

    def test_ios_surfaces_it(self):
        api = (REPO / "ios/Sources/API.swift").read_text(encoding="utf-8")
        self.assertIn('obj["registered"] as? Bool', api)
        view = (REPO / "ios/Sources/Views/AuthFlowsView.swift").read_text(encoding="utf-8")
        self.assertIn("registered", view)

    def test_mobile_type_declares_it(self):
        ts = (REPO / "mobile/src/api/index.ts").read_text(encoding="utf-8")
        i = ts.index("loginCodeRequest")
        self.assertIn("registered?: boolean", ts[i: i + 400])


if __name__ == "__main__":
    unittest.main()
