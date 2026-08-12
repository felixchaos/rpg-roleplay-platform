"""
test_credential_auth_mode.py —— 本地/自托管模型「免 API Key」显式选项(auth_mode)。

来源:OSS PR #102 的诉求(Ollama / vLLM / llama.cpp 只有 base_url、没有 Key),
但**没有采用**该 PR 的隐式判定「空 key + base_url 即放行」——那会让任何一个 key 被清空的
托管 provider 悄悄变成"可用",撞 401 才发现。改成用户显式声明 auth_mode='none'。

锁死的不变量:
  1. BYOK 墙对普通 provider 一字不变:auth_mode='api_key' 且无 key → 不可用。
  2. auth_mode='none' 且有 base_url → 可用(这是本次放开的唯一口子)。
  3. auth_mode='none' 但没 base_url → **不可用**(不指地址的"免 Key"是残缺行,不是本地模型)。
  4. 免鉴权 + 用户仍填了 key → key 照常发送(「同时兼容本地模型 key 和免 key」)。
  5. 送进 openai SDK 的 token 永不为空串 —— 实测 SDK 对 "" 和 None 一样抛
     OpenAIError("Missing credentials"),构造 client 就崩,连请求都发不出去。
  6. SQL 侧判据只有一份(CREDENTIAL_USABLE_SQL),不许各处再抄 length(encrypted_key)>0。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from platform_app.user_credentials import (
    CREDENTIAL_USABLE_SQL,
    NO_AUTH_PLACEHOLDER,
    credential_is_usable,
    resolved_auth_token,
    resolved_is_usable,
)

RPG = Path(__file__).resolve().parents[2]


class CredentialUsability(unittest.TestCase):
    def test_api_key_mode_needs_key(self):
        self.assertFalse(credential_is_usable({"auth_mode": "api_key", "key": "",
                                               "base_url_override": "https://x/v1"}))
        self.assertTrue(credential_is_usable({"auth_mode": "api_key", "key": "sk-1",
                                              "base_url_override": ""}))

    def test_no_auth_mode_usable_without_key(self):
        self.assertTrue(credential_is_usable({"auth_mode": "none", "key": "",
                                              "base_url_override": "http://127.0.0.1:11434/v1"}))

    def test_no_auth_without_base_url_is_not_usable(self):
        # 不指地址的「免 Key」= 残缺行,不能当本地模型放行
        self.assertFalse(credential_is_usable({"auth_mode": "none", "key": "",
                                               "base_url_override": ""}))

    def test_missing_cred(self):
        self.assertFalse(credential_is_usable(None))
        self.assertFalse(credential_is_usable({}))


class ResolvedResultHelpers(unittest.TestCase):
    def test_no_auth_source_is_usable(self):
        r = {"key": "", "source": "user_db_no_auth", "base_url_override": "http://127.0.0.1:11434/v1"}
        self.assertTrue(resolved_is_usable(r))

    def test_plain_missing_key_is_not_usable(self):
        self.assertFalse(resolved_is_usable({"key": "", "source": "none", "base_url_override": ""}))
        self.assertFalse(resolved_is_usable(None))

    def test_token_never_empty_for_no_auth(self):
        # 关键:openai SDK 对空串同样抛 Missing credentials → 必须给占位 token
        tok = resolved_auth_token({"key": "", "source": "user_db_no_auth"})
        self.assertTrue(tok, "免鉴权必须返回非空占位 token,否则 SDK 构造即崩")
        self.assertEqual(tok, NO_AUTH_PLACEHOLDER)

    def test_real_key_wins_over_placeholder(self):
        # 「同时兼容本地模型 key 和免 key」:填了 key 就必须发 key
        self.assertEqual(resolved_auth_token({"key": "sk-real", "source": "user_db"}), "sk-real")
        self.assertEqual(resolved_auth_token({"key": "sk-real", "source": "user_db_no_auth"}), "sk-real")

    def test_no_token_for_unconfigured(self):
        self.assertEqual(resolved_auth_token({"key": "", "source": "none"}), "")


class SqlPredicateIsSingleSource(unittest.TestCase):
    def test_predicate_covers_both_modes(self):
        sql = " ".join(CREDENTIAL_USABLE_SQL.split())
        self.assertIn("length(encrypted_key) > 0", sql)
        self.assertIn("auth_mode = 'none'", sql)
        self.assertIn("base_url_override", sql, "免鉴权必须同时要求 base_url")

    def test_no_stray_copies_of_the_predicate(self):
        """除了单一真相源本身,生产代码里不许再出现手写的 length(encrypted_key)>0 判据。

        这个条件历史上被抄在 llm_backend 两处 + feedback 一处;加免鉴权时漏改任何一处,
        本地 provider 就会在那条路径上"半可用"(能选不能跑 / 能跑不上报)。
        """
        offenders = []
        for path in RPG.rglob("*.py"):
            rel = path.relative_to(RPG).as_posix()
            if rel.startswith(("tests/", "claude_design_upload/")):
                continue
            if rel == "platform_app/user_credentials.py":
                continue  # 真相源自己 + 注释
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"length\(\s*encrypted_key\s*\)\s*>\s*0", text):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}")
        self.assertEqual(offenders, [],
                         "这些地方又手写了凭据可用性谓词,应改用 CREDENTIAL_USABLE_SQL: "
                         + ", ".join(offenders))


class OpenAiBackendNeverPassesEmptyKey(unittest.TestCase):
    def test_backend_uses_resolved_token(self):
        src = (RPG / "agents" / "gm" / "backends" / "openai_compat.py").read_text(encoding="utf-8")
        self.assertIn("resolved_auth_token", src)
        self.assertIn("resolved_is_usable", src)
        # 不许再用「key 为空」当放行判据(那会把 key 被清空的托管 provider 也放进来)
        self.assertNotRegex(
            src, r'if\s+not\s+key\s*:\s*\n\s*raise ValueError',
            "放行判据必须是 resolved_is_usable(source),不是 key 是否为空",
        )


class MigrationAppendOnly(unittest.TestCase):
    def test_auth_mode_migration_present_and_last(self):
        src = (RPG / "platform_app" / "db" / "migrations.py").read_text(encoding="utf-8")
        self.assertIn("user_api_credentials_auth_mode", src)
        self.assertIn("add column if not exists auth_mode", src)
        versions = [int(v) for v in re.findall(r"^\s*\((\d+),\s*\"", src, re.M)]
        self.assertEqual(versions, sorted(versions), "MIGRATIONS 必须版本号单调递增")
        self.assertEqual(len(versions), len(set(versions)), "MIGRATIONS 版本号不能重复")


if __name__ == "__main__":
    unittest.main()
