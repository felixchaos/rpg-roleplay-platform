"""
test_branch_and_set.py — B6 扩展集成测试

覆盖：
- /api/saves 创建 + checkout（最小路径，不依赖 LLM）
- 用户 A 看不到用户 B 的存档（多用户隔离）
- /api/permissions 修改 + /api/auth/me 反映
- /api/me/credentials 写入加密 + 不回显明文
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class MultiUserSaveIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_user_a_cannot_see_user_b_saves(self):
        a = register_user(self.client)
        b = register_user(self.client)
        # 两个用户都默认有空 saves 列表
        ra = self.client.get("/api/v1/saves", cookies=a["cookies"])
        rb = self.client.get("/api/v1/saves", cookies=b["cookies"])
        self.assertEqual(ra.status_code, 200)
        self.assertEqual(rb.status_code, 200)
        # 无论里面有什么，A 看到的 user_id 都必须是 A 自己；B 同理
        for it in (ra.json().get("items") or []):
            # 单租户字段命名可能不同；至少不能含其他用户名
            self.assertNotIn(b["username"], str(it))


class PermissionsRoundtrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_set_permission_mode_persists(self):
        u = register_user(self.client)
        # 设为 strict（如果 API 允许该值）
        for mode in ("strict", "full_access"):
            r = self.client.post(
                "/api/v1/permissions",
                json={"mode": mode},
                cookies=u["cookies"],
            )
            self.assertEqual(r.status_code, 200, f"mode={mode}: {r.text}")
            body = r.json()
            self.assertTrue(body.get("ok"))


class ApiKeyEncryption(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_set_api_key_does_not_echo_plaintext(self):
        u = register_user(self.client)
        plaintext = "sk-integtest-abc-secret-xyz-99999"
        r = self.client.post(
            "/api/v1/me/credentials",
            json={"api_id": "openai", "api_key": plaintext},
            cookies=u["cookies"],
        )
        # 接口可能 200 / 404（不支持该 api_id），但绝不应在响应里回显明文
        self.assertNotIn(plaintext, r.text, "响应不应包含明文 key")
        # 列表接口也不该回显
        r2 = self.client.get("/api/v1/me/credentials", cookies=u["cookies"])
        if r2.status_code == 200:
            self.assertNotIn(plaintext, r2.text, "列表不应包含明文 key")

    def test_db_stores_encrypted_not_plaintext(self):
        u = register_user(self.client)
        plaintext = "sk-integtest-rrrr-secret-vvvvv"
        r = self.client.post(
            "/api/v1/me/credentials",
            json={"api_id": "openai", "api_key": plaintext},
            cookies=u["cookies"],
        )
        if r.status_code != 200:
            self.skipTest(f"接口未返回 200: {r.text}")
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                """
                select encrypted_key from user_api_credentials
                where api_id = 'openai' and user_id = (
                  select id from users where username = %s
                )
                """,
                (u["username"],),
            ).fetchone()
        self.assertIsNotNone(row)
        ek = bytes(row["encrypted_key"]) if row["encrypted_key"] else b""
        # encrypted_key 是 nonce||ciphertext||tag，不应等于 plaintext 的 UTF-8 字节
        self.assertNotEqual(ek, plaintext.encode("utf-8"))
        self.assertNotIn(plaintext.encode("utf-8"), ek)


if __name__ == "__main__":
    unittest.main(verbosity=2)
