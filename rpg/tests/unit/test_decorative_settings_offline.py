"""test_decorative_settings_offline.py — 「UI 存在 ≠ 生效」:没有后端读方的设置项不许再摆在三端。

巡检确认下列设置存进偏好后,全站没有任何后端代码读:
  · 权限页「高风险字段白名单」(perm.high_risk_whitelist)、「自定义高风险白名单」
    (permissions.custom_whitelist)—— 写入闸 state/path_ops._write_path_allowed 在完全访问模式
    下一律放行,根本没有按字段弹确认的机制;
  · 账号设置「允许搜索」「匿名用量统计」「崩溃报告」「个性化推荐」「二次验证(2FA)」「邮件通知」
    (searchable / share_usage / share_crash / ads_track / two_fa / email_notif)—— 没有用户搜索、
    没有用量上报、没有推荐、没有 2FA 实现(web 开着还显示「Authenticator」)、没有按偏好发的邮件;
  · 「资料字段可见性」逐项配置(POST /api/profile/visibility → profile_extras.visibility)无人读。
iOS「编辑资料 → 公开个人主页」写的也是 profile_extras.visibility,而成就墙读的是
user_preferences.public_profile —— 开关拨了等于没拨。

这里锁住:三端(web / 手机 web / iOS)不再出现这些键;保留下来的开关键在后端确实有读方;
旧版 iOS 仍会调的 /api/profile/visibility 把 public_profile 同步进偏好。
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
RPG = ROOT / "rpg"

PERM_FILES = [
    "frontend/src/components/settings/perm-section.jsx",
    "frontend/src/mobile/settings/perm-section.jsx",
    "ios/Sources/Views/SettingsExtras.swift",
]
ACCOUNT_FILES = [
    "frontend/src/components/platform/MeUserSettings.jsx",
    "frontend/src/mobile/me/ViewSettings.jsx",
    "ios/Sources/Views/AccountViews.swift",
    "ios/Sources/API.swift",
    "frontend/src/api-client.js",
]
DEAD_PERM_KEYS = ["high_risk_whitelist", "custom_whitelist"]
DEAD_ACCOUNT_KEYS = ["two_fa", "email_notif", "searchable", "share_usage", "share_crash", "ads_track",
                     "profile/visibility"]


def _code(path: str) -> str:
    """去掉注释再查:说明下线理由的注释里可以提这些键名。"""
    src = (ROOT / path).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(^|[^:])//[^\n]*", r"\1", src)
    return src


@pytest.mark.parametrize("path", PERM_FILES)
def test_permission_pages_have_no_dead_whitelists(path):
    code = _code(path)
    for key in DEAD_PERM_KEYS:
        assert key not in code, f"{path} 又出现了没有后端读方的 {key}"


@pytest.mark.parametrize("path", ACCOUNT_FILES)
def test_account_pages_have_no_dead_toggles(path):
    code = _code(path)
    for key in DEAD_ACCOUNT_KEYS:
        assert not re.search(r"['\"]" + re.escape(key) + r"['\"]", code) and f"/{key}" not in code, \
            f"{path} 又出现了没有后端读方的 {key}"


def _backend_reads(key: str) -> bool:
    for p in RPG.rglob("*.py"):
        if "/tests/" in str(p) or "/.venv" in str(p):
            continue
        if key in p.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


@pytest.mark.parametrize("key", ["public_profile", "perm.default_mode"])
def test_kept_toggles_have_backend_readers(key):
    assert _backend_reads(key), f"保留下来的 {key} 在后端找不到读方"


def test_ios_profile_edit_public_toggle_writes_preference():
    code = _code("ios/Sources/Views/AccountViews.swift")
    assert 'setPreferences(base: store.serverURL, ["public_profile": v])' in code


def test_legacy_visibility_endpoint_mirrors_public_profile():
    src = (RPG / "platform_app" / "frontend_routes" / "profile.py").read_text(encoding="utf-8")
    seg = src[src.index('@router.post("/api/profile/visibility")'):]
    seg = seg[: seg.index("@router.", 10)] if "@router." in seg[10:] else seg
    assert "user_preferences" in seg and "public_profile" in seg
