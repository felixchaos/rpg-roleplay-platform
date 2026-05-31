"""context_engine.formatters — 角色卡 / 世界书渲染函数."""
from __future__ import annotations

import re
from typing import Any

from context_engine._utils import _preview
from context_engine.loaders import _load_worldbook_db


def _player_card(state, chars: dict[str, Any]) -> dict[str, str]:
    player = state.data["player"]
    name = player.get("name") or "玩家"
    card = chars.get(name) or chars.get("杭雁菱") or {}
    text = _format_card(name, {
        "identity": player.get("role") or card.get("identity", ""),
        "appearance": card.get("appearance", ""),
        "personality": card.get("personality", ""),
        "speech_style": card.get("speech_style", ""),
        "current_status": player.get("background") or card.get("current_status", ""),
        "secrets": card.get("secrets", ""),
        "sample_dialogue": card.get("sample_dialogue", []),
    })
    return {"name": name, "text": text}


def _active_character_cards(scan_text: str, chars: dict[str, Any], player_name: str) -> list[dict[str, Any]]:
    active = []
    for name, card in chars.items():
        if name == player_name:
            continue
        aliases = [name, *(card.get("aliases") or [])]
        matched = [alias for alias in aliases if alias and alias in scan_text]
        if not matched:
            continue
        active.append({
            "name": name,
            "matched": matched[:4],
            "priority": 100 + len(matched) * 8,
            "text": _format_card(name, card),
        })
    active.sort(key=lambda x: x["priority"], reverse=True)  # type: ignore[return-value]
    return active[:4]


def _active_worldbook(
    scan_text: str,
    world: dict[str, Any],
    state,
    script_id: int | None = None,
    book_id: int | None = None,
) -> list[dict[str, Any]]:
    # 先取 DB worldbook 条目；为空时回退 JSON 内置条目
    entries: list[dict[str, Any]] = []
    if script_id or book_id:
        try:
            entries = _load_worldbook_db(script_id=script_id, book_id=book_id)
        except Exception:
            entries = []
    if not entries:
        entries = _worldbook_entries(world, state)
    active = []
    for entry in entries:
        matched = [key for key in entry["keys"] if key and key in scan_text]
        if entry.get("regex"):
            matched.extend(pattern for pattern in entry["regex"] if re.search(pattern, scan_text))
        if not matched:
            continue
        entry = dict(entry)
        entry["matched"] = matched[:5]
        entry["score"] = entry.get("priority", 50) + len(matched) * 6
        active.append(entry)
    active.sort(key=lambda x: (x["score"], x.get("priority", 0)), reverse=True)
    return active[:6]


def _worldbook_entries(world: dict[str, Any], state) -> list[dict[str, Any]]:
    concepts = world.get("key_concepts", {})
    factions = world.get("key_factions", {})
    power = world.get("power_system", {})
    current_berlin = world.get("current_berlin", {})
    return [
        _wb("berlin_pressure", "柏林战时暗流", ["柏林", "战役", "大西洋", "军事顾问"], 96,
            f"柏林处于战时前夕：{current_berlin.get('atmosphere', '')} 风险等级：{current_berlin.get('risk_level', '')}。在场势力包括："
            + "；".join(current_berlin.get("power_presence", []))),
        _wb("toulouse", "图卢兹失守", ["图卢兹", "失守", "地联溃败", "反扑"], 88,
            world.get("current_situation", "")),
        _wb("visar", "薇瑟帝国与 Aldnoal", ["薇瑟", "帝国", "aldnoal", "Aldnoah", "烈锋"], 86,
            f"{factions.get('薇瑟帝国', '')}。aldnoal：{concepts.get('aldnoal', '')}。烈锋实验：{concepts.get('烈锋实验', '')}"),
        _wb("earth_federation", "地联势力差异", ["地联", "太平洋方面", "大西洋方面", "伊奈帆"], 82,
            f"大西洋方面：{factions.get('地联大西洋方面', '')}。太平洋方面：{factions.get('地联太平洋方面', '')}。"),
        _wb("snake_network", "蛇信情报网", ["蛇信", "薛克", "监视"], 80,
            factions.get("蛇信", "")),
        _wb("troyard_branch", "特洛耶德欧洲分支", ["特洛耶德", "赫克勒斯", "旧楼", "烈锋实验"], 78,
            factions.get("特洛耶德家族欧洲分支", "")),
        _wb("power_scale", "战力体系", ["魔力", "渊戮", "顶王", "烈锋", "甲胄骑士"], 76,
            f"薇瑟战力：{'、'.join(power.get('visar_empire', {}).get('levels', []))}。"
            f"地联战力：{'、'.join(power.get('earth_federation', {}).get('levels', []))}。"
            "玩家的魔力∞是世界规则之外变量，但仍需要通过剧情摸索控制方式。"),
        _wb("player_resources", "玩家当前资源", ["资源", "特殊小队", "整备班", "甲胄骑士", "权限"], 90,
            "；".join(state.data.get("memory", {}).get("resources", [])) or "暂无明确可支配资源。"),
    ]


def _wb(entry_id: str, title: str, keys: list[str], priority: int, text: str) -> dict[str, Any]:
    return {
        "id": entry_id,
        "title": title,
        "keys": keys,
        "regex": [],
        "priority": priority,
        "text": text,
    }


def _format_card(name: str, card: dict[str, Any]) -> str:
    """渲染 NPC 卡块给 GM prompt。v28:
       - 名字行附 full_name(如有,欧美全名);
       - 在 secrets 前插一行 `背景` = card.background(角色出场前关键经历/动机)。
    """
    sample = "；".join((card.get("sample_dialogue") or [])[:3])
    full_name = (card.get("full_name") or "").strip()
    header = f"【{name}】" if not full_name or full_name == name else f"【{name} / {full_name}】"
    lines = [
        header,
        f"身份：{card.get('identity') or '未知'}",
        f"外貌：{card.get('appearance') or '未记录'}",
        f"性格：{card.get('personality') or '未记录'}",
        f"说话风格：{card.get('speech_style') or '未记录'}",
        f"当前状态：{card.get('current_status') or '未记录'}",
    ]
    # v28: 背景(出场前关键经历 / 动机)非空才输出,避免空字段占行噪声
    bg = card.get("background") or ""
    if bg:
        lines.append(f"背景：{bg}")
    if card.get("secrets"):
        lines.append(f"隐藏信息：{card.get('secrets')}")
    if sample:
        lines.append(f"台词示例：{sample}")
    return "\n".join(lines)


def _strip_card_text(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": card["name"],
        "matched": card.get("matched", []),
        "priority": card.get("priority", 0),
        "preview": _preview(card.get("text", "")),
    }


def _strip_worldbook_text(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "title": entry["title"],
        "matched": entry.get("matched", []),
        "priority": entry.get("priority", 0),
        "score": entry.get("score", 0),
        "preview": _preview(entry.get("text", "")),
    }
