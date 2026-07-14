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

SRC = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((Path(__file__).resolve().parents[2] / "chat_pipeline").glob("*.py")))


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


def test_rewrite_candidate_does_not_continue_from_first_draft():
    """回归防线(行者无疆:『改写改到下一段去了』——原版末尾一声尖叫、改版顺着尖叫往下写):
    改写候选绝不能把改写指令追加到【含首稿的实时 state.history】之上(Phase 5 record_turn 已把
    [玩家行动+首稿]写进历史 → 模型把改写指令当新回合、续写首稿末尾)。必须用【首稿时的历史快照
    + 玩家行动】文本直调 backend 重建上下文,并明确要求「改写替换、不是续写」。"""
    # 改写候选走独立文本直调 helper,不再是 respond_stream_with_tools 追加到实时历史
    helper = SRC.split("def _rewrite_candidate_text", 1)[1].split("async def _gen_candidate_bg", 1)[0]
    assert "gm._backend.stream" in helper, "改写候选应文本直调 backend(不进工具循环、不追加到实时历史)"
    assert "_pre_hist" in helper, "改写候选必须用首稿时的历史快照重建上下文"
    assert ("不是续写" in helper) or ("不要接着往下写" in helper), "改写指令必须明确『不是续写』"
    # 两处调用点都把【历史快照 + 玩家行动】传进去(而非让 GM 读被 record_turn 污染的实时历史)
    gate = _gate_body()
    assert "list(state.history_messages())" in gate and "ctx.message_for_model" in gate
    # 老 bug 契约:gate 内不再用 respond_stream_with_tools 生成改写(那正是续写的根因)
    assert "gm.respond_stream_with_tools" not in gate, "改写候选不应再走会续写的 respond_stream_with_tools"
