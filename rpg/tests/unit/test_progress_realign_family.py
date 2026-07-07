"""矩阵审计 M1-M4 收口(P0):软回退路径的进度信号族对齐。
病根=回退只退 state_snapshot/单信号,下一回合 retrieval self-heal 用 stale occurred
锚点把 progress 顶回=回退被静默撤销、防剧透闸按回退前高进度泄漏。源码结构断言。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = (ROOT / "gm_serving" / "settings.py").read_text(encoding="utf-8")
DELETION = (ROOT / "platform_app" / "branches" / "deletion.py").read_text(encoding="utf-8")
SAVES = (ROOT / "platform_app" / "api" / "saves.py").read_text(encoding="utf-8")


def _func(src, name):
    i = src.find(f"def {name}(")
    assert i != -1, name
    j = src.find("\ndef ", i + 1)
    return src[i:j if j != -1 else len(src)]


def test_realign_covers_full_signal_family():
    body = _func(SETTINGS, "realign_progress_signals")
    assert "'{progress_chapter}', to_jsonb(%s::int)" in body, "A:progress 显式设(可降)"
    assert "least(coalesce((worldline->>'user_progress_floor')" in body, "A:floor clamp 到 ≤target(M3)"
    assert "status='pending'" in body and "source_chapter > %s" in body, "C:未来章锚点重锁"
    assert "recompute_visible_set" in body, "frontier 收缩(flag on)"


def test_all_three_deletion_paths_realign():
    for fn in ("delete_subtree", "rollback_to_message", "rewind_last_round"):
        assert "_realign_after_state_rewind" in _func(DELETION, fn), f"{fn} 必须对齐进度信号族(M1/M2)"
    helper = _func(DELETION, "_realign_after_state_rewind")
    assert "realign_progress_signals" in helper
    assert "return" in helper.split("realign_progress_signals")[0], "无时间线信号时不猜(宁漏勿误)"


def test_rewind_endpoint_uses_shared_realign_and_writes_through_state():
    i = SAVES.find("def api_save_progress_rewind")
    body = SAVES[i:i + 4500]
    assert "realign_progress_signals" in body, "端点必须走统一对齐(M3)"
    assert '"chapter_min"' in body.replace("'", '"') and "runtime_checkouts" in body, "写穿工作树时间线(M4)"
    assert "_state_snapshot_hash" in body, "重算 snapshot_hash(跨 worker 失效)"
