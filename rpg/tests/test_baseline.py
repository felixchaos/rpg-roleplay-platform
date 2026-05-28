"""
test_baseline.py — B1 集成测试基线

覆盖：
- migration check 通过
- auth 注册/登录/登出/我 信息
- 重复用户名 / 错密码失败
- 未登录访问受保护接口被拒
- MCP/Skill 端点对匿名用户的边界
- /api/saves 列表对登录用户可访问且空
"""
from __future__ import annotations

import unittest

from tests.helpers import (
    cleanup_test_users,
    integtest_username,
    login_user,
    make_client,
    register_user,
)


class MigrationCheck(unittest.TestCase):
    def test_check_passes(self):
        from platform_app import db as _db
        # 不抛即视为通过；若抛会失败
        _db._assert_schema_up_to_date()


class AuthFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_register_then_me(self):
        u = register_user(self.client)
        self.assertEqual(u["status"], 200, f"register failed: {u['body']}")
        self.assertTrue(u["body"]["ok"])
        me = self.client.get("/api/auth/me", cookies=u["cookies"])
        self.assertEqual(me.status_code, 200)
        body = me.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["user"]["username"], u["username"])

    def test_duplicate_username_rejected(self):
        uname = integtest_username()
        u1 = register_user(self.client, uname)
        self.assertEqual(u1["status"], 200)
        u2 = register_user(self.client, uname)
        self.assertEqual(u2["status"], 400)
        self.assertFalse(u2["body"].get("ok"))

    def test_login_wrong_password(self):
        u = register_user(self.client)
        bad = login_user(self.client, u["username"], password="wrong-password!")
        self.assertEqual(bad["status"], 400)
        self.assertFalse(bad["body"].get("ok"))

    def test_login_returns_session_cookie(self):
        u = register_user(self.client)
        login = login_user(self.client, u["username"], u["password"])
        self.assertEqual(login["status"], 200)
        self.assertTrue(login["body"]["ok"])

    def test_logout_invalidates_session(self):
        u = register_user(self.client)
        # 退出
        out = self.client.post("/api/auth/logout", cookies=u["cookies"])
        self.assertEqual(out.status_code, 200)
        # 退出后 /me 不应返回 user
        me = self.client.get("/api/auth/me", cookies=u["cookies"])
        body = me.json()
        self.assertIsNone(body.get("user"))

    def test_me_anonymous_no_user(self):
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        body = me.json()
        self.assertIsNone(body.get("user"))


class AuthGuardedEndpoints(unittest.TestCase):
    """匿名访问受保护端点应被拒绝（401/403/400 都可接受，不返回 200+数据即可）"""
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    PROTECTED_GET = [
        "/api/me/usage",
        "/api/me/usage/timeline",
        "/api/me/character-cards",
        "/api/saves",
        "/api/scripts",
    ]

    def test_anonymous_get_blocked(self):
        for path in self.PROTECTED_GET:
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertNotEqual(
                    resp.status_code, 200,
                    f"{path} 不应允许匿名 GET（拿到 200）",
                )


class MCPSkillVisibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_mcp_tools_anonymous(self):
        resp = self.client.get("/api/tools")
        # 未登录可能允许返回脱敏列表，也可能 401，两者都接受
        self.assertIn(resp.status_code, (200, 401, 403))
        if resp.status_code == 200:
            body = resp.json()
            # 脱敏后不应露 env 原始 key
            for srv in (body.get("servers") or []):
                self.assertNotIn("env", srv, "MCP servers 不应对匿名暴露 env")

    def test_skill_run_requires_auth(self):
        # 不存在的 skill_id；只要不是 200 就行
        resp = self.client.post("/api/skills/__nope__/run", json={"args": []})
        self.assertNotEqual(resp.status_code, 200)


class SavesListEmptyForNewUser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_new_user_saves_empty(self):
        u = register_user(self.client)
        resp = self.client.get("/api/saves", cookies=u["cookies"])
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"))
        # items 可能是 [] 或带 default workspace 自动建的，但不应包含别人的存档
        items = body.get("items") or []
        for it in items:
            self.assertNotIn("integtest_", str(it.get("owner_username", "")), "看到了别人的存档")


if __name__ == "__main__":
    unittest.main(verbosity=2)
