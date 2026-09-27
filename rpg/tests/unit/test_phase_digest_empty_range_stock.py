"""存量倒挂空段 / 无回合 phase 的处理(不加 migration)。

open_new_phase 旧缺陷留下的 [s, s-1] 行一个回合都不含,摘要恒败:
  - compact_phase 返回 code="empty_range",closed 行标 digest_empty 终态,不调 LLM;
  - 异步摘要遇 empty_range 不再置 needs_rebuild(否则 cron 每天空转);
  - GM 上下文「最近 4 段」窗口跳过倒挂行,不占名额,与前情提要那条路用同一个判据,
    两条路的归属划分不因此错开。
find_pending 的排除、「只标 closed 行」都是 SQL 行为,在
tests/integration/test_phase_digest_empty_range.py 里用真库验。
"""
from __future__ import annotations

import re

import agents.phase_digest_agent as pda
import context_providers.runtime_phase_digests as rpd
import platform_app.db as _db
import save_phase_manager as spm
from state.phase_digest_policy import (
    RECENT_PHASE_WINDOW,
    drop_empty_ranges,
    is_empty_range,
)


class _Cur:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *a):
        return False


# ── 判据 ───────────────────────────────────────────────────────


def test_empty_range_predicate():
    assert is_empty_range({"turn_start": 1, "turn_end": 0})
    assert not is_empty_range({"turn_start": 1, "turn_end": 1})
    assert not is_empty_range({"turn_start": 5, "turn_end": 9})
    rows = [{"phase_index": 0, "turn_start": 1, "turn_end": 0},
            {"phase_index": 1, "turn_start": 1, "turn_end": 9}]
    assert [p["phase_index"] for p in drop_empty_ranges(rows)] == [1]


# ── GM 上下文:两条注入路 ───────────────────────────────────────


def _phase(i, ts, te, *, status="closed", summary=None):
    return {
        "id": 100 + i, "phase_index": i, "turn_start": ts, "turn_end": te,
        "story_time_label": "", "phase_label": f"段{i}",
        "summary": f"第 {i} 段发生的事" if summary is None else summary,
        "key_events": [], "key_npcs": [], "key_locations": [], "key_decisions": [],
        "emotion_arc": "", "status": status,
    }


def _table():
    # phase 6 是倒挂空段(分支回退后被关成 [121, 120]),夹在最近几段中间
    rows = [_phase(i, i * 20 + 1, i * 20 + 20) for i in range(6)]
    rows.append(_phase(6, 121, 120, summary=""))
    rows += [_phase(7, 121, 140), _phase(8, 141, 160), _phase(9, 161, 175, status="open")]
    return rows


class _PhaseReadDB:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        assert "from save_phase_digests" in flat, flat[:120]
        rows = sorted(self.rows, key=lambda r: r["phase_index"],
                      reverse="order by phase_index desc" in flat)
        m = re.search(r"limit (\d+|%s)", flat)
        if m:
            n = int(params[-1]) if m.group(1) == "%s" else int(m.group(1))
            rows = rows[:n]
        return _Cur(rows=[dict(r) for r in rows])


def _patch_db(monkeypatch, rows):
    db = _PhaseReadDB(rows)
    monkeypatch.setattr(_db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(_db, "connect", lambda *a, **k: _Conn(db))


def test_recent_window_skips_inverted_row(monkeypatch):
    _patch_db(monkeypatch, _table())
    phases = rpd._load_recent_phases(7, limit=RECENT_PHASE_WINDOW)
    idx = [p["phase_index"] for p in phases]
    assert 6 not in idx, "倒挂空段占了最近窗口的名额"
    assert idx == [5, 7, 8, 9], "窗口应是最近 4 个有回合的 phase,时间正序"
    text = rpd._render_phases([p for p in phases if (p.get("summary") or "").strip()])
    assert "turn 121-120" not in text


def test_two_paths_still_partition_with_inverted_rows(monkeypatch):
    """层跳过倒挂行多拿了一段,前情提要那条路必须按同一判据算归属,否则同一段出现两次。"""
    from state.core import GameState

    _patch_db(monkeypatch, _table())
    layer = {p["phase_index"] for p in rpd._load_recent_phases(7, limit=RECENT_PHASE_WINDOW)}
    msgs = GameState({"history": [], "turn": 400}).history_messages(limit_turns=6, save_id=7)
    assert msgs and msgs[0]["role"] == "user"
    prefix = {int(x) for x in re.findall(r"## Phase (\d+):", msgs[0]["content"])}
    assert prefix, "前情提要应至少有一段"
    assert not (layer & prefix), f"同一段在两条路里各出现一次: {sorted(layer & prefix)}"
    assert 6 not in prefix and 6 not in layer
    assert 5 in layer and 5 not in prefix


# ── compact_phase:空段终态 ──────────────────────────────────────


class _MarkDB:
    def __init__(self):
        self.marks: list[tuple] = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        if flat.startswith("update save_phase_digests set metadata"):
            self.marks.append((flat, params[0].obj, params[1], params[2]))
            return _Cur(None)
        raise AssertionError(f"假库没预料到的 SQL: {flat[:120]}")


def _run_compact(monkeypatch, row, commits):
    db = _MarkDB()
    llm_calls: list = []
    monkeypatch.setattr(_db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(_db, "connect", lambda *a, **k: _Conn(db))
    monkeypatch.setattr(pda, "_load_phase_row", lambda sid, pi: dict(row))
    monkeypatch.setattr(pda, "_load_phase_commits", lambda sid, s, e: list(commits))
    monkeypatch.setattr(pda, "_call_llm_with_retry",
                        lambda *a, **k: llm_calls.append(1) or ({}, {}))
    res = pda.compact_phase(631, int(row["phase_index"]), user_id=1, force=True)
    return res, db, llm_calls


def test_inverted_closed_phase_is_marked_terminal(monkeypatch):
    res, db, llm = _run_compact(
        monkeypatch,
        {"phase_index": 0, "turn_start": 1, "turn_end": 0, "status": "closed", "summary": ""},
        commits=[{"turn_index": 1}],  # 就算库里有 turn 1,倒挂区间也不该去取
    )
    assert res.get("code") == "empty_range" and res.get("error")
    assert llm == [], "空段不该调 LLM"
    assert len(db.marks) == 1
    _, meta, sid, pi = db.marks[0]
    assert meta == {"digest_empty": True, "needs_rebuild": False}
    assert (sid, pi) == (631, 0)


def test_closed_phase_without_commits_is_marked_terminal(monkeypatch):
    res, db, llm = _run_compact(
        monkeypatch,
        {"phase_index": 3, "turn_start": 40, "turn_end": 52, "status": "closed", "summary": ""},
        commits=[],
    )
    assert res.get("code") == "empty_range" and llm == []
    assert len(db.marks) == 1


# ── 异步摘要:empty_range 不置 needs_rebuild ─────────────────────


class _SyncThread:
    def __init__(self, target=None, **k):
        self._target = target

    def start(self):
        self._target()


def _run_fire(monkeypatch, result):
    import threading

    writes: list = []

    class _W:
        def execute(self, sql, params=None):
            writes.append(params[0].obj)
            return _Cur(None)

    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(spm, "_load_save_user_id", lambda sid: 1)
    monkeypatch.setattr(pda, "compact_phase", lambda *a, **k: dict(result))
    monkeypatch.setattr(_db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(_db, "connect", lambda *a, **k: _Conn(_W()))
    spm._fire_and_forget_compact(631, 0)
    return writes


def test_fire_and_forget_empty_range_does_not_flag_rebuild(monkeypatch):
    writes = _run_fire(monkeypatch, {"error": "这一段没有可摘要的回合(turn 1-0)",
                                     "code": "empty_range"})
    assert writes == [], "空段重试也不会成功,别再置 needs_rebuild 让 cron 空转"


def test_fire_and_forget_real_failure_still_flags_rebuild(monkeypatch):
    writes = _run_fire(monkeypatch, {"error": "RuntimeError: upstream 502"})
    assert writes == [{"needs_rebuild": True}]
