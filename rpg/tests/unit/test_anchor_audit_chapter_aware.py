"""phase 关闭时的锚点审计:fatal「超期」必须章感知,与 non-fatal 绕过同口径。

save phase 关闭只说明一个压缩窗口满了或标签翻了,不代表原著这段 story_phase 走完了。
旧口径按 phase_label 把整段 story_phase 里所有 pending 的 fatal 全报成超期 —— 玩家才到
第 1 章,同一 story_phase 里第 5、第 9 章的 fatal 也被写进 fatal_anchors_overdue、打 WARNING。

anchor_pace 开(默认)时:只报 source_chapter < 已到达章 的 fatal;玩家尚无任何到达锚点
(reached 为 None)时一个都不报。anchor_pace 关的 legacy 路径不动。
"""
from __future__ import annotations

import pytest

import core.feature_flags as _ff
import platform_app.db as _db
import save_phase_manager as spm


class _Cur:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _AuditDB:
    def __init__(self, anchors, reached):
        self.anchors = anchors
        self.reached = reached
        self.overdue_writes: list[dict] = []
        self.pace_supersede: list[tuple] = []
        self.legacy_supersede: list[tuple] = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        if flat.startswith("select phase_label, turn_end from save_phase_digests"):
            return _Cur({"phase_label": "第一卷", "turn_end": 30})
        if flat.startswith("select id, anchor_key, is_fatal"):
            return _Cur(rows=[dict(a) for a in self.anchors])
        if flat.startswith("select max(source_chapter)"):
            return _Cur({"m": self.reached})
        if flat.startswith("update save_anchor_states") and "source_chapter < %s" in flat:
            self.pace_supersede.append(params)
            return _Cur(rows=[])
        if flat.startswith("update save_anchor_states"):
            self.legacy_supersede.append(params)
            return _Cur(None)
        if flat.startswith("update save_phase_digests set metadata"):
            self.overdue_writes.append(params[0].obj)
            return _Cur(None)
        raise AssertionError(f"假库没预料到的 SQL: {flat[:120]}")


class _Conn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *a):
        return False


def _fatal(ch: int) -> dict:
    return {"id": ch, "anchor_key": f"ch{ch}", "is_fatal": True,
            "summary": f"第 {ch} 章的关键事件", "importance": 90, "source_chapter": ch}


def _nonfatal(ch: int) -> dict:
    return {"id": 100 + ch, "anchor_key": f"nf{ch}", "is_fatal": False,
            "summary": f"第 {ch} 章的小事", "importance": 30, "source_chapter": ch}


def _run(monkeypatch, anchors, *, reached, pace):
    db = _AuditDB(anchors, reached)
    monkeypatch.setattr(_db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(_db, "connect", lambda *a, **k: _Conn(db))
    monkeypatch.setattr(_ff, "feature_enabled_for_save", lambda name, sid, conn=None: pace)
    spm._audit_anchors_on_phase_close(631, 0)
    return db


def _overdue_keys(db) -> list[str]:
    assert len(db.overdue_writes) <= 1
    if not db.overdue_writes:
        return []
    return [a["anchor_key"] for a in db.overdue_writes[0]["fatal_anchors_overdue"]]


def test_pace_only_reports_fatal_behind_reached_chapter(monkeypatch):
    db = _run(monkeypatch, [_fatal(2), _fatal(5), _fatal(9)], reached=3, pace=True)
    assert _overdue_keys(db) == ["ch2"], "只有玩家已推进过其章节的 fatal 才算超期"


def test_pace_reports_nothing_when_nothing_reached(monkeypatch):
    db = _run(monkeypatch, [_fatal(2), _fatal(5), _fatal(9)], reached=None, pace=True)
    assert db.overdue_writes == [], "玩家尚无任何到达锚点时不该报超期"


def test_pace_reached_chapter_itself_is_not_overdue(monkeypatch):
    """当前章(source_chapter == reached)的 fatal 玩家可能还会做,不算超期。"""
    db = _run(monkeypatch, [_fatal(3)], reached=3, pace=True)
    assert db.overdue_writes == []


def test_pace_fatal_without_chapter_is_not_reported(monkeypatch):
    anchor = _fatal(2)
    anchor["source_chapter"] = None
    db = _run(monkeypatch, [anchor], reached=5, pace=True)
    assert db.overdue_writes == []


def test_pace_nonfatal_bypass_unchanged(monkeypatch):
    """non-fatal 那一侧本来就章感知,改动不能碰它:reached 有值才绕过、且按 reached 截。"""
    db = _run(monkeypatch, [_nonfatal(2), _nonfatal(8), _fatal(9)], reached=4, pace=True)
    assert len(db.pace_supersede) == 1 and db.pace_supersede[0][-1] == 4
    assert db.legacy_supersede == []
    assert db.overdue_writes == []

    db = _run(monkeypatch, [_nonfatal(2)], reached=None, pace=True)
    assert db.pace_supersede == [] and db.legacy_supersede == []


@pytest.mark.parametrize("reached", [None, 3])
def test_legacy_path_without_pace_is_untouched(monkeypatch, reached):
    """anchor_pace 关:legacy 行为原样 —— 整段 fatal 都记超期,non-fatal 整段绕过。"""
    db = _run(monkeypatch, [_fatal(2), _fatal(5), _fatal(9), _nonfatal(7)],
              reached=reached, pace=False)
    assert _overdue_keys(db) == ["ch2", "ch5", "ch9"]
    assert len(db.legacy_supersede) == 1
    assert db.pace_supersede == []
