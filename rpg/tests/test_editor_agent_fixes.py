"""编辑器 agent(console_assistant)审计修复回归 · 批次A(2 个 P1)。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_game_console_in_nav_whitelist():
    """P1:activate_save 后「进入游戏」的跨 SPA 目标必须在白名单里(否则导航被静默丢)。
    前端 console-assistant-navigation.jsx MAP + tool 枚举 + prompt 早就用它。"""
    from console_assistant.llm_loop import _NAV_TARGETS_WHITELIST
    assert "game_console" in _NAV_TARGETS_WHITELIST


def test_pg_persist_runs_even_when_redis_disabled(monkeypatch):
    """P1:Redis 未配置/不可达(本地/桌面)时,PG 永久落库仍必须执行,否则对话重启即丢。"""
    import redis_bus
    import console_assistant.conversations as conv
    calls = []
    monkeypatch.setattr(redis_bus, "is_enabled", lambda: False)
    monkeypatch.setattr(conv, "_persist_conv_pg", lambda u, c, d: calls.append((u, c)))
    conv.persist_conversation(1, "cid_test", {"messages": [{"role": "user", "content": "hi"}]})
    assert calls == [(1, "cid_test")], "Redis 关掉时 _persist_conv_pg 未被调用 → 对话丢失"


def test_pg_persist_runs_when_redis_client_none(monkeypatch):
    """Redis 开着但取不到 client 时,PG 也必须写(不能因 Redis 抖动丢历史)。"""
    import redis_bus
    import console_assistant.conversations as conv
    calls = []
    monkeypatch.setattr(redis_bus, "is_enabled", lambda: True)
    monkeypatch.setattr(redis_bus, "get_sync_client", lambda: None)
    monkeypatch.setattr(conv, "_persist_conv_pg", lambda u, c, d: calls.append((u, c)))
    conv.persist_conversation(2, "cid_test2", {"messages": []})
    assert calls == [(2, "cid_test2")]
