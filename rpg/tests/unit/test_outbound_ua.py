"""
test_outbound_ua.py
===================

回归:用户自建中转站挂 Cloudflare 后,WAF 按 User-Agent 拦截。openai SDK 默认 UA
`OpenAI/Python <ver>` 会被 403「Your request was blocked」/ error 1010 直接挡掉
→ 这类中转站「校验连接 / 拉取模型 / 聊天」全部「不可访问」。实测浏览器 UA 能穿透。

不变量(锁死):
  · 所有出站 OpenAI 兼容请求统一覆盖 UA,且**绝不**是 openai SDK / urllib 的默认签名。
  · 两个 SDK 构造点(生成 openai_compat.py、列模型 model_probe.py)都把 default_headers
    传进 OpenAI(...)。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # rpg/
import sys

if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.outbound_ua import openai_default_headers, outbound_user_agent  # noqa: E402

OPENAI_COMPAT_PY = (PROJECT / "agents" / "gm" / "backends" / "openai_compat.py").read_text(encoding="utf-8")
MODEL_PROBE_PY = (PROJECT / "model_probe.py").read_text(encoding="utf-8")


class OutboundUaHelper(unittest.TestCase):
    def test_default_ua_is_not_a_blocked_signature(self):
        ua = outbound_user_agent()
        self.assertTrue(ua)
        self.assertNotIn("OpenAI/Python", ua)
        self.assertNotIn("urllib", ua.lower())
        # 默认走浏览器签名(最稳穿透 WAF)
        self.assertIn("Mozilla/5.0", ua)

    def test_header_dict_sets_user_agent(self):
        h = openai_default_headers()
        self.assertEqual(set(h), {"User-Agent"})
        self.assertEqual(h["User-Agent"], outbound_user_agent())

    def test_env_override_respected(self):
        prev = os.environ.get("RPG_OUTBOUND_UA")
        try:
            os.environ["RPG_OUTBOUND_UA"] = "my-custom-agent/9.9"
            self.assertEqual(outbound_user_agent(), "my-custom-agent/9.9")
            self.assertEqual(openai_default_headers()["User-Agent"], "my-custom-agent/9.9")
        finally:
            if prev is None:
                os.environ.pop("RPG_OUTBOUND_UA", None)
            else:
                os.environ["RPG_OUTBOUND_UA"] = prev

    def test_blank_env_falls_back_to_default(self):
        prev = os.environ.get("RPG_OUTBOUND_UA")
        try:
            os.environ["RPG_OUTBOUND_UA"] = "   "
            self.assertIn("Mozilla/5.0", outbound_user_agent())
        finally:
            if prev is None:
                os.environ.pop("RPG_OUTBOUND_UA", None)
            else:
                os.environ["RPG_OUTBOUND_UA"] = prev


class BothSdkSitesOverrideUa(unittest.TestCase):
    def test_generation_passes_default_headers(self):
        self.assertIn("from core.outbound_ua import openai_default_headers", OPENAI_COMPAT_PY)
        self.assertIn('"default_headers": openai_default_headers()', OPENAI_COMPAT_PY)

    def test_model_listing_passes_default_headers(self):
        self.assertIn("from core.outbound_ua import openai_default_headers", MODEL_PROBE_PY)
        # kwargs 字典现为多行(并入了 http_client=safe_httpx_client(...) 的 SSRF 加固)→ 用
        # [\s\S]*? 跨行匹配,仍锁死「default_headers 走 openai_default_headers()」这一不变量。
        self.assertRegex(
            MODEL_PROBE_PY,
            r'kwargs[\s\S]*?=\s*\{[\s\S]*?"default_headers":\s*openai_default_headers\(\)',
        )


class SdkActuallyOverridesBuiltinUa(unittest.TestCase):
    """行为验证:openai SDK 把我们的 default_headers 合并在最后,确实覆盖内置 UA。"""

    def test_resolved_user_agent_is_overridden(self):
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"openai SDK 不可用: {exc}")
        c = OpenAI(api_key="x", base_url="https://example.com/v1", default_headers=openai_default_headers())
        self.assertEqual(c.default_headers.get("User-Agent"), outbound_user_agent())
        self.assertNotIn("OpenAI/Python", c.default_headers.get("User-Agent", ""))


class SafeUrlopenInjectsUa(unittest.TestCase):
    """回归(2026-06-23):urllib 出站(_harness 子代理 / extractor / embedding / 生图下载)
    走 safe_urlopen,此前漏覆盖 UA → 默认 Python-urllib 被 opencode.ai/zen 等网关 WAF 403,
    而 GM 走 httpx(已覆盖 UA)200 → 「同 key 同模型 GM 通子代理挂」。safe_urlopen 现统一注入
    outbound_user_agent();调用方显式设的 UA 不动。"""

    def _capture_opened_req(self, req):
        import urllib.request as _u

        from core import outbound as ob
        captured = {}

        class _FakeOpener:
            def open(self, r, timeout=None):
                captured["req"] = r

                class _Resp:
                    def __enter__(self_inner):
                        return self_inner

                    def __exit__(self_inner, *a):
                        return False

                return _Resp()

        orig_enforced = ob._ssrf_enforced
        orig_build = _u.build_opener
        try:
            ob._ssrf_enforced = lambda: False  # 跳过 DNS 解析 / IP pin,纯测 UA 注入
            _u.build_opener = lambda *a, **k: _FakeOpener()
            with ob.safe_urlopen(req, timeout=5):
                pass
        finally:
            ob._ssrf_enforced = orig_enforced
            _u.build_opener = orig_build
        return captured["req"]

    def test_injects_default_ua_when_absent(self):
        import urllib.request as _u
        req = _u.Request(
            "https://example.com/v1/chat/completions",
            data=b"{}", method="POST",
            headers={"Authorization": "Bearer x"},
        )
        out = self._capture_opened_req(req)
        self.assertEqual(out.get_header("User-agent"), outbound_user_agent())
        self.assertNotIn("urllib", (out.get_header("User-agent") or "").lower())

    def test_respects_explicit_ua(self):
        import urllib.request as _u
        req = _u.Request(
            "https://example.com/v1/chat/completions",
            headers={"User-Agent": "custom/1.0"},
        )
        out = self._capture_opened_req(req)
        self.assertEqual(out.get_header("User-agent"), "custom/1.0")


if __name__ == "__main__":
    unittest.main()
