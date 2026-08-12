"""RP harness 基准 — 真实 case 提取。

case = 一条真实"上下文→GM 回复"。从存档【活跃 commit 的 state_snapshot blob】读历史
(分支正确,与 materialize 同源),配对 user→assistant 成回合,带上该剧本的 canon 角色别名。
只读 DB。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def _load_canon_aliases(db, script_id: int | None) -> dict[str, list[str]]:
    if not script_id:
        return {}
    rows = db.execute(
        "select name, aliases from kb_canon_entities where script_id=%s and type='character'",
        (script_id,),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        nm = (r.get("name") or "").strip()
        if nm and len(nm) >= 2:
            out[nm] = [nm] + [a for a in (r.get("aliases") or []) if isinstance(a, str) and len(a) >= 2]
    return out


def load_save_cases(db, save_id: int) -> list[dict[str, Any]]:
    """提一个存档的所有回合 case。无活跃 commit / 无历史 → 空。"""
    srow = db.execute(
        "select script_id, active_commit_id from game_saves where id=%s", (save_id,)
    ).fetchone()
    if not srow:
        return []
    script_id = srow.get("script_id")
    commit_id = srow.get("active_commit_id")
    if not commit_id:
        return []
    crow = db.execute(
        "select state_snapshot from branch_commits where id=%s and save_id=%s",
        (commit_id, save_id),
    ).fetchone()
    snap = crow.get("state_snapshot") if isinstance(crow, dict) else None
    if isinstance(snap, str):
        snap = json.loads(snap)
    history = (snap or {}).get("history") or []
    canon = _load_canon_aliases(db, script_id)

    cases: list[dict[str, Any]] = []
    prior_full: list[dict[str, str]] = []     # 完整前文(user+assistant 交替),供 replay 重建
    prior_assistant: list[str] = []           # 仅 assistant,供 prior_echo 等指标
    pending_user = ""
    turn = 0
    for h in history:
        if not isinstance(h, dict):
            continue
        role, content = h.get("role"), (h.get("content") or "")
        if role == "user":
            pending_user = content
            prior_full.append({"role": "user", "content": content})
        elif role == "assistant":
            if not content.strip():
                continue
            cases.append({
                "save_id": save_id, "script_id": script_id, "turn_idx": turn,
                "player_input": pending_user, "gm_response": content,
                "prior": list(prior_full[-8:]),          # 末 8 条,控提示长度
                "prior_assistant": list(prior_assistant),
                "canon_aliases": canon,
            })
            prior_full.append({"role": "assistant", "content": content})
            prior_assistant.append(content)
            pending_user = ""
            turn += 1
    return cases


def iter_cases(db, save_ids: list[int]) -> Iterator[dict[str, Any]]:
    for sid in save_ids:
        try:
            yield from load_save_cases(db, sid)
        except Exception:
            continue


def select_save_ids(db, min_turns: int = 0, limit: int | None = None,
                    only_kb_native: bool = False) -> list[int]:
    """按回合数挑存档(基准默认挑有实质交互的)。"""
    rows = db.execute(
        """
        with t as (select save_id, count(*) filter (where role='assistant') as turns
                   from messages group by save_id)
        select gs.id, coalesce(t.turns,0) as turns
        from game_saves gs left join t on t.save_id = gs.id
        where (%s = false or gs.kb_native)
        order by turns desc
        """,
        (only_kb_native,),
    ).fetchall()
    ids = [r["id"] for r in rows if (r.get("turns") or 0) >= min_turns]
    return ids[:limit] if limit else ids
