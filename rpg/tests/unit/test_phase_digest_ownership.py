"""test_phase_digest_ownership.py — phase digest 两条注入路的归属守卫(v1.82.0)。

背景:同一批 save_phase_digests 经两条互不知情的路进同一个请求 ——
  ① context_providers/runtime_phase_digests.py → 「已发生历史摘要(本存档)」层,
     最近 4 个 phase、每个封顶 450 字符,受层预算管辖。
  ② state.core.history_messages() → 拼成一条 user 消息顶在 messages[] 最前面,
     v1.82.0 之前**无条数上限、无字符上限**,且完全在层预算体系之外。

长局里两者的 phase 集合重叠(实测重叠 3 个),而 ② 那条路随存档变长无限膨胀。
本文件锁住归属划分不重叠、上限真的存在,以及两边用的是同一份常量。
"""
from __future__ import annotations

from state.phase_digest_policy import (
    DIGEST_PREFIX_MAX_CHARS,
    DIGEST_PREFIX_MAX_PHASES,
    PER_PHASE_BUDGET,
    RECENT_PHASE_WINDOW,
    layer_owned_phase_indexes,
    select_prefix_phases,
)


def _phases(n: int, *, all_closed: bool = True) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        out.append({
            "phase_index": i,
            "status": "closed" if (all_closed or i < n) else "open",
            "summary": f"第 {i} 阶段的摘要正文",
            "turn_start": (i - 1) * 10 + 1,
            "turn_end": i * 10,
        })
    return out


def test_the_two_paths_never_share_a_phase():
    """这是本模块存在的首要理由:同一个 phase 只能进一条路。"""
    for n in (1, 3, 4, 5, 8, 12, 30):
        ph = _phases(n, all_closed=False)
        owned = layer_owned_phase_indexes([p["phase_index"] for p in ph])
        prefix = select_prefix_phases(ph, layer_owned=owned, max_recent_turn=10**9)
        overlap = owned & {p["phase_index"] for p in prefix}
        assert not overlap, f"n={n} 两条路重叠了 phase {sorted(overlap)}"


def test_layer_owns_the_most_recent_window():
    ph = _phases(12, all_closed=False)
    owned = layer_owned_phase_indexes([p["phase_index"] for p in ph])
    assert owned == {9, 10, 11, 12}
    assert len(owned) == RECENT_PHASE_WINDOW


def test_prefix_is_capped_by_count():
    ph = _phases(40)
    owned = layer_owned_phase_indexes([p["phase_index"] for p in ph])
    prefix = select_prefix_phases(ph, layer_owned=owned, max_recent_turn=10**9)
    assert len(prefix) <= DIGEST_PREFIX_MAX_PHASES
    # 取的是「最近的」那几个,不是最早的 —— 更早的靠 episodic_recall 按相关性召回
    assert max(p["phase_index"] for p in prefix) == 40 - RECENT_PHASE_WINDOW


def test_prefix_is_time_ascending():
    ph = _phases(20)
    owned = layer_owned_phase_indexes([p["phase_index"] for p in ph])
    prefix = select_prefix_phases(ph, layer_owned=owned, max_recent_turn=10**9)
    idx = [p["phase_index"] for p in prefix]
    assert idx == sorted(idx), "前情提要必须时间正序"


def test_open_and_empty_phases_never_reach_the_prefix():
    ph = _phases(10, all_closed=False)
    ph[2]["summary"] = "   "          # 摘要还没生成
    owned = layer_owned_phase_indexes([p["phase_index"] for p in ph])
    prefix = select_prefix_phases(ph, layer_owned=owned, max_recent_turn=10**9)
    for p in prefix:
        assert p["status"] == "closed"
        assert (p["summary"] or "").strip()


def test_recent_window_respects_the_near_turn_cutoff():
    """近因窗口内的 phase 已由 messages[] 原文覆盖,不该再进前情提要。"""
    ph = _phases(12)
    owned = layer_owned_phase_indexes([p["phase_index"] for p in ph])
    prefix = select_prefix_phases(ph, layer_owned=owned, max_recent_turn=45)
    assert all(p["turn_end"] <= 45 for p in prefix)


def test_provider_and_policy_share_one_source():
    """runtime_phase_digests 必须用同一份常量,否则两条路又会各取各的。"""
    import context_providers.runtime_phase_digests as prov
    assert prov.MAX_PHASES is RECENT_PHASE_WINDOW or prov.MAX_PHASES == RECENT_PHASE_WINDOW
    assert prov.PER_PHASE_BUDGET == PER_PHASE_BUDGET


def test_caps_are_actually_bounded():
    """上限得是个真数,别哪天被改成 None/0 又变回无限。"""
    assert 0 < DIGEST_PREFIX_MAX_PHASES <= 20
    assert 0 < DIGEST_PREFIX_MAX_CHARS <= 20000
    assert 0 < PER_PHASE_BUDGET <= 2000
