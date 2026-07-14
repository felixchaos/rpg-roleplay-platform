"""确定性星期验错(客户 abci 反馈:LLM 算不对星期,「今天周日→明天却写成周六」)。

剧本演绎:默认休眠。只有剧情里有确切「今天=周X」+ 相对日(明天/后天/…)配了星期时,
才用算法查出错的。玄幻/修仙/无日历剧本零副作用。不注入、不改写,只查。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agents.timeline_narrative_guard import (  # noqa: E402
    detect_weekday_violations as chk,
    parse_today_weekday as today,
)


def test_customer_deepseek_case_all_wrong():
    """客户原例:今天周日, 明天周六, 后天周日, 大后天周一 —— 三处相对日全算错,全查出。"""
    v = chk("今天周日，明天周六，后天周日，大后天周一")
    rels = {x["rel"]: (x["claimed_name"], x["expected_name"]) for x in v}
    assert rels["明天"] == ("周六", "周一")
    assert rels["后天"] == ("周日", "周二")
    assert rels["大后天"] == ("周一", "周三")


def test_correct_sequence_passes():
    assert chk("今天周日，明天周一，后天周二") == []
    assert chk("今天周三，明天周四，昨天周二") == []


def test_dormant_without_concrete_anchor():
    """无日历剧本(玄幻/修仙):没有确切「今天=周X」→ 完全不触发,零误伤。"""
    assert chk("他御剑飞行三日,抵达昆仑。明日便是论剑之期。") == []
    assert chk("傍晚时分,他推开门。第二天清晨又出发了。") == []
    assert chk("三天后我们在城门口集合") == []


def test_external_base_from_player_this_turn():
    """玩家本回合确立今天=周日,GM 输出把明天写成周六 → 查出。"""
    base = today("我告诉你,今天周日,明天周一,后天周二")
    assert base == 6
    v = chk("他说明天周六就能休息了", base_weekday=base)
    assert len(v) == 1 and v[0]["expected_name"] == "周一" and v[0]["claimed_name"] == "周六"


def test_variant_naming():
    """礼拜天 / 星期日 / 阿拉伯数字都能认。"""
    v = chk("今天礼拜天,后天星期日有空")   # 后天应周二
    assert v and v[0]["expected_name"] == "周二"
    v2 = chk("今天周1,明天周3")            # 明天应周二
    assert v2 and v2[0]["expected_name"] == "周二"


def test_backward_reference():
    v = chk("今天周三,昨天周日发生的")     # 昨天应周二
    assert v and v[0]["rel"] == "昨天" and v[0]["expected_name"] == "周二"


def test_parse_today_weekday():
    assert today("今天是周日") == 6
    assert today("今天周一,开始新的一周") == 0
    assert today("傍晚时分") is None
    assert today("明天周六") is None  # 只认「今天」,不拿相对日当基准


def test_unified_into_narrative_guards():
    """星期验错必须走【统一的 run_narrative_guards】(和时间跳跃/套路比喻同一个入口),不是孤立接线。"""
    from agents.timeline_narrative_guard import run_narrative_guards
    # run_narrative_guards 内部覆盖星期
    evs = run_narrative_guards("今天周日，明天周六", "", {"turn": 1})
    kinds = [name for name, _ in evs]
    assert "weekday_notice" in kinds
    wd = next(p for name, p in evs if name == "weekday_notice")
    assert any(e["expected"] == "周一" for e in wd["errors"])
    # 无日历剧本:统一入口也零副作用
    assert run_narrative_guards("他御剑飞行三日,明日便是论剑之期。", "", {"turn": 1}) == []


def test_wired_into_pipeline_and_frontend():
    cp = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((REPO / "chat_pipeline").glob("*.py")))
    # 消除散落:不再有孤立的 _weekday_check_events;两路都调统一 run_narrative_guards
    assert "_weekday_check_events" not in cp
    assert cp.count("run_narrative_guards(response, ctx.message_for_model, state)") == 2
    gc = (REPO.parent / "frontend" / "src" / "entries" / "game-console.jsx").read_text(encoding="utf-8")
    assert "on_weekday_notice" in gc
