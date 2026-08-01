"""test_feature_flag_not_self_grantable.py — 灰度特性不能被用户自己开(08-01 反馈)。

反馈:「为什么普通用户可以看到 rath,不对吧??」

查下来「三道防线」一道都不成立:
  ① 导航隐藏(platform-app.jsx adminOnly)—— 只是不显示菜单项
  ② 路由守卫(entries/platform.jsx AdminGuard)—— 只是前端不渲染
  ③ 后端 flag `rath_experiment` —— **读的是 `user_preferences`,而
     `POST /api/me/preference` 收任意键、无白名单** → 任何登录用户 POST 一句
     `{"rath_experiment.enabled": true}` 就把自己放进去了。

也就是说唯一在服务端的那道闸,开关握在被拦的人手里。**用户偏好不是访问控制。**

修的时候差点犯第二个错:一刀切「默认关的特性都不许读偏好」会把**线上靠偏好跑的
按人灰度(金丝雀)静默关掉**。所以分界不在「默认开/关」,而在这个开关控制什么 ——
决定能不能进某个界面的(`_ACCESS_GATED`)才锁,只影响自己这局怎么跑的不锁。

本测试锁五件:
  · 访问闸键的用户偏好被**彻底忽略**,只认 env
  · **靠偏好跑的按人灰度必须活着**(上面那个差点犯的错,钉死)
  · 用户自己的引擎开关照旧可自设,且与访问闸无交集
  · 偏好写入面直接拒绝访问闸键(纵深:让越权当场可见,而不是写进去却静默不生效)
  · RATH 路由有一道**用户改不了**的角色闸,且每个端点都过它
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import unittest
from unittest.mock import patch

_RPG = pathlib.Path(__file__).resolve().parents[2]
REPO = _RPG.parent
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from core import feature_flags as ff  # noqa: E402

# 访问闸 = 决定能不能进某个界面的开关(当前只有 RATH)
_GATED = tuple(sorted(ff._ACCESS_GATED))
# 靠用户偏好按人灰度的引擎开关 —— 必须继续认偏好
_CANARY = ("episodic_recall", "kb_hash_keys")


def _with_pref(value, key, user_id=999):
    """让 get_user_prefs_cached 返回该用户把某特性设成 value 的偏好。"""
    return patch("core.request_cache.get_user_prefs_cached",
                 return_value={f"{key}.enabled": value})


class TestGatedFeaturesIgnoreUserPreference(unittest.TestCase):
    def test_user_cannot_self_grant(self):
        """核心:用户把偏好写成 true 也没用 —— 这正是反馈里那条绕过路径。"""
        for key in _GATED:
            with self.subTest(key=key), _with_pref(True, key):
                self.assertFalse(ff.feature_enabled(key, 999), f"{key} 被用户自己开成功了")

    def test_env_still_decides(self):
        for key in _GATED:
            env_var = ff._FEATURES[key][0]
            with self.subTest(key=key), _with_pref(True, key), \
                 patch.dict(os.environ, {env_var: "1"}):
                self.assertTrue(ff.feature_enabled(key, 999), f"{key} 环境开了却没生效")

    def test_user_cannot_self_revoke_either(self):
        """方向对称:偏好写 false 也不该影响 —— 否则「环境开了但被用户关掉」同样是漂移。"""
        for key in _GATED:
            env_var = ff._FEATURES[key][0]
            with self.subTest(key=key), _with_pref(False, key), \
                 patch.dict(os.environ, {env_var: "1"}):
                self.assertTrue(ff.feature_enabled(key, 999))


class TestPerUserCanaryMustSurvive(unittest.TestCase):
    """差点犯的错:一刀切锁掉「默认关特性读偏好」会把线上按人灰度静默关掉。

    这些开关只影响用户自己这局怎么跑,不是访问闸 —— 偏好必须照旧生效。
    """

    def test_canary_features_still_follow_preference(self):
        for key in _CANARY:
            with self.subTest(key=key), _with_pref(True, key):
                self.assertTrue(ff.feature_enabled(key, 999),
                                f"{key} 的按人灰度被误锁了")

    def test_canary_keys_are_not_access_gates(self):
        self.assertFalse(set(_CANARY) & set(ff._ACCESS_GATED))


class TestUserOwnedSwitchesStillWork(unittest.TestCase):
    """别为了堵洞把用户自己的开关一起锁死 —— 那些是产品明确交给用户的。"""

    def test_access_gates_and_user_switches_are_disjoint(self):
        """一个键不能既摆在用户模块页上、又声称是访问闸。"""
        self.assertFalse(set(ff._ACCESS_GATED) & set(ff._USER_TOGGLABLE))

    def test_toggleable_features_follow_preference(self):
        for key in ("ctx_tiered", "kb_state", "npc_agenda"):
            with self.subTest(key=key), _with_pref(False, key):
                self.assertFalse(ff.feature_enabled(key, 999))
            with self.subTest(key=key), _with_pref(True, key):
                self.assertTrue(ff.feature_enabled(key, 999))

    def test_toggleable_set_matches_frontend_module_list(self):
        """_USER_TOGGLABLE 必须等于前端模块页真正列出来的那些键(前后端单一真相)。

        漂移的后果是双向的:前端列了后端不认 = 开关点了没用;后端认了前端没列 =
        又一个「用户能自开的隐藏特性」。
        """
        js = (REPO / "frontend/src/agent-modules.js").read_text(encoding="utf-8")
        import re
        listed = set(re.findall(r'\{\s*key:\s*"([a-z_]+)"', js))
        self.assertTrue(listed, "没解析到前端 FEATURES 列表,断言失效")
        self.assertEqual(listed, set(ff._USER_TOGGLABLE),
                         "前端模块页与后端 _USER_TOGGLABLE 不一致")


class TestPreferenceEndpointRejectsGatedKeys(unittest.TestCase):
    def test_rejected_feature_keys_picks_only_gated_switches(self):
        payload = {
            "rath_experiment.enabled": True,   # 访问闸 → 拒
            "episodic_recall.enabled": True,   # 按人灰度的引擎开关 → 放行
            "ctx_tiered.enabled": False,       # 用户自己的开关 → 放行
            "theme": "dark",                   # 普通偏好 → 放行
            "something_else.enabled": True,    # 不是特性键 → 放行(拦它是误伤)
        }
        self.assertEqual(ff.rejected_feature_keys(payload), {"rath_experiment.enabled"})

    def test_endpoint_calls_the_rejector(self):
        src = (REPO / "rpg/platform_app/api/me/preferences.py").read_text(encoding="utf-8")
        self.assertIn("rejected_feature_keys", src)
        self.assertIn("status_code=403", src)

    def test_empty_and_garbage_payloads_do_not_explode(self):
        for p in ({}, None, {"a": 1}, {123: True}):
            self.assertEqual(ff.rejected_feature_keys(p), set())


class TestRathHasARoleGateUsersCannotWrite(unittest.TestCase):
    _SRC = (REPO / "rpg/routes/rath.py").read_text(encoding="utf-8")

    def test_gate_checks_admin_role(self):
        """光有 flag 不够 —— flag 的开关在用户自己手里过(现已收紧),角色不是。"""
        body = self._SRC[self._SRC.index("def _flag_ok"):]
        body = body[: body.index("\ndef ", 1)]
        self.assertIn("is_admin", body)

    def test_every_endpoint_goes_through_the_gate(self):
        tree = ast.parse(self._SRC)
        endpoints, ungated = 0, []
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") in
                       ("get", "post", "put", "delete") for d in n.decorator_list):
                continue
            endpoints += 1
            called = {getattr(c.func, "id", None) or getattr(c.func, "attr", "")
                      for c in ast.walk(n) if isinstance(c, ast.Call)}
            if "_flag_ok" not in called:
                ungated.append(n.name)
        self.assertGreater(endpoints, 0)
        self.assertEqual(ungated, [], f"这些 RATH 端点没过闸: {ungated}")


class TestMobileShellHasTheSameGuard(unittest.TestCase):
    """桌面壳有 AdminGuard、移动壳一直没有 —— 直接敲 URL 就能渲染出整套管理/灰度界面。"""

    def test_mobile_root_denies_admin_and_rath_pages(self):
        src = (REPO / "frontend/src/mobile/MobileRoot.jsx").read_text(encoding="utf-8")
        i = src.index("const renderRoute")
        body = src[i: i + 1800]
        self.assertIn("_role !== 'admin'", body)
        self.assertIn("'rath'", body)
        self.assertIn("startsWith('admin-')", body)

    def test_guard_precedes_the_admin_render(self):
        """守卫必须排在渲染之前 —— 顺序反了就等于没有。"""
        src = (REPO / "frontend/src/mobile/MobileRoot.jsx").read_text(encoding="utf-8")
        self.assertLess(src.index("_role !== 'admin'"), src.index("<MobileAdmin"))


if __name__ == "__main__":
    unittest.main()
