"""RATH v3 离线简报桥:玩家回归回合注入离线世界纪要(确定性聚合,零 LLM)。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BR = (ROOT / "rath" / "briefing.py").read_text(encoding="utf-8")
RET = (ROOT / "retrieval.py").read_text(encoding="utf-8")


def test_briefing_deterministic_and_bounded():
    assert "rath\\_%%" in BR, "只聚合 RATH 产物"
    assert "MAX_BRIEF_CHARS = 700" in BR, "纪要有界"
    assert "MIN_GAP_MINUTES = 120" in BR, "连续对话不打扰(间隔<2h 不注入)"
    assert "role = 'user'" in BR, "窗口起点含玩家消息"
    assert "branch_commits" in BR and "greatest" in BR, (
        "kb_native 档不写 flat messages,玩家活动须以 branch_commits 为主源(353 prod 实锤)")
    assert "retired_at_commit is null" in BR, "尊重 tombstone"
    body = BR[BR.find("def build_offline_briefing"):]
    assert "不要一口气复述全部" in body, "GM 指令:自然提及不照本宣科"
    assert "call_agent" not in BR and "llm" not in BR.lower().replace("零 llm", ""), "零 LLM 确定性拼装"


def test_briefing_wired_into_retrieval_gated():
    i = RET.find("离线世界纪要")
    assert i != -1, "必须接进材料装配"
    seg = RET[i:i + 1200]
    assert "rath_experiments" in seg and "'running','paused'" in seg.replace(" ", ""), "只对绑定活跃实验的档注入"
    assert "非致命" in seg, "失败不阻断回合"
