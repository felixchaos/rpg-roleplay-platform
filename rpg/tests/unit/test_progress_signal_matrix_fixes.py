"""进度信号矩阵审计 — 四项确认缺陷修复(单一权威读取器收口)。

权威 = agents.anchor_seed_agent.get_progress_window(综合锚点真实到达 + 玩家显式进度)。

M11 — routes/timeline.py current_chapter 判定优先级颠倒:此前先读裸标量
      worldline.progress_chapter → 出生点兜底,只有都失败才回退 get_progress_window。
      改为 get_progress_window 优先(source + chapter_min 都取自它),它失败才回退旧链。
M9  — platform_app/api/saves.py 的 /anchors 端点 recent_pending 无章节窗口过滤,
      按 importance 全局 top-12 会把远超进度的中后期锚点摘要(剧透)推给前端。
      改为用 get_progress_window 的 [chapter_min, chapter_max] 过滤。
M10 — routes/timeline.py 的「剧本期望线」(script_anchors)此前无 status 字段,
      前端纯章号比较判定 isDone/isCurrent/isPending,与「锚点收束状态」面板
      (save_anchor_states.status)双源矛盾。改为在 script_anchors 每行补聚合
      自 save_anchor_states.status 的 status 字段,章号只作展示。
M5  — gm_serving/serve.py 的 steer 用 _save_ctx 裸标量 ctx["progress_chapter"]
      (无 P4 前沿派生/floor clamp),弃用同函数里已算好的 read_settings 权威值。
      改为优先用 save_settings.get("progress_chapter"),裸标量只作 fallback。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_PY = (ROOT / "routes" / "timeline.py").read_text(encoding="utf-8")
SAVES_PY = (ROOT / "platform_app" / "api" / "saves.py").read_text(encoding="utf-8")
SERVE_PY = (ROOT / "gm_serving" / "serve.py").read_text(encoding="utf-8")


def _func(src: str, name: str) -> str:
    idx = src.find(f"def {name}(")
    assert idx != -1, f"{name} not found"
    end = src.find("\ndef ", idx + 1)
    return src[idx: end if end != -1 else len(src)]


def _route(src: str, path: str) -> str:
    """从源码里截取某个 @router 装饰的路由函数全文(到下一个 @router 或 EOF)。"""
    marker = f'"{path}"'
    idx = src.find(marker)
    assert idx != -1, f"route {path} not found"
    # 往前找 def
    def_idx = src.find("\nasync def ", idx)
    assert def_idx != -1
    end = src.find("\n@router", def_idx + 1)
    return src[def_idx: end if end != -1 else len(src)]


# ────────────────────────────────────────────────────────────
# M11 — current_chapter 优先读 get_progress_window,失败才回退旧链
# ────────────────────────────────────────────────────────────
class M11CurrentChapterPrefersProgressWindow(unittest.TestCase):
    def setUp(self):
        self.body = _route(TIMELINE_PY, "/api/saves/{save_id}/timeline")

    def test_get_progress_window_called_before_worldline_fallback(self):
        idx_gpw = self.body.find("get_progress_window(")
        idx_wl = self.body.find("worldline->>'progress_chapter'")
        self.assertNotEqual(idx_gpw, -1, "必须调用 get_progress_window")
        self.assertNotEqual(idx_wl, -1, "回退链必须仍读 worldline.progress_chapter")
        self.assertLess(
            idx_gpw, idx_wl,
            "get_progress_window 必须先于裸标量 worldline.progress_chapter 判定"
            "(M11:权威读取器应是首选,不是最后兜底)",
        )

    def test_birthpoint_fallback_still_after_progress_window(self):
        # 用实际调用点(win = get_progress_window(...))定位,避免注释里提到
        # "anchor_chapter_range" 字样导致误判(注释解释旧优先级链时会提及该词)。
        idx_gpw_call = self.body.find("win = get_progress_window(")
        idx_bp_code = self.body.find("state_snapshot #>> '{world,timeline,anchor_chapter_range,0}'")
        self.assertNotEqual(idx_gpw_call, -1, "必须实际调用 get_progress_window 并取结果")
        self.assertNotEqual(idx_bp_code, -1, "出生点兜底查询必须保留")
        self.assertLess(idx_gpw_call, idx_bp_code,
                         "出生点兜底仍应在 get_progress_window 调用之后(次级回退)")

    def test_only_one_get_progress_window_call(self):
        # 防止误改成两处调用（旧代码只在最后调一次；新代码应只挪到最前，逻辑不重复）
        self.assertEqual(
            self.body.count("get_progress_window("), 1,
            "get_progress_window 应只被调用一次(挪到最前,不是新增第二处)",
        )

    def test_return_shape_unchanged(self):
        self.assertIn('"script_anchors": script_anchors', TIMELINE_PY)
        self.assertIn('"save_phases": save_phases', TIMELINE_PY)
        self.assertIn('"current_phase_index": active_phase_index', TIMELINE_PY)
        self.assertIn('"current_chapter": current_chapter', TIMELINE_PY)


class M11FunctionalWithFakeDB(unittest.TestCase):
    """功能验证:get_progress_window 命中时,current_chapter 取自它而非裸标量,
    即便两者不一致(模拟窗口权威值与裸标量分叉的场景)。"""

    def test_progress_window_wins_over_raw_worldline_scalar(self):
        import routes.timeline as tl_mod

        class FakeDB:
            def __init__(self):
                self._last_sql = ""

            def execute(self, sql, params=None):
                self._last_sql = sql
                return self

            def fetchone(self):
                if "game_saves" in self._last_sql and "id, script_id, active_phase_index" in self._last_sql:
                    return {"id": 1, "script_id": 42, "active_phase_index": 0}
                if "worldline->>'progress_chapter'" in self._last_sql:
                    # 裸标量刻意给一个与权威窗口不同的值,验证优先级
                    return {"pc": 3}
                if "anchor_chapter_range" in self._last_sql:
                    return {"ch": None}
                return None

            def fetchall(self):
                return []

        fake_db = FakeDB()

        class _Ctx:
            def __enter__(self_inner):
                return fake_db

            def __exit__(self_inner, *a):
                return False

        import unittest.mock as mock

        # 关键:先在 mock 上下文之外把 agents.anchor_seed_agent 真实导入一次。
        # 该模块顶层是 `from platform_app.db import connect, init_db`(模块级绑定,
        # 只在首次 import 时执行一次)。若本进程此前从未导入过它,而恰好在下面
        # mock.patch("platform_app.db.connect", ...) 生效期间被 routes.timeline
        # 首次 import,会把 FakeDB 的 connect 永久绑死进
        # agents.anchor_seed_agent.connect,污染同进程后续其它测试的真实 DB 访问
        # (即便这里的 mock.patch 上下文正常退出恢复了 platform_app.db.connect)。
        import agents.anchor_seed_agent  # noqa: F401

        with mock.patch("platform_app.db.connect", return_value=_Ctx()), \
             mock.patch("platform_app.db.init_db", return_value=None), \
             mock.patch(
                 "agents.anchor_seed_agent.get_progress_window",
                 return_value={"chapter_min": 77, "chapter_max": 127,
                               "source": "satisfied", "last_satisfied_chapter": 76},
             ):
            import asyncio
            result = asyncio.run(tl_mod.api_saves_timeline(1, api_user={"id": 1}))
        import json
        payload = json.loads(result.body)
        self.assertEqual(
            payload["current_chapter"], 76,
            "current_chapter 应取 get_progress_window 的 last_satisfied_chapter(76),"
            "而非裸标量 worldline.progress_chapter(3)",
        )


# ────────────────────────────────────────────────────────────
# M9 — recent_pending 用 get_progress_window 窗口过滤
# ────────────────────────────────────────────────────────────
class M9RecentPendingWindowFiltered(unittest.TestCase):
    def setUp(self):
        self.body = _route(SAVES_PY, "/api/saves/{save_id}/anchors")

    def test_imports_get_progress_window(self):
        self.assertIn("get_progress_window", self.body,
                      "recent_pending 查询前必须先取权威进度窗口")

    def test_get_progress_window_called_before_list_pending(self):
        idx_gpw = self.body.find("get_progress_window(")
        idx_list = self.body.find("list_pending_for_phase(")
        self.assertNotEqual(idx_gpw, -1)
        self.assertNotEqual(idx_list, -1)
        self.assertLess(idx_gpw, idx_list,
                        "必须先算窗口,再用窗口过滤 list_pending_for_phase")

    def test_list_pending_call_passes_chapter_window(self):
        call = self.body[self.body.find("list_pending_for_phase("):]
        call = call[:call.find(")") + 1]
        self.assertIn("chapter_min", call)
        self.assertIn("chapter_max", call)

    def test_window_lookup_is_guarded(self):
        # get_progress_window 失败不该整个端点 500 — 必须有 try/except 兜底
        idx_gpw = self.body.find("get_progress_window(")
        preceding = self.body[max(0, idx_gpw - 200):idx_gpw]
        self.assertIn("try:", preceding,
                     "get_progress_window 调用需 try/except 兜底,不能让 /anchors 端点因此 500")


class M9FunctionalRealDB(unittest.TestCase):
    """用真实 DB 验证:list_pending_for_phase 传入窗口后,窗口外的 pending 锚点确实被排除。"""

    def test_window_excludes_out_of_range_pending(self):
        from platform_app.db import connect, init_db
        from agents.anchor_seed_agent import list_pending_for_phase

        init_db()
        sid = 9990501
        script_sid = 9990501
        with connect() as db:
            db.execute("delete from save_anchor_states where save_id=%s", (sid,))
            u = db.execute("select id from users limit 1").fetchone()
            if not u:
                self.skipTest("no users in DB")
                return
            row = db.execute("select id from scripts where id=%s", (script_sid,)).fetchone()
            if not row:
                db.execute(
                    "insert into scripts (id, owner_id, title) values (%s,%s,%s) "
                    "on conflict do nothing",
                    (script_sid, u["id"], "test-m9-script"),
                )
            srow = db.execute("select id from game_saves where id=%s", (sid,)).fetchone()
            if not srow:
                db.execute(
                    "insert into game_saves (id, user_id, script_id, title, state_path) "
                    "values (%s,%s,%s,%s,%s) on conflict do nothing",
                    (sid, u["id"], script_sid, "test-m9-save", f"/tmp/test-m9-save-{sid}.json"),
                )
            # 两个 pending 锚点:一个在窗口内(ch5),一个远超窗口(ch900,剧透)
            for key, ch in (("m9:near", 5), ("m9:spoiler", 900)):
                db.execute(
                    """insert into save_anchor_states
                       (save_id, anchor_key, source_kind, source_chapter, script_id,
                        summary, importance, status)
                       values (%s,%s,'chapter',%s,%s,%s,50,'pending')
                       on conflict (save_id, anchor_key) do update set status='pending'""",
                    (sid, key, ch, script_sid, f"summary for {key}"),
                )
        try:
            pend = list_pending_for_phase(sid, None, limit=12, chapter_min=1, chapter_max=30)
            keys = {p["anchor_key"] for p in pend}
            self.assertIn("m9:near", keys, "窗口内锚点应保留")
            self.assertNotIn("m9:spoiler", keys, "窗口外(剧透)锚点必须被过滤掉")
        finally:
            with connect() as db:
                db.execute("delete from save_anchor_states where save_id=%s", (sid,))
                db.execute("delete from game_saves where id=%s", (sid,))
                db.execute("delete from scripts where id=%s and title='test-m9-script'", (script_sid,))


# ────────────────────────────────────────────────────────────
# M10 — script_anchors 状态统一以 save_anchor_states.status 为准
# ────────────────────────────────────────────────────────────
class M10ScriptAnchorsStatusFromSaveAnchorStates(unittest.TestCase):
    def setUp(self):
        self.body = _route(TIMELINE_PY, "/api/saves/{save_id}/timeline")

    def test_status_derived_from_save_anchor_states(self):
        self.assertIn("save_anchor_states", self.body)
        # status 聚合查询必须出现在 script_anchors 循环之前
        idx_query = self.body.find("group by phase_label")
        idx_assign = self.body.find('a["status"]')
        self.assertNotEqual(idx_query, -1, "必须有按 phase_label 聚合 save_anchor_states.status 的查询")
        self.assertNotEqual(idx_assign, -1, "script_anchors 每行必须补 status 字段")
        self.assertLess(idx_query, idx_assign)

    def test_status_query_reads_real_status_column(self):
        query = self.body[self.body.find("select phase_label"):]
        query = query[:query.find(").fetchall()")]
        self.assertIn("status in ('occurred','variant','superseded')", query)
        self.assertIn("status = 'pending'", query)

    def test_no_pure_chapter_arithmetic_added_for_status(self):
        # 状态判定不能靠拿 chapter_min/chapter_max 跟某个 current_chapter 比较
        # (那正是 M10 里前端的双源病灶,后端不应重蹈)
        status_block = self.body[self.body.find("anchor_status_by_phase"):]
        self.assertNotIn("chapter_min <= current_chapter", status_block)
        self.assertNotIn("current_chapter <= ", status_block)


class M10FunctionalRealDB(unittest.TestCase):
    """真实 DB:script_timeline_anchors 一行 phase_label 对应的 save_anchor_states
    全部 occurred → 该行 status 应为 done,而非按章号猜测。"""

    def test_status_reflects_save_anchor_states_not_chapter_math(self):
        from platform_app.db import connect, init_db
        import asyncio
        import json
        import routes.timeline as tl_mod

        init_db()
        sid = 9990502
        script_sid = 9990502
        with connect() as db:
            u = db.execute("select id from users limit 1").fetchone()
            if not u:
                self.skipTest("no users in DB")
                return
            db.execute("delete from save_anchor_states where save_id=%s", (sid,))
            db.execute("delete from script_timeline_anchors where script_id=%s", (script_sid,))
            db.execute(
                "insert into scripts (id, owner_id, title) values (%s,%s,%s) "
                "on conflict do nothing",
                (script_sid, u["id"], "test-m10-script"),
            )
            db.execute(
                "insert into game_saves (id, user_id, script_id, title, state_path) "
                "values (%s,%s,%s,%s,%s) on conflict do nothing",
                (sid, u["id"], script_sid, "test-m10-save", f"/tmp/test-m10-save-{sid}.json"),
            )
            # 剧本期望线:第 1-10 章,phase_label = "开端"
            db.execute(
                """insert into script_timeline_anchors
                   (script_id, story_phase, story_time_label, chapter_min, chapter_max)
                   values (%s,'开端','序',1,10)
                   on conflict (script_id, story_phase, story_time_label) do update
                     set chapter_min=1, chapter_max=10""",
                (script_sid,),
            )
            # 该 phase 下的锚点全部已 occurred(哪怕章号 <= current_chapter,也该判 done;
            # 关键测试点是它不依赖 current_chapter 数值,而是这里的真实状态)
            db.execute(
                """insert into save_anchor_states
                   (save_id, anchor_key, source_kind, source_chapter, script_id,
                    summary, phase_label, importance, status)
                   values (%s,'m10:a1','chapter',3,%s,'s','开端',50,'occurred')
                   on conflict (save_id, anchor_key) do update set status='occurred', phase_label='开端'""",
                (sid, script_sid),
            )
        try:
            result = asyncio.run(tl_mod.api_saves_timeline(sid, api_user={"id": u["id"]}))
            payload = json.loads(result.body)
            rows = [r for r in payload["script_anchors"] if r["phase_label"] == "开端"]
            self.assertTrue(rows, "应能读到刚插入的 script_timeline_anchors 行")
            self.assertEqual(rows[0]["status"], "done",
                             "该 phase 下全部 save_anchor_states 均 occurred,status 应为 done")
        finally:
            with connect() as db:
                db.execute("delete from save_anchor_states where save_id=%s", (sid,))
                db.execute("delete from script_timeline_anchors where script_id=%s", (script_sid,))
                db.execute("delete from game_saves where id=%s", (sid,))
                db.execute("delete from scripts where id=%s and title='test-m10-script'", (script_sid,))


# ────────────────────────────────────────────────────────────
# M5 — steer 用 read_settings 权威 progress_chapter,裸标量只作 fallback
# ────────────────────────────────────────────────────────────
class M5SteerUsesReadSettingsProgressChapter(unittest.TestCase):
    def setUp(self):
        self.body = _func(SERVE_PY, "assemble_gm_context")

    def test_steer_call_uses_save_settings_progress_chapter(self):
        steer_call = self.body[self.body.find("steer = ST.resolve_steering_target"):]
        steer_call = steer_call[:steer_call.find(")\n") + 1]
        self.assertNotIn(
            'progress_chapter=ctx["progress_chapter"]', steer_call,
            "resolve_steering_target 不应再直接传裸标量 ctx['progress_chapter']",
        )
        self.assertIn("progress_chapter=_progress_chapter", steer_call)

    def test_progress_chapter_var_prefers_save_settings(self):
        idx_var = self.body.find("_progress_chapter = save_settings.get(")
        idx_steer = self.body.find("steer = ST.resolve_steering_target")
        self.assertNotEqual(idx_var, -1,
                            "必须先从 save_settings(read_settings 权威值)取 progress_chapter")
        self.assertNotEqual(idx_steer, -1)
        self.assertLess(idx_var, idx_steer)

    def test_raw_ctx_scalar_only_used_as_fallback(self):
        # 裸标量必须仍存在,但只能出现在 fallback 分支(None 时)
        fallback_block = self.body[self.body.find("_progress_chapter = save_settings.get("):
                                    self.body.find("steer = ST.resolve_steering_target")]
        self.assertIn('ctx["progress_chapter"]', fallback_block,
                      "裸标量应保留作 fallback,不能整个删除(read_settings 万一为 None 时兜底)")
        self.assertIn("is None", fallback_block,
                      "裸标量必须只在 save_settings 的值为 None 时才使用")

    def test_read_settings_still_called_before_steer(self):
        idx_rs = self.body.find("_set.read_settings(db, save_id)")
        idx_steer = self.body.find("steer = ST.resolve_steering_target")
        self.assertNotEqual(idx_rs, -1)
        self.assertLess(idx_rs, idx_steer)


class M5FunctionalWithFakeDB(unittest.TestCase):
    """功能验证:read_settings 权威值与 _save_ctx 裸标量分叉时,steer 拿到的是权威值。"""

    def test_resolve_steering_target_receives_authoritative_value(self):
        import unittest.mock as mock
        import gm_serving.serve as serve_mod

        class FakeDB:
            pass

        fake_db = FakeDB()
        fake_ctx = {"script_id": 5, "progress_chapter": 3, "mode": "none", "save_id": 1,
                    "commit_id": None}

        captured = {}

        def _fake_resolve(db, *, save_id, script_id, progress_chapter=None, steering_strength="guided"):
            captured["progress_chapter"] = progress_chapter
            return {"soft_goal": "", "pending_anchors": [], "strength": steering_strength}

        with mock.patch("tools_dsl.command_tools_kb._save_ctx", return_value=fake_ctx), \
             mock.patch("gm_serving.settings.read_settings",
                        return_value={"steering_strength": "guided", "progress_chapter": 88}), \
             mock.patch("gm_serving.steering.resolve_steering_target", side_effect=_fake_resolve), \
             mock.patch("gm_serving.context_inject.build_injection",
                        return_value={"text": "", "tokens": 0, "budget": 0}), \
             mock.patch("gm_serving.impact.classify_impact", return_value="low"), \
             mock.patch("gm_serving.impact.needs_offband_sim", return_value=False):
            serve_mod.assemble_gm_context(fake_db, save_id=1, user_id=1, user_input="hi")

        self.assertEqual(
            captured.get("progress_chapter"), 88,
            "steer 应收到 read_settings 权威值(88),而非 _save_ctx 裸标量(3)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
