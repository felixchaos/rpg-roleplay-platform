"""test_memory_settings_source.py — 「设置 → 记忆」写到哪、GM 就得从哪读。

三端设置页(web components/settings/memory-section.jsx、mobile/settings/memory-section.jsx、
iOS SettingsExtras.swift)把召回深度 / 记忆 token 预算 / 固定记忆上限 / 三个分桶开关
全部经 POST /api/me/preference 写进 **user_preferences.preferences["memory.xxx"]**;
而后端唯一的读取口 get_memory_settings 只读 **settings 表**(只有 POST /api/settings 会写,
没有任何客户端调它)。结果:用户在设置页怎么调,MemoryProvider / 固定记忆上限校验拿到的
永远是默认值 —— 整页设置是摆设。

锁死三件事:
1. 偏好里的 memory.* 生效(这是三端真实写入的地方);
2. 偏好覆盖 settings 表的旧值,settings 表只作兜底(老数据 / 直调 /api/settings 的脚本);
3. 单个字段非法只丢那一个字段,不把其它合法设置一起打回默认;设置页读取时兼容的
   旧中文键(settings.召回深度 等)后端也认,用户看到的值 = 生效的值。

DB 全用替身,不打真库。
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch

_RPG = pathlib.Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from platform_app import settings as ps  # noqa: E402
from schemas.memory import MemorySettings  # noqa: E402

_DEFAULT = MemorySettings()


def _ms(prefs: dict, table: dict | None = None) -> MemorySettings:
    with patch.object(ps, "list_settings", return_value=dict(table or {})), \
         patch("core.request_cache.get_user_prefs_cached", return_value=dict(prefs)):
        return ps.get_memory_settings(42)


def test_preferences_written_by_settings_page_take_effect():
    ms = _ms({
        "memory.recall_depth": 12,
        "memory.token_budget": 1500,
        "memory.pinned_max": 40,
        "memory.bucket_pinned_enabled": False,
        "memory.summary_window": 5,
        "memory.auto_archive_after_turns": 120,
    })
    assert ms.recall_depth == 12
    assert ms.token_budget == 1500
    assert ms.pinned_max == 40
    assert ms.bucket_pinned_enabled is False
    assert ms.summary_window == 5
    assert ms.auto_archive_after_turns == 120


def test_preferences_override_legacy_settings_table():
    ms = _ms({"memory.recall_depth": 9}, table={"memory.recall_depth": 3, "memory.token_budget": 400})
    assert ms.recall_depth == 9          # 偏好(设置页真实写入)优先
    assert ms.token_budget == 400        # 偏好没设的字段,settings 表的旧值兜底


def test_one_bad_field_does_not_reset_the_rest():
    ms = _ms({"memory.recall_depth": 999, "memory.token_budget": 1200})
    assert ms.recall_depth == _DEFAULT.recall_depth   # 越界的丢掉
    assert ms.token_budget == 1200                    # 合法的照样生效


def test_legacy_chinese_keys_the_page_still_reads_are_honoured():
    # 设置页 loadOrFallback 读旧中文键展示给用户;后端也得按同一口径生效,否则「看到 8 用的是 5」。
    ms = _ms({"settings.召回深度": 8, "settings.固定记忆上限": 30})
    assert ms.recall_depth == 8
    assert ms.pinned_max == 30
    # 新键在时以新键为准
    ms2 = _ms({"settings.召回深度": 8, "memory.recall_depth": 11})
    assert ms2.recall_depth == 11


def test_no_preferences_no_table_gives_defaults():
    assert _ms({}) == _DEFAULT


def test_unrelated_preference_keys_are_ignored():
    ms = _ms({"memory.mode": "deep", "theme": "dark", "memory.recall_depth": 7})
    assert ms.recall_depth == 7
