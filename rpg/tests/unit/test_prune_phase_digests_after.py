"""回合退回时修剪 save_phase_digests:三处调用方共用一份,删子树删到活跃分支时也修剪。

以前只有 rollback_to_message / rewind_last_round 修剪 phase,delete_subtree 删掉活跃
分支、退回 fallback 后旧 phase 原样留着(修 A 漏 B)。新分支再打到同一回合时,open_new_phase
会把「一个回合都没收进来」的旧 phase 关成倒挂空段 [s, s-1],留下永远摘要不了的毒行。
"""
from __future__ import annotations

import platform_app.branches.deletion as deletion
import platform_app.branches.history_elide as history_elide


class _Cur:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _PhaseStore:
    """save_phase_digests 的内存版,只认修剪那三句;其余 SQL 交给 extra 处理。"""

    def __init__(self, phases, extra=None):
        self.phases = [dict(p) for p in phases]
        self.extra = extra

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        if flat.startswith("select id, phase_index, turn_start, turn_end from save_phase_digests"):
            _sid, deleted_turn = params
            rows = sorted((dict(p) for p in self.phases if p["turn_end"] >= deleted_turn),
                          key=lambda p: p["phase_index"])
            return _Cur(rows=rows)
        if flat.startswith("delete from save_phase_digests where id"):
            self.phases = [p for p in self.phases if p["id"] != params[0]]
            return _Cur(None)
        if flat.startswith("update save_phase_digests set turn_end"):
            for p in self.phases:
                if p["id"] == params[1]:
                    p["turn_end"] = params[0]
            return _Cur(None)
        if self.extra is not None:
            return self.extra(flat, params)
        raise AssertionError(f"假库没预料到的 SQL: {flat[:120]}")


def _phases():
    return [
        {"id": 10, "phase_index": 0, "turn_start": 1, "turn_end": 30},
        {"id": 11, "phase_index": 1, "turn_start": 31, "turn_end": 60},
        {"id": 12, "phase_index": 2, "turn_start": 61, "turn_end": 70},
    ]


def test_prune_truncates_straddling_and_drops_later():
    db = _PhaseStore(_phases())
    fixed, dropped = deletion._prune_phase_digests_after(db, 7, 45)
    assert (fixed, dropped) == (1, 1)
    got = {p["phase_index"]: (p["turn_start"], p["turn_end"]) for p in db.phases}
    assert got == {0: (1, 30), 1: (31, 44)}


def test_prune_never_leaves_inverted_range():
    """删除点正好落在某段起点:那段整行删掉,不会被截成 [s, s-1]。"""
    db = _PhaseStore(_phases())
    deletion._prune_phase_digests_after(db, 7, 31)
    assert all(p["turn_end"] >= p["turn_start"] for p in db.phases)
    assert [p["phase_index"] for p in db.phases] == [0]


def test_prune_back_to_opening_drops_everything():
    db = _PhaseStore(_phases())
    fixed, dropped = deletion._prune_phase_digests_after(db, 7, 1)
    assert (fixed, dropped) == (0, 3) and db.phases == []


def _run_delete_subtree(monkeypatch, *, active_commit_id, phases, fallback_turn=30,
                        remaining_max_turn=None):
    """remaining_max_turn:删完子树后,本存档剩下的 commit 里最大的 turn_index(兄弟分支 /
    主线还活着时比 fallback 大);None = 只剩 fallback 这条线,取 fallback_turn。"""
    node = {"id": 500, "parent_id": 400, "save_id": 7, "turn_index": fallback_turn + 1, "kind": "player"}
    fallback = {"id": 400, "save_id": 7, "turn_index": fallback_turn, "state_path": "/tmp/_g4_fallback.json"}
    remaining = fallback_turn if remaining_max_turn is None else remaining_max_turn

    def extra(flat, params):
        if flat.startswith("select state_path from branch_commits"):
            return _Cur(rows=[{"state_path": ""}])
        if flat.startswith("select * from game_saves"):
            return _Cur({"id": 7, "active_commit_id": active_commit_id})
        if flat.startswith("select * from branch_commits where id = %s and save_id = %s"):
            return _Cur(dict(fallback))
        if flat.startswith("select max(turn_index)") and "from branch_commits" in flat:
            return _Cur({"mx": remaining})
        if flat.startswith("delete from branch_refs") or flat.startswith("delete from branch_commits"):
            return _Cur(None)
        raise AssertionError(f"假库没预料到的 SQL: {flat[:120]}")

    db = _PhaseStore(phases, extra=extra)

    class _Conn:
        def __enter__(self):
            return db

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(deletion, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(deletion, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(deletion, "_commit_for_user", lambda _db, uid, nid: dict(node))
    monkeypatch.setattr(deletion, "round_start_node", lambda _db, n: n)
    monkeypatch.setattr(deletion, "acquire_save_advisory_lock", lambda *a, **k: None)
    monkeypatch.setattr(deletion, "collect_ids", lambda _db, nid, save_id=None: [500, 501])
    monkeypatch.setattr(deletion, "_upsert_ref", lambda *a, **k: {"id": 9})
    monkeypatch.setattr(deletion, "_set_save_active", lambda *a, **k: None)
    monkeypatch.setattr(deletion, "_write_checkout", lambda *a, **k: None)
    monkeypatch.setattr(deletion, "_unlink_branch_state", lambda *a, **k: None)
    monkeypatch.setattr(deletion, "_realign_after_state_rewind", lambda *a, **k: None)
    monkeypatch.setattr(deletion, "tree", lambda uid, sid: {"save_id": sid})
    monkeypatch.setattr(history_elide, "hydrate_commit_state", lambda _db, sid, c: {"turn": 30})
    monkeypatch.setattr(deletion._runtime_module, "activate_state_snapshot",
                        lambda *a, **k: {"ok": True})
    deletion.delete_subtree(1, 500)
    return db


def test_delete_subtree_on_active_branch_prunes_phases(monkeypatch):
    """删掉活跃分支(turn 31 起)→ 退回 fallback(turn 30),31 之后的 phase 一并修剪。"""
    db = _run_delete_subtree(monkeypatch, active_commit_id=501, phases=_phases())
    got = {p["phase_index"]: (p["turn_start"], p["turn_end"]) for p in db.phases}
    assert got == {0: (1, 30)}, "被删分支里的 phase 还留着 —— 新分支再到 turn 31 会关出倒挂空段"


def test_delete_subtree_off_active_branch_leaves_phases(monkeypatch):
    """删的不是活跃分支:活跃指针没动,phase 也不该动。"""
    db = _run_delete_subtree(monkeypatch, active_commit_id=999, phases=_phases())
    assert [(p["turn_start"], p["turn_end"]) for p in db.phases] == [(1, 30), (31, 60), (61, 70)]


def _mainline_phases():
    """主线打到第 60 回合:p0-p3 已关闭并摘要,p4 还开着。"""
    return [
        {"id": 20, "phase_index": 0, "turn_start": 1, "turn_end": 12},
        {"id": 21, "phase_index": 1, "turn_start": 13, "turn_end": 24},
        {"id": 22, "phase_index": 2, "turn_start": 25, "turn_end": 36},
        {"id": 23, "phase_index": 3, "turn_start": 37, "turn_end": 48},
        {"id": 24, "phase_index": 4, "turn_start": 49, "turn_end": 60},
    ]


def test_delete_active_branch_keeps_phases_still_covered_by_sibling(monkeypatch):
    """第 20 回合 fork 出的支线 B 是活跃分支,主线还在(打到第 60 回合)。删掉 B 退回第 20 回合,
    phase 表不分分支,21-60 回合的摘要属于主线 —— 不能剪。以前按 fallback+1 修剪,把 p1 截成
    [13,20]、p2-p4 整行删掉,玩家切回主线末端后 21-48 回合的前情提要永久丢失。"""
    db = _run_delete_subtree(monkeypatch, active_commit_id=501, phases=_mainline_phases(),
                             fallback_turn=20, remaining_max_turn=60)
    got = [(p["turn_start"], p["turn_end"]) for p in db.phases]
    assert got == [(1, 12), (13, 24), (25, 36), (37, 48), (49, 60)], "主线仍覆盖的阶段摘要被删了"


def test_delete_active_branch_prunes_only_past_surviving_commits(monkeypatch):
    """兄弟分支只打到第 25 回合,被删的支线走到过第 60 回合:第 25 回合之后已没有任何 commit,
    那些阶段只可能来自被删的分支,照常剪到 25。"""
    db = _run_delete_subtree(monkeypatch, active_commit_id=501, phases=_mainline_phases(),
                             fallback_turn=20, remaining_max_turn=25)
    got = [(p["turn_start"], p["turn_end"]) for p in db.phases]
    assert got == [(1, 12), (13, 24), (25, 25)]
