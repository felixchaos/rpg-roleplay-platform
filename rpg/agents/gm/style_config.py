"""agents.gm.style_config — GM 叙事倾向旋钮的「存储层」(Phase 2)。

style_harness.py 是纯函数(schema + 渲染 + resolve),不碰 DB。本模块负责从三处现有
存储读出各层 gm_style 覆盖,喂给 style_harness.resolve_profile:

  · 用户级默认: user_preferences.preferences.gm_style   (jsonb,表已存在)
  · 剧本级覆盖: script_overrides.data.gm_style          (jsonb,表已存在)
  · 存档级覆盖: state.data['player_private']['gm_style'] (存档内 JSON)

任一层缺失 / 读失败 → 该层 None → resolve 取默认。三处都没配 → 完整默认 profile,
渲染与 Phase 1 默认逐字一致(零回归)。所有 DB 读包 try/except,绝不让取风格的过程
影响 GM 主流程。
"""
from __future__ import annotations

from typing import Any

from agents.gm.style_harness import resolve_profile


def _read_user_gm_style(user_id: int | None) -> dict | None:
    if not user_id:
        return None
    try:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(user_id),),
            ).fetchone()
        if not row:
            return None
        prefs = row["preferences"] if hasattr(row, "__getitem__") else row[0]
        if isinstance(prefs, dict):
            gs = prefs.get("gm_style")
            return gs if isinstance(gs, dict) else None
    except Exception:
        pass
    return None


def _read_script_gm_style(script_id: int | None) -> dict | None:
    if not script_id:
        return None
    try:
        from platform_app.knowledge.script_overrides import get_overrides_by_script_id
        data = get_overrides_by_script_id(int(script_id)) or {}
        gs = data.get("gm_style")
        return gs if isinstance(gs, dict) else None
    except Exception:
        return None


def _read_save_gm_style(state: Any) -> dict | None:
    try:
        data = getattr(state, "data", None) or {}
        pp = data.get("player_private") or {}
        gs = pp.get("gm_style")
        return gs if isinstance(gs, dict) else None
    except Exception:
        return None


def resolve_for_state(user_id: int | None, script_id: int | None, state: Any) -> dict[str, int]:
    """读三层覆盖并归并出完整 6 维 profile。任意层缺失/失败 → 取默认,零回归。"""
    return resolve_profile(
        user_default=_read_user_gm_style(user_id),
        script_override=_read_script_gm_style(script_id),
        save_override=_read_save_gm_style(state),
    )
