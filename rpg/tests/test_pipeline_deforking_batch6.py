"""流水线去 fork · 批次6:审计遗漏补修(真机 e2e 复查后发现的漏项)。"""
from pathlib import Path
import inspect

REPO = Path(__file__).resolve().parent.parent


def test_reveal_clause_v2_gets_progress_chapter():
    """P1(休眠):retrieval.py v2 前沿门控分支必须传 progress_chapter(否则新档层级树空)。"""
    import retrieval
    src = inspect.getsource(retrieval.retrieve_context)
    # 三处 _rc_v2 调用都要带 progress_chapter(主 + parent + shadow)
    assert src.count("progress_chapter=_progress_chapter") >= 3, \
        "v2 reveal 分支仍有漏传 progress_chapter 的 _rc_v2 调用"


def test_harness_recall_guard_present():
    """P2:harness 路径(所有 BYOK 主路径)时间跳跃必须有 recall/time-value 双门,与 llm_curator 对齐。"""
    cp = (REPO / "agents" / "context_agent.py").read_text(encoding="utf-8")
    # 两个分支都必须出现这两道门(harness 分支此前缺)
    assert cp.count("is_recall_framing(user_input)") >= 2
    assert cp.count("looks_like_time_value(target)") >= 2
