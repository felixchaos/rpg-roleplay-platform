"""群反馈(行者无疆):worldbook 子代理选章不读进度——current_phase=自由标签(「玩家分支」)
匹配不到 phase → fallback 开端;且五等分巨窗 phase(开端1..78)下 chapter_facts 恒从
phase 开头拉最早5章 → GM 把 ch1-5 的生化危机当刚发生。进度地板钳制(源码结构断言)。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "agents" / "worldbook_agent.py").read_text(encoding="utf-8")


def test_consult_clamps_anchor_to_progress_floor():
    i = SRC.find("def consult(")
    body = SRC[i:SRC.find("\ndef ", i + 1)]
    assert "get_progress_window" in body, "consult 必须读权威进度窗口作地板"
    assert "progress_floor" in body, "anchor 整体落后进度时须按进度章重定位 phase"
    # 中段抬地板分支:phase 巨窗(开端1..78)且进度在中段时,chapter_min 抬到进度章
    assert 'anchor["chapter_min"] = _pc' in body
    # 显式跳跃语义优先,不被地板覆盖
    assert "not jump_to_chapter and not jump_to_phase" in body


def test_phase_digest_summary_filtered_at_midphase():
    """行者无疆二报:锚点/章节事实已钳到进度,但阶段摘要整段注入 phase 头部内容
    (拆书病态拼接体,全是 ch1-5 生化危机)→子代理复述旧事。中段/重定位时 summary
    必须换成 ≥进度章的 key_events(带章号确定性过滤),渲染层空 summary 跳过该段。"""
    src = (ROOT / "agents" / "worldbook_agent.py").read_text(encoding="utf-8")
    i = src.find("def consult(")
    body = src[i:src.find("\ndef _resolve_anchor")]
    assert '"floored"' in body, "中段抬升必须打标"
    assert 'anchor.get("source") == "progress_floor" or anchor.get("floored")' in body
    assert '>= _floor_ch' in body, "key_events 按 ≥进度章过滤"
    # 渲染层:summary 空则跳过阶段摘要段(不再输出空段/整段病态拼接)
    j = src.find("def to_context_text")
    render = src[j:j + 1200]
    assert "str(self.phase_digest.get('summary') or '').strip()" in render
