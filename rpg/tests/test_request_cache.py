"""tests.test_request_cache — unit tests for core.request_cache

覆盖三个场景:
1. 请求内多次调用相同 user_id → DB 只查一次
2. 跨请求 reset_request_caches() → 重新查 DB
3. 非请求上下文(ContextVar == None) → 每次都查 DB
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch


# ── 在 import core.request_cache 之前先 stub 掉 platform_app.db ──────────────
# request_cache 在 _select_all_prefs 内部 lazy import platform_app.db,
# 用 MagicMock stub 避免真实 DB 连接。

def _make_prefs_stub(user_prefs: dict) -> MagicMock:
    """构造返回固定 preferences dict 的 DB stub。"""
    row = {"preferences": user_prefs}
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    db_ctx = MagicMock()
    db_ctx.__enter__ = MagicMock(return_value=cursor)
    db_ctx.__exit__ = MagicMock(return_value=False)
    connect = MagicMock(return_value=db_ctx)
    # cursor.execute 返回 cursor 自身(链式)
    db_ctx.execute = MagicMock(return_value=cursor)
    return connect


# ─────────────────────────────────────────────────────────────────────────────


def _fresh_module():
    """每次返回一个新加载(未缓存)的 core.request_cache 模块实例。"""
    import importlib

    # 先清掉 sys.modules 里旧版本,避免 ContextVar 状态污染
    for key in list(sys.modules):
        if "request_cache" in key:
            del sys.modules[key]
    import core.request_cache as m  # noqa: F401
    importlib.reload(m)
    return m


class TestRequestScopedPrefs:
    """user_preferences 缓存行为。"""

    def test_same_request_single_select(self):
        """同一请求内多次 get_user_prefs_cached(uid) 只执行 1 次 SELECT。"""
        import core.request_cache as m

        select_count = 0

        def fake_select(uid):
            nonlocal select_count
            select_count += 1
            return {"gm.api_id": "vertex_ai", "gm.model_real_name": "gemini-pro"}

        m.reset_request_caches()

        with patch.object(m, "_select_all_prefs", side_effect=fake_select):
            r1 = m.get_user_prefs_cached(42)
            r2 = m.get_user_prefs_cached(42)
            r3 = m.get_user_prefs_cached(42)

        assert select_count == 1, f"期望 1 次 SELECT,实际 {select_count} 次"
        assert r1 == r2 == r3
        assert r1["gm.api_id"] == "vertex_ai"

    def test_different_users_separate_selects(self):
        """同一请求内不同 user_id 各自 SELECT 一次。"""
        import core.request_cache as m

        calls: list[int] = []

        def fake_select(uid):
            calls.append(uid)
            return {"uid": uid}

        m.reset_request_caches()

        with patch.object(m, "_select_all_prefs", side_effect=fake_select):
            m.get_user_prefs_cached(1)
            m.get_user_prefs_cached(2)
            m.get_user_prefs_cached(1)  # 命中 cache

        assert calls == [1, 2], f"期望 [1, 2],实际 {calls}"

    def test_cross_request_reselect(self):
        """reset_request_caches() 后下一次请求重新查 DB。"""
        import core.request_cache as m

        call_count = 0

        def fake_select(uid):
            nonlocal call_count
            call_count += 1
            return {}

        # 请求 1
        m.reset_request_caches()
        with patch.object(m, "_select_all_prefs", side_effect=fake_select):
            m.get_user_prefs_cached(99)
        assert call_count == 1

        # 请求 2
        m.reset_request_caches()
        with patch.object(m, "_select_all_prefs", side_effect=fake_select):
            m.get_user_prefs_cached(99)
        assert call_count == 2, "reset 后应重新查 DB"

    def test_no_request_context_always_selects(self):
        """非请求上下文(ContextVar == None)每次都直接查 DB。"""
        import core.request_cache as m

        # 强制回到 None 状态(模拟 cron / test 直接调用)
        m._user_prefs_cache.set(None)

        call_count = 0

        def fake_select(uid):
            nonlocal call_count
            call_count += 1
            return {}

        with patch.object(m, "_select_all_prefs", side_effect=fake_select):
            m.get_user_prefs_cached(7)
            m.get_user_prefs_cached(7)
            m.get_user_prefs_cached(7)

        assert call_count == 3, f"非请求上下文应每次查 DB,实际 {call_count} 次"


class TestRequestScopedApiCreds:
    """user_api_credentials 缓存行为。"""

    def test_same_key_single_fetch(self):
        """同一请求内 (user_id, api_id) 相同只取一次凭据。"""
        import core.request_cache as m

        fetch_count = 0

        def fake_fetch(uid, api_id):
            nonlocal fetch_count
            fetch_count += 1
            return {"api_id": api_id, "key": "sk-test", "base_url_override": ""}

        m.reset_request_caches()

        with patch.object(m, "_fetch_cred", side_effect=fake_fetch):
            c1 = m.get_api_cred_cached(1, "anthropic")
            c2 = m.get_api_cred_cached(1, "anthropic")

        assert fetch_count == 1
        assert c1 == c2
        assert c1["key"] == "sk-test"

    def test_none_result_cached(self):
        """凭据不存在(None)也缓存,避免重复无效查。"""
        import core.request_cache as m

        fetch_count = 0

        def fake_fetch(uid, api_id):
            nonlocal fetch_count
            fetch_count += 1
            return None

        m.reset_request_caches()

        with patch.object(m, "_fetch_cred", side_effect=fake_fetch):
            m.get_api_cred_cached(5, "openai")
            m.get_api_cred_cached(5, "openai")

        assert fetch_count == 1, "None 结果也应缓存"

    def test_cross_request_cred_reselect(self):
        """两次请求之间 reset,凭据重新获取。"""
        import core.request_cache as m

        call_count = 0

        def fake_fetch(uid, api_id):
            nonlocal call_count
            call_count += 1
            return {"api_id": api_id, "key": "key", "base_url_override": ""}

        m.reset_request_caches()
        with patch.object(m, "_fetch_cred", side_effect=fake_fetch):
            m.get_api_cred_cached(3, "vertex_ai")
        assert call_count == 1

        m.reset_request_caches()
        with patch.object(m, "_fetch_cred", side_effect=fake_fetch):
            m.get_api_cred_cached(3, "vertex_ai")
        assert call_count == 2


class TestLlmBackendIntegration:
    """llm_backend.resolve_preferred_* 是否正确走缓存。"""

    def test_resolve_preferred_model_cached(self):
        """两次 resolve_preferred_model 同 uid → _select_all_prefs 只调一次。"""
        import core.request_cache as rc
        import core.llm_backend as lb

        rc.reset_request_caches()

        prefs = {"set_parser.model_real_name": "gemini-pro", "set_parser.api_id": "vertex_ai"}
        select_count = 0

        def fake_select(uid):
            nonlocal select_count
            select_count += 1
            return prefs

        with patch.object(rc, "_select_all_prefs", side_effect=fake_select):
            m1 = lb.resolve_preferred_model(42, "set_parser.model_real_name")
            m2 = lb.resolve_preferred_model(42, "set_parser.model_real_name")
            a1 = lb.resolve_preferred_api(42, "set_parser.api_id")

        assert select_count == 1, f"缓存未命中,查了 {select_count} 次"
        assert m1 == "gemini-pro"
        assert m2 == "gemini-pro"
        assert a1 == "vertex_ai"

    def test_resolve_none_user_id(self):
        """user_id=None 或 0 直接返回 None,不查 DB。"""
        import core.request_cache as rc
        import core.llm_backend as lb

        rc.reset_request_caches()
        select_count = 0

        def fake_select(uid):
            nonlocal select_count
            select_count += 1
            return {}

        with patch.object(rc, "_select_all_prefs", side_effect=fake_select):
            assert lb.resolve_preferred_model(None) is None
            assert lb.resolve_preferred_api(0) is None

        assert select_count == 0
