"""群反馈(行者无疆):rail 档「原文完全没注入」——/set 跳章到 ch17 后,rail 原文注入
仍按出生点建档写死的 anchor_chapter_range[1,1] 选章(该字段建档后无任何代码更新,
却拥有绝对优先级)。新序:timeline.chapter_min/max(/set 时间跳跃持续更新的鲜活锚定)
优先,range 只作建档兜底。源码结构断言(与 test_anchor_pov_and_progress_fixes 同风格)。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "retrieval.py").read_text(encoding="utf-8")


def _block():
    i = SRC.find('anchor_range = (timeline.get("anchor_chapter_range")')
    assert i != -1
    return SRC[i:i + 1600]


def test_fresh_timeline_anchor_takes_priority_over_birth_range():
    b = _block()
    # 鲜活锚定读取必须存在,且其赋值分支在 anchor_range 分支之前(elif 关系)
    assert 'timeline.get("chapter_min")' in b
    assert b.find('timeline.get("chapter_min")') < b.find("isinstance(anchor_range, list)")
    assert "elif isinstance(anchor_range" in b, "range 必须降级为 elif 兜底,不得再无条件覆盖"


def test_priority_guard_only_on_positive_chapter():
    b = _block()
    assert "_tl_cmin > 0" in b, "chapter_min<=0/缺失时必须回退 range(不破坏出生点档行为)"
