"""三端「设置 → 记忆」页没设过值时显示的默认值,必须等于后端真正生效的默认值。

get_memory_settings 改成读偏好之后,设置页的值开始真正生效;没动过某一项的用户,GM 用的是
MemorySettings 的默认值。以前三端都把召回深度显示成 6、摘要窗口显示成 8,后端默认却是 5 和 10:
用户看到的和实际生效的不是一回事(设置是逐项保存的,不去碰那一项,它就一直显示假值)。

这里把 web / 手机 / iOS 的初始显示值逐项对齐到 schemas.memory.MemorySettings 的默认值。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from schemas.memory import MemorySettings

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "frontend" / "src" / "components" / "settings" / "memory-section.jsx"
MOBILE = REPO / "frontend" / "src" / "mobile" / "settings" / "memory-section.jsx"
IOS = REPO / "ios" / "Sources" / "Views" / "SettingsExtras.swift"

WEB_STATE = {
    "recall_depth": "recallDepth",
    "summary_window": "summaryWindow",
    "token_budget": "tokenBudget",
    "auto_archive_after_turns": "autoArchiveAfter",
    "pinned_max": "pinnedMax",
    "bucket_pinned_enabled": "bucketPinnedEnabled",
    "bucket_world_enabled": "bucketWorldEnabled",
    "bucket_character_enabled": "bucketCharacterEnabled",
}
MOBILE_STATE = {
    "recall_depth": "recallDepth",
    "summary_window": "summaryWindow",
    "token_budget": "tokenBudget",
    "auto_archive_after_turns": "autoArchive",
    "pinned_max": "pinnedMax",
    "bucket_pinned_enabled": "bucketPinned",
    "bucket_world_enabled": "bucketWorld",
    "bucket_character_enabled": "bucketChar",
}
IOS_STATE = {
    "recall_depth": "recall",
    "summary_window": "summary",
    "token_budget": "budget",
    "auto_archive_after_turns": "archive",
    "pinned_max": "pinnedMax",
    "bucket_pinned_enabled": "bPinned",
    "bucket_world_enabled": "bWorld",
    "bucket_character_enabled": "bChar",
}


def _backend_default(field: str):
    return MemorySettings.model_fields[field].default


def _literal(raw: str):
    raw = raw.strip()
    if raw in ("true", "false"):
        return raw == "true"
    return float(raw)


def _same(backend, shown) -> bool:
    if isinstance(backend, bool):
        return shown is backend
    return not isinstance(shown, bool) and float(backend) == shown


def _jsx_initial(src: str, var: str):
    m = re.search(rf"const \[{var}, set\w+\] = useState(?:PL)?\(([^)]*)\)", src)
    assert m, f"找不到 {var} 的 useState 初值"
    return _literal(m.group(1))


def test_every_memory_setting_is_covered():
    assert set(WEB_STATE) == set(MemorySettings.model_fields)
    assert set(MOBILE_STATE) == set(MemorySettings.model_fields)
    assert set(IOS_STATE) == set(MemorySettings.model_fields)


@pytest.mark.parametrize("path,mapping", [(WEB, WEB_STATE), (MOBILE, MOBILE_STATE)], ids=["web", "mobile"])
def test_frontend_initial_values_match_backend_defaults(path, mapping):
    src = path.read_text(encoding="utf-8")
    wrong = {}
    for field, var in mapping.items():
        shown = _jsx_initial(src, var)
        if not _same(_backend_default(field), shown):
            wrong[field] = (shown, _backend_default(field))
    assert not wrong, f"{path.name} 显示的默认值与后端生效值不符(显示, 后端): {wrong}"


def test_ios_initial_and_fallback_values_match_backend_defaults():
    src = IOS.read_text(encoding="utf-8")
    body = src[src.index("struct MemoryView"):]
    body = body[: body.index("// MARK:", 1)]
    wrong = {}
    for field, var in IOS_STATE.items():
        m = re.search(rf"@State private var {var} = ([\w.]+)", body)
        assert m, f"找不到 iOS {var} 的 @State 初值"
        shown = _literal(m.group(1))
        if not _same(_backend_default(field), shown):
            wrong[f"{field}(@State)"] = (shown, _backend_default(field))
        # 读偏好时没有该键的回退值
        m = re.search(rf'{var} = read(?:Dbl|Bool)\(pr, \["memory\.{field}"[^\]]*\], ([\w.]+)\)', body)
        assert m, f"找不到 iOS {var} 的读取回退值"
        shown = _literal(m.group(1))
        if not _same(_backend_default(field), shown):
            wrong[f"{field}(回退)"] = (shown, _backend_default(field))
    assert not wrong, f"iOS 记忆页显示的默认值与后端生效值不符(显示, 后端): {wrong}"
