"""context_engine.loaders — 角色卡 / 世界书加载函数."""
from __future__ import annotations

import json
from typing import Any
from context_engine._constants import CHAR_IDX, WORLD_IDX


def _safe_load_chars(script_id, book_id, manifest) -> dict[str, Any]:
    """state_schema 层需要 chars dict 来列出已知 NPC enum。
    模组场景没有小说角色卡 → 返回空 dict，不再误读 .webnovel/indexes。"""
    if not manifest:
        return _load_characters(script_id=script_id, book_id=book_id)
    if manifest.get("kind") == "novel_adaptation":
        return _load_characters(script_id=script_id, book_id=book_id)
    return {}


def _load_characters(script_id: int | None = None, book_id: int | None = None) -> dict[str, Any]:
    """task 80: 通用底座 — 优先从 DB character_cards 取。
    传了 script_id/book_id 表示指定剧本: DB 空就返 {} (不要回退 JSON,
    那是单一书的固化数据,会污染其它剧本)。
    完全没传 (legacy 兼容): 才允许 JSON 回退。
    """
    scoped = bool(script_id or book_id)
    if scoped:
        try:
            return _load_characters_db(script_id=script_id, book_id=book_id) or {}
        except Exception:
            return {}
    try:
        with open(CHAR_IDX, "r", encoding="utf-8") as f:
            return json.load(f).get("characters", {})
    except Exception:
        return {}


def _load_characters_db(script_id: int | None, book_id: int | None) -> dict[str, Any]:
    """从 character_cards 表读取该 script/book 启用的角色卡，转成 JSON 风格 dict。"""
    from platform_app.db import connect
    where_clauses = ["enabled = true"]
    params: list[Any] = []
    if script_id:
        where_clauses.append("script_id = %s")
        params.append(int(script_id))
    elif book_id:
        where_clauses.append("book_id = %s")
        params.append(int(book_id))
    sql = (
        "select name, aliases, identity, appearance, personality, speech_style, "
        "current_status, secrets, sample_dialogue, token_budget, priority "
        "from character_cards where " + " and ".join(where_clauses) +
        " order by priority desc, id asc"
    )
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        out[r["name"]] = {
            "aliases": r["aliases"] or [],
            "identity": r["identity"] or "",
            "appearance": r["appearance"] or "",
            "personality": r["personality"] or "",
            "speech_style": r["speech_style"] or "",
            "current_status": r["current_status"] or "",
            "secrets": r["secrets"] or "",
            "sample_dialogue": r["sample_dialogue"] or [],
            "priority": int(r["priority"] or 100),
            "token_budget": int(r["token_budget"] or 450),
        }
    return out


def _load_worldbook_db(script_id: int | None, book_id: int | None) -> list[dict[str, Any]]:
    """从 worldbook_entries 取启用条目；返回 _worldbook_entries 风格的 list。"""
    from platform_app.db import connect
    where_clauses = ["enabled = true"]
    params: list[Any] = []
    if script_id:
        where_clauses.append("script_id = %s")
        params.append(int(script_id))
    elif book_id:
        where_clauses.append("book_id = %s")
        params.append(int(book_id))
    sql = (
        "select id, title, content, keys, regex_keys, priority, token_budget "
        "from worldbook_entries where " + " and ".join(where_clauses) +
        " order by priority desc, id asc"
    )
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": f"db_{r['id']}",
            "title": r["title"] or "",
            "keys": r["keys"] or [],
            "regex": r["regex_keys"] or [],
            "priority": int(r["priority"] or 50),
            "text": r["content"] or "",
            "token_budget": int(r["token_budget"] or 250),
        })
    return out


def _load_world(script_id: int | None = None) -> dict[str, Any]:
    """task 80: 传 script_id → 从 worldbook_entries 取该剧本设定; 不传 → 老的 JSON 兼容。"""
    if script_id:
        try:
            from platform_app.db import connect as _connect
            with _connect() as db:
                rows = db.execute(
                    "select title, content from worldbook_entries "
                    "where script_id=%s and enabled=true order by priority desc, id asc",
                    (int(script_id),),
                ).fetchall()
            if rows:
                return {"entries": [{"title": r["title"], "content": r["content"]} for r in rows]}
        except Exception:
            pass
        return {}
    try:
        with open(WORLD_IDX, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
