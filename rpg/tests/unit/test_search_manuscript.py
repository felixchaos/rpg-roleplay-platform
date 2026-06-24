"""search_manuscript 全书检索工具 —— Python 侧匹配/片段/偏移逻辑测试(mock DB)。

这是写作搭档 agent「先读后写、避免与全书矛盾」真正落地的关键工具:跨全书一次定位某词/人物/
设定/伏笔的所有出处 + 字符偏移,再用 get_chapter_text 精读。本测试钉死匹配逻辑(子串/正则/
大小写无关/上下文片段/偏移/0 命中);SQL 侧的 ILIKE 粗筛 + 章节范围在真库 e2e 验。
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from tools_dsl import command_tools_script_write as sw  # noqa: E402


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        # 不模拟 SQL 过滤(ILIKE/范围在真库验);返回全部行,交 Python 侧精确匹配。
        return _FakeCursor(self.rows)


def _patch_db(monkeypatch, rows):
    import platform_app.db as dbmod

    @contextlib.contextmanager
    def fake_connect():
        yield _FakeDB(rows)

    monkeypatch.setattr(dbmod, "connect", fake_connect)
    monkeypatch.setattr(dbmod, "init_db", lambda: None)
    monkeypatch.setattr(sw, "_user_can_read_script", lambda db, sid, uid: True)


def _row(ci, title, content):
    return {"chapter_index": ci, "title": title, "content": content}


def test_substring_match_across_chapters(monkeypatch):
    rows = [
        _row(3, "觉醒", "夜里他第一次感到重力控制的征兆,指尖发麻。"),
        _row(7, "试炼", "战斗中重力控制再次浮现,这次他主动用了它。"),
        _row(5, "日常", "这一章只是吃饭睡觉,什么都没发生。"),
    ]
    _patch_db(monkeypatch, rows)
    out = sw._t_search_manuscript(1, 1, {"query": "重力控制"}, None)
    assert "2 处命中" in out and "2 章" in out
    assert "【第3章 觉醒】@" in out
    assert "【第7章 试炼】@" in out
    assert "【第5章" not in out  # 无命中的章不出现


def test_offset_points_at_real_position(monkeypatch):
    content = "前情提要。" * 4 + "关键词出现在这里。"
    pos = content.index("关键词")
    _patch_db(monkeypatch, [_row(1, "测试", content)])
    out = sw._t_search_manuscript(1, 1, {"query": "关键词", "context_chars": 20}, None)
    assert f"@{pos}:" in out, f"偏移应指向真实位置 {pos};out={out}"


def test_case_insensitive(monkeypatch):
    _patch_db(monkeypatch, [_row(1, "x", "the Gravity Engine hums.")])
    out = sw._t_search_manuscript(1, 1, {"query": "gravity engine"}, None)
    assert "1 处命中" in out


def test_regex_mode(monkeypatch):
    _patch_db(monkeypatch, [_row(1, "x", "编号 A-117 与 A-118 都在场。")])
    out = sw._t_search_manuscript(1, 1, {"query": r"A-\d{3}", "regex": True}, None)
    assert "2 处命中" in out


def test_bad_regex_returns_error_not_crash(monkeypatch):
    _patch_db(monkeypatch, [_row(1, "x", "whatever")])
    out = sw._t_search_manuscript(1, 1, {"query": "A-[", "regex": True}, None)
    assert out.startswith("失败: 正则无效")


def test_zero_hits(monkeypatch):
    _patch_db(monkeypatch, [_row(1, "x", "完全无关的内容")])
    out = sw._t_search_manuscript(1, 1, {"query": "不存在的词"}, None)
    assert "0 命中" in out


def test_missing_query():
    out = sw._t_search_manuscript(1, 1, {}, None)
    assert out.startswith("失败") and "query" in out


def test_max_results_caps_listing(monkeypatch):
    # 同一章里 50 处命中,max_results=5 → 只列 5 条但总数如实报告
    content = "。".join(["命中"] * 50)
    _patch_db(monkeypatch, [_row(1, "密集", content)])
    out = sw._t_search_manuscript(1, 1, {"query": "命中", "max_results": 5}, None)
    assert "50 处命中" in out
    assert out.count("【第1章 密集】@") == 5
