"""
command_tools_anchors.py — task 136: 世界线收束机制 · GM 工具

公开 3 个 dispatcher 工具:
  list_pending_anchors      — GM 查看待发生的原著锚点
  mark_anchor_satisfied     — GM 标记某锚点已经发生 (按原著或变体)
  mark_anchor_superseded    — GM 标记某锚点被剧情绕过 (rare,需 reason)

允许 origin: llm_chat (GM 主要用户) + ui_button + api_direct + console_assistant。
注意 satisfied / superseded 是【非破坏性】的状态变更,GM 必须有权限调,否则
"原著事件按变体方式发生"这种判断没人记账,锚点会反复重复触发。
"""
from __future__ import annotations

import json

from tools_dsl.command_dispatcher import ToolSpec, get_registry

_ANCHOR_READ_ORIGINS = frozenset({"ui_button", "api_direct", "console_assistant", "llm_chat", "llm_set"})
# GM 直接负责标记锚点状态, 必须给 llm_chat origin
_ANCHOR_MUTATE_ORIGINS = frozenset({"ui_button", "api_direct", "console_assistant", "llm_chat"})


# ────────────────────────────────────────────────────────────
# Tool executors
# ────────────────────────────────────────────────────────────


def _own_save(db, save_id: int, user_id: int) -> bool:
    row = db.execute(
        "select 1 from game_saves where id = %s and user_id = %s",
        (save_id, user_id),
    ).fetchone()
    return bool(row)


def _t_list_pending_anchors(user_id: int, args: dict) -> str:
    """list_pending_anchors — 列待发生锚点。

    args:
      save_id: 必填
      phase_label: 可选, 过滤当前阶段
      chapter_min / chapter_max: 可选, 章节窗口
      limit: 默认 5, 上限 20
      include_metadata: 默认 false, true 时附带 participants/locations/concepts
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    phase_label = (args.get("phase_label") or "").strip() or None
    try:
        chapter_min = int(args.get("chapter_min")) if args.get("chapter_min") is not None else None
        chapter_max = int(args.get("chapter_max")) if args.get("chapter_max") is not None else None
    except (TypeError, ValueError):
        return "失败: chapter_min / chapter_max 必须整数"
    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))
    include_meta = bool(args.get("include_metadata"))

    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
        from agents.anchor_seed_agent import list_pending_for_phase, summarize_save_anchor_state
        anchors = list_pending_for_phase(
            save_id, phase_label,
            limit=limit, chapter_min=chapter_min, chapter_max=chapter_max,
        )
        if not include_meta:
            for a in anchors:
                a.pop("metadata", None)
        summary = summarize_save_anchor_state(save_id)
        return json.dumps({
            "save_id": save_id,
            "filter": {
                "phase_label": phase_label,
                "chapter_min": chapter_min, "chapter_max": chapter_max,
                "limit": limit,
            },
            "pending_count_total": summary["pending"],
            "fatal_pending_count": summary["fatal_pending"],
            "occurred_count": summary["occurred"],
            "variant_count": summary["variant"],
            "superseded_count": summary["superseded"],
            "avg_drift": summary["avg_drift"],
            "anchors": anchors,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_mark_anchor_satisfied(user_id: int, args: dict) -> str:
    """mark_anchor_satisfied — 锚点已经发生。

    args:
      save_id: 必填
      anchor_key: 必填 (来自 list_pending_anchors 返回)
                  也支持 anchor_id (整数主键)
      how_it_happened: 必填,描述"实际怎么发生的"(可以是变体)
      drift_score: 可选 0.0-1.0, 默认 0.0 (完全按原著) / 0.5 (中度变体) / 1.0 (核心保留方式全变)
      occurred_at_turn: 可选,默认拿存档当前最大 turn
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    anchor_key = (args.get("anchor_key") or "").strip()
    anchor_id_raw = args.get("anchor_id")
    if not anchor_key and anchor_id_raw is None:
        return "失败: anchor_key 或 anchor_id 至少给一个"
    how = (args.get("how_it_happened") or "").strip()
    if not how:
        return "失败: how_it_happened 必填,描述事件实际怎么发生"
    if len(how) > 600:
        how = how[:600]
    try:
        drift = float(args.get("drift_score") if args.get("drift_score") is not None else 0.0)
    except (TypeError, ValueError):
        drift = 0.0
    drift = max(0.0, min(1.0, drift))
    new_status = "variant" if drift >= 0.15 else "occurred"
    try:
        occurred_turn = int(args.get("occurred_at_turn")) if args.get("occurred_at_turn") is not None else None
    except (TypeError, ValueError):
        return "失败: occurred_at_turn 必须整数"

    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            # 默认 occurred_turn 从 branch_commits 最大值取
            if occurred_turn is None:
                r = db.execute(
                    "select coalesce(max(turn_index), 0) as t from branch_commits where save_id = %s",
                    (save_id,),
                ).fetchone()
                occurred_turn = int((r or {}).get("t") or 0)
            # 锁定锚点
            if anchor_key:
                row = db.execute(
                    """
                    select id, status, summary, source_chapter from save_anchor_states
                    where save_id = %s and anchor_key = %s
                    """,
                    (save_id, anchor_key),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    select id, status, summary, source_chapter from save_anchor_states
                    where save_id = %s and id = %s
                    """,
                    (save_id, int(anchor_id_raw)),
                ).fetchone()
            if not row:
                return f"失败: 找不到锚点 (save={save_id}, key={anchor_key!r}, id={anchor_id_raw})"
            if row.get("status") in ("occurred", "variant"):
                return (
                    f"提示: 锚点 {anchor_key or row['id']} 已经是 {row['status']},未变动。"
                    f" (要重新标记请先 mark_anchor_superseded 再操作)"
                )
            db.execute(
                """
                update save_anchor_states set
                  status = %s,
                  variant_description = %s,
                  occurred_at_turn = %s,
                  drift_score = %s,
                  updated_at = now()
                where save_id = %s and id = %s
                """,
                (new_status, how, occurred_turn, drift, save_id, row["id"]),
            )
            # BUG-3: 锚点满足 = 玩家已推进到该锚点所在原著章节 → 同步玩家进度
            # (advance_progress 取 max 只增不减,幂等)。让 progress_chapter 真随剧情走,
            # 防剧透集合(Phase D canon_repo._reveal_clause + retrieval 层级图)随之扩。
            _src_ch = row.get("source_chapter")
            if isinstance(_src_ch, int) and _src_ch >= 1:
                try:
                    from gm_serving.settings import advance_progress
                    advance_progress(db, save_id, _src_ch)
                except Exception:
                    pass  # 进度同步失败不阻断锚点标记
        return json.dumps({
            "ok": True,
            "anchor_id": row["id"],
            "anchor_key": anchor_key or None,
            "previous_status": row.get("status"),
            "new_status": new_status,
            "drift_score": drift,
            "occurred_at_turn": occurred_turn,
            "summary": row.get("summary", "")[:120],
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_mark_anchor_superseded(user_id: int, args: dict) -> str:
    """mark_anchor_superseded — 锚点被剧情绕过, 永远不会按这个 anchor 发生了。
    例如玩家穿越前就阻止了某事件的前置条件。需要 reason。
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    anchor_key = (args.get("anchor_key") or "").strip()
    anchor_id_raw = args.get("anchor_id")
    if not anchor_key and anchor_id_raw is None:
        return "失败: anchor_key 或 anchor_id 至少给一个"
    reason = (args.get("reason") or "").strip()
    if not reason:
        return "失败: reason 必填 (说明为什么这个锚点已经不可能发生)"
    if len(reason) > 600:
        reason = reason[:600]

    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            if anchor_key:
                row = db.execute(
                    "select id, status, is_fatal, summary from save_anchor_states "
                    "where save_id = %s and anchor_key = %s",
                    (save_id, anchor_key),
                ).fetchone()
            else:
                row = db.execute(
                    "select id, status, is_fatal, summary from save_anchor_states "
                    "where save_id = %s and id = %s",
                    (save_id, int(anchor_id_raw)),
                ).fetchone()
            if not row:
                return f"失败: 找不到锚点 (save={save_id}, key={anchor_key!r})"
            if row.get("is_fatal"):
                return (
                    "拒绝: 这是 is_fatal=true 的锚点 (死神来了模式),原则上必发生,"
                    "不能 superseded。请改用 mark_anchor_satisfied 描述"
                    "实际发生方式 (可以高 drift_score)。"
                )
            if row.get("status") == "superseded":
                return "提示: 锚点已是 superseded 状态,未变动。"
            db.execute(
                """
                update save_anchor_states set
                  status = 'superseded',
                  variant_description = %s,
                  drift_score = 1.0,
                  updated_at = now()
                where save_id = %s and id = %s
                """,
                (reason, save_id, row["id"]),
            )
        return json.dumps({
            "ok": True,
            "anchor_id": row["id"],
            "anchor_key": anchor_key or None,
            "new_status": "superseded",
            "reason": reason,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_record_history_anchor(user_id: int, args: dict) -> str:
    """record_history_anchor — 写"存档独立时间线"里玩家创造的重要历史节点。
    跟 mark_anchor_satisfied(剧本未来锚点)是两套表两套语义。

    args:
      save_id           : 必填
      summary           : 必填,事件描述 (≤800 chars)
      importance        : 0-100, 默认 50 (建议 60 起留档)
      tags / characters / locations : list[str],可选
      linked_canon_keys / linked_pending_anchors : list[str],可选,关联到 KB / 剧本锚点
      ingame_chapter    : int 可选,玩家声明的"原著章节进度"
      source            : 'gm_generated' | 'player_declared',默认前者
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    summary = (args.get("summary") or "").strip()
    if not summary:
        return "失败: summary 必填"
    # 安全围栏:save 必须属于当前用户(LLM 可在 args 注入任意 save_id;复用兄弟工具同款 _own_save)
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        if not _own_save(db, int(save_id_raw), user_id):
            return f"失败 (权限): save {int(save_id_raw)} 不属于当前用户或不存在"
    try:
        from agents.save_history import record_history_anchor
        linked_pending = args.get("linked_pending_anchors") or []
        if not isinstance(linked_pending, list):
            linked_pending = []
        result = record_history_anchor(
            int(save_id_raw),
            summary=summary,
            importance=int(args.get("importance") or 50),
            story_time_label=(args.get("story_time_label") or "").strip(),
            ingame_chapter=args.get("ingame_chapter"),
            tags=args.get("tags") or None,
            characters=args.get("characters") or None,
            locations=args.get("locations") or None,
            linked_canon_keys=args.get("linked_canon_keys") or None,
            linked_pending_anchors=linked_pending or None,
            source=(args.get("source") or "gm_generated").strip(),
        )
        if not result.get("ok"):
            return f"失败: {result.get('error', '未知')}"
        # iter#5: 级联自动 mark_anchor_satisfied — 用户的"大体对应,允许不同"诉求
        # linked_pending_anchors 非空 → 同事务里把对应 pending 标 satisfied,防止
        # 同事件【未来段 pending】+【过去段 history】双重注入,GM 看到自相矛盾上下文
        # 又触发一次(典型记忆污染)。
        cascade_results: list[str] = []
        if linked_pending:
            try:
                from platform_app.db import connect, init_db
                init_db()
                _drift = float(args.get("drift_score") or 0.5)  # history 改写默认为中度变体
                _drift = max(0.0, min(1.0, _drift))
                _new_status = "variant" if _drift >= 0.15 else "occurred"
                with connect() as db:
                    for ak in linked_pending:
                        ak_str = str(ak).strip()
                        if not ak_str:
                            continue
                        row = db.execute(
                            """
                            update save_anchor_states
                            set status = %s, drift_score = %s,
                                variant_description = %s,
                                occurred_at_turn = (select coalesce((state_snapshot->>'turn')::int, 0)
                                                    from game_saves where id = %s),
                                updated_at = now()
                            where save_id = %s and anchor_key = %s
                              and status = 'pending'
                            returning anchor_key, status
                            """,
                            (_new_status, _drift, summary[:240],
                             int(save_id_raw), int(save_id_raw), ak_str),
                        ).fetchone()
                        if row:
                            cascade_results.append(f"{ak_str}→{row['status']}")
            except Exception as casc_exc:
                cascade_results.append(f"[级联失败:{type(casc_exc).__name__}: {casc_exc}]")
        cascade_str = (f" + 级联标记 pending: {', '.join(cascade_results)}"
                       if cascade_results else "")
        return (f"OK: 历史锚点已写入(id={result['id']}, turn={result['turn_occurred']}, "
                f"importance={result['importance']}){cascade_str}")
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_check_pending_anchor_drift(user_id: int, args: dict) -> str:
    """check_pending_anchor_drift — 反查指定 pending anchor 是否已被某条 history 改写。

    args:
      save_id     : 必填
      anchor_keys : 必填,list[str] 一批 anchor_key
    返回 JSON {anchor_key: [{turn, summary, importance, characters}]},
    空数组表示该 anchor 未被任何 history 改写,GM 仍应正常触发。
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    aks = args.get("anchor_keys")
    if not isinstance(aks, list) or not aks:
        return "失败: anchor_keys 必填且为非空数组"
    # 安全围栏:save 必须属于当前用户(防 LLM 注入异档 save_id 跨用户读)
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        if not _own_save(db, int(save_id_raw), user_id):
            return f"失败 (权限): save {int(save_id_raw)} 不属于当前用户或不存在"
    try:
        from agents.save_history import find_history_for_pending
        result = find_history_for_pending(int(save_id_raw), [str(x) for x in aks])
        import json
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_recent_history(user_id: int, args: dict) -> str:
    """list_recent_history — 查存档最近的历史锚点 (按 turn 倒序)。

    args:
      save_id           : 必填
      limit             : 1-20,默认 8
      min_importance    : 只返 importance >= 此值的,默认 0
      character_filter  : 可选,只返 characters 含该名字的
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    # 安全围栏:save 必须属于当前用户(防 LLM 注入异档 save_id 跨用户读)
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        if not _own_save(db, int(save_id_raw), user_id):
            return f"失败 (权限): save {int(save_id_raw)} 不属于当前用户或不存在"
    try:
        from agents.save_history import list_recent_history
        items = list_recent_history(
            int(save_id_raw),
            limit=int(args.get("limit") or 8),
            min_importance=int(args.get("min_importance") or 0),
            character_filter=args.get("character_filter") or None,
        )
        import json
        return json.dumps(items, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_summarize_anchors(user_id: int, args: dict) -> str:
    """summarize_anchors — 当前存档的整体锚点收束状态。"""
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
        from agents.anchor_seed_agent import summarize_save_anchor_state
        s = summarize_save_anchor_state(save_id)
        return json.dumps(s, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────
# Registration
# ────────────────────────────────────────────────────────────


def _t_claim_protagonist_pov(user_id: int, args: dict) -> str:
    """claim_protagonist_pov — 玩家显式声明「我就是原作主角 X」(灵魂占据 X 的身体)。

    **重要语义边界**:
    isekai 默认设定是「玩家用 自定义角色卡 的肉身 + 现代灵魂,与原作主角【平行共存】」 —
    爱丽丝(原作主角)应作为独立 NPC 触发她自己的登场 anchor,杭雁菱(玩家)在同一场景平行加入。

    本工具**只在玩家显式声明** "我就是 X / 我占据了 X 的身体 / 灵魂寄宿在 X 身上"
    这种**主角身份覆盖**情境下由 GM 主动调用。**不要默认调用**。

    调用效果:
      · 找指定原作主角 X (或 importance 第 1 character 作为兜底)
      · 把 "X(character)首次登场" 类 pending anchor 标记 satisfied
        (drift=0, resolution='玩家声明自己就是 X')
      · player.aliases 加入 X (GM 看到 "X" 这个名字时知道指向玩家)
      · player_meta.pov_replaces 加入 X (结构化关系,后续工具可查)

    幂等可重复调。

    args:
      save_id: 必填
      original_protag_name: 强烈建议显式传 — 不传按 type='character' importance desc 第 1 取兜底
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    explicit_name = (args.get("original_protag_name") or "").strip()
    try:
        from platform_app.db import connect, init_db
        from psycopg.types.json import Jsonb
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            # 1. 拿 script_id
            sr = db.execute(
                "select script_id, state_snapshot from game_saves where id=%s", (save_id,),
            ).fetchone()
            if not sr:
                return "失败: 存档不存在"
            script_id = int(sr["script_id"])
            state = sr["state_snapshot"] if isinstance(sr["state_snapshot"], dict) else {}
            # 2. 找原作主角
            name = explicit_name
            if not name:
                r = db.execute(
                    "select name from kb_canon_entities where script_id=%s and type='character' "
                    "order by importance desc nulls last limit 1",
                    (script_id,),
                ).fetchone()
                if r:
                    name = r["name"]
            if not name:
                return "失败: 未找到原作主角(剧本可能还未跑 canon_extract)"
            # 3. 找该主角的"首次登场"类 pending anchors
            #    匹配:summary 含 "X(character)首次登场" OR must_preserve::text 含 "X 参与"
            rows = db.execute(
                """select id, anchor_key, summary, status from save_anchor_states
                   where save_id=%s and status='pending'
                     and (summary like %s or must_preserve::text like %s)""",
                (save_id, f"%{name}(character)首次登场%", f"%{name} 参与%"),
            ).fetchall() or []
            # 4. mark satisfied
            satisfied = []
            for r in rows:
                db.execute(
                    """update save_anchor_states set
                         status='occurred', drift_score=0.0,
                         variant_description=%s, updated_at=now()
                       where id=%s and save_id=%s""",
                    (f"玩家以「{state.get('player', {}).get('name', '玩家')}」代入 {name} 的 POV 位置 — 主角登场即玩家入场", r["id"], save_id),
                )
                satisfied.append({"anchor_id": r["id"], "anchor_key": r["anchor_key"], "summary": (r["summary"] or "")[:120]})
            # 5. 把原作主角名加进 player.aliases (持久化到 state_snapshot)
            player = state.setdefault("player", {})
            aliases = player.get("aliases") or []
            if isinstance(aliases, str):
                # 老存档 aliases 可能是字符串
                aliases = [a.strip() for a in aliases.split(",") if a.strip()]
            if name not in aliases:
                aliases.append(name)
            player["aliases"] = aliases
            # 也记录到 pov_replaces 元字段(让 GM 查 lookup_player_pov 时能拿到结构化关系)
            player_meta = state.setdefault("player_meta", {})
            pov_list = player_meta.get("pov_replaces") or []
            if name not in pov_list:
                pov_list.append(name)
            player_meta["pov_replaces"] = pov_list
            db.execute(
                "update game_saves set state_snapshot=%s, updated_at=now() where id=%s",
                (Jsonb(state), save_id),
            )
        return json.dumps({
            "ok": True,
            "original_protag_name": name,
            "satisfied_anchors": satisfied,
            "satisfied_count": len(satisfied),
            "player_aliases_added": name,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_revoke_protagonist_pov(user_id: int, args: dict) -> str:
    """revoke_protagonist_pov — 撤销玩家代入原作主角 X 的状态(claim_protagonist_pov 的逆操作)。

    把因 claim 操作而标 satisfied 的 anchor 重置 pending,清 player.aliases 里的 X 项,
    清 player_meta.pov_replaces 里的 X。

    场景:误调 claim 后撤销,或玩家从"我就是 X" 转变为 "我是独立穿越者跟 X 平行"。

    args:
      save_id: 必填
      original_protag_name: 必填 — 要撤销哪个原作主角的 POV 声明
    """
    save_id_raw = args.get("save_id")
    if not isinstance(save_id_raw, (int, float, str)) or not str(save_id_raw).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    save_id = int(save_id_raw)
    name = (args.get("original_protag_name") or "").strip()
    if not name:
        return "失败: original_protag_name 必填"
    try:
        from platform_app.db import connect, init_db
        from psycopg.types.json import Jsonb
        init_db()
        with connect() as db:
            if not _own_save(db, save_id, user_id):
                return f"失败 (权限): save {save_id} 不属于当前用户或不存在"
            # 1. 把 claim 标记过的 anchor 重置 pending。
            #    BUG 修复:原来用 `summary like '%X(character)首次登场%' AND variant_description like`,
            #    但 claim 是按 (summary OR must_preserve like '%X 参与%') 标记的 —— 靠 must_preserve
            #    命中(summary 无"首次登场")的锚点会被 claim 标 occurred 却永不被 revoke 重置,
            #    POV 切回后原著事件永久吞失。改为只按 claim 自己写的 variant_description 签名
            #    "代入 {name} 的 POV 位置"(claim 给所有标记锚点统一写入)精确反查,与 claim 镜像。
            rows = db.execute(
                """select id, anchor_key, summary, status, variant_description
                   from save_anchor_states
                   where save_id=%s and status in ('occurred','variant')
                     and variant_description like %s""",
                (save_id, f"%代入 {name} 的 POV 位置%"),
            ).fetchall() or []
            reverted = []
            for r in rows:
                db.execute(
                    """update save_anchor_states set
                         status='pending', drift_score=0.0,
                         variant_description='', occurred_at_turn=NULL, updated_at=now()
                       where id=%s and save_id=%s""",
                    (r["id"], save_id),
                )
                reverted.append({"anchor_id": r["id"], "anchor_key": r["anchor_key"], "summary": (r["summary"] or "")[:80]})
            # 2. 从 player.aliases / player_meta.pov_replaces 移除 X
            sr = db.execute(
                "select state_snapshot from game_saves where id=%s", (save_id,),
            ).fetchone()
            state = sr["state_snapshot"] if isinstance(sr["state_snapshot"], dict) else {}
            player = state.setdefault("player", {})
            aliases = player.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [a.strip() for a in aliases.split(",") if a.strip()]
            aliases = [a for a in aliases if a != name]
            player["aliases"] = aliases
            player_meta = state.setdefault("player_meta", {})
            pov_list = player_meta.get("pov_replaces") or []
            pov_list = [x for x in pov_list if x != name]
            player_meta["pov_replaces"] = pov_list
            db.execute(
                "update game_saves set state_snapshot=%s, updated_at=now() where id=%s",
                (Jsonb(state), save_id),
            )
        return json.dumps({
            "ok": True,
            "original_protag_name": name,
            "reverted_anchors": reverted,
            "reverted_count": len(reverted),
            "player_aliases_after": aliases,
            "pov_replaces_after": pov_list,
        }, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def register_anchor_tools() -> None:
    registry = get_registry()
    specs = [
        ToolSpec(
            name="list_pending_anchors",
            description=(
                "【世界线收束】查询当前存档待发生的原著锚点事件。"
                "GM 应每隔几轮调用一次,了解『剧本必须发生但还没发生』的关键事件,"
                "并主动设计场景把剧情往那里引。返回按 importance desc 排序的列表,"
                "含 anchor_key / chapter / summary / must_preserve / may_vary / is_fatal。"
                "is_fatal=true 表示死神来了模式 — 玩家任何阻止尝试都会以替代方式触发。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "phase_label": {"type": "string", "description": "可选,只取该 phase 下的锚点"},
                    "chapter_min": {"type": "integer", "description": "可选,章节范围下限"},
                    "chapter_max": {"type": "integer", "description": "可选,章节范围上限"},
                    "limit": {"type": "integer", "description": "返回条数 (1-20),默认 5", "default": 5},
                    "include_metadata": {"type": "boolean", "description": "true 时附带 participants/locations,默认 false"},
                },
                "required": ["save_id"],
            },
            executor=_t_list_pending_anchors,
            scope="user",
            origins=_ANCHOR_READ_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 1, "limit": 5},
                {"save_id": 1, "phase_label": "柏林暗流篇", "limit": 3},
                {"save_id": 1, "chapter_min": 10, "chapter_max": 30},
            ],
        ),
        ToolSpec(
            name="mark_anchor_satisfied",
            description=(
                "【世界线收束】标记某个原著锚点已经在本存档发生。"
                "drift_score=0 表示完全按原著方式发生; drift_score>=0.15 时 status 变为 variant "
                "(以变体方式发生,核心保留但具体不同)。how_it_happened 必填,"
                "描述本存档里这件事是怎么发生的 (会写入日志供后续 audit)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "anchor_key": {"type": "string", "description": "锚点 key (如 'chapter:12:event:3')"},
                    "anchor_id": {"type": "integer", "description": "或锚点主键 id (anchor_key 二选一)"},
                    "how_it_happened": {"type": "string", "description": "事件实际发生方式描述"},
                    "drift_score": {"type": "number", "description": "0.0-1.0, 偏离原著程度"},
                    "occurred_at_turn": {"type": "integer", "description": "可选,默认存档当前 turn"},
                },
                "required": ["save_id", "how_it_happened"],
            },
            executor=_t_mark_anchor_satisfied,
            scope="user",
            origins=_ANCHOR_MUTATE_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 1, "anchor_key": "chapter:12:event:0",
                 "how_it_happened": "穆蕾莉娅在地下车场对 MC 透露异端情报,而非原著的浴室场景",
                 "drift_score": 0.3},
                {"save_id": 1, "anchor_key": "chapter:7:event:2",
                 "how_it_happened": "完全按原著方式 — Kaiserin 当夜命令清空北区情报站", "drift_score": 0.0},
            ],
        ),
        ToolSpec(
            name="mark_anchor_superseded",
            description=(
                "【世界线收束】标记某个原著锚点已被剧情绕过,永远不会按这个锚点发生。"
                "is_fatal=true 锚点【拒绝】被 superseded (死神来了模式不可绕过)。"
                "非 fatal 锚点也需谨慎用 — 大多数偏离应该用 mark_anchor_satisfied 配 drift_score 来记录。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "anchor_key": {"type": "string", "description": "锚点 key"},
                    "anchor_id": {"type": "integer", "description": "或锚点主键 id"},
                    "reason": {"type": "string", "description": "为什么这个锚点已经不可能发生 (必填)"},
                },
                "required": ["save_id", "reason"],
            },
            executor=_t_mark_anchor_superseded,
            scope="user",
            origins=_ANCHOR_MUTATE_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 1, "anchor_key": "chapter:18:event:1",
                 "reason": "MC 提前 6 章拦截了图卢兹方面的密令,该事件的前置条件已不存在"},
            ],
        ),
        ToolSpec(
            name="record_history_anchor",
            description=(
                "【存档独立时间线】记录玩家在这个世界线创造的重要历史节点 (过去时态)。"
                "跟 mark_anchor_satisfied 不同 — 那个标记的是【原著】锚点;本工具记录【玩家创造】"
                "的新事件,不必对应任何原著锚点。importance 建议:60+ 改变 NPC 关系/势力立场,"
                "80+ 改写原著锚点(linked_pending_anchors 不空),90+ 引入新角色/势力。"
                "不需要每轮都调 — 流水账有 state.history 兜;只在事件足够 important 时留档。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "summary": {"type": "string", "description": "事件描述,过去时态,≤800 字"},
                    "importance": {"type": "integer", "description": "0-100,建议 ≥60", "default": 60},
                    "story_time_label": {"type": "string", "description": "事件发生时存档 world.time,可空"},
                    "ingame_chapter": {"type": "integer", "description": "玩家声明的原著章节进度"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签数组,如 ['政治','穿越者泄密']"},
                    "characters": {"type": "array", "items": {"type": "string"}, "description": "涉及人物名"},
                    "locations": {"type": "array", "items": {"type": "string"}, "description": "涉及地点"},
                    "linked_canon_keys": {"type": "array", "items": {"type": "string"}, "description": "关联到 canon 实体 logical_key"},
                    "linked_pending_anchors": {"type": "array", "items": {"type": "string"}, "description": "**强引导**:若改写了某原著锚点,务必填 anchor_key (本工具会自动同步 mark_anchor_satisfied,防止 GM 下一轮看到 pending 还重复触发)。"},
                    "drift_score": {"type": "number", "minimum": 0, "maximum": 1, "description": "若 linked_pending_anchors 非空,级联标记时用的 drift_score (0=按原著,1=核心保留全变),默认 0.5"},
                    "source": {"type": "string", "enum": ["gm_generated", "player_declared"], "default": "gm_generated"},
                },
                "required": ["save_id", "summary"],
            },
            executor=_t_record_history_anchor,
            scope="user",
            origins=_ANCHOR_MUTATE_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 2, "summary": "MC 在波斯王宫向国王透露 1914 年一战会爆发的情报,国王召开内阁紧急会议",
                 "importance": 75, "tags": ["政治", "穿越者泄密"], "characters": ["波斯国王"], "locations": ["波斯王宫"]},
                {"save_id": 2, "summary": "MC 拯救了原著中应被刺杀的林有德,改写了陨石坑刺杀事件",
                 "importance": 90, "characters": ["林有德"], "linked_pending_anchors": ["chapter:162:event:0"]},
            ],
        ),
        ToolSpec(
            name="check_pending_anchor_drift",
            description=(
                "【世界线收束】反查指定 pending anchor 是否已被某条 history 改写 (linked_pending_anchors 反查)。"
                "用于排查『为什么 retrieve_context 注入了某 pending,但我觉得已经做过这件事了』的怀疑。"
                "返回每个 anchor 对应的 history 列表 (turn 倒序前 3 条),空数组表示未改写。"
                "GM 一般不需要主动调 — retrieve_context 已经在注入时自动标 ⚠ 并显示 drift_marker。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer"},
                    "anchor_keys": {"type": "array", "items": {"type": "string"},
                                    "description": "要反查的 anchor_key 数组"},
                },
                "required": ["save_id", "anchor_keys"],
            },
            executor=_t_check_pending_anchor_drift,
            scope="user",
            origins=_ANCHOR_READ_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="list_recent_history",
            description=(
                "【存档独立时间线】查询本存档最近的历史锚点 (按 turn 倒序)。"
                "GM 应在每轮开始时调一次,了解【玩家在这个世界线已经做过什么】,"
                "避免叙事重复或与已发生事实矛盾(记忆污染防护)。"
                "返回 list[{turn, summary, importance, tags, characters, locations, linked_anchors}]。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "limit": {"type": "integer", "description": "返回条数,默认 8", "default": 8},
                    "min_importance": {"type": "integer", "description": "只返 importance ≥ 此值的", "default": 0},
                    "character_filter": {"type": "string", "description": "可选,只返涉及此角色的"},
                },
                "required": ["save_id"],
            },
            executor=_t_list_recent_history,
            scope="user",
            origins=_ANCHOR_READ_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 2, "limit": 5},
                {"save_id": 2, "character_filter": "林有德", "min_importance": 60},
            ],
        ),
        ToolSpec(
            name="summarize_anchors",
            description=(
                "【世界线收束】返回当前存档的锚点整体收束状态: pending / occurred / variant / superseded "
                "各多少,fatal_pending 数,avg_drift。GM 偶尔调用看一眼整体偏离度。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                },
                "required": ["save_id"],
            },
            executor=_t_summarize_anchors,
            scope="user",
            origins=_ANCHOR_READ_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="revoke_protagonist_pov",
            description=(
                "【主角身份覆盖·撤销】claim_protagonist_pov 的逆操作。"
                "把因 claim 操作标 satisfied 的 'X 首次登场' anchor 重置 pending,"
                "清 player.aliases / pov_replaces 里的 X 项。"
                "用于纠正误调或玩家身份语义切换。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer"},
                    "original_protag_name": {"type": "string"},
                },
                "required": ["save_id", "original_protag_name"],
            },
            executor=_t_revoke_protagonist_pov,
            scope="user",
            origins=_ANCHOR_MUTATE_ORIGINS,
            destructive=False,
            input_examples=[{"save_id": 3, "original_protag_name": "爱丽丝"}],
        ),
        ToolSpec(
            name="claim_protagonist_pov",
            description=(
                "【主角身份覆盖】玩家**显式声明** '我就是原作主角 X'(灵魂占据 X 的身体)。\n\n"
                "**重要边界**:isekai 默认是「玩家自定义角色卡 + 现代灵魂,与原作主角【平行共存】」 — \n"
                "原作主角作为独立 NPC 正常登场,玩家在同一场景平行加入。**不要默认调用本工具**。\n\n"
                "**只在以下情形 GM 主动调**:\n"
                "  · 玩家明说 '我就是 X' / '我占据了 X 的身体' / '我的灵魂在 X 身上'\n"
                "  · 玩家选 user_card 时显式标注其为某原作角色的化名\n\n"
                "调用效果:'X(character)首次登场' 类 pending anchor → satisfied (drift=0),\n"
                "player.aliases 加 X,player_meta.pov_replaces 加 X。幂等可重复。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "目标存档 id"},
                    "original_protag_name": {
                        "type": "string",
                        "description": "可选,原作主角名(完全匹配 kb_canon_entities.name)。不传按 type='character' importance desc 第 1 取。",
                    },
                },
                "required": ["save_id"],
            },
            executor=_t_claim_protagonist_pov,
            scope="user",
            origins=_ANCHOR_MUTATE_ORIGINS,
            destructive=False,
            input_examples=[
                {"save_id": 3},  # 默认按 importance 取
                {"save_id": 3, "original_protag_name": "爱丽丝"},
            ],
        ),
    ]
    for spec in specs:
        if not registry.has(spec.name):
            registry.register(spec)


__all__ = ["register_anchor_tools"]
