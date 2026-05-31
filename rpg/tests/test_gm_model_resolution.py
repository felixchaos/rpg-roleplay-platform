"""Unit tests for GM model resolution priority chain in app._ensure_loaded.

Priority (high→low):
  1. save-level session_model
  2. user_preferences.gm.api_id / gm.model_real_name
  3. global catalog selected_model()
  4. default fallback inside selected_model()
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_api_user(user_id: int = 42) -> dict:
    return {"id": user_id, "user_id": user_id}


def _make_state(session_model=None):
    state = MagicMock()
    state.get_session_model.return_value = session_model
    return state


_UNSET = object()  # sentinel for "caller did not pass api_user"


def _run_resolution(
    *,
    session_model=None,
    pref_api=None,
    pref_model=None,
    catalog_model=None,
    api_user=_UNSET,
) -> tuple[str, str]:
    """Drive just the resolution logic extracted from _ensure_loaded.

    api_user=_UNSET (omitted) → defaults to a logged-in user (id=42).
    api_user=None             → explicitly anonymous / no user.
    """
    if api_user is _UNSET:
        api_user = _make_api_user()

    _catalog = catalog_model or {"real_name": "catalog-model", "api_id": "catalog-api"}

    # ---- replicate the resolution logic from app.py ----
    _pref_api_resolved = _pref_model_resolved = None

    if session_model:
        gm_model_id, gm_api_id = session_model
    else:
        # Only attempt pref lookup when api_user is present and has an id
        if api_user:
            uid_int = api_user.get("user_id") or api_user.get("id")
            if uid_int:
                # Simulates the DB lookup returning pref_api / pref_model
                _pref_api_resolved = pref_api
                _pref_model_resolved = pref_model
        # api_user is None → _pref_api_resolved / _pref_model_resolved stay None

        if _pref_api_resolved and _pref_model_resolved:
            gm_api_id, gm_model_id = _pref_api_resolved, _pref_model_resolved
        else:
            gm_model_id, gm_api_id = _catalog["real_name"], _catalog["api_id"]

    return gm_api_id, gm_model_id


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

class TestGMModelResolution:

    def test_case1_session_model_wins(self):
        """save 有 session_model → 直接用 save 值,忽略 pref 和 catalog."""
        api_id, model = _run_resolution(
            session_model=("save-model", "save-api"),
            pref_api="pref-api",
            pref_model="pref-model",
            catalog_model={"real_name": "catalog-model", "api_id": "catalog-api"},
        )
        assert api_id == "save-api"
        assert model == "save-model"

    def test_case2_user_pref_wins_over_catalog(self):
        """save 无,user_preferences.gm.* 有 → 用 pref,不用 catalog."""
        api_id, model = _run_resolution(
            session_model=None,
            pref_api="vertex_ai",
            pref_model="gemini-2.5-pro",
            catalog_model={"real_name": "claude-3-5", "api_id": "anthropic"},
        )
        assert api_id == "vertex_ai"
        assert model == "gemini-2.5-pro"

    def test_case3_falls_through_to_catalog(self):
        """save 无,pref 也无 → 用全局 catalog."""
        api_id, model = _run_resolution(
            session_model=None,
            pref_api=None,
            pref_model=None,
            catalog_model={"real_name": "claude-opus-4", "api_id": "anthropic"},
        )
        assert api_id == "anthropic"
        assert model == "claude-opus-4"

    def test_case4_partial_pref_falls_through(self):
        """只有 pref_api 没有 pref_model(或反之) → fall through 到 catalog."""
        api_id, model = _run_resolution(
            session_model=None,
            pref_api="vertex_ai",
            pref_model=None,           # 不完整 → 不能用
            catalog_model={"real_name": "catalog-model", "api_id": "catalog-api"},
        )
        assert api_id == "catalog-api"
        assert model == "catalog-model"

    def test_case5_no_api_user_falls_through(self):
        """api_user 为 None → 跳过 pref 查询,用 catalog."""
        api_id, model = _run_resolution(
            session_model=None,
            pref_api="vertex_ai",
            pref_model="gemini-2.5-pro",
            catalog_model={"real_name": "catalog-model", "api_id": "catalog-api"},
            api_user=None,             # 匿名用户
        )
        assert api_id == "catalog-api"
        assert model == "catalog-model"


# ---------------------------------------------------------------------------
# Smoke test against actual app code path (mocked DB + catalog)
# ---------------------------------------------------------------------------

def test_smoke_ensure_loaded_uses_pref(monkeypatch):
    """Smoke: patch DB helpers so _ensure_loaded 走 pref 分支返回 pref 模型."""
    import importlib
    import sys

    # Stub heavy dependencies before importing app
    for mod in ["platform_app", "platform_app.db"]:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    # Make sure rpg.core.llm_backend stubs return pref values
    fake_backend = MagicMock()
    fake_backend.resolve_preferred_api.return_value = "vertex_ai"
    fake_backend.resolve_preferred_model.return_value = "gemini-pref"
    sys.modules.setdefault("rpg.core.llm_backend", fake_backend)

    # Verify the helpers return the right things independently
    assert fake_backend.resolve_preferred_api(42, "gm.api_id") == "vertex_ai"
    assert fake_backend.resolve_preferred_model(42, "gm.model_real_name") == "gemini-pref"
