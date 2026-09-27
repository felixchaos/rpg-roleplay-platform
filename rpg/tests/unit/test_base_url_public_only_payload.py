"""/api/state 的 app.base_url_public_only 必须与 _validate_base_url 的真实判据同源。

背景:设置页 EditApiModal 会对「后端必拒」的 base_url(http / 本机 / 局域网)提前报错并禁用
保存。以前前端从 app.deployment 串自己推「是不是云端」,和后端 _validate_base_url 用的
require_auth() 错位:
  · RPG_DEPLOYMENT_MODE=multiuser(开源 docker-compose 默认)后端必拒,前端不提示;
  · local + RPG_REQUIRE_AUTH=1 同上;
  · server + RPG_REQUIRE_AUTH=0 后端放行,前端却把保存按钮禁掉。
现在后端直接把判据放进 payload,前端只读这个布尔。

锁的不变量:对每种 env 组合,payload 里的 flag == 「_validate_base_url 会不会拒本机 http
地址」。改任何一侧的判据,这里都会红。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import app as app_module
from platform_app.user_credentials import _validate_base_url

_MODEL = {
    "display_name": "M", "real_name": "m-1", "capabilities": [],
    "api_display_name": "A", "api_id": "a", "model_id": "m-1",
}


class _FakeState:
    data: dict = {}

    def status_payload(self) -> dict:
        return {}


def _payload_app(env: dict[str, str]) -> dict:
    with mock.patch.dict(os.environ, env, clear=False), \
         mock.patch.object(app_module, "_ensure_loaded", return_value=_FakeState()), \
         mock.patch.object(app_module, "load_catalog_for_user", return_value={}), \
         mock.patch.object(app_module, "_resolve_effective_model_view", return_value=dict(_MODEL)), \
         mock.patch("platform_app.usage.context_window_for", return_value=0):
        return app_module._payload(None, include_catalog=False)["app"]


def _validator_rejects_local_http(env: dict[str, str]) -> bool:
    with mock.patch.dict(os.environ, env, clear=False):
        try:
            _validate_base_url("http://127.0.0.1:11434/v1")
        except ValueError:
            return True
        return False


# (RPG_DEPLOYMENT_MODE, RPG_REQUIRE_AUTH, 期望 flag)
_CASES = [
    ("server", "", True),
    ("multiuser", "", True),     # 非 local 别名一律按 server(fail-closed)
    ("local", "1", True),        # 显式开鉴权
    ("server", "0", False),      # 显式关鉴权:后端放行 http
    ("desktop", "", False),
    ("local", "", False),
]


class BaseUrlPublicOnlyPayload(unittest.TestCase):
    def test_flag_matches_validator_for_each_env(self):
        for mode, auth, expected in _CASES:
            env = {"RPG_DEPLOYMENT_MODE": mode, "RPG_REQUIRE_AUTH": auth}
            with self.subTest(mode=mode, auth=auth):
                app_block = _payload_app(env)
                self.assertIs(app_block["base_url_public_only"], expected)
                self.assertEqual(
                    app_block["base_url_public_only"], _validator_rejects_local_http(env),
                    "payload 判据与 _validate_base_url 的真实行为错位",
                )

    def test_deployment_field_still_present(self):
        # FeedbackDrawer 的「是否自部署」仍读 app.deployment,新字段不能顶掉它。
        app_block = _payload_app({"RPG_DEPLOYMENT_MODE": "desktop", "RPG_REQUIRE_AUTH": ""})
        self.assertEqual(app_block["deployment"], "desktop")


if __name__ == "__main__":
    unittest.main()
