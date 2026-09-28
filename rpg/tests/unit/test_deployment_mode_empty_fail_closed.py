"""RPG_DEPLOYMENT_MODE 设成空串时,「本地放宽」类开关必须 fail-closed,且与开源线同一个答案。

背景:编排文件里写 `RPG_DEPLOYMENT_MODE=${VAR}` 而 VAR 没设,进程拿到的是空串(不是没设)。
生产线 `deployment_mode_normalized()` 把空串兜底成 "local",于是:
  - core/startup.py 的 Origin 守卫(_origin_allowed)与 CORS allow_origin_regex 对局域网来源放宽;
  - tools_dsl/tool_registry.py 的 `mode = ... or "local"` 让 skill 导入、MCP 写配置按本地默认打开。
同一个进程里,鉴权判定(effective_auth_required)却把空串当未知模式 → 强制登录(见
test_deployment_mode_predicate_parity.py)。开源线这两处都走 is_local_mode() —— 空串不算本地。

两条线的「部署模式缝」写法不同是有意的(生产 `_deployment_mode() in _LOCAL_MODES`,开源线
`is_local_mode()`),这里只锁判定结果:
  - 本地放宽只在模式属于 LOCAL_MODES 时打开(没设变量 = 默认 local,照旧放宽);
  - 空串 / 纯空白 / 未知模式一律按 server 处理;
  - 显式 RPG_ENABLE_SKILL_IMPORT / RPG_ENABLE_MCP_CONFIG_WRITE 覆盖仍优先。
"""
from __future__ import annotations

import logging
import os
import unittest
from unittest import mock

from fastapi import FastAPI

from core import startup
from core.config import LOCAL_MODES
from tools_dsl import tool_registry

# None = 环境里根本没有这个变量
_MODES = [None, "", "   ", "local", "desktop", "self_hosted", "self-hosted", " Local ", "DESKTOP",
          "server", "production", "prod", "cloud", "multiuser"]
_LAN_ORIGIN = "http://192.168.1.4:7860"


def _env(mode: str | None, **extra: str):
    drop = {"RPG_DEPLOYMENT_MODE", "RPG_ENABLE_SKILL_IMPORT", "RPG_ENABLE_MCP_CONFIG_WRITE"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    if mode is not None:
        env["RPG_DEPLOYMENT_MODE"] = mode
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


def _oss_is_local(mode: str | None) -> bool:
    """开源线 core.config.is_local_mode() 的判定:没设 → 默认 local;设了就 strip+lower 后查集合。"""
    raw = "local" if mode is None else mode
    return raw.strip().lower() in LOCAL_MODES


class TestLanOriginRelaxation(unittest.TestCase):
    def test_origin_guard_matches_oss(self):
        for mode in _MODES:
            with self.subTest(mode=mode), _env(mode), \
                 mock.patch.object(startup, "_origins", ["http://127.0.0.1:7860"]):
                self.assertEqual(startup._origin_allowed(_LAN_ORIGIN), _oss_is_local(mode))

    def test_empty_mode_rejects_lan_origin(self):
        with _env(""), mock.patch.object(startup, "_origins", ["https://example.invalid"]):
            self.assertFalse(startup._origin_allowed(_LAN_ORIGIN))
            self.assertFalse(startup._origin_allowed("http://localhost:5174"))

    def _cors_regex(self):
        app = FastAPI()
        root = logging.getLogger()
        before = list(root.filters)
        try:
            startup.configure_app(app)
        finally:
            root.filters[:] = before      # configure_app 会往 root logger 挂过滤器,别留给其他用例
        cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
        self.assertEqual(len(cors), 1)
        return cors[0].kwargs.get("allow_origin_regex")

    def test_cors_regex_matches_oss(self):
        for mode in _MODES:
            with self.subTest(mode=mode), _env(mode):
                regex = self._cors_regex()
                self.assertEqual(regex is not None, _oss_is_local(mode))


class TestConfigHelper(unittest.TestCase):
    def test_is_local_deployment_mode_fail_closed(self):
        """这个 helper 两条线都没有调用方,但名字就是「放宽类判定」该用的那个 —— 别让它留着
        fail-open 的坑(开源线同名函数仍走 local 兜底,移植时一并改)。"""
        from core.config import is_local_deployment_mode
        for mode in _MODES:
            with self.subTest(mode=mode), _env(mode):
                self.assertEqual(is_local_deployment_mode(), _oss_is_local(mode))


class TestToolCapabilities(unittest.TestCase):
    def test_defaults_match_oss(self):
        for mode in _MODES:
            with self.subTest(mode=mode), _env(mode):
                caps = tool_registry.deployment_capabilities()
                want = _oss_is_local(mode)
                self.assertEqual(caps["skill_import_enabled"], want)
                self.assertEqual(caps["mcp_config_write_enabled"], want)

    def test_empty_mode_reported_as_server(self):
        with _env(""):
            self.assertEqual(tool_registry.deployment_capabilities()["deployment_mode"], "server")

    def test_explicit_override_still_wins(self):
        with _env("", RPG_ENABLE_SKILL_IMPORT="1", RPG_ENABLE_MCP_CONFIG_WRITE="1"):
            caps = tool_registry.deployment_capabilities()
            self.assertTrue(caps["skill_import_enabled"])
            self.assertTrue(caps["mcp_config_write_enabled"])
        with _env("local", RPG_ENABLE_SKILL_IMPORT="0", RPG_ENABLE_MCP_CONFIG_WRITE="0"):
            caps = tool_registry.deployment_capabilities()
            self.assertFalse(caps["skill_import_enabled"])
            self.assertFalse(caps["mcp_config_write_enabled"])


if __name__ == "__main__":
    unittest.main()
