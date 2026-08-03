"""存档没有 open phase 时,每回合的 ensure 必须补开一个 —— 判据是「有没有 open
phase」,不是「有没有 phase 行」。

老实现(ensure_initial_phase)见到任何一行 phase 就早退,而 detect_phase_boundary
在无 active phase 时恒 False ⇒ 只要有一条路径把 open phase 关掉却没重开
(compact_phase(force=True)、open_new_phase 关完插入失败、老 /compact …),
该存档**自此永久停止折叠历史**:save_phase_digests 冻在开局那几个 phase,
前情提要/「已发生历史摘要」从此永远讲开局剧情。生产 12 个存档处于该状态,
其中一个已打到 1000+ 回合、前情提要还停在第 1-12 回合(激光通道)。
"""
from __future__ import annotations

import platform_app.db as _db
import save_phase_manager as spm


class _FakeCur:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    def __init__(self, log, max_index):
        self.log = log
        self.max_index = max_index

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.log.append((flat, params))
        if "coalesce(max(phase_index)" in flat:
            return _FakeCur({"mx": self.max_index})
        return _FakeCur(None)


class _FakeConn:
    def __init__(self, log, max_index):
        self.log = log
        self.max_index = max_index

    def __enter__(self):
        return _FakeDB(self.log, self.max_index)

    def __exit__(self, *a):
        return False


def _run(monkeypatch, *, active, max_index):
    log: list[tuple[str, object]] = []
    monkeypatch.setattr(spm, "get_active_phase", lambda sid: active)
    monkeypatch.setattr(_db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(_db, "connect", lambda *a, **k: _FakeConn(log, max_index))
    spm.ensure_active_phase(268, 1041, "蜂巢外", "第116章")
    return log


def test_frozen_save_reopens_next_phase(monkeypatch):
    """phase 0/1 都 closed(冻结档)→ 补开 phase 2,从当前回合起算。"""
    log = _run(monkeypatch, active=None, max_index=1)
    inserts = [(s, p) for s, p in log if s.startswith("insert into save_phase_digests")]
    assert inserts, "冻结档没被补开 phase —— 历史折叠永久停摆"
    _, params = inserts[0]
    assert params[1] == 2, f"新 phase index 应为 max+1=2,实际 {params[1]}"
    assert params[2] == 1041 and params[3] == 1041, "新 phase 从当前回合起算"
    assert "open" in params, "新 phase 必须是 open"
    ups = [(s, p) for s, p in log if s.startswith("update game_saves set active_phase_index")]
    assert ups and ups[0][1][0] == 2, "active_phase_index 没跟上"


def test_no_bare_existence_early_return(monkeypatch):
    """老病灶:见到任何 phase 行就 return。冻结档必须**不**走这条早退。"""
    log = _run(monkeypatch, active=None, max_index=1)
    assert not any("select 1 from save_phase_digests" in s for s, _ in log), \
        "还在用「有没有 phase 行」当判据 —— 冻结档会继续被早退掉"


def test_first_turn_still_opens_phase_zero(monkeypatch):
    log = _run(monkeypatch, active=None, max_index=-1)
    inserts = [(s, p) for s, p in log if s.startswith("insert into save_phase_digests")]
    assert inserts and inserts[0][1][1] == 0, "全新存档首回合仍应开 phase 0"


def test_healthy_save_untouched(monkeypatch):
    """已有 open phase 的档零扰动(绝大多数存档走这条)。"""
    log = _run(monkeypatch, active={"phase_index": 3, "status": "open"}, max_index=3)
    assert log == [], "有 open phase 时不该有任何写入"


def test_legacy_name_still_exported():
    assert spm.ensure_initial_phase is spm.ensure_active_phase
