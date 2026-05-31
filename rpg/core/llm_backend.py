"""core.llm_backend — LLM backend resolver helpers (共享给 agents/)。

抽出来的目的: command_agent / extractor / 其他 agent 不再各自实现。

用法示例:
    from core.llm_backend import (
        resolve_preferred_model as _resolve_preferred_model,
        resolve_preferred_api  as _resolve_preferred_api,
        detect_default_api     as _detect_default_api,
    )
"""
from __future__ import annotations

from typing import Optional


def detect_default_api() -> str:
    """启动时检测可用 backend: 优先 vertex_ai (SA 文件), 然后 anthropic (env key)."""
    import os as _os
    from pathlib import Path as _Path

    sa_path = _Path(__file__).parent.parent / "vertex_sa.json"
    if sa_path.exists():
        return "vertex_ai"
    if _os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "vertex_ai"  # 默认仍兜底 vertex,失败时调用方走 fallback


def resolve_preferred_model(
    user_id: Optional[int],
    pref_key: str = "set_parser.model_real_name",
) -> Optional[str]:
    """从用户偏好推断该用户应该用的 model。

    Args:
        user_id:  用户 ID，None 时直接返回 None。
        pref_key: user_preferences.preferences 字典里的键名，
                  不同 agent 使用不同命名空间，如:
                  - command_agent: "set_parser.model_real_name"
                  - extractor:     "extractor.model_real_name"

    内部使用 request-scoped cache（core.request_cache），一个请求内
    相同 user_id 只查一次 DB；非请求上下文每次直接查。
    """
    if not user_id:
        return None
    try:
        from core.request_cache import get_user_prefs_cached

        prefs = get_user_prefs_cached(int(user_id))
        return prefs.get(pref_key) or None
    except Exception:
        return None


def resolve_preferred_api(
    user_id: Optional[int],
    pref_key: str = "set_parser.api_id",
) -> Optional[str]:
    """从用户偏好推断该用户应该用的 API provider。

    Args:
        user_id:  用户 ID，None 时直接返回 None。
        pref_key: user_preferences.preferences 字典里的键名，
                  不同 agent 使用不同命名空间，如:
                  - command_agent: "set_parser.api_id"
                  - extractor:     "extractor.api_id"

    内部使用 request-scoped cache，同一请求内 user_id 相同时复用
    preferences dict，不重复 SELECT。
    """
    if not user_id:
        return None
    try:
        from core.request_cache import get_user_prefs_cached

        prefs = get_user_prefs_cached(int(user_id))
        return prefs.get(pref_key) or None
    except Exception:
        return None


__all__ = [
    "detect_default_api",
    "resolve_preferred_model",
    "resolve_preferred_api",
]
