"""open_new_phase 不得把一个回合都没收进来的 open phase 关成倒挂空区间。

现场日志:`[phase_digest async] save N phase 0 LLM error: no branch_commits in turn 1-0`,
紧跟两行 anchor_audit「N 个 is_fatal 锚点超期未触发」。

open_new_phase 以前一律按 turn_index-1 关当前 open phase。当该 phase 本身从 turn_index
或更晚起算时(continue_from / activate_node / 删子树回退到开场后残留旧分支的 open phase,
新分支 turn 1 标签一变 detect_phase_boundary 就触发;同回合二次开 phase;阈值配成 ≤1),
关出来的是 [s, s-1] —— compact 恒失败并留 needs_rebuild 毒行(cron 每天 force 重试、
按 save_id 排序占满 LIMIT 饿死真该重试的行),锚点审计还把整段 story_phase 的 fatal 报成超期。

本文件用内存假库跑 open_new_phase 的真实代码,锁住:
  - 空 open phase 就地改造,不关、不插、不摘要、不审计;
  - 正常换段行为不变(关到 turn_index-1、开 max+1、摘要 + 审计);
  - /compact 路径(open phase 已被 compact_phase 关掉)仍跑审计;
  - 历史异常的多条 open 行在改造时一并按各自 turn_end 关掉;
  - 全程只开一条连接(不在持有连接时再开一条查 max)。
"""
from __future__ import annotations

import platform_app.db as _db
import save_phase_manager as spm


class _Cur:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([row] if row is not None else [])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _PhaseDB:
    """只模拟 open_new_phase 用到的那几句:save_phase_digests 的读 / 关 / 改 / 插,
    以及 game_saves.active_phase_index 的写。行存在 self.rows(phase_index → dict)。"""

    def __init__(self, rows: list[dict]):
        self.rows = {int(r["phase_index"]): dict(r) for r in rows}
        self.active_phase_index = None
        self.log: list[str] = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        self.log.append(flat)
        if flat.startswith("select 1 from game_saves where id = %s for update"):
            return _Cur({"?column?": 1})
        if flat.startswith("select phase_index, turn_start, turn_end from save_phase_digests"):
            opens = sorted(
                (r for r in self.rows.values() if r["status"] == "open"),
                key=lambda r: -r["phase_index"],
            )
            return _Cur(rows=[dict(r) for r in opens], row=(dict(opens[0]) if opens else None))
        if "coalesce(max(phase_index)" in flat:
            mx = max(self.rows) if self.rows else -1
            return _Cur({"mx": mx})
        if flat.startswith("update save_phase_digests set status = 'closed', updated_at = now() where save_id = %s and phase_index = %s"):
            self.rows[int(params[1])]["status"] = "closed"
            return _Cur(None)
        if flat.startswith("update save_phase_digests set status = 'closed', turn_end = %s"):
            for r in self.rows.values():
                if r["status"] == "open":
                    r["status"] = "closed"
                    r["turn_end"] = int(params[0])
            return _Cur(None)
        if flat.startswith("update save_phase_digests set turn_start = %s"):
            ts, te, label, stl, _sid, idx = params
            r = self.rows[int(idx)]
            r.update(turn_start=ts, turn_end=te, phase_label=label, story_time_label=stl)
            return _Cur(dict(r))
        if flat.startswith("insert into save_phase_digests"):
            idx, ts, te, stl, label = params[1], params[2], params[3], params[4], params[5]
            self.rows[int(idx)] = {
                "phase_index": int(idx), "turn_start": ts, "turn_end": te,
                "story_time_label": stl, "phase_label": label, "status": "open",
            }
            return _Cur(dict(self.rows[int(idx)]))
        if flat.startswith("update game_saves set active_phase_index"):
            self.active_phase_index = int(params[0])
            return _Cur(None)
        raise AssertionError(f"假库没预料到的 SQL: {flat[:120]}")


class _Conn:
    def __init__(self, db, counter):
        self.db = db
        self.counter = counter

    def __enter__(self):
        self.counter["open"] += 1
        self.counter["max_depth"] = max(self.counter["max_depth"], self.counter["open"])
        self.counter["total"] += 1
        return self.db

    def __exit__(self, *a):
        self.counter["open"] -= 1
        return False


def _run(monkeypatch, rows, *, turn_index, label="玩家分支", story_time=""):
    db = _PhaseDB(rows)
    counter = {"open": 0, "total": 0, "max_depth": 0}
    fired: list[int] = []
    audited: list[int] = []
    monkeypatch.setattr(_db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(_db, "connect", lambda *a, **k: _Conn(db, counter))
    monkeypatch.setattr(spm, "_fire_and_forget_compact", lambda sid, pi: fired.append(pi))
    monkeypatch.setattr(spm, "_audit_anchors_on_phase_close", lambda sid, pi: audited.append(pi))
    row = spm.open_new_phase(631, turn_index=turn_index, phase_label=label,
                             story_time_label=story_time)
    assert counter["max_depth"] == 1, "open_new_phase 在持有连接时又开了一条连接(连接池死锁风险)"
    return db, fired, audited, row, counter


def _inverted(db):
    return [r for r in db.rows.values() if r["turn_end"] < r["turn_start"]]


def test_same_turn_open_phase_is_repurposed_not_closed_empty(monkeypatch):
    """phase 0 从 turn 1 起算、又在 turn 1 要开新 phase → 不关成 [1,0],就地改造。"""
    db, fired, audited, row, counter = _run(
        monkeypatch,
        [{"phase_index": 0, "turn_start": 1, "turn_end": 1, "status": "open",
          "phase_label": "第一卷"}],
        turn_index=1, label="玩家分支", story_time="黄昏",
    )
    assert not _inverted(db), "把 turn_start=1 的 phase 按 turn 0 关掉 = 倒挂空区间 [1,0]"
    assert list(db.rows) == [0], "不该另插一个新 phase"
    assert db.rows[0]["status"] == "open"
    assert db.rows[0]["phase_label"] == "玩家分支" and db.rows[0]["story_time_label"] == "黄昏"
    assert db.active_phase_index == 0
    assert fired == [] and audited == [], "空段不该触发 compact / 锚点审计"
    assert row["phase_index"] == 0 and row["turn_start"] == 1
    assert counter["total"] == 1


def test_stale_branch_phase_is_repurposed(monkeypatch):
    """分支回退后残留的旧 open phase(turn_start 远大于当前回合)同样就地改造成从当前回合起算。"""
    db, fired, audited, row, _ = _run(
        monkeypatch,
        [
            {"phase_index": 0, "turn_start": 1, "turn_end": 29, "status": "closed", "phase_label": "a"},
            {"phase_index": 1, "turn_start": 30, "turn_end": 44, "status": "open", "phase_label": "b"},
        ],
        turn_index=5,
    )
    assert not _inverted(db)
    assert db.rows[1]["turn_start"] == 5 and db.rows[1]["turn_end"] == 5
    assert db.rows[1]["status"] == "open"
    assert db.rows[0]["status"] == "closed" and db.rows[0]["turn_end"] == 29, "别的行不该被碰"
    assert db.active_phase_index == 1
    assert fired == [] and audited == []
    assert row["phase_index"] == 1


def test_normal_boundary_still_closes_and_digests(monkeypatch):
    """正常换段(open phase 已含若干回合):关到 turn_index-1、开 max+1、摘要 + 审计。"""
    db, fired, audited, row, counter = _run(
        monkeypatch,
        [{"phase_index": 0, "turn_start": 1, "turn_end": 30, "status": "open", "phase_label": "a"}],
        turn_index=31, label="b",
    )
    assert db.rows[0]["status"] == "closed" and db.rows[0]["turn_end"] == 30
    assert db.rows[1]["status"] == "open" and db.rows[1]["turn_start"] == 31
    assert db.active_phase_index == 1
    assert row["phase_index"] == 1
    assert fired == [0] and audited == [0]
    assert counter["total"] == 1, "新 index 必须在同一条连接里算"


def test_single_turn_phase_closes_normally(monkeypatch):
    """phase 只含 1 个回合(turn_start == turn_index-1)仍是正常关段,不走改造。"""
    db, fired, audited, row, _ = _run(
        monkeypatch,
        [{"phase_index": 2, "turn_start": 7, "turn_end": 7, "status": "open", "phase_label": "a"}],
        turn_index=8,
    )
    assert db.rows[2]["status"] == "closed" and db.rows[2]["turn_end"] == 7
    assert row["phase_index"] == 3
    assert fired == [2] and audited == [2]


def test_first_phase_when_none_open(monkeypatch):
    db, fired, audited, row, _ = _run(monkeypatch, [], turn_index=1)
    assert row["phase_index"] == 0 and db.rows[0]["status"] == "open"
    assert db.active_phase_index == 0
    assert fired == [] and audited == []


def test_compact_path_keeps_audit(monkeypatch):
    """/compact:compact_phase(force=True) 已把 phase 2 关掉,再调 open_new_phase(cur+1)。
    本次一行都没关,但老 phase 的审计仍得跑(不能改成按实际关了几行决定)。"""
    db, fired, audited, row, _ = _run(
        monkeypatch,
        [{"phase_index": 2, "turn_start": 40, "turn_end": 52, "status": "closed", "phase_label": "a"}],
        turn_index=53,
    )
    assert row["phase_index"] == 3 and db.rows[3]["turn_start"] == 53
    assert db.rows[2]["turn_end"] == 52, "已关的 phase 区间不该被改"
    assert fired == [2] and audited == [2]


def test_repurpose_closes_lower_stray_open_rows(monkeypatch):
    """历史异常:同时开着多条 open 行。改造最新那条时,较小 index 的按各自 turn_end 关掉,
    区间正常的补摘要,倒挂的不摘要;都不跑审计。"""
    db, fired, audited, row, _ = _run(
        monkeypatch,
        [
            {"phase_index": 0, "turn_start": 1, "turn_end": 12, "status": "open", "phase_label": "a"},
            {"phase_index": 1, "turn_start": 9, "turn_end": 8, "status": "open", "phase_label": "b"},
            {"phase_index": 2, "turn_start": 20, "turn_end": 20, "status": "open", "phase_label": "c"},
        ],
        turn_index=3,
    )
    assert db.rows[2]["status"] == "open" and db.rows[2]["turn_start"] == 3
    assert db.rows[0]["status"] == "closed" and db.rows[0]["turn_end"] == 12, "按自己的 turn_end 关"
    assert db.rows[1]["status"] == "closed" and db.rows[1]["turn_end"] == 8
    assert fired == [0], "只有区间正常的那条补摘要"
    assert audited == []
    assert db.active_phase_index == 2


def test_threshold_is_clamped_to_two(monkeypatch):
    """阈值配成 ≤1 时,ensure 刚开的 1 回合 phase 当回合就「满」→ 段永远收不进回合。"""
    from core.config import phase_turn_threshold
    for raw, want in (("1", 2), ("0", 2), ("-5", 2), ("2", 2), ("30", 30)):
        monkeypatch.setenv("RPG_PHASE_TURN_THRESHOLD", raw)
        assert phase_turn_threshold() == want


def test_locks_game_saves_row_before_reading_open_phase(monkeypatch):
    """同一存档的开段必须串行:事务第一句先锁 game_saves 那一行,再读 open 行。

    只锁 open 行做不到串行(READ COMMITTED):A 锁住 p5、关掉、插入 p6 后提交;B 拿到锁时 p5
    已不是 open 被排除,p6 又不在 B 那条语句的快照里 → B 看到「没有 open」走 else 分支,
    随后的 UPDATE 能看到 p6,按 T-1 把它关成倒挂 [T,T-1] 并对当前剧情段跑锚点审计。锁一行
    必定存在的 game_saves 记录,B 等 A 提交后再读,看到的就是 p6(走就地改造,不关不审计)。
    锁顺序也与 deletion.py(先 game_saves 后 save_phase_digests)一致,消掉 ABBA 窗口。"""
    for rows, turn in (
        ([{"phase_index": 0, "turn_start": 1, "turn_end": 30, "status": "open", "phase_label": "a"}], 31),
        ([{"phase_index": 0, "turn_start": 5, "turn_end": 5, "status": "open", "phase_label": "a"}], 5),
        ([], 1),
    ):
        db, *_ = _run(monkeypatch, rows, turn_index=turn)
        assert db.log[0] == "select 1 from game_saves where id = %s for update", db.log[:2]
        assert db.log[1].startswith("select phase_index, turn_start, turn_end from save_phase_digests")
