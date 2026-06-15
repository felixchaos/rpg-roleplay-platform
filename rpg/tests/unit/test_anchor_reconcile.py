"""离线单测:每回合确定性「世界线锚点」兜底判定器 gm_serving.anchor_reconcile。

全部 mock,不连真 DB / 不调真 LLM。验证:
  - 命中 → 确定性 UPDATE pending→occurred/variant + advance_progress(max-only)
  - 不命中 / 低置信空 → 零写入
  - 无模型降级(judge 抛/返空)→ 静默 0,不破回合
  - 窗口外 anchor_key 命中 → 拒绝(防剧透,绝不跳远未来)
  - judge 编造 anchor_key(不在 pending 列表)→ 拒绝
  - fatal 锚点确实到达 → 允许标记(反映已发生)
  - drift 阈值:>=0.15 → variant,<0.15 → occurred(与 mark_anchor_satisfied 一致)
  - 异常不破回合(get_progress_window 抛 → 返 0)
  - 成本门控:窗口内无 pending → 零 LLM 调用(judge 不被调)
  - env RPG_ANCHOR_AUTO_RECONCILE=0 → 完全跳过
  - 已非 pending(GM 本轮自调过)→ UPDATE ... where status='pending' 返 None → 不计数
  - 单回合标记上限(_MAX_MARK_PER_TURN)
"""
import os
import unittest
from unittest import mock

from gm_serving import anchor_reconcile as ar


# ────────────────────────────────────────────────────────────
#  Fake DB:记录 execute 调用,按 SQL 关键词返回预置 row
# ────────────────────────────────────────────────────────────
class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeDB:
    """最小 psycopg 连接替身:
    - select max(turn_index) → {"t": <turn>}
    - update save_anchor_states ... returning → 命中 set 里的 key 才返 row,否则 None
    """
    def __init__(self, *, max_turn=42, pending_keys=None, src_chapter=12):
        self.max_turn = max_turn
        # 仍 pending(可被本兜底 UPDATE 命中)的 anchor_key 集合
        self.pending_keys = set(pending_keys if pending_keys is not None else [])
        self.src_chapter = src_chapter
        self.updates = []  # (anchor_key, new_status, drift)
        self.calls = []    # 原始 (sql, params)

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        s = " ".join(sql.split())
        if "max(turn_index)" in s:
            return _FakeResult(row={"t": self.max_turn})
        if s.startswith("update save_anchor_states"):
            # params: (new_status, desc, occurred_turn, drift, save_id, anchor_key)
            new_status, _desc, _turn, drift, _sid, anchor_key = params
            if anchor_key in self.pending_keys:
                self.updates.append((anchor_key, new_status, drift))
                # 模拟 "returning id, source_chapter"
                return _FakeResult(row={"id": 999, "source_chapter": self.src_chapter})
            # 已非 pending(GM 本轮自调过 / 并发已标)→ returning 返 None
            return _FakeResult(row=None)
        return _FakeResult(row=None)


def _pending(*keys, fatal_keys=()):
    return [
        {"anchor_key": k, "summary": f"概要 {k}", "is_fatal": (k in fatal_keys),
         "chapter": 12}
        for k in keys
    ]


def _judge_returns(*hits):
    """构造一个固定返回 hits 的注入判定器(避开 E731 lambda)。"""
    def _judge(user_id, turn_text, pending, **kw):
        return list(hits)
    return _judge


def _judge_raises(exc):
    def _judge(*a, **k):
        raise exc
    return _judge


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        # 默认开启
        os.environ["RPG_ANCHOR_AUTO_RECONCILE"] = "1"
        # advance_progress 记录调用,不连库
        self.adv_calls = []
        self._adv_patch = mock.patch(
            "gm_serving.settings.advance_progress",
            side_effect=lambda db, sid, ch: self.adv_calls.append((sid, ch)),
        )
        self._adv_patch.start()
        self.addCleanup(self._adv_patch.stop)

    def tearDown(self):
        os.environ.pop("RPG_ANCHOR_AUTO_RECONCILE", None)

    # 统一 patch 窗口 + pending,judge / db 用注入
    def _run(self, *, pending, judge, db, win=None):
        win = win or {"chapter_min": 11, "chapter_max": 60, "source": "satisfied"}
        with mock.patch.object(ar, "get_progress_window", return_value=win), \
             mock.patch.object(ar, "list_pending_for_phase", return_value=pending):
            return ar.reconcile_anchors_for_turn(
                1, 7, "本回合 GM 正文……", db=db, _judge=judge,
            )

    # ── 命中:variant 落库 + 进度推进 ──────────────────────────
    def test_hit_marks_variant_and_advances(self):
        db = FakeDB(pending_keys={"chapter:12:event:0"}, src_chapter=12)
        judge = _judge_returns({"anchor_key": "chapter:12:event:0", "drift_score": 0.3})
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 1)
        self.assertEqual(db.updates, [("chapter:12:event:0", "variant", 0.3)])
        self.assertEqual(self.adv_calls, [(1, 12)])

    # ── drift < 0.15 → occurred ───────────────────────────────
    def test_hit_low_drift_marks_occurred(self):
        db = FakeDB(pending_keys={"chapter:12:event:0"})
        judge = _judge_returns({"anchor_key": "chapter:12:event:0", "drift_score": 0.0})
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 1)
        self.assertEqual(db.updates[0][1], "occurred")

    # ── 不命中 / 低置信空数组 → 零写入 ────────────────────────
    def test_no_hit_writes_nothing(self):
        db = FakeDB(pending_keys={"chapter:12:event:0"})
        judge = _judge_returns()  # 判定器保守判空
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 0)
        self.assertEqual(db.updates, [])
        self.assertEqual(self.adv_calls, [])

    # ── 无模型降级:judge 抛 → 静默 0,不破回合 ────────────────
    def test_judge_raises_swallowed(self):
        db = FakeDB(pending_keys={"chapter:12:event:0"})
        judge = _judge_raises(RuntimeError("无可用 BYOK 模型"))
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 0)
        self.assertEqual(db.updates, [])

    # ── 默认判定器:无 key(harness 抛)→ 静默返 [] ────────────
    def test_default_judge_no_key_silent(self):
        with mock.patch(
            "agents._harness.resolve_api_and_model",
            side_effect=RuntimeError("no BYOK"),
        ):
            out = ar._default_judge(7, "正文", _pending("chapter:12:event:0"))
        self.assertEqual(out, [])

    def test_default_judge_call_fails_silent(self):
        with mock.patch(
            "agents._harness.resolve_api_and_model",
            return_value=("anthropic", "claude-haiku-4-5"),
        ), mock.patch(
            "agents._harness.call_agent_json",
            side_effect=RuntimeError("401 no credentials"),
        ):
            out = ar._default_judge(7, "正文", _pending("chapter:12:event:0"))
        self.assertEqual(out, [])

    # ── 窗口外 anchor_key 命中 → 拒绝(防剧透,绝不跳远未来)──
    def test_out_of_window_key_rejected(self):
        db = FakeDB(pending_keys={"chapter:99:event:0"})  # 即便 DB 里 pending
        # 窗口内 pending 只含早章;judge 却命中远未来章
        judge = _judge_returns({"anchor_key": "chapter:99:event:0", "drift_score": 0.0})
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 0)
        self.assertEqual(db.updates, [])

    # ── judge 编造 anchor_key(不在 pending 列表)→ 拒绝 ──────
    def test_fabricated_key_rejected(self):
        db = FakeDB(pending_keys={"chapter:12:event:0"})
        judge = _judge_returns({"anchor_key": "made:up:key", "drift_score": 0.5})
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 0)

    # ── fatal 锚点确实到达 → 允许标记 ─────────────────────────
    def test_fatal_anchor_can_be_marked(self):
        db = FakeDB(pending_keys={"chapter:12:death:0"})
        judge = _judge_returns({"anchor_key": "chapter:12:death:0", "drift_score": 0.2})
        n = self._run(
            pending=_pending("chapter:12:death:0", fatal_keys=("chapter:12:death:0",)),
            judge=judge, db=db,
        )
        self.assertEqual(n, 1)
        self.assertEqual(db.updates[0][1], "variant")

    # ── 异常不破回合:窗口查询抛 → 返 0 ───────────────────────
    def test_progress_window_raises_swallowed(self):
        db = FakeDB(pending_keys={"chapter:12:event:0"})
        judge = _judge_returns({"anchor_key": "chapter:12:event:0", "drift_score": 0.0})
        with mock.patch.object(ar, "get_progress_window", side_effect=RuntimeError("DB down")), \
             mock.patch.object(ar, "list_pending_for_phase", return_value=_pending("chapter:12:event:0")):
            n = ar.reconcile_anchors_for_turn(1, 7, "正文", db=db, _judge=judge)
        self.assertEqual(n, 0)

    # ── 成本门控:窗口内无 pending → 零 LLM 调用 ──────────────
    def test_no_pending_zero_llm_call(self):
        db = FakeDB()
        called = {"n": 0}
        def judge(*a, **k):
            called["n"] += 1
            return []
        n = self._run(pending=[], judge=judge, db=db)
        self.assertEqual(n, 0)
        self.assertEqual(called["n"], 0)  # judge 绝不被调

    # ── env 关闭 → 完全跳过(judge 不调) ─────────────────────
    def test_env_disabled_skips(self):
        os.environ["RPG_ANCHOR_AUTO_RECONCILE"] = "0"
        db = FakeDB(pending_keys={"chapter:12:event:0"})
        called = {"n": 0}
        def judge(*a, **k):
            called["n"] += 1
            return [{"anchor_key": "chapter:12:event:0", "drift_score": 0.0}]
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 0)
        self.assertEqual(called["n"], 0)

    # ── 已非 pending(GM 本轮自调过)→ UPDATE 返 None → 不计数 ─
    def test_already_marked_not_double_counted(self):
        # judge 命中,但 DB 里该 key 已非 pending(pending_keys 不含)
        db = FakeDB(pending_keys=set())
        judge = _judge_returns({"anchor_key": "chapter:12:event:0", "drift_score": 0.0})
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 0)
        self.assertEqual(self.adv_calls, [])  # 没真标 → 不推进

    # ── 单回合标记上限 ────────────────────────────────────────
    def test_per_turn_cap(self):
        keys = [f"chapter:12:event:{i}" for i in range(10)]
        db = FakeDB(pending_keys=set(keys))
        judge = _judge_returns(*[{"anchor_key": k, "drift_score": 0.0} for k in keys])
        n = self._run(pending=_pending(*keys), judge=judge, db=db)
        self.assertEqual(n, ar._MAX_MARK_PER_TURN)
        self.assertEqual(len(db.updates), ar._MAX_MARK_PER_TURN)

    # ── 重复 key 去重 ─────────────────────────────────────────
    def test_duplicate_keys_deduped(self):
        db = FakeDB(pending_keys={"chapter:12:event:0"})
        judge = _judge_returns(
            {"anchor_key": "chapter:12:event:0", "drift_score": 0.0},
            {"anchor_key": "chapter:12:event:0", "drift_score": 0.5},
        )
        n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
        self.assertEqual(n, 1)
        self.assertEqual(len(db.updates), 1)

    # ── 空 turn_text / 缺 save_id → 早退 0 ────────────────────
    def test_empty_inputs_early_return(self):
        db = FakeDB(pending_keys={"chapter:12:event:0"})
        judge = _judge_returns({"anchor_key": "chapter:12:event:0", "drift_score": 0.0})
        with mock.patch.object(ar, "get_progress_window") as gpw:
            self.assertEqual(ar.reconcile_anchors_for_turn(1, 7, "", db=db, _judge=judge), 0)
            self.assertEqual(ar.reconcile_anchors_for_turn(0, 7, "x", db=db, _judge=judge), 0)
            self.assertEqual(ar.reconcile_anchors_for_turn(1, 0, "x", db=db, _judge=judge), 0)
            gpw.assert_not_called()  # 早退在窗口查询之前


if __name__ == "__main__":
    unittest.main()
