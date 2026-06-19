"""context_engine.loaders — 角色卡 / 世界书加载函数."""
from __future__ import annotations

import json
from typing import Any

from context_engine._constants import CHAR_IDX, WORLD_IDX

# 进度感知角色卡:partial(穿越者模糊预知)在严格进度上叠加的近未来缓冲章数。
# 温和放宽,避免大幅剧透;none=0(严格),omniscient=不 gate。
_PARTIAL_LOOKAHEAD_CHAPTERS = 20


def _safe_load_chars(script_id, book_id, manifest,
                     progress_chapter: int | None = None,
                     foreknowledge_mode: str = "omniscient") -> dict[str, Any]:
    """state_schema 层需要 chars dict 来列出已知 NPC enum。
    模组场景没有小说角色卡 → 返回空 dict，不再误读 .webnovel/indexes。

    progress_chapter / foreknowledge_mode(进度感知角色卡 Phase 1B):透传给
    _load_characters → _load_characters_db 的 reveal 闸。state_schema 用途默认
    omniscient(列 NPC enum 不该被进度截断);GM 卡注入路径(novel.py)显式传严格档。
    """
    if not manifest:
        return _load_characters(script_id=script_id, book_id=book_id,
                                progress_chapter=progress_chapter,
                                foreknowledge_mode=foreknowledge_mode)
    if manifest.get("kind") == "novel_adaptation":
        return _load_characters(script_id=script_id, book_id=book_id,
                                progress_chapter=progress_chapter,
                                foreknowledge_mode=foreknowledge_mode)
    return {}


def _load_characters(script_id: int | None = None, book_id: int | None = None,
                     progress_chapter: int | None = None,
                     foreknowledge_mode: str = "omniscient",
                     save_id: int | None = None) -> dict[str, Any]:
    """task 80: 通用底座 — 优先从 DB character_cards 取。
    传了 script_id/book_id 表示指定剧本: DB 空就返 {} (不要回退 JSON,
    那是单一书的固化数据,会污染其它剧本)。
    完全没传 (legacy 兼容): 才允许 JSON 回退。

    progress_chapter / foreknowledge_mode(进度感知角色卡 Phase 1B):
    默认 omniscient(向后兼容:不传=不 gate,管理/枚举视角看全部)。GM 注入路径
    显式传 progress_chapter + 严格档以挡掉「序章看到尚未登场角色」。
    """
    scoped = bool(script_id or book_id)
    if scoped:
        try:
            return _load_characters_db(script_id=script_id, book_id=book_id,
                                       progress_chapter=progress_chapter,
                                       foreknowledge_mode=foreknowledge_mode,
                                       save_id=save_id) or {}
        except Exception:
            return {}
    try:
        with open(CHAR_IDX, encoding="utf-8") as f:
            return json.load(f).get("characters", {})
    except Exception:
        return {}


def _load_characters_db(script_id: int | None, book_id: int | None,
                        progress_chapter: int | None = None,
                        foreknowledge_mode: str = "omniscient",
                        save_id: int | None = None) -> dict[str, Any]:
    """从 character_cards 表读取该 script/book 启用的角色卡，转成 JSON 风格 dict。

    进度感知角色卡 Phase 1B — reveal 闸(防剧透,确定性):
      none / partial : WHERE 追加 (first_revealed_chapter<=progress OR first_revealed_chapter=0)
                       → 挡掉中后期才登场、当前进度尚未揭示的角色。
                       first_revealed_chapter=0(未知)= 保守放行(别误隐藏该出场的角色,
                       与 canon_repo._reveal_clause 同语义)。
      omniscient / progress_chapter=None : 不 gate(管理/枚举/全知视角看全部)。
    """
    from platform_app.db import connect
    where_clauses = ["enabled = true"]
    params: list[Any] = []
    if script_id:
        where_clauses.append("script_id = %s")
        params.append(int(script_id))
    elif book_id:
        where_clauses.append("book_id = %s")
        params.append(int(book_id))
    # 进度感知 reveal 闸:omniscient 或 progress=None 不加;否则按进度截断(0=保守放行)。
    #   none    : first_revealed_chapter <= progress(严格)
    #   partial : <= progress + 近未来缓冲(穿越者模糊预知,温和放宽,参 canon_repo partial 语义)
    mode = (foreknowledge_mode or "omniscient").lower()
    base_where, base_params = list(where_clauses), list(params)  # reveal 之前的 base 过滤(影子比对用)

    # P4(S3):reveal 闸两套。旧=标量 first_revealed_chapter<=ceiling;新=前沿 reveal_clause_v2(save_id)。
    from kb.reveal import _frontier_on, _frontier_shadow, _shadow_diff_log, reveal_clause_v2

    def _old_reveal() -> tuple[str | None, list]:
        if mode != "omniscient" and progress_chapter is not None:
            ceiling = int(progress_chapter)
            if mode == "partial":
                ceiling += _PARTIAL_LOOKAHEAD_CHAPTERS
            return "(first_revealed_chapter <= %s or first_revealed_chapter = 0)", [ceiling]
        return None, []

    use_v2 = save_id is not None and _frontier_on(save_id) and mode != "omniscient"
    if use_v2:
        # 进度章传给前沿门控:开局/当前章实体(序章人物)按章节可见,不被空前沿误藏。
        # progress 缺省时按已到达锚点确定性派生(绝不超玩家真实进度)。
        from kb.reveal import derived_progress_chapter as _dpc
        _prog = progress_chapter if progress_chapter is not None else _dpc(int(save_id))
        rc, rcp = reveal_clause_v2(int(save_id), mode, prefix="", has_public_knowledge=False,
                                   has_famous=False, progress_chapter=_prog)
        where_clauses.append(rc); params.extend(rcp)
    else:
        rc, rcp = _old_reveal()
        if rc:
            where_clauses.append(rc); params.extend(rcp)
    # v28: 显式补 card_type='npc' 过滤(PC/persona 不应进 GM 检索池);
    # 加 full_name / background / first_revealed_chapter — background 是 v28 核心新增
    # 给 GM context 看角色前史/出身/动机,first_revealed_chapter 是进度感知 reveal 闸的依据。
    sql = (
        "select script_id, name, full_name, aliases, identity, background, "
        "appearance, personality, speech_style, current_status, secrets, "
        "sample_dialogue, token_budget, priority, first_revealed_chapter, avatar_path "
        "from character_cards where " + " and ".join(where_clauses) +
        " and card_type = 'npc' "
        "order by priority desc, id asc"
    )
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
        # 影子比对:旧 vs 新 reveal 放行的 name 集合(同 base 过滤),diff 落日志,不改返回。
        if (_frontier_shadow() and save_id is not None and mode != "omniscient"
                and progress_chapter is not None):
            def _names(_rc, _rcp):
                wc = base_where + ([_rc] if _rc else [])
                s = ("select name from character_cards where " + " and ".join(wc)
                     + " and card_type = 'npc'")
                return {r["name"] for r in db.execute(s, base_params + list(_rcp)).fetchall()}
            o_rc, o_rcp = _old_reveal()
            n_rc, n_rcp = reveal_clause_v2(int(save_id), mode, prefix="", has_public_knowledge=False,
                                           has_famous=False, progress_chapter=progress_chapter)
            _shadow_diff_log("load_characters", _names(o_rc, o_rcp), _names(n_rc, n_rcp))
    out: dict[str, Any] = {}
    for r in rows:
        out[r["name"]] = {
            "script_id": r["script_id"],  # 让 _format_card 能查 documents 兜底原文片段
            "full_name": r["full_name"] or "",
            "aliases": r["aliases"] or [],
            "identity": r["identity"] or "",
            "background": r["background"] or "",
            "appearance": r["appearance"] or "",
            "personality": r["personality"] or "",
            "speech_style": r["speech_style"] or "",
            "current_status": r["current_status"] or "",
            "secrets": r["secrets"] or "",
            "sample_dialogue": r["sample_dialogue"] or [],
            "priority": int(r["priority"] or 100),
            "token_budget": int(r["token_budget"] or 450),
            "first_revealed_chapter": int(r["first_revealed_chapter"] or 0),
            "avatar_path": r["avatar_path"] or "",
        }
    return out


def _load_worldbook_db(script_id: int | None, book_id: int | None,
                       save_id: int | None = None, mode: str = "omniscient") -> list[dict[str, Any]]:
    """从 worldbook_entries 取启用条目；返回 _worldbook_entries 风格的 list。

    P4(S4):世界书此前【无】进度门控(门控缺口)——所有启用条目对任何进度等价可见。flag on 时
    按前沿门控(reveal_clause_v2)挡掉当前进度尚未揭示的条目。注意这是【新增】门控,非等价替换:
    影子比对的 diff = 被新门控挡掉的未来条目(预期非空,人工核实皆为未揭示剧透即正确)。"""
    from platform_app.db import connect
    where_clauses = ["enabled = true"]
    params: list[Any] = []
    if script_id:
        where_clauses.append("script_id = %s")
        params.append(int(script_id))
    elif book_id:
        where_clauses.append("book_id = %s")
        params.append(int(book_id))
    base_where, base_params = list(where_clauses), list(params)  # 门控之前(影子比对用)

    from kb.reveal import (_frontier_on, _frontier_shadow, _shadow_diff_log,
                           derived_progress_chapter as _dpc, reveal_clause_v2)
    m = (mode or "omniscient").lower()
    use_v2 = save_id is not None and _frontier_on(save_id) and m != "omniscient"
    _wprog = _dpc(int(save_id)) if use_v2 else None  # 世界书无 progress 入参 → 按已到达锚点派生
    if use_v2:
        rc, rcp = reveal_clause_v2(int(save_id), m, prefix="", has_public_knowledge=False,
                                   has_famous=False, progress_chapter=_wprog)
        where_clauses.append(rc); params.extend(rcp)

    sql = (
        "select id, title, content, keys, regex_keys, priority, token_budget "
        "from worldbook_entries where " + " and ".join(where_clauses) +
        " order by priority desc, id asc"
    )
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
        # 影子比对:旧(无门控全集) vs 新(前沿过滤)。预期 new_only 为空、old_only=被挡的未来条目。
        if _frontier_shadow() and save_id is not None and m != "omniscient":
            old_ids = {r["id"] for r in db.execute(
                "select id from worldbook_entries where " + " and ".join(base_where),
                base_params).fetchall()}
            n_rc, n_rcp = reveal_clause_v2(int(save_id), m, prefix="", has_public_knowledge=False,
                                           has_famous=False, progress_chapter=_wprog)
            new_ids = {r["id"] for r in db.execute(
                "select id from worldbook_entries where " + " and ".join(base_where + [n_rc]),
                base_params + list(n_rcp)).fetchall()}
            _shadow_diff_log("worldbook GATE-NEW(挡未揭示条目预期)", old_ids, new_ids)
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
        with open(WORLD_IDX, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
