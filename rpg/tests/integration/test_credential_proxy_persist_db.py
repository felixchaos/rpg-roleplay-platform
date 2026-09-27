"""凭据代理持久化的真库往返(反馈 #107)。

本地版连不上某个供应商时,出路是在「连接方式」里配 HTTP 代理。以前这条路有两个洞:
  · 设置页只改代理、不重填 key → keep_key 路径根本不写 metadata,提示成功但没存上;
  · 手机端 / 供应商卡片这些没有代理输入的表单重新存 key → metadata 被覆盖成 {},
    把别处配好的代理悄悄清掉。

约定(单测锁参数派发,这里锁 SQL 真的这么落库):
  proxy=None  → 不动已存代理(新行则为空);
  proxy=""    → 清掉代理;
  proxy="..." → 换成这个代理;
  完整保存与 keep_key 两条路径同一语义,metadata 里别的键不受影响。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("RPG_DEPLOYMENT_MODE", "local")  # 本地模式 → 加密走文件 fallback

_PROXY_A = "http://127.0.0.1:7890"
_PROXY_B = "http://127.0.0.1:7891"


class TestCredentialProxyPersist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            db.execute("delete from users where username = %s", ("integtest_proxy_persist",))
            row = db.execute(
                """
                insert into users(username, display_name, password_hash, email)
                values (%s, %s, %s, %s)
                returning id
                """,
                ("integtest_proxy_persist", "Proxy Persist", "x", "integtest_proxy_persist@example.test"),
            ).fetchone()
            cls.uid = int(row["id"])

    @classmethod
    def tearDownClass(cls):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from user_model_entries where user_id = %s", (cls.uid,))
            db.execute("delete from user_api_credentials where user_id = %s", (cls.uid,))
            db.execute("delete from users where id = %s", (cls.uid,))

    def setUp(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from user_api_credentials where user_id = %s", (self.uid,))
        # 本地单用户模式:允许 127.0.0.1 代理;保存后的模型同步打桩,零真实网络
        import core.config
        import model_probe
        for target, attr, value in (
            (core.config, "require_auth", lambda: False),
            (model_probe, "list_remote_models", lambda *a, **k: {"ok": False, "error": "stub", "models": []}),
        ):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def _meta(self) -> dict:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select metadata from user_api_credentials where user_id = %s and api_id = %s",
                (self.uid, "deepseek"),
            ).fetchone()
        return dict((row or {}).get("metadata") or {})

    def _listed_proxy(self) -> str:
        from platform_app import user_credentials
        items = user_credentials.list_credentials(self.uid).get("items") or []
        hit = [c for c in items if c.get("api_id") == "deepseek"]
        self.assertEqual(len(hit), 1)
        return hit[0].get("proxy_url") or ""

    def _save(self, key: str, **kw):
        from platform_app import user_credentials
        return user_credentials.set_credential(self.uid, "deepseek", key, allow_base_url=True, **kw)

    def test_full_save_semantics(self):
        self._save("sk-one", proxy=_PROXY_A)
        self.assertEqual(self._meta().get("proxy"), _PROXY_A)
        self.assertEqual(self._listed_proxy(), _PROXY_A)

        # 手机端 / 供应商卡片重新存 key:没提代理 → 保留
        self._save("sk-two")
        self.assertEqual(self._meta().get("proxy"), _PROXY_A)

        # 设置页选回「直连」:显式空串 → 清掉
        self._save("sk-three", proxy="")
        self.assertNotIn("proxy", self._meta())
        self.assertEqual(self._listed_proxy(), "")

    def test_new_row_without_proxy_is_empty(self):
        self._save("sk-one")
        self.assertEqual(self._meta(), {})

    def test_keep_key_semantics(self):
        self._save("sk-one", proxy=_PROXY_A)

        # 只改代理、不重填 key
        self._save("", preserve_key_if_empty=True, proxy=_PROXY_B)
        self.assertEqual(self._meta().get("proxy"), _PROXY_B)

        # 只改地址、没提代理 → 代理保留
        self._save("", preserve_key_if_empty=True, base_url_override="https://relay.example.com/v1")
        self.assertEqual(self._meta().get("proxy"), _PROXY_B)

        # 改回直连
        self._save("", preserve_key_if_empty=True, proxy="")
        self.assertNotIn("proxy", self._meta())

        # key 还在(keep_key 没把凭据删掉)
        from platform_app import user_credentials
        self.assertEqual((user_credentials.get_credential(self.uid, "deepseek") or {}).get("key"), "sk-one")

    def _base_url(self) -> str:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select base_url_override from user_api_credentials where user_id = %s and api_id = %s",
                (self.uid, "deepseek"),
            ).fetchone()
        return str((row or {}).get("base_url_override") or "")

    def test_base_url_override_tristate(self):
        """接口地址与代理同一约定:None = 不动已存值;"" = 清空;其余 = 替换。
        手机端「API」只带 key 重存,不能把设置页配好的中转站地址冲掉。"""
        relay = "https://relay.example.com/v1"
        self._save("sk-one", base_url_override=relay)
        self.assertEqual(self._base_url(), relay)

        # 没有地址输入的表单重新存 key
        self._save("sk-two", base_url_override=None)
        self.assertEqual(self._base_url(), relay)
        from platform_app import user_credentials
        self.assertEqual((user_credentials.get_credential(self.uid, "deepseek") or {}).get("key"), "sk-two")
        self.assertEqual(user_credentials.stored_base_url_override(self.uid, "deepseek"), relay)

        # keep_key 只改代理、没提地址
        self._save("", preserve_key_if_empty=True, base_url_override=None, proxy=_PROXY_A)
        self.assertEqual(self._base_url(), relay)
        self.assertEqual(self._meta().get("proxy"), _PROXY_A)

        # 显式清空(回到目录默认地址)
        self._save("sk-three", base_url_override="")
        self.assertEqual(self._base_url(), "")

    def test_new_row_without_base_url_is_empty(self):
        self._save("sk-one", base_url_override=None)
        self.assertEqual(self._base_url(), "")

    def test_other_metadata_keys_survive_proxy_changes(self):
        from psycopg.types.json import Jsonb

        from platform_app.db import connect
        self._save("sk-one", proxy=_PROXY_A)
        with connect() as db:
            db.execute(
                "update user_api_credentials set metadata = metadata || %s "
                "where user_id = %s and api_id = %s",
                (Jsonb({"note": "keep-me"}), self.uid, "deepseek"),
            )
        self._save("", preserve_key_if_empty=True, proxy=_PROXY_B)
        self.assertEqual(self._meta(), {"proxy": _PROXY_B, "note": "keep-me"})
        self._save("sk-two", proxy="")
        self.assertEqual(self._meta(), {"note": "keep-me"})


if __name__ == "__main__":
    unittest.main()
