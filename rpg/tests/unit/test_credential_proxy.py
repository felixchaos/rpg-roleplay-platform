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
        """httpx 客户端只在 `not byok_only`(=非 require_auth=本地模式)时才带 proxy。

        SSRF 统一出站层改造后,门控写法从 `if _proxy and not byok_only: kwargs["proxy"]=…`
        变为三元 `_use_proxy = _proxy if (_proxy and not byok_only) else None`,再喂给
        core.outbound.safe_httpx_client(proxy=_use_proxy)。守护语义不变:托管后端
        (require_auth=True → byok_only)拿到的永远是 None。
        """
        # 必须出现「读 proxy」+「(_proxy and not byok_only) 门控出 _use_proxy」
        self.assertIn('result.get("proxy")', OPENAI_COMPAT_PY)
        self.assertRegex(
            OPENAI_COMPAT_PY,
            r'_use_proxy\s*=\s*_proxy\s+if\s*\(\s*_proxy\s+and\s+not\s+byok_only\s*\)\s*else\s+None',
            "proxy 必须经 `(_proxy and not byok_only)` 门控成 _use_proxy(否则为 None)"
            " —— 托管后端(require_auth)绝不能用用户 proxy(SSRF)。",
        )
        # proxy 只能以门控后的 _use_proxy 进入出站客户端(SSRF 安全层 safe_httpx_client)
        self.assertRegex(OPENAI_COMPAT_PY, r'safe_httpx_client\([^)]*proxy=_use_proxy')

    def test_no_unconditional_proxy_pass(self):
        """不得有「未经门控就把 proxy 传给出站客户端」的写法。"""
        # httpx.Client(...) 的实参里不应直接出现 proxy=（必须走 safe_httpx_client）
        for m in re.finditer(r'httpx\.Client\(([^)]*)\)', OPENAI_COMPAT_PY):
            self.assertNotIn('proxy=', m.group(1),
                "httpx.Client(...) 不应直接传 proxy= —— 必须经门控走 safe_httpx_client。")
        # safe_httpx_client(...) 若带 proxy 实参,只允许是门控后的 _use_proxy,
        # 严禁直接传未门控的 _proxy / result.get("proxy")。
        for m in re.finditer(r'safe_httpx_client\(([^)]*)\)', OPENAI_COMPAT_PY):
            args = m.group(1)
            if 'proxy=' in args:
                self.assertIn('proxy=_use_proxy', args,
                    "safe_httpx_client 的 proxy 只能传门控后的 _use_proxy(托管后端=None)。")


class SetCredentialValidatesProxy(unittest.TestCase):
    def test_proxy_param_exists(self):
        self.assertRegex(USER_CRED_PY, r'def set_credential\([^)]*proxy:\s*str',
                         "set_credential 应接 proxy 参数。")

    def test_proxy_format_validated_but_not_ssrf_blocked(self):
        """proxy 做格式校验(scheme://host),但**不**调 _validate_base_url(那会拦 127.0.0.1,
        而本地梯子恰恰是 localhost)。"""
        self.assertIn("socks5", USER_CRED_PY)  # 允许 socks5 代理
        # 找到 proxy 校验那段,确认它用的是格式正则,而不是 _validate_base_url(proxy)。
        # 代码现写作 `re.match(r"...", proxy, ...)`(proxy 在 re.match 之后)→ 用此序匹配。
        self.assertRegex(USER_CRED_PY, r're\.match\([^\n]*proxy')
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
