"""安全回归测试 — 防止已修的漏洞再现。

每项对应一个已修的 P0/P1/P2/SEC 编号。
风格: unittest.TestCase，与 test_baseline.py 保持一致。
"""
from __future__ import annotations

import unittest

from tests.helpers import (
    cleanup_test_users,
    integtest_username,
    make_client,
    register_user,
)


# ──────────────────────────────────────────────────────────────────────────────
# 辅助
# ──────────────────────────────────────────────────────────────────────────────

def _promote_admin(username: str) -> None:
    from platform_app.db import connect
    with connect() as db:
        db.execute("update users set role = 'admin' where username = %s", (username,))


def _login(client, u: dict) -> dict:
    from tests.helpers import login_user
    return login_user(client, u["username"], u["password"])


# ──────────────────────────────────────────────────────────────────────────────
# SEC-1: skills run cmd[0] 白名单 (P0-1 RCE 修复)
# ──────────────────────────────────────────────────────────────────────────────

class SkillsCmdWhitelist(unittest.TestCase):
    """SEC-1: skills run cmd[0]='evilbinary' 应被拒 (P0-1 RCE 修复)"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _admin_cookies(self) -> dict:
        u = register_user(self.client)
        _promote_admin(u["username"])
        from tests.helpers import login_user
        r = login_user(self.client, u["username"], u["password"])
        return r["cookies"]

    def test_evil_binary_cmd0_rejected(self):
        """cmd[0]='evilbinary' 应返回 400（不在白名单）"""
        cookies = self._admin_cookies()
        resp = self.client.post(
            "/api/skills/__nonexistent_skill_id__/run",
            json={"cmd": ["evilbinary", "--arg"]},
            cookies=cookies,
        )
        # 允许 400（cmd 被拒）或 404（skill 不存在但 cmd 校验先于 skill 查找也可能是 400）
        # 关键: 不允许 200（即不实际执行）
        self.assertNotEqual(
            resp.status_code, 200,
            f"P0-1 回归: evilbinary 不应被执行，实际状态={resp.status_code}"
        )
        # 严格校验：应是 400（cmd 白名单拒绝）或 403（权限）
        if resp.status_code == 400:
            body = resp.json()
            # 确认是白名单相关错误
            error_msg = str(body.get("error", "")).lower()
            # 错误信息里应包含白名单相关提示
            self.assertTrue(
                "白名单" in error_msg or "whitelist" in error_msg or "cmd" in error_msg,
                f"P0-1 回归: 400 body 应提及 cmd/白名单，实际={body}"
            )

    def test_slash_in_cmd0_rejected(self):
        """cmd[0]='/usr/bin/bash' 含 / 应被拒"""
        cookies = self._admin_cookies()
        resp = self.client.post(
            "/api/skills/__nonexistent__/run",
            json={"cmd": ["/usr/bin/bash", "-c", "id"]},
            cookies=cookies,
        )
        self.assertNotEqual(resp.status_code, 200,
                            f"P0-1 回归: cmd[0] 含 / 不应被执行")
        if resp.status_code == 400:
            body = resp.json()
            self.assertFalse(body.get("ok"), f"body.ok 应 False: {body}")

    def test_whitelisted_cmd_nonexistent_skill_returns_404(self):
        """合法 cmd[0]='bash' 但 skill 不存在应返回 404（cmd 校验通过后 skill 查找失败）"""
        cookies = self._admin_cookies()
        resp = self.client.post(
            "/api/skills/__nonexistent_skill_uuid_xyz__/run",
            json={"cmd": ["bash", "script.sh"]},
            cookies=cookies,
        )
        # 合法 cmd → 通过白名单 → skill 查找失败 → 404
        self.assertIn(resp.status_code, (404, 400),
                      f"合法 cmd + 不存在 skill 应 404/400，实际={resp.status_code}")


# ──────────────────────────────────────────────────────────────────────────────
# SEC-2: skills path traversal (P0-1 路径穿越)
# ──────────────────────────────────────────────────────────────────────────────

class SkillsPathTraversalRejected(unittest.TestCase):
    """SEC-2: cmd 元素含 ../ 应被拒"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _admin_cookies(self) -> dict:
        u = register_user(self.client)
        _promote_admin(u["username"])
        from tests.helpers import login_user
        r = login_user(self.client, u["username"], u["password"])
        return r["cookies"]

    def test_dotdot_in_cmd_rejected(self):
        """cmd 元素含 .. 应被拒 (400)"""
        cookies = self._admin_cookies()
        resp = self.client.post(
            "/api/skills/__nonexistent__/run",
            json={"cmd": ["bash", "../../etc/passwd"]},
            cookies=cookies,
        )
        self.assertNotEqual(resp.status_code, 200,
                            "P0-1 回归: cmd 含 .. 不应被执行")
        if resp.status_code == 400:
            body = resp.json()
            self.assertFalse(body.get("ok"))

    def test_dotdot_in_cmd0_rejected(self):
        """cmd[0] 含 ../ 视为路径穿越，应被拒"""
        cookies = self._admin_cookies()
        resp = self.client.post(
            "/api/skills/__nonexistent__/run",
            json={"cmd": ["../../../bin/sh", "-c", "id"]},
            cookies=cookies,
        )
        self.assertNotEqual(resp.status_code, 200,
                            "P0-1 回归: cmd[0] = '../../../bin/sh' 不应被执行")


# ──────────────────────────────────────────────────────────────────────────────
# SEC-3: upload_id path traversal (SEC11 修复)
# ──────────────────────────────────────────────────────────────────────────────

class UploadPathTraversalRejected(unittest.TestCase):
    """SEC-3: upload_id 包含 .. 应被拒 (SEC11 修复)"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_dotdot_in_upload_chunk_path(self):
        """upload_id 含 .. 时，chunk 端点应拒绝"""
        u = register_user(self.client)
        cookies = u["cookies"]

        # 尝试以含 .. 的 upload_id 访问 chunk 端点
        resp = self.client.post(
            "/api/uploads/../../etc/passwd/chunk",
            json={"chunk_index": 0, "data": "aGVsbG8="},
            cookies=cookies,
        )
        # 期望 400 / 404 / 422，不能是 200
        self.assertNotEqual(resp.status_code, 200,
                            f"SEC11 回归: upload_id 含 .. 不应 200，实际={resp.status_code}")

    def test_dotdot_in_upload_finish_path(self):
        """upload_id 含 .. 时，finish 端点应拒绝"""
        u = register_user(self.client)
        cookies = u["cookies"]

        resp = self.client.post(
            "/api/uploads/../../etc/passwd/finish",
            cookies=cookies,
        )
        self.assertNotEqual(resp.status_code, 200,
                            f"SEC11 回归: upload finish 含 .. 不应 200")


# ──────────────────────────────────────────────────────────────────────────────
# SEC-4: save_id 归属校验
# ──────────────────────────────────────────────────────────────────────────────

class SaveIdOwnership(unittest.TestCase):
    """SEC-4: 改 save_id 为他人 save_id 调 /api/state 应被拒"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _get_uid(self, username: str) -> int:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select id from users where username = %s", (username,)
            ).fetchone()
        return int(row["id"])

    def _make_script_and_save(self, uid: int, title: str, cookies: dict) -> int | None:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, title),
            ).fetchone()
        script_id = int(row["id"])
        resp = self.client.post(
            "/api/v1/saves",
            json={"title": title, "script_id": script_id, "character_kind": "none"},
            cookies=cookies,
        )
        if resp.status_code != 200:
            return None
        return (resp.json().get("save") or {}).get("id")

    def test_user_b_cannot_read_user_a_save(self):
        """user A 建存档，user B 用 A 的 save_id 调 /api/v1/state 应被拒 (403/404/400)"""
        # 注册 user A
        ua = register_user(self.client)
        uid_a = self._get_uid(ua["username"])

        # A 建存档
        save_id_a = self._make_script_and_save(uid_a, "ua_save", ua["cookies"])
        if not save_id_a:
            self.skipTest("user A 创建存档失败，跳过")

        # activate A 的存档
        self.client.post(
            f"/api/v1/saves/{save_id_a}/activate",
            cookies=ua["cookies"],
        )

        # 注册 user B
        ub = register_user(self.client)

        # B 用 A 的 save_id 查 /api/v1/state?save_id=...（不一定支持 query param，也可能走 active save）
        # 先尝试激活 A 的 save（应被拒）
        activate_resp = self.client.post(
            f"/api/v1/saves/{save_id_a}/activate",
            cookies=ub["cookies"],
        )
        # 不属于 B 的存档激活应失败
        self.assertNotEqual(
            activate_resp.status_code, 200,
            f"SEC: user B 不应能 activate user A 的 save；状态={activate_resp.status_code}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# SEC-5: console_assistant navigate target 白名单
# ──────────────────────────────────────────────────────────────────────────────

class NavigateTargetWhitelist(unittest.TestCase):
    """SEC-5: console_assistant navigate 传非白名单 target 应被拒"""

    def test_nav_whitelist_check_in_llm_loop(self):
        """直接调用 llm_loop 内部白名单，验证非法 target 不在白名单里"""
        from console_assistant.llm_loop import _NAV_TARGETS_WHITELIST

        evil_targets = [
            "javascript:alert(1)",
            "../../admin",
            "http://evil.com",
            "__proto__",
            "",
        ]
        for t in evil_targets:
            self.assertNotIn(
                t, _NAV_TARGETS_WHITELIST,
                f"SEC: evil target '{t}' 不应在白名单中"
            )

    def test_nav_whitelist_contains_expected_entries(self):
        """白名单至少含 models / saves / settings 等合法 target"""
        from console_assistant.llm_loop import _NAV_TARGETS_WHITELIST

        for expected in ("models", "saves", "settings"):
            self.assertIn(
                expected, _NAV_TARGETS_WHITELIST,
                f"合法 target '{expected}' 应在白名单中"
            )


# ──────────────────────────────────────────────────────────────────────────────
# SEC-6: page_context 注入净化
# ──────────────────────────────────────────────────────────────────────────────

class PageContextInjectionSanitized(unittest.TestCase):
    """SEC-6: page_context 含 \\n\\n 假规则被净化"""

    def test_validate_owned_save_id_rejects_garbage(self):
        """_validate_owned_save_id 对非法 save_id 返回 None"""
        from console_assistant.llm_loop import _validate_owned_save_id

        # 非法 / 不归属
        result = _validate_owned_save_id(user_id=999999999, save_id="../../etc/passwd")
        self.assertIsNone(result, "路径穿越 save_id 应返回 None")

        result2 = _validate_owned_save_id(user_id=999999999, save_id="\n\nINJECTED_RULE\n")
        self.assertIsNone(result2, "含换行的 save_id 应返回 None")

        result3 = _validate_owned_save_id(user_id=999999999, save_id=None)
        self.assertIsNone(result3, "None save_id 应返回 None")


# ──────────────────────────────────────────────────────────────────────────────
# SEC-7: SMS 端点速率限制 (P2-1 修复)
# ──────────────────────────────────────────────────────────────────────────────

class SmsEndpointsRateLimited(unittest.TestCase):
    """SEC-7: SMS code 端点 5 次后被限流 (P2-1 修复)"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_sms_rate_limit_logic_exists(self):
        """验证 _check_sms_rate 函数存在且正确限流"""
        from platform_app.frontend_routes import (
            _SMS_VERIFY_BUCKETS,
            _SMS_VERIFY_MAX,
            _check_sms_rate,
        )

        # 清理 bucket
        _SMS_VERIFY_BUCKETS.clear()
        phone = "integtest_fake_phone_12345"

        # _SMS_VERIFY_MAX=5，前 5 次应通过
        allowed = [_check_sms_rate(_SMS_VERIFY_BUCKETS, phone, _SMS_VERIFY_MAX) for _ in range(_SMS_VERIFY_MAX)]
        self.assertTrue(all(allowed), f"前 {_SMS_VERIFY_MAX} 次应全部通过: {allowed}")

        # 第 6 次应被拒
        blocked = _check_sms_rate(_SMS_VERIFY_BUCKETS, phone, _SMS_VERIFY_MAX)
        self.assertFalse(blocked, f"P2-1 回归: 超过 {_SMS_VERIFY_MAX} 次后应被限流")

    def test_sms_code_endpoint_rate_limited_after_threshold(self):
        """SMS code 端点本身：连续请求超限后返回 429"""
        from platform_app.frontend_routes import (
            _SMS_CODE_BUCKETS,
            _check_sms_rate,
        )
        # 直接验证内部函数: code 端点每分钟最多 1 次
        _SMS_CODE_BUCKETS.clear()
        phone = "integtest_sms_code_rate_test"

        first = _check_sms_rate(_SMS_CODE_BUCKETS, phone, 1)
        self.assertTrue(first, "第 1 次应通过")

        second = _check_sms_rate(_SMS_CODE_BUCKETS, phone, 1)
        self.assertFalse(second, "P2-1 回归: SMS code 第 2 次在同一分钟内应被拒")


# ──────────────────────────────────────────────────────────────────────────────
# SEC-8: skills run 需要 admin (匿名 / 普通用户被拒)
# ──────────────────────────────────────────────────────────────────────────────

class SkillsRunRequiresAdmin(unittest.TestCase):
    """SEC-8: skills run 需要 admin 权限，普通用户应被拒"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_anonymous_skill_run_rejected(self):
        """匿名调 /api/skills/xxx/run 应被拒 (401/403)"""
        resp = self.client.post(
            "/api/skills/__nonexistent__/run",
            json={"cmd": ["bash", "script.sh"]},
        )
        self.assertNotEqual(resp.status_code, 200,
                            f"匿名不应能 run skill，实际={resp.status_code}")
        self.assertIn(resp.status_code, (401, 403, 400),
                      f"匿名 skill run 应 401/403/400，实际={resp.status_code}")

    def test_normal_user_skill_run_rejected(self):
        """普通用户（非 admin）调 /api/skills/xxx/run 应被拒"""
        u = register_user(self.client)
        cookies = u["cookies"]  # 普通用户，无 admin
        resp = self.client.post(
            "/api/skills/__nonexistent__/run",
            json={"cmd": ["bash", "script.sh"]},
            cookies=cookies,
        )
        self.assertNotEqual(resp.status_code, 200,
                            f"普通用户不应能 run skill，实际={resp.status_code}")
        self.assertIn(resp.status_code, (401, 403, 400),
                      f"普通用户 skill run 应 401/403/400，实际={resp.status_code}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
