"""acceptance 硬闸的行为契约(源码级)。

v1.32.9~1.32.13:gate 曾对首稿做 verify+retry 并【直接替换】首稿(重 apply 第二稿 ops)。
生产流式路径下这暴露成客户可见的「正文流完 5-10 秒自己变一套」(行者无疆反馈)。

现改版(A/B 用户裁决):
  · 首稿(玩家流式读到的)永远是权威版 —— gate 不再 apply 第二稿 ops、不替换 response/state。
  · 只在 unmet + 节流放行时生成【改写候选】、落 acceptance_ab_log、yield `acceptance_alt` 事件,
    并排给玩家选择(选改写才换消息,走 /api/acceptance/choice)。
本文件锁住这些不变量,防回归成「静默替换」。
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2] / "chat_pipeline.py").read_text(encoding="utf-8")


def _gate_body() -> str:
    # 取 _acceptance_gate 闭包体(到下一个顶层注释块为止)
    after = SRC.split("def _acceptance_gate", 1)[1]
    return after.split("# ── W1 容量优化", 1)[0]


def test_gate_does_not_reapply_or_replace_first_draft():
    """核心回归防线:gate 内绝不重 apply 第二稿、绝不替换 canonical。
    否则又变回「静默替换首稿」的老 bug。"""
    gate = _gate_body()
    assert "apply_structured_updates" not in gate, "gate 不应再 apply 第二稿 ops(会污染由首稿产生的确定性 state)"
    assert "_resp = _r2" not in gate, "gate 不应把 response 替换成第二稿(首稿永远是权威版)"
    # candidate 只捕获正文,不进 write-context(不写库)
    assert "ChatWriteContext" not in gate


def test_gate_emits_acceptance_alt_event_and_logs():
    """改写候选必须以 `acceptance_alt` 事件下发前端,并落 acceptance_ab_log 供数据采集。"""
    gate = _gate_body()
    assert '"acceptance_alt"' in gate or "'acceptance_alt'" in gate
    assert "_log_acceptance_ab" in gate


def test_gate_throttles_by_min_interval():
    """节流:每存档最多每 _ACCEPTANCE_AB_MIN_INTERVAL 回合提供一次改写候选。"""
    gate = _gate_body()
    assert "_ACCEPTANCE_AB_MIN_INTERVAL" in gate
    assert "last_offer_turn" in gate


def test_rewrite_candidate_uses_same_toolset_as_first_pass():
    """改写候选与首稿必须用同一工具集(_gm_tools),不能写死 unified_tools(slim 档不一致)。"""
    gate = _gate_body()
    idx = gate.find("gm.respond_stream_with_tools")
    assert idx > 0, "改写候选的 gm 调用不在 _acceptance_gate 闭包里"
    window = gate[idx:idx + 400]
    assert "tools=_gm_tools" in window
