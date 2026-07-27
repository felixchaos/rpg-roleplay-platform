"""test_retrieval_chapter_window.py — 检索章节窗口必须跟着玩家进度走。

群反馈(行者无疆,save 268,玩家在第 67 章):「只有锚点章节(67章)原文是对的,
=== 当前剧情阶段 (phase fallback) 还是从第一章开始召回,=== Postgres ChapterFact ===
也是第一章开始召回,=== Postgres 原文片段 === 时第六章开始召回」。

三处独立缺陷,链式放大:
  A. `timeline_filter_for_label` 把 `_db_available` 早退放在最前面 —— 那个 SQLite 索引
     只有内置 demo 剧本才有(同函数下方还硬编码「图卢兹/柏林」),**所有导入剧本恒返回空
     窗口**。而 `world.time` 明写着「第67章·…」,章号就在字符串里,`_direct_chapter` 也早
     就能解析,纯算术、根本不需要 SQLite。
  B. 空窗口 → `_resolve_active_phase_range` 兜底,而它在 `active_phase_index` 断链时
     (生产实证:save 268 active_phase_index=2,save_phase_digests 只有 0/1)直接退到
     「剧本最早期 phase」= 第 1-78 章,完全不看进度。该返回值还喂 main_quest 派生。
  C. ChapterFact `order by chapter` + limit 在宽窗口下恒取窗口头部 → 第 1-5 章。

本文件锁这三条,任一回退都红。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from timeline_index import timeline_filter_for_label

_NO_SQLITE = Path("/nonexistent/timeline-index-should-not-exist.sqlite")


# ── A:标签里有章号就必须给出窗口,不依赖 SQLite ────────────────────────────
@pytest.mark.parametrize("label,ch", [
    ("第67章·回归主神空间后·第三十次死神训练结束·清晨", 67),
    ("第1章 开端", 1),
    ("Chapter 12 aftermath", 12),
    ("推进到第 233 回 决战", 233),
])
def test_direct_chapter_label_yields_window_without_sqlite(label, ch):
    f = timeline_filter_for_label(label, db_path=_NO_SQLITE)
    assert f["anchor_chapter"] == ch
    assert f["chapter_min"] == max(1, ch - 1)
    assert f["chapter_max"] == ch + 1
    assert f["confidence"] > 0


@pytest.mark.parametrize("label", ["", "推进到女主被冰雪女王当抱枕（已到达）", "序幕之后"])
def test_label_without_chapter_number_still_empty_without_sqlite(label):
    """没章号就只能靠语义索引;缺 SQLite 时仍返回空窗口(交给上层的进度派生兜底)。"""
    f = timeline_filter_for_label(label, db_path=_NO_SQLITE)
    assert f["chapter_min"] is None and f["chapter_max"] is None


# ── B:phase 解析必须认进度 ────────────────────────────────────────────────
# 生产实测:phase_digests.summary 全站 521/521 条都是「第N章 · <该章开头正文>」的拼接,
# 不是摘要。它会毒到两个下游(phase 块注入 + main_quest 派生)。
_JUNK_SUMMARY = ("第1章 · ============================================================；"
                 "===========================================================\n"
                 "第2章 · 【时间线锚点：无限历元年】\n第3章 · 郑吒一直觉得自己死在现实中")


class _FakeDB:
    """按 SQL 关键字派发的假连接。phase_digests 用剧本真实形态(每段 78 章)。"""

    PHASES = [("开端", 1, 78), ("发展前期", 79, 156), ("发展中期", 157, 234),
              ("发展后期", 235, 312), ("结局", 313, 390)]

    def __init__(self, active_phase_index=None, save_phase_labels=()):
        self.active_phase_index = active_phase_index
        self.save_phase_labels = dict(save_phase_labels)

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        rows: list[dict] = []
        if "from game_saves" in s:
            rows = [{"active_phase_index": self.active_phase_index}]
        elif "from save_phase_digests" in s:
            lbl = self.save_phase_labels.get(params[1])
            rows = [{"phase_label": lbl}] if lbl else []
        elif "from phase_digests" in s:
            cand = [{"phase_label": p, "chapter_min": lo, "chapter_max": hi,
                     "summary": _JUNK_SUMMARY} for p, lo, hi in self.PHASES]
            if "and phase_label = %s" in s:
                cand = [r for r in cand if r["phase_label"] == params[1]]
            elif "chapter_min <= %s and chapter_max >= %s" in s:
                cand = [r for r in cand
                        if r["chapter_min"] <= params[1] and r["chapter_max"] >= params[2]]
                cand.sort(key=lambda r: -r["chapter_min"])
            elif "and chapter_min <= %s" in s:
                cand = [r for r in cand if r["chapter_min"] <= params[1]]
                cand.sort(key=lambda r: -r["chapter_min"])
            else:
                cand.sort(key=lambda r: (r["chapter_min"], r["chapter_max"]))
            rows = cand
        return _FakeCur(rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeCur:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


@pytest.fixture
def phase_db(monkeypatch):
    def _install(db):
        import platform_app.db as _db
        monkeypatch.setattr(_db, "init_db", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(_db, "connect", lambda *a, **k: db, raising=False)
    return _install


def _resolve(**kw):
    from retrieval.progress import _resolve_active_phase_range
    return _resolve_active_phase_range(9, 143, **kw)


def test_phase_falls_back_to_progress_when_active_index_dangles(phase_db):
    """save 268 的真实形态:active_phase_index=2 但 save_phase_digests 只有 0/1。"""
    phase_db(_FakeDB(active_phase_index=2, save_phase_labels={0: "玩家分支", 1: "T病毒爆发前夜"}))
    got = _resolve(progress_chapter=67)
    assert (got["chapter_min"], got["chapter_max"]) == (1, 78)
    got2 = _resolve(progress_chapter=200)
    assert got2["phase_label"] == "发展中期", "进度在第 200 章却没落到对应 phase"


def test_phase_beyond_all_ranges_never_jumps_forward(phase_db):
    """发散档进度超出所有 phase → 取不晚于进度的最后一个,绝不跳到更后面(剧透只退不进)。"""
    phase_db(_FakeDB(active_phase_index=None))
    got = _resolve(progress_chapter=9999)
    assert got["phase_label"] == "结局"


def test_phase_without_progress_keeps_earliest_fallback(phase_db):
    """进度未知时保持原行为(最早期 phase),零变化。"""
    phase_db(_FakeDB(active_phase_index=None))
    assert _resolve(progress_chapter=None)["phase_label"] == "开端"


def test_active_phase_index_still_wins_when_resolvable(phase_db):
    """能解析到 active phase 时仍以它为准,进度只是兜底。"""
    phase_db(_FakeDB(active_phase_index=1, save_phase_labels={1: "发展后期"}))
    assert _resolve(progress_chapter=67)["phase_label"] == "发展后期"


# ── C:ChapterFact 排序锚在玩家当前章 ──────────────────────────────────────
def test_chapterfact_orders_around_progress_not_window_head():
    src = Path(__import__("platform_app.knowledge.retrieval", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    assert "abs(chapter - %s)" in src, "ChapterFact 没有按玩家当前章排序"
    assert "order by chapter\n" in src or 'order by chapter"' in src, "progress 未知时应保留原升序行为"
    assert "sorted(fact_rows" in src, "取回后应按章号升序注入(给 GM 读的是时间顺序)"


# ── 链路守卫:assemble 的回退链顺序 ────────────────────────────────────────
def test_assemble_derives_window_from_progress_before_phase():
    src = Path(__import__("retrieval.assemble", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    i_prog = src.index("按进度派生检索窗口")
    i_phase = src.index("_resolve_active_phase_range(_sid_for_phase")
    assert i_prog < i_phase, "进度派生窗口必须排在 phase fallback 之前"
    assert "progress_chapter=_progress_chapter" in src[i_phase: i_phase + 200], \
        "phase fallback 没把进度传下去"


# ── phase summary 是章节原文拼接,不能当摘要用 ──────────────────────────────
def test_junk_phase_summary_is_dropped(phase_db):
    """否则 phase 块注入一屏 ==== + 第 1-3 章原文,main_quest 还会被派生成同样的垃圾。"""
    phase_db(_FakeDB(active_phase_index=None))
    assert _resolve(progress_chapter=67)["summary"] == ""


def test_real_summary_survives():
    from retrieval.progress import _usable_phase_summary
    good = "主角一行人初入主神空间，完成第一场生化危机试炼并组建中洲队。"
    assert _usable_phase_summary(good) == good
    for junk in ["第1章 · 正文开头", "=" * 40, "-" * 30, "", None]:
        assert _usable_phase_summary(junk) == ""
