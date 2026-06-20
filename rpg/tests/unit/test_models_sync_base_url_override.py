"""
test_models_sync_base_url_override.py
=====================================

回归:用户把内置 provider(如 OpenAI)的 Base URL 改成自建中转站
(user_api_credentials.base_url_override),但「校验连接 / 拉取模型」
(`POST /api/models/remote/sync`)永远打 catalog 里的官方端点(api.openai.com),
拿中转站的 key 打官方 → 联通性「不可访问」、拉到的模型不是中转站真实清单。

根因(确定性,落代码缝):
  · `_redact_catalog` 对非 admin 抹掉 api.base_url(部署形状信息)→ 前端 body.base_url 传空;
  · 旧 sync 端点 `base_url = body.base_url or catalog默认`,再 `if not base_url: base_url = cred_base`
    —— catalog 默认非空,cred_base(中转站)永远兜不到。

不变量(锁死):sync 端点解析 base_url 时,**用户凭证的 base_url_override 优先**于
body / catalog 默认。与生成路径(openai_compat.py 早已 base_url_override 优先)保持一致。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
MODELS_PY = (PROJECT / "rpg" / "routes" / "models.py").read_text(encoding="utf-8")
MODEL_PROBE_PY = (PROJECT / "rpg" / "model_probe.py").read_text(encoding="utf-8")
OPENAI_COMPAT_PY = (PROJECT / "rpg" / "agents" / "gm" / "backends" / "openai_compat.py").read_text(encoding="utf-8")
SETTINGS_JSX = (PROJECT / "frontend" / "src" / "pages" / "settings.jsx").read_text(encoding="utf-8")
MOBILE_SETTINGS_JSX = (PROJECT / "frontend" / "src" / "mobile" / "pages" / "MobileSettings.jsx").read_text(encoding="utf-8")


class SyncEndpointPrefersCredentialOverride(unittest.TestCase):
    def test_base_url_override_has_priority(self):
        """cred_base(base_url_override)必须排在 base_url 解析链最前面。"""
        # 解析行形如:  base_url = cred_base or (body or {}).get("base_url") or meta_api.get("base_url", "")
        self.assertRegex(
            MODELS_PY,
            r"base_url\s*=\s*cred_base\s+or\s+\(body[^\n]*\)\.get\(\"base_url\"\)\s+or\s+meta_api\.get\(\"base_url\"",
            "sync 端点必须 `base_url = cred_base or body... or catalog...`(override 优先)",
        )

    def test_old_fallback_only_pattern_is_gone(self):
        """旧的「仅当 body 为空才兜底 cred」反模式必须删除,否则官方端点(非空)永远赢。"""
        self.assertNotRegex(
            MODELS_PY,
            r"if\s+not\s+base_url\s*:\s*\n\s*base_url\s*=\s*cred_base",
            "旧反模式 `if not base_url: base_url = cred_base` 仍在 → override 会被官方默认压住",
        )

    def test_cred_base_still_read_from_credential(self):
        """仍然从用户凭证读取 base_url_override(权威来源)。"""
        self.assertIn('cred_base = (_cred or {}).get("base_url_override") or ""', MODELS_PY)

    def test_ssrf_validation_retained(self):
        """最终 base_url 仍过 _validate_base_url(override 在落库时已校验为公网,这里也会通过)。"""
        self.assertIn("_validate_base_url(base_url)", MODELS_PY)


class RemoteListProbeHonorsOverride(unittest.TestCase):
    """task #9 只修了 admin 的 `/api/models/remote/sync`(显式 api_override 注入 base_url)。
    但 per-user `GET /api/models/remote` → list_remote_models(无 api_override)→ 走 catalog
    裸 api → `_list_openai_compat_models` 旧实现只读 `api.get("base_url")`(官方端点),无视
    用户「连接方式」里配的 base_url_override。表现:运行路径(openai_compat backend)能打到
    自建中转站 / 本地 llama.cpp,但选择器「拉取模型」却空/错(看不到、选不到自己的模型)。
    不变量:probe 拉模型清单也必须 base_url_override 优先,与运行/sync 两路径对齐。
    """

    def test_probe_resolves_credential_override(self):
        """_list_openai_compat_models 必须通过 _resolve_provider_creds 拿到 base_url_override。"""
        self.assertIn("_resolve_provider_creds(api, user_id)", MODEL_PROBE_PY)

    def test_probe_prefers_override_over_catalog(self):
        """base_url 解析:per-credential override 优先于 catalog 默认。"""
        self.assertRegex(
            MODEL_PROBE_PY,
            r'base_url\s*=\s*creds\["base_url_override"\]\s+or\s+api\.get\("base_url"\)',
            "probe 必须 `base_url = creds[base_url_override] or api.base_url`(override 优先)",
        )

    def test_combined_resolver_returns_override(self):
        """_resolve_provider_creds 返回 key + base_url_override(单一真源 resolve_api_key)。"""
        self.assertIn('"base_url_override": (result.get("base_url_override") or "").strip()', MODEL_PROBE_PY)


class GenerationAlreadyHonorsOverride(unittest.TestCase):
    def test_openai_compat_prefers_override(self):
        """sync 端点的修复使其与生成路径一致 —— 生成早已 base_url_override 优先。"""
        self.assertRegex(
            OPENAI_COMPAT_PY,
            r'effective_base\s*=\s*result\.get\("base_url_override"\)\s+or\s+base_url',
        )


class FrontendRowUsesOwnOverride(unittest.TestCase):
    def test_desktop_row_prefers_credential_override(self):
        """非 admin 的 api.base_url 被 redact 成空;行/编辑弹窗须用 cred.base_url_override 兜底,
        避免显示空、且重新保存 key 时把 override 清掉。"""
        self.assertRegex(
            SETTINGS_JSX,
            r"base_url:\s*cred\.base_url_override\s*\|\|\s*api\.base_url",
        )

    def test_mobile_row_prefers_credential_override(self):
        self.assertRegex(
            MOBILE_SETTINGS_JSX,
            r"base_url:\s*cred\.base_url_override\s*\|\|\s*api\.base_url",
        )

    def test_mobile_credmap_carries_override(self):
        """mobile credMap 之前不带 base_url_override,补上才有得兜底。"""
        self.assertIn("base_url_override: c.base_url_override", MOBILE_SETTINGS_JSX)


if __name__ == "__main__":
    unittest.main()
