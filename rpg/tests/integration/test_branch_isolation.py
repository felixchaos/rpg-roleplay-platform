"""
test_branch_isolation.py — 回归测试：前端 BranchesPage 不能再用 mock save_id 打后端

来源：UI 审计任务 2。Platform.html#saves-branches 之前用
window.MOCK_PLATFORM.saves[0].id（hard-coded 11）当默认 selectedSave，
当前用户多半不拥有 save 11，结果首屏触发 GET /api/branches/11 -> 403。

修复在前端（platform-app.jsx BranchesPage）：初始 saves=[]、selectedSave=undefined，
只有从 /api/saves 真实回包里挑出当前用户的存档才会触发 branches.list。

这里用 backend TestClient 验证后端契约不变（403 仍是正确反应，且 owner 200），
作为对前端修复假设的固定锚：哪天后端把 403 改成静默 200，前端就再也察觉不到漏权。
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class BranchesAuthorizationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_owner_can_read_own_branches(self):
        """已登录 + 拥有 save：GET /api/branches/{owner_save_id} 应 200"""
        u = register_user(self.client)
        from platform_app.db import connect
        with connect() as db:
            uid_row = db.execute(
                "select id from users where username = %s", (u["username"],),
            ).fetchone()
            uid = int(uid_row["id"])
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "integtest_branch_owner_script"),
            ).fetchone()
            sv = db.execute(
                """
                insert into game_saves(user_id, script_id, title, state_path)
                values (%s, %s, %s, %s) returning id
                """,
                (uid, int(scr["id"]), "integtest_save", ""),
            ).fetchone()
            save_id = int(sv["id"])

        r = self.client.get(f"/api/branches/{save_id}", cookies=u["cookies"])
        self.assertEqual(
            r.status_code, 200,
            f"owner 应能读自己的分支，实际 {r.status_code} body={r.text[:200]}",
        )

    def test_foreign_user_gets_4xx_not_silent_data(self):
        """登录用户 B 读用户 A 的 save：必须 4xx，不能漏数据。
        这是前端 BranchesPage 修复前 console 报 403 的根因。
        """
        a = register_user(self.client)
        b = register_user(self.client)
        from platform_app.db import connect
        with connect() as db:
            uid_row = db.execute(
                "select id from users where username = %s", (a["username"],),
            ).fetchone()
            a_uid = int(uid_row["id"])
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (a_uid, "integtest_branch_foreign_script"),
            ).fetchone()
            sv = db.execute(
                """
                insert into game_saves(user_id, script_id, title, state_path)
                values (%s, %s, %s, %s) returning id
                """,
                (a_uid, int(scr["id"]), "integtest_a_save", ""),
            ).fetchone()
            a_save_id = int(sv["id"])

        r = self.client.get(f"/api/branches/{a_save_id}", cookies=b["cookies"])
        self.assertIn(
            r.status_code, (400, 401, 403, 404),
            f"跨用户读分支必须 4xx，实际 {r.status_code}：data leak 风险",
        )

    def test_anonymous_blocked(self):
        """匿名读任意分支：401（避免未登录用户也能爬别人的分支）"""
        r = self.client.get("/api/branches/1")
        self.assertIn(r.status_code, (400, 401, 403))


if __name__ == "__main__":
    unittest.main(verbosity=2)
