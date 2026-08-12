"""开局「强制下一拍」必须指向最近的待发生锚点,而非全局/窗口里最重要的远章人物。

用户(行者无疆)反复反馈:「开局强制下一拍还是指向多章以后的人物」。
根因:gm_serving/steering.py:_fallback_soft_goal(本书 worldline_nodes 空,实际走此路径)
旧实现用 list_pending_for_phase 的默认 importance DESC 排序 + 开局窗口回退成 [1,30] +
窗口内空时退回全档按 importance —— 于是开局把「下一拍」指到最重要的远章角色。

修复:按 source_chapter ASC(order_by_chapter=True)取【最近的下一个】,并锚定 progress_chapter。
本测试用忠实模拟 list_pending_for_phase 排序语义的假实现,断言 rail/guided 都取近锚点。
"""
from __future__ import annotations

from agents import anchor_seed_agent as ASA
from gm_serving import steering as ST

# 数据集:开局两个近锚点(ch1/ch2,低重要度)+ 远章重要角色(ch28,高重要度)。
# 旧实现(importance DESC)开局会把 ch28 当「下一拍」;修复后按 chapter ASC 取 ch1/ch2,
# 远章重要角色在开局完全不出现(连 rail 的「其后」提示都是 ch2,不是 ch28)。
_NEAR = {"anchor_key": "a_ch1", "chapter": 1, "summary": "开局:主角在村口醒来",
         "importance": 30, "must_preserve": [], "may_vary": []}
_NEAR2 = {"anchor_key": "a_ch2", "chapter": 2, "summary": "村中遭遇盗匪",
          "importance": 20, "must_preserve": [], "may_vary": []}
_FAR = {"anchor_key": "a_ch28", "chapter": 28, "summary": "重要角色·圣女蕾穆丽娜登场",
        "importance": 99, "must_preserve": [], "may_vary": []}
_ALL = [_NEAR, _NEAR2, _FAR]


def _fake_window(*_a, **_k):
    # 开局:无 occurred 锚点 → 回退 [1,30]
    return {"chapter_min": 1, "chapter_max": 30, "source": "fallback",
            "last_satisfied_chapter": None}


def _fake_list_pending(save_id, phase=None, *, limit=5, chapter_min=None,
                       chapter_max=None, order_by_chapter=False):
    """忠实复刻真 list_pending_for_phase 的排序/过滤语义。"""
    rows = [a for a in _ALL
            if (chapter_min is None or a["chapter"] >= chapter_min)
            and (chapter_max is None or a["chapter"] <= chapter_max)]
    if order_by_chapter:
        rows.sort(key=lambda a: (a["chapter"], -a["importance"]))   # 最近优先
    else:
        rows.sort(key=lambda a: (-a["importance"], a["chapter"]))   # 旧:最重要优先
    return rows[:max(1, limit)]


def _patch(monkeypatch):
    monkeypatch.setattr(ASA, "get_progress_window", _fake_window)
    monkeypatch.setattr(ASA, "list_pending_for_phase", _fake_list_pending)


def test_rail_opening_targets_nearest_not_most_important(monkeypatch):
    _patch(monkeypatch)
    out = ST._fallback_soft_goal(123, wl_key="wl", steering_strength="rail", progress_chapter=1)
    # 下一拍 = 近锚点,绝不是远章重要角色
    assert "开局:主角在村口醒来" in out["soft_goal"]
    assert "圣女蕾穆丽娜" not in out["soft_goal"]
    assert out["pending_anchors"] == ["a_ch1"]
    # 仍是 rail 强措辞
    assert "强制收束" in out["soft_goal"]


def test_guided_opening_targets_nearest(monkeypatch):
    _patch(monkeypatch)
    out = ST._fallback_soft_goal(123, wl_key="wl", steering_strength="guided", progress_chapter=1)
    assert "开局:主角在村口醒来" in out["soft_goal"]
    assert "圣女蕾穆丽娜" not in out["soft_goal"]


def test_free_injects_nothing(monkeypatch):
    _patch(monkeypatch)
    out = ST._fallback_soft_goal(123, wl_key="wl", steering_strength="free", progress_chapter=1)
    assert out["soft_goal"] == ""
    assert out["pending_anchors"] == []


def test_never_points_behind_player(monkeypatch):
    """玩家已在 ch28(progress_chapter=28):窗口锚定后只剩 ch28,不会倒回 ch1。"""
    _patch(monkeypatch)
    out = ST._fallback_soft_goal(123, wl_key="wl", steering_strength="rail", progress_chapter=28)
    assert "圣女蕾穆丽娜登场" in out["soft_goal"]
    assert out["pending_anchors"] == ["a_ch28"]
