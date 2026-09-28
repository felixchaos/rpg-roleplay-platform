"""部署模式判定的同源守卫:同一个问题,生产线里不许有两个答案。

生产线与开源线的「部署模式缝」是有意保留的(生产用 deployment_mode() + LOCAL_MODES,
开源线收敛成 is_local_mode()/is_server_mode()),但缝里的**判定结果**必须和开源线一致。
回灌核对时发现两处不一致:

  1. 「要不要强制登录」有三份实现:config.effective_auth_required(注释自称「等价
     app.py:_api_auth_required」)、app._api_auth_required、_deps._auth_required。
     RPG_DEPLOYMENT_MODE 被设成空串时(例如编排文件里 `RPG_DEPLOYMENT_MODE=${VAR}` 而 VAR 未设),
     app.py 那份先经 deployment_mode_normalized() 把空串兜底成 "local" → 判「不强制」,
     另外两份判「强制」。于是 app.py 上的路由对匿名请求放行,platform 路由却要求登录。
     开源线早已让 app.py 直接委托 effective_auth_required(fail-closed)。
  2. app._check_probe_permission 用 `deployment_mode() == "local"` 判本地模式,
     漏掉 desktop / self_hosted 别名,也不容忍大小写与空白;其余各处都认 LOCAL_MODES。

本文件锁:三份鉴权判定对任意取值给出同一个答案;探测权限认全部本地别名。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import app as app_module
from core.config import effective_auth_required
from platform_app.api import _deps

# None = 环境里根本没有这个变量
_MODES = [None, "", "local", "desktop", "self_hosted", "self-hosted", " Local ",
          "server", "production", "prod", "cloud", "multiuser"]
_EXPLICIT = [None, "", "0", "1"]
_LOCAL_ALIASES = ["local", "desktop", "self_hosted", "self-hosted", " Local ", "DESKTOP"]


def _env(mode: str | None, explicit: str | None = None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("RPG_DEPLOYMENT_MODE", "RPG_REQUIRE_AUTH")}
    if mode is not None:
        env["RPG_DEPLOYMENT_MODE"] = mode
    if explicit is not None:
        env["RPG_REQUIRE_AUTH"] = explicit
    return mock.patch.dict(os.environ, env, clear=True)


class TestAuthRequiredHasOneAnswer(unittest.TestCase):
    def test_all_implementations_agree(self):
        for mode in _MODES:
            for explicit in _EXPLICIT:
                with self.subTest(mode=mode, explicit=explicit), _env(mode, explicit):
                    want = effective_auth_required()
                    self.assertEqual(app_module._api_auth_required(), want)
                    self.assertEqual(_deps._auth_required(), want)

    def test_empty_mode_is_fail_closed(self):
        """设了但为空 ≠ 没设:没设走默认 local,空串按未知模式处理 → 强制登录。"""
        with _env(""):
            self.assertTrue(app_module._api_auth_required())
            self.assertTrue(_deps._auth_required())
        with _env(None):
            self.assertFalse(app_module._api_auth_required())

    def test_empty_mode_admin_dependency_rejects_anonymous(self):
        """后果层面:空串时 app.py 的 get_current_admin(非 strict)依赖以前对匿名请求直接放行。"""
        from fastapi import HTTPException
        with _env(""), mock.patch.object(app_module, "platform_current_user", return_value=None):
            with self.assertRaises(HTTPException) as cm:
                app_module._require_api_user(mock.Mock(), admin=True)
            self.assertEqual(cm.exception.status_code, 401)


class TestProbePermissionKnowsEveryLocalAlias(unittest.TestCase):
    _USER = {"id": 7, "role": "user"}

    def _check(self, mode: str | None):
        with _env(mode), \
             mock.patch("platform_app.user_credentials.get_credential", return_value=None), \
             mock.patch("model_registry.load_model_catalog", return_value={}), \
             mock.patch("model_registry.find_api", return_value={"id": "x", "enabled": True}):
            return app_module._check_probe_permission(self._USER, "x")

    def test_local_aliases_allow_probe_with_server_credentials(self):
        for mode in _LOCAL_ALIASES:
            with self.subTest(mode=mode):
                self.assertIsNone(self._check(mode))

    def test_server_and_unknown_modes_deny(self):
        for mode in ("server", "prod", "multiuser", ""):
            with self.subTest(mode=mode):
                resp = self._check(mode)
                self.assertIsNotNone(resp)
                self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
