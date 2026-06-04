"""inventory.py — 物品授予 (grant) 与房间 loot 拾取 (pickup)。

与 consume.py 对称：consume 是"减"，这里是"加"。两者都经 RulesEngine /
State Gate 落 canonical player_character.inventory，再派生 memory.resources。

授予路径（弥补"有消耗无授予"的结构缺口）：
  · grant_item_action   —— GM 叙事"你获得 X"经规则引擎写 canonical inventory
  · pickup_loot_action  —— 把当前房间 loot 项规范化后 grant 进背包，并标记已拾取防重刷
  · parse_pickup_intent —— 从玩家文本确定性解析拾取意图（不依赖 LLM）
"""
from __future__ import annotations

import re
from datetime import datetime

import modules as module_registry

# 拾取动词。刻意与 consume 的"拿出/拿来/点亮"等错开，避免同义冲突。
_PICKUP_VERBS_CN = ("捡起", "捡走", "捡", "拾起", "拾取", "拾", "拿走", "取走",
                    "收起", "收好", "带走", "装进背包", "放进背包", "塞进背包")
_PICKUP_VERBS_EN = ("pick up", "pickup", "pick", "take", "grab", "loot", "collect")


def _current_room(state) -> dict:
    scene = state.data.get("scene") or {}
    return scene.get("current_room") or {}


def _module_loot_catalog(module_id: str) -> dict[str, dict]:
    """加载模组 loot.json 目录，按 id 索引（提供 kind / name / description 兜底）。"""
    if not module_id:
        return {}
    try:
        bundle = module_registry.load_module(module_id)
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for entry in (bundle.get("loot") or []):
        eid = str(entry.get("id") or "").strip()
        if eid:
            out[eid] = entry
    return out


def grant_item_action(state, item_id: str, name: str | None = None,
                      qty: int = 1, kind: str = "misc", reason: str = "") -> dict:
    """RulesEngine grant_item 入口（chat 流程 / /api/rules/action 都用）。

    返回 {ok, result, dice_log_entry?, error}。
    成功时 player_character.inventory 已累加 / 新建，memory.resources 已同步。
    """
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"ok": False, "error": "缺少 item_id"}
    try:
        qty = int(qty or 1)
    except (TypeError, ValueError):
        qty = 1
    result = state.grant_inventory_item(item_id, name=name, qty=qty, kind=kind)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "grant_item 失败"}
    pc = state.data.get("player_character") or {}
    granted = result.get("granted")
    entry = {
        "kind": "grant_item",
        "actor": pc.get("name") or "player",
        "target": result.get("item_name") or result.get("item_id"),
        "expression": "",
        "rolls": [],
        "modifier": 0,
        "total": granted,
        "dc": None,
        "success": True,
        "reason": reason or f"获得 {result.get('item_name')} ×{granted}",
        "ts": datetime.now().isoformat(timespec="seconds"),
        "extra": {
            "item_id": result.get("item_id"),
            "qty_before": result.get("qty_before"),
            "qty_after": result.get("qty_after"),
            "created": result.get("created"),
        },
    }
    state.append_dice_log(entry)
    return {
        "ok": True,
        "result": {
            "kind": "grant_item",
            "actor": entry["actor"],
            "target": entry["target"],
            "success": True,
            "gm_facts": [
                f"{entry['actor']} 获得 {result.get('item_name')} ×{granted}"
                f"（背包共 {result.get('qty_after')}）。"
            ],
            "extra": entry["extra"],
        },
        "dice_log_entry": entry,
    }


def pickup_loot_action(state, item_id: str, location_id: str | None = None,
                       reason: str = "") -> dict:
    """把当前房间 loot 项规范化后 grant 进 inventory，并标记该 loot 已拾取防重刷。

    约束：
      · 只能拾取玩家当前所在房间的 loot（location_id 给定时必须等于当前房间）。
      · 命中 loot 后写 scene["taken_loot"]（持久），并从 current_room.loot 即时移除；
        module_ops 在重建房间 snapshot 时按 taken_loot 过滤，防止重新进房刷新可拾取。
    """
    scene = state.data.setdefault("scene", {})
    module_id = scene.get("module_id")
    if not module_id:
        return {"ok": False, "error": "未加载模组，无法拾取"}
    cur_id = scene.get("location_id")
    if location_id and str(location_id) != str(cur_id):
        return {"ok": False,
                "error": f"只能拾取当前房间 {cur_id!r} 的物品（请求的是 {location_id!r}）"}
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"ok": False, "error": "缺少 item_id"}

    room = _current_room(state)
    loot = list(room.get("loot") or [])
    # 按 item_id 精确命中房间 loot
    target = next((item for item in loot if str(item.get("item_id") or item.get("id") or "") == item_id), None)
    if target is None:
        return {"ok": False,
                "error": f"当前房间没有可拾取的 {item_id!r}",
                "available": [str(item.get("item_id") or item.get("id") or "") for item in loot]}

    taken = scene.setdefault("taken_loot", [])
    if item_id in taken:
        return {"ok": False, "error": f"{item_id} 已被拾取"}

    catalog = _module_loot_catalog(module_id)
    cat = catalog.get(item_id) or {}
    name = target.get("name") or cat.get("name") or item_id
    kind = target.get("kind") or cat.get("kind") or "misc"
    try:
        qty = int(target.get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1

    grant = grant_item_action(
        state, item_id=item_id, name=name, qty=qty, kind=kind,
        reason=reason or f"从 {room.get('name') or cur_id} 拾取 {name}",
    )
    if not grant.get("ok"):
        return grant

    # 标记已拾取（持久）+ 即时从当前房间 snapshot 移除
    taken.append(item_id)
    room["loot"] = [item for item in loot if str(item.get("item_id") or item.get("id") or "") != item_id]

    grant.setdefault("result", {})["pickup"] = {
        "item_id": item_id, "name": name, "location_id": cur_id,
    }
    return grant


def parse_pickup_intent(text: str, state) -> list[dict]:
    """从玩家文本里确定性解析"拾取当前房间 loot"的意图。

    返回 list of {item_id, name, qty, matched}。
    匹配范围严格限定为当前房间真实存在、且尚未被拾取的 loot 项，
    避免凭空捡到不存在的物品（与 parse_consume_intent 限定 inventory 同思路）。
    """
    if not text:
        return []
    room = _current_room(state)
    loot = list(room.get("loot") or [])
    if not loot:
        return []
    scene = state.data.get("scene") or {}
    taken = set(scene.get("taken_loot") or [])

    text_str = str(text)
    out: list[dict] = []
    seen: set[str] = set()

    all_verbs = list(_PICKUP_VERBS_CN) + list(_PICKUP_VERBS_EN)
    verb_pattern = "|".join(re.escape(v) for v in all_verbs)

    # 可拾取目标：item_id / name，按名称长度降序，长名优先（防短名遮蔽）
    targets: list[tuple[str, str]] = []  # (match_text_lower, item_id)
    for item in loot:
        iid = str(item.get("item_id") or item.get("id") or "").strip()
        if not iid or iid in taken:
            continue
        nm = str(item.get("name") or "").strip()
        targets.append((iid.lower(), iid))
        if nm:
            targets.append((nm.lower(), iid))
    targets.sort(key=lambda t: -len(t[0]))

    for verb_match in re.finditer(verb_pattern, text_str, re.IGNORECASE):
        window = text_str[verb_match.end(): verb_match.end() + 24].lower()
        hit_id = None
        best_off = None
        for match_low, iid in targets:
            if len(match_low) < 2:
                continue  # 不靠单字匹配
            idx = window.find(match_low)
            if idx >= 0 and (best_off is None or idx < best_off):
                best_off = idx
                hit_id = iid
        if not hit_id or hit_id in seen:
            continue
        seen.add(hit_id)
        loot_entry = next(
            (item for item in loot if str(item.get("item_id") or item.get("id") or "") == hit_id), {}
        )
        try:
            qty = int(loot_entry.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        out.append({
            "item_id": hit_id,
            "name": loot_entry.get("name") or hit_id,
            "qty": qty,
            "matched": text_str[verb_match.start(): verb_match.end() + 24],
        })
    return out
