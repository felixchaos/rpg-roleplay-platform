"""test_context_budget_solver.py — 层预算全局求解的性质测试。

求解器的三条硬性质(违反任何一条都会让上下文装配出现难查的偏差):
  ① 预算宽裕时 = 每层拿满 want → 与引入求解前逐字节相同(这是本次改动的风险闸)
  ② 任何情况下不超预算、不超 want
  ③ 装不下时按 priority 升序丢弃,高优先级层不会先于低优先级层被丢
"""
from __future__ import annotations

from context_engine.budget import solve_layer_budgets


def _specs():
    return [
        {"id": "rules", "min": 2000, "want": 2000, "priority": 100},
        {"id": "npc_cards", "min": 1500, "want": 12000, "priority": 60},
        {"id": "novel_retrieval", "min": 2000, "want": 20000, "priority": 40},
        {"id": "hypotheses", "min": 360, "want": 1200, "priority": 32},
        {"id": "user_input", "min": 2400, "want": 2400, "priority": 0},
    ]


def test_generous_budget_is_byte_identical_to_no_solving():
    specs = _specs()
    want_total = sum(s["want"] for s in specs)
    granted, dropped = solve_layer_budgets(specs, want_total * 3)
    assert dropped == []
    assert granted == {s["id"]: s["want"] for s in specs}


def test_zero_budget_means_no_solving():
    specs = _specs()
    granted, dropped = solve_layer_budgets(specs, 0)
    assert dropped == []
    assert granted == {s["id"]: s["want"] for s in specs}


def test_never_exceeds_budget_or_want():
    specs = _specs()
    for budget in (6500, 9000, 14000, 25000, 37600):
        granted, dropped = solve_layer_budgets(specs, budget)
        assert sum(granted.values()) <= budget, (budget, granted)
        for s in specs:
            if s["id"] in granted:
                assert granted[s["id"]] <= s["want"]
                assert granted[s["id"]] >= s["min"]


def test_drops_lowest_priority_first():
    specs = _specs()
    # min 之和 = 8260;给 5000 装不下,必须丢层
    granted, dropped = solve_layer_budgets(specs, 5000)
    assert dropped, "预算低于 min 之和却一层没丢"
    # user_input(priority 0)最先走,rules(priority 100)最后走
    assert dropped[0] == "user_input"
    assert "rules" not in dropped


def test_headroom_is_shared_not_monopolised():
    """剩余预算按 (want-min) 比例分,不是高优先级层一口吃光。"""
    specs = _specs()
    granted, dropped = solve_layer_budgets(specs, 12000)
    assert not dropped
    # novel_retrieval headroom 最大,应当分到最多的增量,但 npc_cards 不能是 0 增量
    assert granted["npc_cards"] > 1500
    assert granted["novel_retrieval"] > 2000


def test_duplicate_ids_collapse():
    specs = [
        {"id": "a", "min": 10, "want": 100, "priority": 50},
        {"id": "a", "min": 20, "want": 200, "priority": 50},
    ]
    granted, _ = solve_layer_budgets(specs, 10_000)
    assert granted == {"a": 200}


def test_min_is_clamped_to_want():
    """min > want 会让求解无解;必须被夹住而不是抛。"""
    specs = [{"id": "a", "min": 999, "want": 100, "priority": 50}]
    granted, dropped = solve_layer_budgets(specs, 50)
    assert dropped == ["a"] or granted.get("a", 0) <= 100
