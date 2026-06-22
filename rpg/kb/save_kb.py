"""kb.save_kb — 完整的「存档知识库」DB-resident 引擎(可行性验证版,与现有 blob 平行,不动生产)。

目标(用户拍板):存档状态**完全数据库化**、**单一来源**、是剧本知识库的**运行时态**。
本模块把整坨 GameState.data JSONB blob **完整无遗漏**地拆成 DB 行,并能**无损投影回**等价 state
——「新系统本身先是一个完整的」+ 可行性验证。**完整性靠 by-construction**:每个非瞬态字段都路由到
唯一归宿,materialize 逆向重组;深比对(原 state ∪ 减瞬态)== materialize 结果。

路由(单一来源,blob 退场):
  · 关系(relationships)                 → kb_relationships(from='_player' 或 NPC↔NPC)
  · 离散事实/事件(memory.facts 标 source=memory / world.known_events 标 source=world)→ kb_events
  · 人/物/概念/地点(含玩家 _player)+ 剧本 canon T0 → kb_entities(COW,T0 seed)
  · 对话历史(history)                   → messages 表(已存在;不重复存,单一来源)
  · **其余所有顶层键**(player/world/memory 标量/worldline/scene/encounter/ruleset/dice_log/
    permissions/player_private/player_character/active_entities/turn/session_model/meta)
                                         → kb_worldline_vars(logical_key=顶层键,value=该子树 jsonb)
  · 纯瞬态(last_context*/last_projection/_active_save_id/_turn_images_generated 等)→ 丢弃,非状态

绝不写剧本域(只读 kb_canon_entities);所有写落 save 域 COW 行,born_commit=当前 commit。
"""
from __future__ import annotations

from typing import Any

from kb import live_repo, t0_seed

# 顶层纯瞬态键:每回合重生/纯运行时指针,不是存档状态 → 不进 KB。
_DROP_TOP = {"_active_save_id", "_turn_images_generated", "history", "relationships"}
# memory 子键里的瞬态(调试缓存)。
_DROP_MEM = {"last_context", "last_retrieval", "last_context_agent", "last_structured_updates"}
# worldline 子键里的瞬态。
_DROP_WL = {"last_projection", "last_validation", "pending_projection"}


def _setvar(db, save_id: int, commit_id: int, key: str, value: Any) -> None:
    live_repo.set_worldline_var(db, save_id, commit_id, key, value=value)


def _vars(db, save_id: int, commit_id: int) -> dict[str, Any]:
    rows = live_repo._newest_visible(db, "kb_worldline_vars", save_id, commit_id, ("logical_key", "value"))
    return {r["logical_key"]: r["value"] for r in rows}


# ── 写:把整坨 blob state 完整拆进 KB(by-construction 不遗漏)──────────────────
def import_state(db, save_id: int, commit_id: int, state_data: dict) -> dict[str, int]:
    n_var = n_rel = n_evt = n_ent = n_skip = 0
    sd = dict(state_data or {})

    # no-op 守卫:每回合全量 re-import,但绝大多数 var/关系/事实并未变 → 若仍逐条 INSERT 新 COW 行,
    # 行数随回合数 ×N 线性爆炸(实测 17 可见键 7 回合=119 行),且 _newest_visible 每读全表扫历史 →
    # 长局退化。读当前可见(继承自父 commit;本 commit 尚未写自有行,故即父态),与待写值 byte 比对,
    # 相同则跳过。跳过一条 byte-相同写【绝不改变】_newest_visible 结果(继承值本就等同),故零正确性风险。
    cur_rel = {
        r["logical_key"]: (r["kind"], r.get("metadata") or {})
        for r in live_repo._newest_visible(db, "kb_relationships", save_id, commit_id,
                                           ("logical_key", "kind", "metadata"))
    }
    cur_evt = {
        r["logical_key"]: r["summary"]
        for r in live_repo._newest_visible(db, "kb_events", save_id, commit_id, ("logical_key", "summary"))
    }
    cur_var = _vars(db, save_id, commit_id)

    # 1) 关系 → kb_relationships
    for npc, rel in (sd.get("relationships") or {}).items():
        kind = rel if isinstance(rel, str) else (rel.get("status") if isinstance(rel, dict) else str(rel))
        kind = str(kind or "neutral")
        meta = rel if isinstance(rel, dict) else {}
        lk = f"_player->{npc}"
        if cur_rel.get(lk) == (kind, meta):
            n_skip += 1
            continue
        live_repo.set_relationship(db, save_id, commit_id, lk,
                                   from_key="_player", to_key=str(npc), kind=kind,
                                   note="", metadata=meta)
        n_rel += 1

    # 2) 事实/事件 → kb_events(分别标 source 以便无损还原 memory.facts vs world.known_events)
    mem = dict(sd.get("memory") or {})
    world = dict(sd.get("world") or {})
    for i, f in enumerate(mem.get("facts") or []):
        lk = f"fact:{i}"
        summ = f if isinstance(f, str) else str(f)
        if cur_evt.get(lk) == summ:
            n_skip += 1
            continue
        live_repo.record_event(db, save_id, commit_id, lk, summary=summ,
                               story_time=str(world.get("time") or ""), metadata={"source": "memory.facts"})
        n_evt += 1
    for i, ev in enumerate(world.get("known_events") or []):
        lk = f"kevt:{i}"
        summ = ev if isinstance(ev, str) else str(ev)
        if cur_evt.get(lk) == summ:
            n_skip += 1
            continue
        live_repo.record_event(db, save_id, commit_id, lk, summary=summ,
                               story_time=str(world.get("time") or ""), metadata={"source": "world.known_events"})
        n_evt += 1

    # 3) 玩家自身 → kb_entities(_player)
    player = dict(sd.get("player") or {})
    if player.get("name"):
        live_repo.upsert_entity(db, save_id, commit_id, "_player", name=player["name"], type="player",
                                status="live", summary=player.get("background") or "",
                                attrs={"role": player.get("role"), "current_location": player.get("current_location")},
                                origin="player", metadata={})
        n_ent += 1

    # 4) 其余所有顶层键 → kb_worldline_vars(子树作为该键的 value;剥掉已路由/瞬态子键)
    for top, val in sd.items():
        if top in _DROP_TOP:
            continue
        if top == "memory":
            val = {k: v for k, v in (val or {}).items() if k not in _DROP_MEM and k != "facts"}
        elif top == "world":
            val = {k: v for k, v in (val or {}).items() if k != "known_events"}
        elif top == "worldline":
            val = {k: v for k, v in (val or {}).items() if k not in _DROP_WL}
        if top in cur_var and cur_var[top] == val:
            n_skip += 1
            continue
        _setvar(db, save_id, commit_id, top, val)
        n_var += 1

    return {"vars": n_var, "relationships": n_rel, "events": n_evt, "entities": n_ent, "skipped": n_skip}


# ── 读:从 KB 完整投影回等价 state ─────────────────────────────────────────────
def materialize(db, save_id: int, commit_id: int) -> dict[str, Any]:
    v = _vars(db, save_id, commit_id)
    state: dict[str, Any] = {k: val for k, val in v.items()}  # 顶层子树 vars 直接还原

    # 事件层 → 还原 memory.facts / world.known_events(按 source)
    evts = live_repo._newest_visible(db, "kb_events", save_id, commit_id, ("logical_key", "summary", "metadata"))
    facts, kevts = [], []
    for e in sorted(evts, key=lambda x: x["logical_key"]):
        src = (e.get("metadata") or {}).get("source")
        (facts if src == "memory.facts" or e["logical_key"].startswith("fact:") else kevts).append(e["summary"])
    state.setdefault("memory", {})
    if facts or "memory" in state:
        state["memory"]["facts"] = facts
    state.setdefault("world", {})
    if kevts or "world" in state:
        state["world"]["known_events"] = kevts

    # 关系层 → 还原 relationships(玩家视角)。metadata 非空=原 dict 关系;空=原字符串关系用 kind 还原。
    rels = live_repo._newest_visible(db, "kb_relationships", save_id, commit_id,
                                     ("logical_key", "from_key", "to_key", "kind", "metadata"))
    state["relationships"] = {r["to_key"]: (r["metadata"] if r.get("metadata") else r["kind"])
                              for r in rels if r["from_key"] == "_player"}

    # 历史 → messages(单一来源,不在 KB 重复;集成时还原成 state.data["history"] 供 GM 读)
    msgs = db.execute(
        "select role, content from messages where save_id = %s order by id", (save_id,)
    ).fetchall()
    state["history"] = [{"role": m["role"], "content": m["content"]} for m in msgs]
    state["_history_count"] = len(msgs)

    # 世界树实体(运行时可见;含 T0 seed)— 供 KB 查询,不属 blob 顶层
    state["_entities_visible"] = len(live_repo._newest_visible(db, "kb_entities", save_id, commit_id, ("logical_key",)))
    return state


def seed_full_t0(db, save_id: int, script_id: int, *, commit_id: int | None = None,
                 keys: list[str] | None = None) -> dict[str, Any]:
    """完整 T0 seed:剧本 canon 实体 → 存档 kb_entities(继承字段)。keys=定向/懒。"""
    return t0_seed.seed_save_kb_from_script(db, save_id, script_id, commit_id=commit_id, keys=keys)


# ── 史官:从本回合正文【确定性】维护结构化 KB(实体 encountered + 全部关系)──────────
# 根因修复:GM(flash)只读 KB、不调结构化写工具,LLM 提取器又偶发漏(漏了卡切尔关系)。
# → 不靠 LLM 调工具:确定性扫正文里出现的 canon 实体名/别名,凡出现的 character 一律确保
#   存档关系(_player→NPC),凡出现的实体标 encountered。这样「遇到谁」由代码缝保证,不漏。
def maintain_structured_kb(db, save_id: int, script_id: int, commit_id: int,
                           prose: str, player_name: str = "") -> dict[str, Any]:
    if not prose or not script_id:
        return {"entities": 0, "relationships": 0, "mentioned": []}
    canon = db.execute(
        "select logical_key, name, type, summary, aliases, identity, background "
        "from kb_canon_entities where script_id = %s", (int(script_id),)
    ).fetchall()
    # 关系只给「无歧义的人」:同名实体若还以非 character 类型出现(如 人造邪神/伟大意志 同时是
    # character 和 concept),多半是势力/概念/怪物而非人 → 不建人际关系(避免误判过捕)。
    _ambiguous = {c["name"] for c in canon if (c.get("type") or "") != "character"}
    # 已有关系目标(不覆盖 GM/提取器已设的更具体 kind)
    existing_rel = {
        r["to_key"] for r in live_repo._newest_visible(
            db, "kb_relationships", save_id, commit_id, ("logical_key", "to_key", "from_key"))
        if r["from_key"] == "_player"
    }
    n_ent = n_rel = 0
    seen_keys: set[str] = set()
    mentioned: list[str] = []
    for c in canon:
        names = [c["name"]] + [a for a in (c.get("aliases") or []) if a]
        names = [n for n in names if isinstance(n, str) and len(n) >= 2]
        if not any(n in prose for n in names):
            continue
        lk = c["logical_key"]
        if lk in seen_keys:
            continue
        seen_keys.add(lk)
        mentioned.append(c["name"])
        # 标 encountered(运行时实体态,COW 新行覆盖 T0)
        attrs = {"_encountered": True, "_encountered_commit": commit_id}
        for k in ("identity", "background"):
            if c.get(k):
                attrs[k] = c[k]
        live_repo.upsert_entity(db, save_id, commit_id, lk, name=c["name"],
                                type=(c.get("type") or "entity"), status="live",
                                summary=(c.get("summary") or ""), attrs=attrs,
                                origin="recorder", metadata={"source": "prose_mention"})
        n_ent += 1
        # character(非玩家本人、非歧义概念/势力)且尚无关系 → 确定性补一条「初识」
        if (c.get("type") or "") == "character" and c["name"] != (player_name or "") \
                and c["name"] not in _ambiguous and c["name"] not in existing_rel:
            live_repo.set_relationship(db, save_id, commit_id, f"_player->{c['name']}",
                                       from_key="_player", to_key=c["name"], kind="初识",
                                       note="本回合正文中出现并交互", metadata={})
            existing_rel.add(c["name"])
            n_rel += 1
    return {"entities": n_ent, "relationships": n_rel, "mentioned": mentioned}
