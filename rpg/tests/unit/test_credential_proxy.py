"""
test_credential_proxy.py
========================

「连接方式 = HTTP 代理」做成真·每凭据出站代理(本地部署用户经梯子访问 Google 等)。

关键安全不变量(必须锁死):per-credential proxy 是 SSRF 风险源 —— 代理 URL 合法地可指向
127.0.0.1(本地梯子),无法用「禁私网」校验拦截。因此 **proxy 只在本地模式(非 require_auth)
才真正被 httpx 使用**;托管多用户后端(require_auth=True)永不使用用户 proxy → 零 SSRF。
若有人去掉这条 gate,等于给托管后端开了 SSRF 口子 —— 本测试就是防回归。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
OPENAI_COMPAT_PY = (PROJECT / "rpg" / "agents" / "gm" / "backends" / "openai_compat.py").read_text(encoding="utf-8")
USER_CRED_PY = (PROJECT / "rpg" / "platform_app" / "user_credentials.py").read_text(encoding="utf-8")


class ProxyOnlyUsedInLocalMode(unittest.TestCase):
    def test_proxy_gated_behind_not_byok_only(self):
        """httpx 客户端只在 `not byok_only`(=非 require_auth=本地模式)时才带 proxy。"""
        # 必须出现「读 proxy」+「按 not byok_only 门控后才塞进 client kwargs」
        self.assertIn('result.get("proxy")', OPENAI_COMPAT_PY)
        self.assertRegex(
            OPENAI_COMPAT_PY,
            r'if\s+_proxy\s+and\s+not\s+byok_only',
            "proxy 必须用 `if _proxy and not byok_only` 门控 —— 托管后端(require_auth)绝不能用用户 proxy(SSRF)。",
        )
        # proxy 只能通过那个门控分支进入 client kwargs
        self.assertRegex(OPENAI_COMPAT_PY, r'_client_kwargs\["proxy"\]\s*=\s*_proxy')

    def test_no_unconditional_proxy_pass(self):
        """不得有「无条件把 proxy 传给 httpx.Client」的写法。"""
        # httpx.Client(...) 的实参里不应直接出现 proxy=（必须走 _client_kwargs 门控）
        for m in re.finditer(r'httpx\.Client\(([^)]*)\)', OPENAI_COMPAT_PY):
            self.assertNotIn('proxy=', m.group(1),
                "httpx.Client(...) 不应直接传 proxy= —— 必须经 not byok_only 门控的 _client_kwargs。")


class SetCredentialValidatesProxy(unittest.TestCase):
    def test_proxy_param_exists(self):
        self.assertRegex(USER_CRED_PY, r'def set_credential\([^)]*proxy:\s*str',
                         "set_credential 应接 proxy 参数。")

    def test_proxy_format_validated_but_not_ssrf_blocked(self):
        """proxy 做格式校验(scheme://host),但**不**调 _validate_base_url(那会拦 127.0.0.1,
        而本地梯子恰恰是 localhost)。"""
        self.assertIn("socks5", USER_CRED_PY)  # 允许 socks5 代理
        # 找到 proxy 校验那段,确认它用的是格式正则,而不是 _validate_base_url(proxy)
        self.assertRegex(USER_CRED_PY, r'proxy.*re\.match', )
        self.assertNotRegex(USER_CRED_PY, r'_validate_base_url\(\s*proxy\s*\)',
                            "不能对 proxy 调 _validate_base_url —— 会拦掉合法的本地 127.0.0.1 梯子。")

    def test_local_proxy_url_passes_regex(self):
        """本地梯子地址(127.0.0.1)必须通过格式校验。"""
        rx = re.compile(r"^(https?|socks5h?)://[^\s/]+", re.IGNORECASE)
        for ok in ("http://127.0.0.1:7890", "socks5://127.0.0.1:1080", "https://proxy.lan:8080"):
            self.assertTrue(rx.match(ok), f"{ok} 应通过")
        for bad in ("127.0.0.1:7890", "javascript:alert(1)", "ftp://x"):
            self.assertFalse(rx.match(bad), f"{bad} 应被拒")


if __name__ == "__main__":
    unittest.main()
