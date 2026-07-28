"""test_timeline_panel_current_chapter.py — 世界线面板的「当前章」必须认玩家显式进度。

群反馈(行者无疆,save 268):`/set 推进到第68章` 之后「世界线不变,而且这个世界线发生了
回退(之前应该是 64 章左右,之前也出现过类似情况)……召回的和思考时的锚点是根据强制约束
来的」——检索与软引导都跟上了强制约束,唯独世界线面板没跟。

病灶:`routes/timeline._resolve_current_chapter` 取的是
`win["last_satisfied_chapter"] or win["chapter_min"]`。前者是 `get_progress_window`
**内部的锚点单路中间值**(`max(source_chapter)`),后者才是「锚点真实到达」与「玩家显式
进度 worldline.progress_chapter」**取 max 后**的权威值。取前者 = 主动丢掉那个 max:
  ① /set 抬了 progress_chapter,面板不动;
  ② 锚点集合因回滚/换分支缩小时 `max(source_chapter)` 会**下降**(实证:ch64 → ch58),
     而 progress_chapter 单调只增,于是面板成了全站唯一会「回退」的消费者。
该函数上方的注释本来就写着「chapter_min 都取自它」——注释是对的,实现跑偏了。

本文件锁:显式进度赢时必须用 chapter_min;锚点赢时维持原语义(pace 开/关都不许整体位移)。
"""
from __future__ import annotations

import pytest

from routes.timeline import _resolve_current_chapter


class _FakeCur:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    """回退链用的假连接;主路径命中时不该被读到。"""

    def __init__(self, progress_chapter=None, birthpoint=None):
        self.progress_chapter = progress_chapter
        self.birthpoint = birthpoint
        self.hits = 0

    def execute(self, sql, params=None):
        self.hits += 1
        s = " ".join(sql.split())
        if "worldline->>'progress_chapter'" in s:
            return _FakeCur({"pc": self.progress_chapter})
        if "anchor_chapter_range" in s:
            return _FakeCur({"ch": self.birthpoint})
        return _FakeCur(None)


@pytest.fixture
def window(monkeypatch):
    def _install(win):
        import agents.anchor_seed_agent as _a
        monkeypatch.setattr(_a, "get_progress_window", lambda *a, **k: win, raising=False)
    return _install


# ── 症状 ①:玩家 /set 抬进度,面板必须跟上 ────────────────────────────────
def test_explicit_progress_wins_over_stale_anchor_chapter(window):
    """save 268 的真实形态:锚点到达停在 58,玩家 /set 推进到 68 → progress_chapter 赢。"""
    window({"chapter_min": 68, "chapter_max": 118,
            "last_satisfied_chapter": 58, "source": "progress_chapter"})
    assert _resolve_current_chapter(_FakeDB(), 268, 143) == 68


def test_anchor_regression_no_longer_drags_the_panel_back(window):
    """锚点集合缩小(ch64 → ch58)但 progress_chapter 单调不退 → 面板不回退。"""
    window({"chapter_min": 69, "chapter_max": 119,
            "last_satisfied_chapter": 58, "source": "progress_chapter"})
    assert _resolve_current_chapter(_FakeDB(), 268, 143) == 69


# ── 锚点赢时维持既有语义(不许整体位移) ──────────────────────────────────
def test_pace_on_unchanged(window):
    """anchor_pace 开:chapter_min == last_sat,取哪个都一样,行为零变化。"""
    window({"chapter_min": 69, "chapter_max": 119,
            "last_satisfied_chapter": 69, "source": "satisfied"})
    assert _resolve_current_chapter(_FakeDB(), 268, 143) == 69


def test_pace_off_not_shifted_forward(window):
    """anchor_pace 关:chapter_min = last_sat + 1。直接改用 chapter_min 会让所有存档
    的高亮前移一章 —— 必须仍取锚点章。"""
    window({"chapter_min": 59, "chapter_max": 108,
            "last_satisfied_chapter": 58, "source": "satisfied"})
    assert _resolve_current_chapter(_FakeDB(), 268, 143) == 58


# ── 无锚点 / 回退链保持原样 ──────────────────────────────────────────────
def test_no_anchor_yet_uses_chapter_min(window):
    """出生点档:还没有任何锚点到达,last_satisfied 为空 → 用 chapter_min。"""
    window({"chapter_min": 50, "chapter_max": 100,
            "last_satisfied_chapter": None, "source": "progress_chapter"})
    assert _resolve_current_chapter(_FakeDB(), 9, 1) == 50


def test_falls_back_to_worldline_when_window_unavailable(window, monkeypatch):
    import agents.anchor_seed_agent as _a
    monkeypatch.setattr(_a, "get_progress_window",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
                        raising=False)
    assert _resolve_current_chapter(_FakeDB(progress_chapter="37"), 9, 1) == 37


def test_falls_back_to_birthpoint_then_one(window, monkeypatch):
    import agents.anchor_seed_agent as _a
    monkeypatch.setattr(_a, "get_progress_window",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
                        raising=False)
    assert _resolve_current_chapter(_FakeDB(birthpoint="50"), 9, 1) == 50
    assert (_resolve_current_chapter(_FakeDB(), 9, 1) or 1) == 1
