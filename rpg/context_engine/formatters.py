"""context_engine.formatters — 角色卡 / 世界书渲染函数."""
from __future__ import annotations

import re
from typing import Any

from context_engine._utils import _preview
from context_engine.loaders import _load_worldbook_db
from config.glossary import load_glossary


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
        # Worldbook entries use glossary for IP-specific titles/keys.
        # Edit rpg/config/novel_glossary.json (gitignored) to change names.
        _wb_from_glossary("berlin_pressure", 96,
            f"{_gloss('worldbook_entries.berlin_pressure.title')}",
            _gloss_keys("berlin_pressure"),
            f"{_gloss('worldbook_entries.berlin_pressure.title').replace(' ', '')}处于战时前夕："
            f"{current_berlin.get('atmosphere', '')} 风险等级：{current_berlin.get('risk_level', '')}。在场势力包括："
            + "；".join(current_berlin.get("power_presence", []))),
        _wb_from_glossary("toulouse", 88,
            _gloss("worldbook_entries.toulouse.title"),
            _gloss_keys("toulouse"),
            world.get("current_situation", "")),
        _wb_from_glossary("realm_main_entry", 86,
            _gloss("worldbook_entries.realm_main_entry.title"),
            _gloss_keys("realm_main_entry"),
            f"{factions.get(_gloss('world_terms.realm_main'), '')}。"
            f"{_gloss('world_terms.tech_keyword')}：{concepts.get(_gloss('world_terms.tech_keyword'), '')}。"
            f"{_gloss('faction_map_keys.forge_experiment')}：{concepts.get(_gloss('faction_map_keys.forge_experiment'), '')}"),
        _wb_from_glossary("earth_fed", 82,
            _gloss("worldbook_entries.earth_fed.title"),
            _gloss_keys("earth_fed"),
            f"大西洋方面：{factions.get('地联大西洋方面', '')}。太平洋方面：{factions.get('地联太平洋方面', '')}。"),
        _wb_from_glossary("intel_net", 80,
            _gloss("worldbook_entries.intel_net.title"),
            _gloss_keys("intel_net"),
            factions.get(_gloss("world_terms.intel_network"), "")),
        _wb_from_glossary("person2_branch", 78,
            _gloss("worldbook_entries.person2_branch.title"),
            _gloss_keys("person2_branch"),
            factions.get("特洛耶德家族欧洲分支", "")),
        _wb_from_glossary("power_scale", 76,
            _gloss("worldbook_entries.power_scale.title"),
            _gloss_keys("power_scale"),
            f"{_gloss('world_terms.realm_main')}战力：{'、'.join(power.get('visar_empire', {}).get('levels', []))}。"
            f"地联战力：{'、'.join(power.get('earth_federation', {}).get('levels', []))}。"
            f"玩家的{_gloss('world_terms.magic_system')}∞是世界规则之外变量，但仍需要通过剧情摸索控制方式。"),
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


# --- Glossary-aware helpers (IP-name indirection) ---

def _gloss(key: str, default: str = "") -> str:
    """Dot-path accessor into novel_glossary; falls back to default."""
    from config.glossary import get_term
    return get_term(key, default)


def _gloss_keys(entry_id: str) -> list[str]:
    """Return the keys list for a named worldbook entry from the glossary."""
    from config.glossary import load_glossary as _load_g
    g = _load_g()
    return list(g.get("worldbook_entries", {}).get(entry_id, {}).get("keys", []))


def _wb_from_glossary(entry_id: str, priority: int, title: str,
                      keys: list[str], text: str) -> dict[str, Any]:
    """Like _wb but title/keys come from the glossary-backed helpers."""
    return _wb(entry_id, title, keys, priority, text)


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
