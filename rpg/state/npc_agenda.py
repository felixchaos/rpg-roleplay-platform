"""state/npc_agenda.py — NPC 议程 v0（活世界·柱子3）确定性核心。

设计文档: docs/design/npc_agenda_v0.md

目标: NPC 从「布景」变「角色」——每个活跃 NPC 带一份持续演化的议程（此刻想要
什么/对玩家什么态度），GM 生成时看得见，跨回合连续。改道背景：原方案（人物卡
motivation 接线）被证实是「写了没存」的死字段（生产 150 卡采样零持久化），不
基于死字段建设；本案做**存档级动态议程状态**，与静态人物卡正交（卡=出厂设定，
议程=当下活状态）。

原则（与设计文档一致，柱子1/2 同一套纪律）：
- 存储/上限/去重/剪枝/注入 = 确定性代码；LLM 只写议程内容。
- 本模块两个纯函数只操作 state.data dict，不做任何 IO（方便单测、方便
  GameState mixin 薄封装调用）。

状态结构 (state.data["npc_agendas"]: dict[str, dict])，键=NPC 名（与
relationships 键同源）：
    {
      "雷纳德": {
        "goal": "查清东林兽伤的真相，保住村子的猎场",
        "stance": "对玩家信任但保留观察",
        "updated_turn": 15,
      }
    }
"""
from __future__ import annotations

from typing import Any

from state.parsers import _clean_item

# 上限：防 LLM 刷屏 / 无限膨胀。超出时剪掉 updated_turn 最旧的（活跃度自然淘汰）。
MAX_AGENDAS = 12

# 单条 goal / stance 各截断到这个长度（验收截断）。
MAX_FIELD_LEN = 60

# 默认注入条数上限（agendas_for_injection）。
DEFAULT_INJECTION_LIMIT = 6


def _agendas(state_data: dict) -> dict:
    """取 / 建 state_data["npc_agendas"] dict 引用（原地可变）。"""
    agendas = state_data.setdefault("npc_agendas", {})
    if not isinstance(agendas, dict):
        agendas = {}
        state_data["npc_agendas"] = agendas
    return agendas


def _known_npc_names(state_data: dict) -> set[str]:
    """名字白名单：relationships 键 ∪ active_entities 的 name 字段（防 LLM 发明路人）。"""
    names: set[str] = set()
    relationships = state_data.get("relationships")
    if isinstance(relationships, dict):
        names.update(str(k) for k in relationships.keys() if k)
    active_entities = state_data.get("active_entities")
    if isinstance(active_entities, list):
        for e in active_entities:
            if isinstance(e, dict):
                name = e.get("name")
                if name:
                    names.add(str(name))
    return names


def upsert_agenda(
    state_data: dict,
    *,
    name: str,
    goal: str | None = None,
    stance: str | None = None,
    turn: int | None = None,
) -> tuple[bool, str]:
    """登记/更新一条 NPC 议程。纯函数，直接改 state_data，不做 IO。

    返回 (ok, message)。message 是给 updates 列表用的中文说明（成功/拒绝原因）。

    校验顺序（设计文档 §1/§3 用例口径）：
      1. name 必填，且必须已存在于 relationships ∪ active_entities（防 LLM 发明路人）
      2. goal / stance 至少给一个（部分更新合并，不整条覆盖）
      3. 单条截断到 MAX_FIELD_LEN
      4. 上限 MAX_AGENDAS 条 → 剪掉 updated_turn 最旧的（新条目自身不会被自己挤掉）
    """
    name = _clean_item(name or "")
    if not name:
        return False, "议程登记忽略（缺 name）"

    goal_clean = _clean_item(goal or "")[:MAX_FIELD_LEN] if goal else ""
    stance_clean = _clean_item(stance or "")[:MAX_FIELD_LEN] if stance else ""
    if not goal_clean and not stance_clean:
        return False, f"议程登记忽略（{name} 缺 goal 与 stance，至少给一个）"

    known = _known_npc_names(state_data)
    if name not in known:
        return False, f"议程登记拒绝（{name} 不在场上已知角色名单，防臆造路人）"

    agendas = _agendas(state_data)
    turn_int = int(turn if turn is not None else state_data.get("turn", 0) or 0)

    existing = agendas.get(name)
    if isinstance(existing, dict):
        # 部分更新合并：只给了 goal 就不覆盖已有 stance，反之亦然。
        entry: dict[str, Any] = dict(existing)
        if goal_clean:
            entry["goal"] = goal_clean
        if stance_clean:
            entry["stance"] = stance_clean
        entry["updated_turn"] = turn_int
    else:
        entry = {
            "goal": goal_clean,
            "stance": stance_clean,
            "updated_turn": turn_int,
        }

    agendas[name] = entry

    # 上限剪枝：超出 MAX_AGENDAS 时剪掉 updated_turn 最旧的（自然淘汰非活跃 NPC）。
    if len(agendas) > MAX_AGENDAS:
        oldest_name = min(agendas, key=lambda k: agendas[k].get("updated_turn", 0))
        if oldest_name != name:
            del agendas[oldest_name]
        else:
            # 极端情况：新写入的这条恰好也是最旧的（如批量倒序写入相同 turn）——
            # 不淘汰自己，改淘汰次旧的，保证本次写入始终成功落地。
            candidates = [k for k in agendas if k != name]
            if candidates:
                second_oldest = min(candidates, key=lambda k: agendas[k].get("updated_turn", 0))
                del agendas[second_oldest]

    return True, f"议程更新：{name}（{goal_clean or stance_clean}）"


def agendas_for_injection(
    state_data: dict,
    relationships_keys: Any = None,
    limit: int = DEFAULT_INJECTION_LIMIT,
) -> list[dict]:
    """本回合注入用的议程集合：只取与当前在场相关的（键在 relationships_keys 里的
    优先），按 updated_turn 降序，最多 limit 条。

    relationships_keys 缺省时用 state_data["relationships"] 的键。不做扫描/写入，
    只读当前快照。
    """
    agendas = _agendas(state_data)
    if not agendas:
        return []

    if relationships_keys is None:
        rel = state_data.get("relationships")
        relationships_keys = rel.keys() if isinstance(rel, dict) else []
    known_keys = set(relationships_keys or [])

    entries = []
    for name, entry in agendas.items():
        if not isinstance(entry, dict):
            continue
        entries.append((name, entry))

    # 排序：在场相关的（键在 relationships 里）优先，同组内按 updated_turn 降序。
    entries.sort(
        key=lambda item: (
            0 if item[0] in known_keys else 1,
            -int(item[1].get("updated_turn", 0) or 0),
        )
    )

    out: list[dict] = []
    for name, entry in entries[:limit]:
        out.append({
            "name": name,
            "goal": entry.get("goal", ""),
            "stance": entry.get("stance", ""),
            "updated_turn": entry.get("updated_turn", 0),
        })
    return out


__all__ = [
    "MAX_AGENDAS",
    "MAX_FIELD_LEN",
    "DEFAULT_INJECTION_LIMIT",
    "upsert_agenda",
    "agendas_for_injection",
]
