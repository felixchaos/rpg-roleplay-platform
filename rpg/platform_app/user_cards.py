"""
user_cards.py — 用户级 persona / character card CRUD

两个独立资源：
- user_personas        玩家自己的多个身份，可在任何剧本/存档里选
- user_character_cards 用户自创的 NPC 卡，可挂到任何剧本/检索时与剧本卡合并

所有接口严格按 user_id 隔离。
"""
from __future__ import annotations

import re
from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, init_db, expose


_SLUG_RE = re.compile(r"[^0-9A-Za-z_一-鿿]+")


def _slugify(text: str) -> str:
    cleaned = _SLUG_RE.sub("-", (text or "").strip()).strip("-")
    return cleaned[:80] or "untitled"


def _normalize_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    if isinstance(value, str):
        # 允许逗号/分号/中文顿号分隔
        return [p.strip() for p in re.split(r"[,，;；、]", value) if p.strip()]
    return [value]


# ══════════════════════════════════════════════════════════════════════
#  USER PERSONAS（玩家身份卡）
# ══════════════════════════════════════════════════════════════════════
def list_personas(user_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "select * from user_personas where user_id = %s order by is_default desc, updated_at desc, id desc",
            (user_id,),
        ).fetchall()
    return {"ok": True, "items": [expose(r) for r in rows], "total": len(rows)}


def get_persona(user_id: int, persona_id: int) -> dict[str, Any] | None:
    init_db()
    with connect() as db:
        row = db.execute(
            "select * from user_personas where id = %s and user_id = %s",
            (persona_id, user_id),
        ).fetchone()
    return expose(row) if row else None


def upsert_persona(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """创建或更新 persona。payload 至少要有 name；其他字段可选。
    可传 id 强制更新某条；否则按 slug 决定 insert/update。
    """
    init_db()
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("persona.name 不能为空")
    persona_id = payload.get("id")
    slug = (payload.get("slug") or "").strip() or _slugify(name)
    is_default = bool(payload.get("is_default"))

    fields = {
        "name": name,
        "role": (payload.get("role") or "").strip(),
        "background": (payload.get("background") or "").strip(),
        "appearance": (payload.get("appearance") or "").strip(),
        "personality": (payload.get("personality") or "").strip(),
        "avatar_path": (payload.get("avatar_path") or "").strip(),
        "tags": Jsonb(_normalize_list(payload.get("tags"))),
        "metadata": Jsonb(payload.get("metadata") or {}),
        "is_default": is_default,
    }

    with connect() as db:
        if persona_id:
            # 确保归属再 update
            owned = db.execute(
                "select 1 from user_personas where id = %s and user_id = %s",
                (int(persona_id), user_id),
            ).fetchone()
            if not owned:
                raise ValueError("persona 不存在或无权访问")
            db.execute(
                """
                update user_personas set
                  name = %(name)s, slug = %(slug)s, role = %(role)s,
                  background = %(background)s, appearance = %(appearance)s,
                  personality = %(personality)s, avatar_path = %(avatar_path)s,
                  tags = %(tags)s, metadata = %(metadata)s, is_default = %(is_default)s,
                  row_version = row_version + 1, updated_at = now()
                where id = %(id)s and user_id = %(user_id)s
                """,
                {**fields, "slug": slug, "id": int(persona_id), "user_id": user_id},
            )
            row = db.execute("select * from user_personas where id = %s", (int(persona_id),)).fetchone()
        else:
            # 按 (user_id, slug) upsert
            row = db.execute(
                """
                insert into user_personas(
                  user_id, slug, name, role, background, appearance,
                  personality, avatar_path, tags, metadata, is_default
                ) values (
                  %(user_id)s, %(slug)s, %(name)s, %(role)s, %(background)s, %(appearance)s,
                  %(personality)s, %(avatar_path)s, %(tags)s, %(metadata)s, %(is_default)s
                )
                on conflict(user_id, slug) do update set
                  name = excluded.name, role = excluded.role,
                  background = excluded.background, appearance = excluded.appearance,
                  personality = excluded.personality, avatar_path = excluded.avatar_path,
                  tags = excluded.tags, metadata = excluded.metadata,
                  is_default = excluded.is_default,
                  row_version = user_personas.row_version + 1, updated_at = now()
                returning *
                """,
                {**fields, "user_id": user_id, "slug": slug},
            ).fetchone()

        # 只允许一个默认 persona：其他全部清零
        if is_default and row:
            db.execute(
                "update user_personas set is_default = false where user_id = %s and id <> %s",
                (user_id, int(row["id"])),
            )
    return expose(row) or {}


def delete_persona(user_id: int, persona_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        cur = db.execute(
            "delete from user_personas where id = %s and user_id = %s returning id",
            (persona_id, user_id),
        ).fetchone()
    return {"ok": True, "deleted": bool(cur), "id": persona_id}


# ══════════════════════════════════════════════════════════════════════
#  USER CHARACTER CARDS（用户自创 NPC 卡）
# ══════════════════════════════════════════════════════════════════════
def list_user_cards(user_id: int, q: str | None = None, enabled_only: bool = False) -> dict[str, Any]:
    init_db()
    where = ["user_id = %s"]
    params: list[Any] = [user_id]
    if enabled_only:
        where.append("enabled = true")
    if q:
        where.append("(lower(name) like %s or lower(identity) like %s)")
        like = f"%{q.lower()}%"
        params.extend([like, like])
    with connect() as db:
        rows = db.execute(
            f"select * from user_character_cards where {' and '.join(where)} "
            "order by priority desc, updated_at desc, id desc",
            tuple(params),
        ).fetchall()
    return {"ok": True, "items": [expose(r) for r in rows], "total": len(rows)}


def get_user_card(user_id: int, card_id: int) -> dict[str, Any] | None:
    init_db()
    with connect() as db:
        row = db.execute(
            "select * from user_character_cards where id = %s and user_id = %s",
            (card_id, user_id),
        ).fetchone()
    return expose(row) if row else None


def upsert_user_card(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    init_db()
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("character.name 不能为空")
    card_id = payload.get("id")
    slug = (payload.get("slug") or "").strip() or _slugify(name)

    fields = {
        "name": name,
        "slug": slug,
        "aliases": Jsonb(_normalize_list(payload.get("aliases"))),
        "identity": (payload.get("identity") or "").strip(),
        "appearance": (payload.get("appearance") or "").strip(),
        "personality": (payload.get("personality") or "").strip(),
        "speech_style": (payload.get("speech_style") or "").strip(),
        "current_status": (payload.get("current_status") or "").strip(),
        "secrets": (payload.get("secrets") or "").strip(),
        "sample_dialogue": Jsonb(_normalize_list(payload.get("sample_dialogue"))),
        "tags": Jsonb(_normalize_list(payload.get("tags"))),
        "metadata": Jsonb(payload.get("metadata") or {}),
        "token_budget": int(payload.get("token_budget") or 450),
        "priority": int(payload.get("priority") or 100),
        "enabled": bool(payload.get("enabled", True)),
        "scope": str(payload.get("scope") or "private").strip(),
    }

    with connect() as db:
        if card_id:
            owned = db.execute(
                "select 1 from user_character_cards where id = %s and user_id = %s",
                (int(card_id), user_id),
            ).fetchone()
            if not owned:
                raise ValueError("card 不存在或无权访问")
            db.execute(
                """
                update user_character_cards set
                  name=%(name)s, slug=%(slug)s, aliases=%(aliases)s,
                  identity=%(identity)s, appearance=%(appearance)s,
                  personality=%(personality)s, speech_style=%(speech_style)s,
                  current_status=%(current_status)s, secrets=%(secrets)s,
                  sample_dialogue=%(sample_dialogue)s, tags=%(tags)s, metadata=%(metadata)s,
                  token_budget=%(token_budget)s, priority=%(priority)s,
                  enabled=%(enabled)s, scope=%(scope)s,
                  row_version = row_version + 1, updated_at = now()
                where id = %(id)s and user_id = %(user_id)s
                """,
                {**fields, "id": int(card_id), "user_id": user_id},
            )
            row = db.execute("select * from user_character_cards where id = %s", (int(card_id),)).fetchone()
        else:
            row = db.execute(
                """
                insert into user_character_cards(
                  user_id, slug, name, aliases, identity, appearance, personality,
                  speech_style, current_status, secrets, sample_dialogue,
                  tags, metadata, token_budget, priority, enabled, scope
                ) values (
                  %(user_id)s, %(slug)s, %(name)s, %(aliases)s, %(identity)s, %(appearance)s,
                  %(personality)s, %(speech_style)s, %(current_status)s, %(secrets)s,
                  %(sample_dialogue)s, %(tags)s, %(metadata)s, %(token_budget)s,
                  %(priority)s, %(enabled)s, %(scope)s
                )
                on conflict(user_id, slug) do update set
                  name=excluded.name, aliases=excluded.aliases,
                  identity=excluded.identity, appearance=excluded.appearance,
                  personality=excluded.personality, speech_style=excluded.speech_style,
                  current_status=excluded.current_status, secrets=excluded.secrets,
                  sample_dialogue=excluded.sample_dialogue,
                  tags=excluded.tags, metadata=excluded.metadata,
                  token_budget=excluded.token_budget, priority=excluded.priority,
                  enabled=excluded.enabled, scope=excluded.scope,
                  row_version = user_character_cards.row_version + 1, updated_at = now()
                returning *
                """,
                {**fields, "user_id": user_id},
            ).fetchone()
    return expose(row) or {}


def delete_user_card(user_id: int, card_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        cur = db.execute(
            "delete from user_character_cards where id = %s and user_id = %s returning id",
            (card_id, user_id),
        ).fetchone()
    return {"ok": True, "deleted": bool(cur), "id": card_id}


# ══════════════════════════════════════════════════════════════════════
#  检索辅助：合并 script-level + user-level
# ══════════════════════════════════════════════════════════════════════
def user_cards_for_retrieval(user_id: int, names: list[str]) -> list[dict[str, Any]]:
    """按角色名（含 aliases）匹配用户级 NPC 卡，给 context_engine 用。"""
    if not user_id or not names:
        return []
    init_db()
    name_lc = [n.lower() for n in names if n]
    with connect() as db:
        # 拉出当前用户所有 enabled 卡，本地过滤匹配（卡数量少，直接 in-memory）
        rows = db.execute(
            "select * from user_character_cards where user_id = %s and enabled = true",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        card = expose(r) or {}
        candidates = [card.get("name", "").lower()] + [str(a).lower() for a in (card.get("aliases") or [])]
        if any(n in candidates or any(n in c or c in n for c in candidates) for n in name_lc):
            out.append(card)
    return out
