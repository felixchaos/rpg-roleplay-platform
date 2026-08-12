"""RATH v4 preflight 单测(D4/D5)。

对齐 scratchpad/rath_v4_contracts.md 的 preflight 响应契约(字段一个不能少/拼错,
前端已按此接好 UI)与 scratchpad/rath_v4_audit_compat.json 的 D1/D2/D3 分层方案。

用 mock db(不连库)驱动 rath.engine._compute_preflight / create_experiment —— 两者共用
同一份判定(D5:防止「预检说能建、真建又用另一套口径拒绝」的双标准漂移)。
_resolve_progress 统一 monkeypatch 掉(避免真触发 agents.anchor_seed_agent 里的 DB 访问)。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rath import engine  # noqa: E402

# ── mock db 基础设施(风格延续 test_rath_briefing.py 的 _MockDB) ──────────

class _Result:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = many if many is not None else []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _FakeDB:
    """按 SQL 关键字分派的假连接。覆盖 _compute_preflight 与 create_experiment 会
    发出的全部查询;未预期的 SQL 直接抛错,防止测试静默漏检某条新查询。"""

    def __init__(self, *, save_row, chapter_facts=None, anchors=None, cast_imp=None,
                 wb_count=0, loc_count=0, exp_count=0, dup_exp=None, insert_row=None):
        self.save_row = save_row
        self.chapter_facts = chapter_facts or []
        self.anchors = anchors or []
        self.cast_imp = cast_imp or []
        self.wb_count = wb_count
        self.loc_count = loc_count
        self.exp_count = exp_count
        self.dup_exp = dup_exp
        self.insert_row = insert_row
        self.calls: list[tuple[str, object]] = []
        self.committed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        s = sql.lower()
        if "from game_saves" in s:
            return _Result(one=self.save_row)
        if "from chapter_facts" in s:
            return _Result(many=self.chapter_facts)
        if "from script_timeline_anchors" in s:
            return _Result(many=self.anchors)
        if "count(*) c from rath_experiments where user_id" in s:
            return _Result(one={"c": self.exp_count})
        if "select id from rath_experiments where save_id" in s:
            return _Result(one=self.dup_exp)
        if s.strip().startswith("insert into rath_experiments"):
            return _Result(one=self.insert_row)
        if "from character_cards" in s:
            return _Result(many=self.cast_imp)
        if "from worldbook_entries" in s:
            return _Result(one={"c": self.wb_count})
        if "from kb_canon_entities" in s and "count(*)" in s:
            return _Result(one={"c": self.loc_count})
        raise AssertionError(f"未预期的 SQL(mock 未覆盖): {sql!r}")

    def commit(self):
        self.committed = True


class _CM:
    """包一层 with-connect() 上下文管理器,供 create_experiment 的 `with connect() as db`
    使用(engine.py 在函数体内 late-import platform_app.db.connect,monkeypatch 模块属性
    即可让该次调用拿到 fake)。"""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        return False


def _patch_progress(monkeypatch, chapter: int = 1) -> None:
    monkeypatch.setattr(engine, "_resolve_progress", lambda save_id, script_id: chapter)


CONTRACT_KEYS = {"ok", "can_create", "reason", "tier", "river", "cast",
                 "worldbook", "locations", "warnings"}


# ── D4: tier 三档 ────────────────────────────────────────────────────────

def test_tier_full_requires_chapter_facts_and_cast_and_worldbook(monkeypatch):
    _patch_progress(monkeypatch)
    db = _FakeDB(
        save_row={"id": 1, "user_id": 9, "script_id": 42, "save_kind": "game"},
        chapter_facts=[{"chapter": 1, "summary": "开局"}, {"chapter": 2, "summary": "冲突"}],
        cast_imp=[{"name": "A", "i": 1}, {"name": "B", "i": 2}, {"name": "C", "i": 3},
                  {"name": "D", "i": 4}, {"name": "E", "i": 5}],
        wb_count=5, loc_count=10,
    )
    out = engine._compute_preflight(db, 9, 1)
    assert out["ok"] is True
    assert out["tier"] == "full"
    assert out["can_create"] is True
    assert out["reason"] == ""
    assert out["river"] == {"beats": 2, "source": "chapter_facts"}
    assert out["cast"]["count"] == 5
    assert out["worldbook"]["count"] == 5
    assert out["locations"]["count"] == 10
    assert out["warnings"] == []


def test_tier_degraded_when_river_falls_back_to_anchors(monkeypatch):
    """D3:chapter_facts 空但 script_timeline_anchors 有锚点——river.source='anchors',
    tier 判定要求 source=='chapter_facts' 才给 full,故降为 degraded,warnings 具体化。"""
    _patch_progress(monkeypatch)
    db = _FakeDB(
        save_row={"id": 1, "user_id": 9, "script_id": 42, "save_kind": "game"},
        chapter_facts=[],
        anchors=[{"chapter": 1, "summary": "开局锚点"}, {"chapter": 3, "summary": "转折锚点"}],
        cast_imp=[{"name": "A", "i": 1}, {"name": "B", "i": 2}, {"name": "C", "i": 3}],
        wb_count=5, loc_count=0,
    )
    out = engine._compute_preflight(db, 9, 1)
    assert out["tier"] == "degraded"
    assert out["river"] == {"beats": 2, "source": "anchors"}
    assert any("回退为时间线锚点" in w and "2条" in w for w in out["warnings"])
    assert any("无独立地点条目" in w for w in out["warnings"])


def test_tier_degraded_when_river_and_anchors_both_empty(monkeypatch):
    _patch_progress(monkeypatch)
    db = _FakeDB(
        save_row={"id": 1, "user_id": 9, "script_id": 42, "save_kind": "game"},
        chapter_facts=[], anchors=[], cast_imp=[], wb_count=0, loc_count=0,
    )
    out = engine._compute_preflight(db, 9, 1)
    assert out["tier"] == "degraded"
    assert out["river"] == {"beats": 0, "source": "none"}
    assert any("完全依赖自由演化" in w for w in out["warnings"])
    assert any("角色卡不足" in w for w in out["warnings"])
    assert any("世界书条目不足" in w for w in out["warnings"])


def test_tier_degraded_when_cast_ranked_below_three(monkeypatch):
    """D1 排名化回归:即便所有 importance 都远低于旧的绝对阈值100,只要排名>0 就该被
    计入(无职转生同款场景:max_imp 118,大量卡在个位数——这里故意全部 <100)。"""
    _patch_progress(monkeypatch)
    db = _FakeDB(
        save_row={"id": 1, "user_id": 9, "script_id": 42, "save_kind": "game"},
        chapter_facts=[{"chapter": 1, "summary": "x"}],
        cast_imp=[{"name": "A", "i": 3}, {"name": "B", "i": 1}],  # 只有2人 importance>0
        wb_count=5, loc_count=1,
    )
    out = engine._compute_preflight(db, 9, 1)
    assert out["cast"]["count"] == 2, "旧绝对阈值(>=100)会把两人都清零成0,这里必须是排名后的2"
    assert out["tier"] == "degraded"  # 卡司<3
    assert any("角色卡不足" in w and "仅2人" in w for w in out["warnings"])


def test_tier_free_when_no_script_id(monkeypatch):
    """save 无剧本关联(酒馆/自由档天然场景)——tier=free,不查任何材料表,can_create 仍为真。"""
    _patch_progress(monkeypatch)
    db = _FakeDB(save_row={"id": 1, "user_id": 9, "script_id": None, "save_kind": "game"})
    out = engine._compute_preflight(db, 9, 1)
    assert out["tier"] == "free"
    assert out["can_create"] is True
    assert out["river"] == {"beats": 0, "source": "none"}
    assert out["cast"]["count"] == 0
    assert out["worldbook"]["count"] == 0
    assert out["locations"]["count"] == 0
    assert out["warnings"] == []
    # 无剧本时不该碰 chapter_facts/character_cards 等材料表
    touched = " ".join(sql.lower() for sql, _ in db.calls)
    assert "chapter_facts" not in touched and "character_cards" not in touched


# ── D4: 酒馆档拒建 ───────────────────────────────────────────────────────

def test_tavern_save_forces_can_create_false_regardless_of_tier(monkeypatch):
    _patch_progress(monkeypatch)
    db = _FakeDB(
        save_row={"id": 1, "user_id": 9, "script_id": None, "save_kind": "tavern"},
    )
    out = engine._compute_preflight(db, 9, 1)
    assert out["can_create"] is False
    assert out["reason"] == "酒馆存档暂不支持离线世界"


# ── D4: IDOR ─────────────────────────────────────────────────────────────

def test_idor_save_not_owned_by_user():
    db = _FakeDB(save_row={"id": 1, "user_id": 999, "script_id": 42, "save_kind": "game"})
    out = engine._compute_preflight(db, 9, 1)
    assert out["ok"] is False
    assert "error" in out
    assert "can_create" not in out  # 未通过归属校验,不应继续算 tier


def test_idor_save_not_found():
    db = _FakeDB(save_row=None)
    out = engine._compute_preflight(db, 9, 1)
    assert out["ok"] is False


# ── D4: 契约字段全集 ──────────────────────────────────────────────────────

def test_contract_field_set_complete_and_no_extraneous_keys(monkeypatch):
    _patch_progress(monkeypatch)
    db = _FakeDB(
        save_row={"id": 1, "user_id": 9, "script_id": 42, "save_kind": "game"},
        chapter_facts=[{"chapter": 1, "summary": "开局"}],
        cast_imp=[{"name": "A", "i": 10}], wb_count=1, loc_count=1,
    )
    out = engine._compute_preflight(db, 9, 1)
    assert set(out.keys()) == CONTRACT_KEYS, f"字段集合须精确匹配契约: {set(out.keys())}"
    assert set(out["river"].keys()) == {"beats", "source"}
    assert set(out["cast"].keys()) == {"count"}
    assert set(out["worldbook"].keys()) == {"count"}
    assert set(out["locations"].keys()) == {"count"}
    assert out["river"]["source"] in ("chapter_facts", "anchors", "none")
    assert out["tier"] in ("full", "degraded", "free")
    assert isinstance(out["warnings"], list)


# ── D5: create_experiment 复用 preflight 判定 ────────────────────────────

def _insert_row(save_id=1, script_id=42):
    return {
        "id": 7, "save_id": save_id, "script_id": script_id, "status": "running",
        "pause_reason": None, "accel": 60, "tick_interval_sec": 1800,
        "world_clock_min": 0, "ticks_today": 0, "scenes_today": 0,
        "directive": "", "last_tick_at": "", "created_at": "",
    }


def test_create_experiment_rejects_tavern_save_with_400_shape(monkeypatch):
    _patch_progress(monkeypatch)
    db = _FakeDB(save_row={"id": 1, "user_id": 9, "script_id": None, "save_kind": "tavern"})
    monkeypatch.setattr("platform_app.db.connect", lambda: _CM(db))
    monkeypatch.setattr("platform_app.db.init_db", lambda: None)
    out = engine.create_experiment(9, 1)
    assert out["ok"] is False
    assert out["error"] == "酒馆存档暂不支持离线世界"
    # 拒建必须发生在任何写操作之前——不能出现 insert
    assert not any(sql.strip().lower().startswith("insert into rath_experiments")
                   for sql, _ in db.calls)


def test_create_experiment_rejects_idor_before_touching_experiment_table(monkeypatch):
    _patch_progress(monkeypatch)
    db = _FakeDB(save_row={"id": 1, "user_id": 999, "script_id": 42, "save_kind": "game"})
    monkeypatch.setattr("platform_app.db.connect", lambda: _CM(db))
    monkeypatch.setattr("platform_app.db.init_db", lambda: None)
    out = engine.create_experiment(9, 1)
    assert out["ok"] is False
    assert "不属于你" in out["error"]


def test_create_experiment_succeeds_and_attaches_preflight_warnings(monkeypatch):
    _patch_progress(monkeypatch)
    db = _FakeDB(
        save_row={"id": 1, "user_id": 9, "script_id": 42, "save_kind": "game"},
        chapter_facts=[], anchors=[],  # 两者皆空 → warnings 非空(D3 自由演化提示)
        cast_imp=[{"name": "A", "i": 5}], wb_count=0, loc_count=0,
        exp_count=0, dup_exp=None, insert_row=_insert_row(),
    )
    monkeypatch.setattr("platform_app.db.connect", lambda: _CM(db))
    monkeypatch.setattr("platform_app.db.init_db", lambda: None)
    out = engine.create_experiment(9, 1)
    assert out["ok"] is True
    assert "warnings" in out and isinstance(out["warnings"], list) and out["warnings"]
    assert out["experiment"]["id"] == 7
    assert db.committed


def test_create_experiment_still_enforces_running_cap_after_preflight_passes(monkeypatch):
    """D5 不能吃掉既有闸门:preflight 通过后,MAX_RUNNING_PER_USER/去重闸依旧生效。"""
    _patch_progress(monkeypatch)
    db = _FakeDB(
        save_row={"id": 1, "user_id": 9, "script_id": 42, "save_kind": "game"},
        chapter_facts=[{"chapter": 1, "summary": "x"}],
        cast_imp=[{"name": "A", "i": 5}], wb_count=5,
        exp_count=engine.MAX_RUNNING_PER_USER,
    )
    monkeypatch.setattr("platform_app.db.connect", lambda: _CM(db))
    monkeypatch.setattr("platform_app.db.init_db", lambda: None)
    out = engine.create_experiment(9, 1)
    assert out["ok"] is False
    assert "同时最多" in out["error"]


# ── D1: 排名化纯函数 ──────────────────────────────────────────────────────

def test_ranked_top_names_ignores_absolute_value_only_relative_rank():
    imp = {"甲": 1, "乙": 2, "丙": 3, "丁": 4, "戊": 5, "己": 6}  # 全部远低于旧绝对阈值100
    top = engine._ranked_top_names(imp, 5)
    assert top == ["己", "戊", "丁", "丙", "乙"], "top5 且按 importance 降序,不看绝对值"


def test_ranked_top_names_excludes_zero_and_negative():
    imp = {"甲": 0, "乙": -1, "丙": 5}
    assert engine._ranked_top_names(imp, 5) == ["丙"]


def test_ranked_top_names_empty_when_all_zero():
    assert engine._ranked_top_names({"甲": 0, "乙": 0}, 5) == []


# ── D3: _load_canon_beats 退化阶梯 ────────────────────────────────────────

class _BeatsDB:
    def __init__(self, chapter_facts=None, anchors=None):
        self.chapter_facts = chapter_facts or []
        self.anchors = anchors or []
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        s = sql.lower()
        if "from chapter_facts" in s:
            return _Result(many=self.chapter_facts)
        if "from script_timeline_anchors" in s:
            return _Result(many=self.anchors)
        raise AssertionError(sql)


def test_load_canon_beats_prefers_chapter_facts_over_anchors():
    db = _BeatsDB(chapter_facts=[{"chapter": 1, "summary": "正文"}],
                   anchors=[{"chapter": 1, "summary": "锚点(不该被用到)"}])
    out = engine._load_canon_beats(db, 42, 1, 12, limit=12)
    assert out["source"] == "chapter_facts"
    assert out["rows"] == [{"chapter": 1, "summary": "正文"}]
    # 锚点表压根不该被查询(短路优化,亦证明"优先级"真的生效)
    assert not any("script_timeline_anchors" in sql.lower() for sql, _ in db.calls)


def test_load_canon_beats_falls_back_to_anchors_when_chapter_facts_empty():
    db = _BeatsDB(chapter_facts=[], anchors=[{"chapter": 3, "summary": "锚点摘要"}])
    out = engine._load_canon_beats(db, 42, 1, 12, limit=12)
    assert out["source"] == "anchors"
    assert out["rows"] == [{"chapter": 3, "summary": "锚点摘要"}]


def test_load_canon_beats_free_evolution_when_both_empty():
    db = _BeatsDB(chapter_facts=[], anchors=[])
    out = engine._load_canon_beats(db, 42, 1, 12, limit=12)
    assert out == {"rows": [], "source": "none"}


def test_load_canon_beats_no_script_id_short_circuits():
    db = _BeatsDB(chapter_facts=[{"chapter": 1, "summary": "不该被查到"}])
    out = engine._load_canon_beats(db, 0, 1, 12, limit=12)
    assert out == {"rows": [], "source": "none"}
    assert db.calls == [], "script_id=0 应直接短路,不发任何查询"


def test_load_canon_beats_open_ended_range_used_for_refill():
    """canon_refill 场景:chapter_to=None,不设上界。"""
    db = _BeatsDB(chapter_facts=[{"chapter": 20, "summary": "续拉段"}])
    out = engine._load_canon_beats(db, 42, 20, limit=8)
    assert out["source"] == "chapter_facts"
    sql, params = db.calls[0]
    assert "between" not in sql.lower(), "开放式续拉不应使用 between 上界"
    assert ">=" in sql or ">= " in sql or ">=%s" in sql.replace(" ", "")
