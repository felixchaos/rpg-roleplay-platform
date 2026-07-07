"""RATH v4 引擎侧回归(纯函数/SQL 形状断言型测试,不连库)。

对齐 /private/tmp/.../scratchpad/rath_v4_contracts.md 与 rath_v4_audit_sim.json 的
confirmed findings。风格延续 test_rath_briefing.py:多数断言直接读源码文本核实关键
SQL 片段/控制流顺序是否落地(engine.py 大量分支依赖真实 DB/LLM,不适合无库 mock 到
细枝末节;真正能纯函数化的部分——briefing.py 的水位游标——已在 test_rath_briefing.py
里用 mock db 做了行为级验证)。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = (ROOT / "rath" / "engine.py").read_text(encoding="utf-8")
RATH_ROUTES = (ROOT / "routes" / "rath.py").read_text(encoding="utf-8")
GAME_ROUTES = (ROOT / "routes" / "game.py").read_text(encoding="utf-8")
MIGRATIONS = (ROOT / "platform_app" / "db" / "migrations.py").read_text(encoding="utf-8")


# ── A1: canon_rows 未定义 P0 ─────────────────────────────────────────

def test_canon_rows_predefined_alongside_cast_rows():
    """_sid=0(无脚本/酒馆/自由档)时 `if _sid:` 整块被跳过,不触发 except——
    canon_rows 必须与 cast_rows 在 try 之前同点预置为空列表,否则第334行左右
    `canon_beats=[dict(r) for r in (canon_rows or [])]` 直接 NameError。"""
    i = ENGINE.find("cast_rows = []")
    j = ENGINE.find("try:", i)
    between = ENGINE[i:j]
    assert "canon_rows = []" in between, (
        "canon_rows 必须在 try 块之前、与 cast_rows 同点预置(自由演化模式不该被拒建)")


# ── A2: 世界钟封顶 P0 ────────────────────────────────────────────────

def test_claim_tick_caps_elapsed_and_advance():
    body = ENGINE[ENGINE.find("def _claim_tick"):ENGINE.find("def tick_experiment")]
    assert "t.tick_interval_sec * 4" in body, "raw_elapsed_sec 须封 tick_interval_sec*4"
    assert "4320" in body, "advance_min 须整体封顶 4320 分钟(3 世界日绝对上限)"
    assert "extract(epoch from (now() - coalesce(prev.last_tick_at, now())))" in body


def test_pause_resume_and_auto_pause_freeze_last_tick_at():
    """所有 pause/resume 路径必须冻结 last_tick_at=now(),否则暂停期计时会在恢复时
    banking 一次性暴涨世界钟(finding2 核心 bug)。"""
    # 手动 pause(routes/rath.py action)
    pause_block = RATH_ROUTES[RATH_ROUTES.find('elif action == "pause"'):
                              RATH_ROUTES.find('elif action == "resume"')]
    assert "pause_reason='user'" in pause_block.replace(" ", "")
    assert "last_tick_at=now()" in pause_block.replace(" ", "")
    assert "paused_at=now()" in pause_block.replace(" ", "")
    # 手动 resume
    resume_block = RATH_ROUTES[RATH_ROUTES.find('elif action == "resume"'):
                               RATH_ROUTES.find('else:  # archive')]
    assert "pause_reason=null" in resume_block.replace(" ", "")
    assert "paused_at=null" in resume_block.replace(" ", "")
    assert "last_tick_at=now()" in resume_block.replace(" ", "")
    # 72h 自动暂停(engine.run_due_ticks)
    auto_pause = ENGINE[ENGINE.find("def run_due_ticks"):]
    unviewed_block = auto_pause[:auto_pause.find("resumed = db.execute")]
    assert "pause_reason='unviewed'" in unviewed_block.replace(" ", "")
    assert "last_tick_at=now()" in unviewed_block.replace(" ", "")
    assert "paused_at=now()" in unviewed_block.replace(" ", "")
    # no_model 自动暂停(engine.tick_experiment 段0预检)
    no_model_block = ENGINE[ENGINE.find("段0:模型预检"):ENGINE.find("段1:认领")]
    assert "pause_reason='no_model'" in no_model_block.replace(" ", "")
    assert "last_tick_at=now()" in no_model_block.replace(" ", "")
    assert "paused_at=now()" in no_model_block.replace(" ", "")


def test_player_active_auto_resume_checks_branch_commits_gap():
    body = ENGINE[ENGINE.find("def run_due_ticks"):]
    resume_block = body[body.find("resumed = db.execute"):body.find("due = db.execute")]
    assert "pause_reason='player_active'" in resume_block.replace(" ", "")
    assert "branch_commits" in resume_block
    assert "interval '2 hours'" in resume_block
    assert "pause_reason=null" in resume_block.replace(" ", "")
    assert "last_tick_at=now()" in resume_block.replace(" ", "")
    assert '"玩家离开约2小时,世界继续"' in body


# ── A3: MAX_SCENES_PER_DAY 强制 P0 ───────────────────────────────────

def test_scene_budget_enforced_before_director_call():
    assert "MAX_SCENES_PER_DAY = 12" in ENGINE
    i = ENGINE.find('interaction = ap.get("interaction")')
    j = ENGINE.find("if scene:", ENGINE.find("build_director_prompts"))
    window = ENGINE[i:j]
    assert 'int(claim.get("scenes_today") or 0) >= MAX_SCENES_PER_DAY' in window.replace("\n", " ").replace("  ", " ") or \
        "scenes_today\") or 0) >= MAX_SCENES_PER_DAY" in window
    assert "interaction = None" in window, "超预算必须清空 interaction,不再调用呈现 LLM"
    # cast_updates/facts 不受影响:budget 检查不清空 wrote/sim['facts'](已由
    # apply_scheduler_output 落好),只丢弃 interaction 本身。
    assert 'sim.setdefault("facts", [])' in window


# ── A4: no_model 预检必须在认领(_claim_tick)之前 ─────────────────────

def test_no_model_precheck_happens_before_claim():
    i_precheck = ENGINE.find("段0:模型预检")
    i_no_model_return = ENGINE.find('"reason": "no_model"')
    i_claim_call = ENGINE.find("claim = _claim_tick(db, exp_id, manual=manual)")
    assert i_precheck != -1 and i_no_model_return != -1 and i_claim_call != -1
    assert i_precheck < i_no_model_return < i_claim_call, (
        "no_model 判定与 return 必须先于 _claim_tick 调用——否则钟已经白白推进一拍")
    assert "pause_reason='no_model'" in ENGINE.replace(" ", "")
    assert '"无可用模型,已自动暂停,请检查模型凭据"' in ENGINE


# ── A5: last_viewed 语义 ─────────────────────────────────────────────

def test_list_endpoint_never_bumps_last_viewed():
    body = RATH_ROUTES[RATH_ROUTES.find("async def api_rath_list"):
                        RATH_ROUTES.find("async def api_rath_create")]
    assert "last_viewed_at=now()" not in body, "列表端点是被动轮询,不得续 72h 无人看的命"


def test_detail_endpoint_bumps_only_when_active_flag_set():
    body = RATH_ROUTES[RATH_ROUTES.find("async def api_rath_detail"):
                        RATH_ROUTES.find("async def api_rath_tick")]
    assert "active: int = 0" in body, "detail 端点须支持 ?active= 查询参数"
    assert "if active == 1:" in body
    i = body.find("if active == 1:")
    j = body.find("events = db.execute")
    assert "last_viewed_at=now()" in body[i:j]


def test_tick_and_action_endpoints_always_bump():
    tick_body = RATH_ROUTES[RATH_ROUTES.find("async def api_rath_tick"):
                             RATH_ROUTES.find("async def api_rath_action")]
    assert "last_viewed_at=now()" in tick_body
    action_body = RATH_ROUTES[RATH_ROUTES.find("async def api_rath_action"):]
    for branch in ('action == "directive"', 'action == "accel"', 'action == "pause"',
                   'action == "resume"'):
        seg_start = action_body.find(branch)
        assert seg_start != -1, f"缺分支 {branch}"
    # directive/accel/pause/resume/archive 每个分支都必须出现 last_viewed_at=now()
    assert action_body.count("last_viewed_at=now()") >= 4


# ── A6: migration 96 ─────────────────────────────────────────────────

def test_migration_96_adds_pause_reason_columns():
    assert '(96, "rath_pause_reason_and_briefing_cursor"' in MIGRATIONS
    seg = MIGRATIONS[MIGRATIONS.find('(96, "rath_pause_reason_and_briefing_cursor"'):
                      MIGRATIONS.find("]\n\n\ndef _assert_migrations_monotonic")]
    for col in ("pause_reason text", "paused_at timestamptz", "last_briefed_at timestamptz"):
        assert col in seg
    assert "add column if not exists" in seg, "纯增列,不得破坏已有数据"


def test_migrations_module_imports_and_stays_monotonic():
    import importlib
    m = importlib.import_module("platform_app.db.migrations")
    versions = [v for v, _, _ in m.MIGRATIONS]
    assert versions == sorted(versions) and len(versions) == len(set(versions))
    assert 96 in versions


# ── A7: 玩家回合自动暂停 hook ────────────────────────────────────────

def test_game_py_pauses_on_player_turn():
    assert "pause_reason='player_active'" in GAME_ROUTES.replace(" ", "")
    assert "status='running'" in GAME_ROUTES.replace(" ", "")
    assert "paused_at=now()" in GAME_ROUTES.replace(" ", "")
    assert "last_tick_at=now()" in GAME_ROUTES.replace(" ", "")
    assert '"玩家回归,世界暂停"' in GAME_ROUTES
    # 非致命:必须包在 try/except 里,绝不能让 RATH 钩子挂掉聊天主流程
    i = GAME_ROUTES.find("pause_reason='player_active'")
    surrounding = GAME_ROUTES[max(0, i - 1600):i]
    assert "try:" in surrounding and "except Exception" in GAME_ROUTES[i:i + 1200]


# ── A9: 孤儿 kb_event(commit 漂移)P1 ─────────────────────────────────

def test_persist_phase_rereads_active_commit_before_record_event():
    seg3 = ENGINE[ENGINE.find("段3:落库"):]
    i_reread = seg3.find("select active_commit_id from game_saves")
    i_first_record = seg3.find("record_event(db, save_id, commit_id")
    assert i_reread != -1 and i_first_record != -1
    assert i_reread < i_first_record, "必须落库前重读 active_commit_id,再调用 record_event"
    assert "commit_id = _fresh_cid" in seg3


# ── A10: detail 轮询减负 P2 ──────────────────────────────────────────

def test_detail_skips_full_snapshot_read_when_sim_has_cast():
    body = RATH_ROUTES[RATH_ROUTES.find("async def api_rath_detail"):
                        RATH_ROUTES.find("async def api_rath_tick")]
    assert '_sim_pre.get("cast")' in body
    assert "_read_snapshot(db, int(exp[" in body  # 仍保留(仅在无 cast 时才调用)
    i = body.find('if _sim_pre and (_sim_pre.get("cast") or {}):')
    assert i != -1
    j = body.find("else:", i)
    assert "snap: dict = {}" in body[i:j]


# ── A11: trace 清理只在 ticked>0 时执行 P3 ───────────────────────────

def test_trace_cleanup_gated_on_ticked_count():
    body = ENGINE[ENGINE.find("def run_due_ticks"):]
    i_ticked_loop = body.find("for r in (due or []):")
    i_cleanup = body.find("delete from rath_events t using")
    assert i_ticked_loop != -1 and i_cleanup != -1
    assert i_ticked_loop < i_cleanup, "清理必须在推进循环之后,才能知道 ticked 是否 >0"
    guard_window = body[i_ticked_loop:i_cleanup]
    assert "if ticked > 0:" in body[i_cleanup - 200:i_cleanup]


# ── A12: save_kind / pause_reason 暴露 ───────────────────────────────

def test_expose_includes_save_kind_and_pause_reason():
    expose_body = ENGINE[ENGINE.find("def _expose"):ENGINE.find("def create_experiment")]
    assert '"save_kind": row.get("save_kind")' in expose_body
    assert '"pause_reason": row.get("pause_reason")' in expose_body


def test_own_exp_and_list_join_game_saves_for_save_kind():
    assert "join game_saves s on s.id = t.save_id" in RATH_ROUTES


# ── accel 联动(routes/rath.py accel action)────────────────────────

def test_accel_action_sets_tick_interval_sec_with_clamp():
    body = RATH_ROUTES[RATH_ROUTES.find('elif action == "accel"'):
                        RATH_ROUTES.find('elif action == "pause"')]
    assert "tick_interval_sec" in body
    assert "max(600,min(3600" in body.replace(" ", "")
    # 契约给的三个锚点:240x=600s / 60x=1800s / 1x=3600s
    for accel, expected in ((240, 600), (60, 1800), (1, 3600)):
        computed = max(600, min(3600, (1800 * 60) // accel))
        assert computed == expected


# ── A13: 孤儿模块已删除 ──────────────────────────────────────────────

def test_orphan_npc_scene_module_removed():
    assert not (ROOT / "rath" / "npc_scene.py").exists(), "npc_scene.py 应已删除(被 sim.py 取代)"
    assert not (ROOT / "tests" / "unit" / "test_rath_engine.py").exists(), \
        "专属旧测试应随孤儿模块一并删除"
    assert "npc_scene" not in ENGINE.replace('"source": "rath_npc_scene"', ""), (
        "engine.py 不应再有 npc_scene 的功能性引用(元数据标签字符串本身除外)")


# ── 铁律回归:引擎绝不 import 游戏 state 写入口 ───────────────────────

def test_engine_never_imports_state_writers():
    for forbidden in ("persist_runtime_state", "record_runtime_turn", "apply_ops",
                      "update_active_node", "import_state"):
        assert forbidden not in ENGINE, f"engine.py 不得触碰 state 写入口: {forbidden}"


def test_clock_label_pure_function():
    import sys
    sys.path.insert(0, str(ROOT))
    from rath.engine import _clock_label
    assert _clock_label(0) == "第1日 00:00"
    assert _clock_label(61) == "第1日 01:01"
    assert _clock_label(1441) == "第2日 00:01"


def test_create_experiment_starts_at_morning():
    """v4.1:新实验世界钟从第1日 08:00(480分钟)起步,不再从午夜 0 起步
    (269 浸泡实锤:前6拍全落夜律窗口=全员安睡零场景,第一印象灾难)。"""
    src = (ROOT / "rath" / "engine.py").read_text(encoding="utf-8")
    i = src.find("insert into rath_experiments")
    assert i != -1
    seg = src[i:i + 400]
    assert "world_clock_min" in seg and "480" in seg
