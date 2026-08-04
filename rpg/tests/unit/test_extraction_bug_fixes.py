"""回归:拆库审计(2026-07-10)从 6 个抽出库回灌主仓的确定性 bug 修复。

每条对应一个已发布的独立库(tavern-card 家族),库内已锁同款回归。此文件锁主仓侧。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── #1 json_ops: 围栏内非 ops JSON 数组不得被静默吞掉(llm-scrub) ──────────
def test_fenced_non_ops_array_preserved():
    from state.json_ops import _extract_json_state_ops, strip_json_state_ops
    ops, text = _extract_json_state_ops("看这个列表：\n```json\n[1,2,3]\n```\n然后继续。")
    assert ops == []
    assert "[1,2,3]" in text  # 数组保留,不再随 ops 一起蒸发
    assert "[1,2,3]" in strip_json_state_ops("x\n```json\n[1,2,3]\n```\ny")


def test_fenced_ops_array_still_stripped():
    from state.json_ops import _extract_json_state_ops
    ops, text = _extract_json_state_ops('```json\n[{"op":"set","path":"x","value":1}]\n```')
    assert ops and ops[0]["op"] == "set"
    assert "```" not in text  # 真 ops 仍剥净


# ── #2 cliche: 功能词里的孤立「如」不得误判成明喻套路(zh-narrative-guard) ──
def test_cliche_function_word_ru_not_flagged():
    from agents.timeline_narrative_guard import detect_cliche_violations
    for t in ("如果投石入水就糟了", "例如投石这种手段", "假如投石击中他", "如今投石已是往事"):
        assert detect_cliche_violations(t) == [], t


def test_cliche_real_simile_still_flagged():
    from agents.timeline_narrative_guard import detect_cliche_violations
    assert detect_cliche_violations("像投石一样落进水里")       # 显式明喻标记
    assert detect_cliche_violations("他如投石入水般消失")       # 文言「如」+入水般


# ── #4 episodic: 摘录锚点不受 str 哈希随机化影响(gram-recall) ────────────
def test_excerpt_anchor_deterministic():
    from kb.episodic import _excerpt_around_match
    text = "零" * 300 + "菲奥娜" + "一" * 300 + "银怀表" + "零" * 300
    # 同长 gram(菲奥娜/银怀表 都 3 字)加字典序 tiebreak → 锚点固定,与迭代序无关
    got = {_excerpt_around_match(text, "菲奥娜和银怀表") for _ in range(20)}
    assert len(got) == 1


# ── #7 adaptive_split: 巨号跳章不得无界枚举 gap(zh-chapter-splitter) ──────
def test_gap_enumeration_bounded():
    from ingest.adaptive_split import _MAX_GAP_SPAN, fuse
    t0 = time.time()
    _fused, gaps = fuse(
        [{"title": "第1章", "content": "a"}, {"title": "第20000000章", "content": "b"}],
        "a\nb",
    )
    assert time.time() - t0 < 1.0        # 不再撑爆内存/挂死
    assert len(gaps) <= 1                 # 巨跳只记一条汇总
    assert gaps and gaps[0].get("truncated") and gaps[0]["gap_span"] > _MAX_GAP_SPAN


def test_small_gap_still_enumerated():
    from ingest.adaptive_split import fuse
    _fused, gaps = fuse([{"title": "第1章", "content": "a"}, {"title": "第4章", "content": "b"}], "a\nb")
    assert len(gaps) == 2                 # 正常跳号(2,3)仍逐条列出


# ── #6 SSRF: 版本无关地拦 6to4/NAT64 内嵌私有 IPv4(safe-outbound) ────────
def test_ssrf_embedded_private_v4_blocked():
    from platform_app.user_credentials import _ip_is_internal
    assert _ip_is_internal("2002:a00:1::")        # 6to4 包 10.0.0.1
    assert _ip_is_internal("64:ff9b::a00:1")      # NAT64 包 10.0.0.1
    assert _ip_is_internal("::ffff:10.0.0.1")     # IPv4-mapped
    assert _ip_is_internal("10.0.0.1")
    assert not _ip_is_internal("8.8.8.8")         # 公网仍放行


# ── #3 crypto: 篡改密文可观测(byok-vault);行为契约仍返 ""(不破调用方) ──
def test_decrypt_tamper_returns_empty_not_raise(caplog):
    import logging

    from utils.crypto import decrypt_api_key, encrypt_api_key
    blob = bytearray(encrypt_api_key("sk-secret-123", 42, "openai"))
    blob[-1] ^= 0xFF  # 翻末字节 → GCM 认证失败
    with caplog.at_level(logging.WARNING):
        out = decrypt_api_key(bytes(blob), 42, "openai")
    assert out == ""                                        # 契约不变
    assert any("GCM" in r.message for r in caplog.records)  # 但可观测
    assert "sk-secret" not in caplog.text                    # 不泄漏明文/密钥
