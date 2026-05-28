"""
test_anon_platform_gate.py — UI 审计任务 3 回归

防止再次出现：
1) data-loader 在匿名态下泄露 mock admin 用户
2) Platform.html / Game Console.html 未在加载完成后跳转 Login
3) /api/auth/me 给匿名返回 user!=null 让前端误判
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import cleanup_test_users, make_client, register_user

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"


class FrontendGateWiring(unittest.TestCase):
    """静态扫描前端文件，确认 gate 代码在位（防止 revert）。"""

    def test_data_loader_exposes_authed_flag(self):
        src = (FRONTEND / "src" / "data-loader.js").read_text(encoding="utf-8")
        self.assertIn("window.RPG_AUTH", src, "data-loader 必须暴露 window.RPG_AUTH 让 mount 同步读")
        self.assertIn("authed", src)
        self.assertIn("anonymizeUser", src, "匿名时必须脱敏 platform.user，否则 mock admin 漏到 UI")

    def test_data_loader_resolves_with_authed(self):
        src = (FRONTEND / "src" / "data-loader.js").read_text(encoding="utf-8")
        # bootstrap 应把 authed 传给 resolvers；同时检查异常路径也带上
        self.assertIn("readyResolvers.forEach", src)
        # 两条 resolve 路径（有 window.api / 没有 window.api）都要带 authed
        resolve_lines = [ln for ln in src.splitlines() if "readyResolvers.forEach" in ln]
        for ln in resolve_lines:
            self.assertIn("authed", ln, f"resolve 行必须传 authed：{ln.strip()}")

    def test_platform_html_gates_on_authed(self):
        src = (FRONTEND / "Platform.html").read_text(encoding="utf-8")
        self.assertIn("info.authed", src, "Platform.html 必须用 info.authed 判断")
        self.assertIn("Login.html", src, "未登录必须跳 Login.html")
        self.assertIn("offline", src, "?offline=1 设计预览旁路必须保留")

    def test_game_console_html_gates_on_authed(self):
        src = (FRONTEND / "Game Console.html").read_text(encoding="utf-8")
        self.assertIn("info.authed", src)
        self.assertIn("Login.html", src)

    def test_login_html_NOT_gated(self):
        # Login.html 自己不能跳 Login.html，否则死循环
        src = (FRONTEND / "Login.html").read_text(encoding="utf-8")
        self.assertNotIn("location.replace(\"Login.html\"", src)
        self.assertNotIn("location.href = \"Login.html\"", src)

    def test_authpage_honors_next_param_safely(self):
        src = (FRONTEND / "src" / "platform-app.jsx").read_text(encoding="utf-8")
        # 安全：必须有开放重定向防护
        self.assertIn("__nextOrDefault", src)
        self.assertRegex(src, r"\[a-z\]\[a-z0-9\+\.\\-\]\*:|\^\\/\\/", "next= 必须拒绝绝对 URL/协议相对 URL")


class BackendAnonContract(unittest.TestCase):
    """后端契约：匿名 me 返回 user=null（驱动前端 authed=false 分支）。
    如果哪天后端把这个改成 user={...guest...}，前端 gate 会失效。
    """

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_anonymous_me_returns_null_user(self):
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 200, "/api/auth/me 必须对匿名 200，否则前端连 gate 都跑不到")
        body = r.json()
        self.assertIsNone(body.get("user"), f"匿名 user 必须 null，实际：{body.get('user')}")

    def test_anonymous_business_endpoints_4xx(self):
        # Gate 假设这些接口对匿名 401/403，不能静默 200 漏数据
        for p in ("/api/scripts", "/api/saves", "/api/library?path="):
            r = self.client.get(p)
            self.assertIn(r.status_code, (400, 401, 403), f"{p} 匿名必须 4xx")

    def test_authenticated_me_returns_user(self):
        u = register_user(self.client)
        r = self.client.get("/api/auth/me", cookies=u["cookies"])
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json().get("user"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
