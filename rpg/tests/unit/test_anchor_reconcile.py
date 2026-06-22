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
    def __init__(self, *, max_turn=42, pending_keys=None, src_chapter=12, occurred_max=0,
                 progress_pc=None, script_id_val=None, chapter_rows=None):
        self.max_turn = max_turn
        # 仍 pending(可被本兜底 UPDATE 命中)的 anchor_key 集合
        self.pending_keys = set(pending_keys if pending_keys is not None else [])
        self.src_chapter = src_chapter
        # 已确认锚点最大原著章(_apply_estimate 算 ceiling 的 floor 真源)
        self.occurred_max = occurred_max
        # _load_estimate_context 备料用:进度/剧本/章节地图
        self.progress_pc = progress_pc
        self.script_id_val = script_id_val
        self.chapter_rows = chapter_rows or []
        self.updates = []  # (anchor_key, new_status, drift)
        self.calls = []    # 原始 (sql, params)

    # _load_estimate_context 用 `with connect() as db`,需上下文管理器协议
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        s = " ".join(sql.split())
        if "max(turn_index)" in s:
            return _FakeResult(row={"t": self.max_turn})
        if "progress_chapter" in s and "game_sessions" in s:
            return _FakeResult(row={"pc": self.progress_pc})
        if "script_id" in s and "game_saves" in s:
            return _FakeResult(row={"script_id": self.script_id_val})
        if "from chapter_facts" in s:
            return _FakeResult(rows=self.chapter_rows)
        if "max(source_chapter)" in s:
            return _FakeResult(row={"c": self.occurred_max})
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


def _judge_dict(reached=(), estimated=None):
    """新式判定器:返回 {reached, estimated_chapter}(测有界叙事章估计)。"""
    def _judge(user_id, turn_text, pending, **kw):
        return {"reached": list(reached), "estimated_chapter": estimated}
    return _judge


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        # 默认开启
        os.environ["RPG_ANCHOR_AUTO_RECONCILE"] = "1"
        # 这些估章/ceiling 用例针对【非限速】基线写就(pace 现已默认开,会叠加 +2 cap)。
        # 显式钉死 pace=off 以隔离测 ceiling 数学;限速 +2 的覆盖见 test_apply_estimate_pace_cap。
        os.environ["RPG_ANCHOR_PACE"] = "0"
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
        os.environ.pop("RPG_ANCHOR_PACE", None)

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

    # ── 默认判定器:无 key(harness 抛)→ 静默返空契约 dict ──────
    _EMPTY_JUDGE = {"reached": [], "estimated_chapter": None}

    def test_default_judge_no_key_silent(self):
        with mock.patch(
            "agents._harness.resolve_api_and_model",
            side_effect=RuntimeError("no BYOK"),
        ):
            out = ar._default_judge(7, "正文", _pending("chapter:12:event:0"))
        self.assertEqual(out, self._EMPTY_JUDGE)

    def test_default_judge_call_fails_silent(self):
        with mock.patch(
            "agents._harness.resolve_api_and_model",
            return_value=("anthropic", "claude-haiku-4-5"),
        ), mock.patch(
            "agents._harness.call_agent_json",
            side_effect=RuntimeError("401 no credentials"),
        ):
            out = ar._default_judge(7, "正文", _pending("chapter:12:event:0"))
        self.assertEqual(out, self._EMPTY_JUDGE)

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

    # ── 成本门控:窗口内无 pending + 估章关 → 零 LLM 调用 ──────
    #    (Bug B 后:无 pending 但估章开会为估章发 1 次调用,设计 §73;故此处显式关估章验成本闸仍在)
    def test_no_pending_estimate_off_zero_llm_call(self):
        os.environ["RPG_PROGRESS_NARRATIVE_ESTIMATE"] = "0"
        try:
            db = FakeDB()
            called = {"n": 0}
            def judge(*a, **k):
                called["n"] += 1
                return []
            n = self._run(pending=[], judge=judge, db=db)
            self.assertEqual(n, 0)
            self.assertEqual(called["n"], 0)  # 估章关 + 无 pending → judge 绝不被调
        finally:
            os.environ.pop("RPG_PROGRESS_NARRATIVE_ESTIMATE", None)

    # ── Bug B §73:窗口内无 pending 但估章开 → 仍跑判定器只估章并推进(根治 >50 章空白段冻结)──
    def test_no_pending_estimate_still_runs(self):
        db = FakeDB(occurred_max=0)
        called = {"n": 0}
        def judge(user_id, turn_text, pending, **kw):
            called["n"] += 1
            return {"reached": [], "estimated_chapter": 6}
        n = self._run(pending=[], judge=judge, db=db)
        self.assertEqual(n, 0, "无 pending → 无锚点标记")
        self.assertEqual(called["n"], 1, "估章开 → 判定器仍被调一次只估章")
        self.assertEqual(self.adv_calls, [(1, 6)], "无 pending 也能靠估章推进")

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

    # ══ Bug B 有界叙事章估计 ══════════════════════════════════════

    # ── 无锚点命中也能靠估章推进(save 139 的核心场景)──────────
    def test_estimate_advances_without_anchor_hit(self):
        # 注入 _judge → 不走真实备料(prev 默认 1);floor=0 → ceiling=1+12=13;est=6 → 推到 6。
        db = FakeDB(pending_keys=set(), occurred_max=0)
        judge = _judge_dict(reached=[], estimated=6)
        n = self._run(pending=_pending("chapter:9:event:0"), judge=judge, db=db)
        self.assertEqual(n, 0, "无锚点命中 → 标记数 0")
        self.assertEqual(db.updates, [], "无锚点 UPDATE")
        self.assertEqual(self.adv_calls, [(1, 6)], "估章把进度从 1 推到 6")

    # ── 估章超上限被 clamp 到 ceiling(防 ch77 乱跳)────────────
    def test_estimate_clamped_to_ceiling(self):
        db = FakeDB(pending_keys=set(), occurred_max=0)
        judge = _judge_dict(reached=[], estimated=99)
        n = self._run(pending=_pending("chapter:9:event:0"), judge=judge, db=db)
        self.assertEqual(n, 0)
        self.assertEqual(self.adv_calls, [(1, 13)], "prev=1,floor=0,CAP=12 → 钳到 13")

    # ── 锚点命中 + 估章同回合都推进 ─────────────────────────────
    def test_estimate_with_anchor_hit_both_advance(self):
        db = FakeDB(pending_keys={"chapter:9:event:0"}, src_chapter=9, occurred_max=0)
        judge = _judge_dict(
            reached=[{"anchor_key": "chapter:9:event:0", "drift_score": 0.0}],
            estimated=6,
        )
        n = self._run(pending=_pending("chapter:9:event:0"), judge=judge, db=db)
        self.assertEqual(n, 1, "锚点标记 1 个")
        # 锚点 advance(9) + 估章 advance(6) 都调用(advance_progress 自身 max-only 兜底)
        self.assertIn((1, 9), self.adv_calls)
        self.assertIn((1, 6), self.adv_calls)

    # ── env 关闭 → 不估章 ───────────────────────────────────────
    def test_estimate_env_off(self):
        os.environ["RPG_PROGRESS_NARRATIVE_ESTIMATE"] = "0"
        try:
            db = FakeDB(pending_keys=set(), occurred_max=0)
            judge = _judge_dict(reached=[], estimated=6)
            n = self._run(pending=_pending("chapter:9:event:0"), judge=judge, db=db)
            self.assertEqual(n, 0)
            self.assertEqual(self.adv_calls, [], "开关关 → 不推进")
        finally:
            os.environ.pop("RPG_PROGRESS_NARRATIVE_ESTIMATE", None)

    # ── _apply_estimate clamp 直测(各边界)─────────────────────
    def test_apply_estimate_clamp_unit(self):
        # 区间内
        self.adv_calls.clear()
        out = ar._apply_estimate(FakeDB(occurred_max=0), 1, prev_progress=5, estimated_chapter=8)
        self.assertEqual(out, 8)
        self.assertEqual(self.adv_calls, [(1, 8)])
        # 超 ceiling = max(floor=0,prev=5)+12 = 17 → 钳 17
        self.adv_calls.clear()
        out = ar._apply_estimate(FakeDB(occurred_max=0), 1, prev_progress=5, estimated_chapter=99)
        self.assertEqual(out, 17)
        self.assertEqual(self.adv_calls, [(1, 17)])
        # 低于 prev → 不推进(回退是 rewind 的职责)
        self.adv_calls.clear()
        out = ar._apply_estimate(FakeDB(occurred_max=0), 1, prev_progress=5, estimated_chapter=3)
        self.assertEqual(out, 0)
        self.assertEqual(self.adv_calls, [])
        # floor(已确认锚点)抬高 ceiling:floor=20 → ceiling=32 → est=99 钳 32
        self.adv_calls.clear()
        out = ar._apply_estimate(FakeDB(occurred_max=20), 1, prev_progress=5, estimated_chapter=99)
        self.assertEqual(out, 32)
        self.assertEqual(self.adv_calls, [(1, 32)])

    # ── _apply_estimate 限速(pace on,默认):每回合进度最多 +_PACE_CAP 章 ──
    def test_apply_estimate_pace_cap(self):
        os.environ["RPG_ANCHOR_PACE"] = "1"  # 覆盖 setUp 的 off,测限速层
        self.adv_calls.clear()
        # prev=5, est=99, floor=0:非限速会钳到 ceiling=17;限速叠加 → min(17, prev+_PACE_CAP)=5+2=7
        out = ar._apply_estimate(FakeDB(occurred_max=0), 1, prev_progress=5, estimated_chapter=99)
        self.assertEqual(out, 5 + ar._PACE_CAP, "pace on → 每回合最多 +_PACE_CAP 章")
        self.assertEqual(self.adv_calls, [(1, 5 + ar._PACE_CAP)])

    # ── _normalize_judge_result 兼容新旧返回 ────────────────────
    def test_normalize_judge_result(self):
        # 新式 dict
        self.assertEqual(
            ar._normalize_judge_result({"reached": [{"anchor_key": "a"}], "estimated_chapter": 7}),
            ([{"anchor_key": "a"}], 7),
        )
        # 旧式裸 list → 无估章
        self.assertEqual(ar._normalize_judge_result([{"anchor_key": "a"}]), ([{"anchor_key": "a"}], None))
        # est < 1 / 非法 → None
        self.assertEqual(ar._normalize_judge_result({"reached": [], "estimated_chapter": 0}), ([], None))
        self.assertEqual(ar._normalize_judge_result({"reached": [], "estimated_chapter": "x"}), ([], None))
        # 垃圾 → 空
        self.assertEqual(ar._normalize_judge_result(None), ([], None))
        self.assertEqual(ar._normalize_judge_result("nope"), ([], None))

    # ── 关估章不连累锚点兜底(env=0 + 真锚点命中仍标记)──────────
    def test_estimate_off_anchor_still_marks(self):
        os.environ["RPG_PROGRESS_NARRATIVE_ESTIMATE"] = "0"
        try:
            db = FakeDB(pending_keys={"chapter:12:event:0"}, src_chapter=12, occurred_max=0)
            judge = _judge_dict(
                reached=[{"anchor_key": "chapter:12:event:0", "drift_score": 0.0}],
                estimated=6,
            )
            n = self._run(pending=_pending("chapter:12:event:0"), judge=judge, db=db)
            self.assertEqual(n, 1, "关估章不影响锚点标记")
            self.assertEqual(self.adv_calls, [(1, 12)], "只有锚点推进(12),无估章推进(6)")
        finally:
            os.environ.pop("RPG_PROGRESS_NARRATIVE_ESTIMATE", None)

    # ── _load_estimate_context 备料:列映射 + [max(floor,prev), +CAP] 边界 ──
    def test_load_estimate_context(self):
        fake = FakeDB(progress_pc="5", script_id_val=143, occurred_max=2,
                      chapter_rows=[{"chapter": 5, "label": "活下去", "summary": "S5"},
                                    {"chapter": 6, "label": "激光", "summary": "S6"}])
        with mock.patch("platform_app.db.connect", return_value=fake), \
             mock.patch("platform_app.db.init_db"):
            ctx = ar._load_estimate_context(99)
        self.assertEqual(ctx["prev"], 5)
        self.assertEqual(ctx["floor"], 2)
        self.assertEqual(ctx["script_id"], 143)
        self.assertEqual([c["chapter"] for c in ctx["window_chapters"]], [5, 6])
        self.assertEqual(ctx["window_chapters"][0]["label"], "活下去")
        # 章节地图边界 = [max(1,floor=2,prev=5), +CAP] = [5, 17]
        cf_call = next(c for c in fake.calls if "from chapter_facts" in " ".join(c[0].split()))
        self.assertEqual(cf_call[1], (143, 5, 5 + ar._LOOKAHEAD_CAP))

    # ── 无 script_id → 备料返 None(本回合关估章)────────────────
    def test_load_estimate_context_no_script(self):
        fake = FakeDB(progress_pc="3", script_id_val=None)
        with mock.patch("platform_app.db.connect", return_value=fake), \
             mock.patch("platform_app.db.init_db"):
            self.assertIsNone(ar._load_estimate_context(99))


if __name__ == "__main__":
    unittest.main()
