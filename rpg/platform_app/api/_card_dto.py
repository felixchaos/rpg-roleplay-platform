"""api/_card_dto.py — 统一角色卡 DTO 序列化器 (v28 三表合一后)。

把 character_cards 表行(card_type ∈ npc/pc/persona)序列化成单一 schema 的对象,
前端用同一组件就能渲染 NPC、玩家 PC、persona 三态。

字段 schema:
{
  id, public_id, card_type, source, user_id, script_id, book_id, slug,

  # 身份
  name, full_name, aliases[],
  identity, background,

  # 描述
  appearance, personality, speech_style, current_status,
  secrets,                # PC 卡的 secrets 仍由 workspace 剥到 player_private,不进 GM prompt
  sample_dialogue[], avatar_path,

  # 检索/章节闸
  first_revealed_chapter, importance, scope,
  token_budget, priority, enabled,

  # persona 专用
  is_default,

  tags[], metadata{}, row_version,
  created_at, updated_at
}

兼容字段(老前端):
  - persona 行额外暴露 role = identity(原 user_personas.role 列已合并到 identity)
"""
from __future__ import annotations

from typing import Any


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def card_to_dto(row: Any, *, persona_role_alias: bool = True) -> dict[str, Any]:
    """character_cards 行 → 统一 DTO。row 可以是 dict 或 RowProxy/RealDictRow。

    persona_role_alias: persona 卡额外暴露 role 字段(= identity),给历史前端兼容用。
    """
    if not row:
        return {}
    d = dict(row)
    card_type = d.get("card_type") or "npc"
    out: dict[str, Any] = {
        "id": d.get("id"),
        "public_id": str(d["public_id"]) if d.get("public_id") is not None else None,
        "uid": str(d["public_id"]) if d.get("public_id") is not None else None,  # 兼容 expose() 风格

        "card_type": card_type,
        "source": d.get("source") or "extracted",

        "user_id": d.get("user_id"),
        "script_id": d.get("script_id"),
        "book_id": d.get("book_id"),
        "slug": d.get("slug") or "",

        # 身份段
        "name": d.get("name") or "",
        "full_name": d.get("full_name") or "",
        "aliases": d.get("aliases") or [],
        "identity": d.get("identity") or "",
        "background": d.get("background") or "",

        # 描述段
        "appearance": d.get("appearance") or "",
        "personality": d.get("personality") or "",
        "speech_style": d.get("speech_style") or "",
        "current_status": d.get("current_status") or "",
        "secrets": d.get("secrets") or "",
        "sample_dialogue": d.get("sample_dialogue") or [],
        "avatar_path": d.get("avatar_path") or "",

        # 检索/章节闸
        "first_revealed_chapter": int(d.get("first_revealed_chapter") or 0),
        "importance": int(d.get("importance") or 0),
        "scope": d.get("scope") or "script",
        "token_budget": int(d.get("token_budget") or 450),
        "priority": int(d.get("priority") or 100),
        "enabled": bool(d.get("enabled", True)),

        # persona 专用
        "is_default": bool(d.get("is_default", False)),

        # 其他
        "tags": d.get("tags") or [],
        "metadata": d.get("metadata") or {},
        "row_version": int(d.get("row_version") or 1),
        "created_at": _iso(d.get("created_at")),
        "updated_at": _iso(d.get("updated_at")),
    }
    # 老前端兼容:persona 行的 role 字段
    if persona_role_alias and card_type == "persona":
        out["role"] = out["identity"]
    return out


def cards_to_dto_list(rows: list[Any], *, persona_role_alias: bool = True) -> list[dict[str, Any]]:
    return [card_to_dto(r, persona_role_alias=persona_role_alias) for r in rows]


def card_page_payload(rows: list[Any], limit: int, *,
                      persona_role_alias: bool = True) -> dict[str, Any]:
    """跟 db.utils.page_payload 同形(items + page),但 items 走 DTO 而不是裸 expose。"""
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = str(visible[-1]["id"]) if has_more and visible else None
    return {
        "items": cards_to_dto_list(visible, persona_role_alias=persona_role_alias),
        "page": {
            "limit": limit,
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
    }
