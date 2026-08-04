"""context_engine.formatters — 角色卡 / 世界书渲染函数."""
from __future__ import annotations

import re
from typing import Any

from context_engine._utils import _preview
from context_engine.loaders import _load_worldbook_db


def _player_card(state, chars: dict[str, Any]) -> dict[str, str]:
    player = state.data["player"]
    name = player.get("name") or "玩家"
    # 任意 user 的 player card 都应按 player.name 命中;之前回落硬编码「杭雁菱」是
    # 老《我蕾穆丽娜不爱你》存档遗留 default,跨用户没意义,移除。
    card = chars.get(name) or {}
    # !! 安全边界 !! character_cards.secrets 字段是玩家自填的"我自己知道但 NPC/GM
    # 不应知道"信息(如"穿越者"身份)。**绝不能注入 GM context**,否则 GM 拿到
    # 后可能在叙事里让 NPC 说出"异界来客"这种泄露玩家秘密的话(实际泄露案例)。
    # state.player_private.* 早有同等隔离,但 character_cards.secrets 是另一条
    # 入口路径,之前漏修。这里显式不读 secrets 字段。
    # sample_dialogue 也包含玩家私人语气样本,GM 可借此学语气,但不应该把"卡里
    # 的对白原话"当成 NPC 已知信息 — 暂时保留(GM 一般只学风格,不会复述),
    # 后续可再加 strip。
    # !! 秘密剥离 !! player.background 是玩家可写字段(/set、老存档可埋"我是穿越者/原著她会
    # 死"这类秘密段),必须像 short_summary(core.py:726)一样剥掉 `## 秘密/隐藏/元知识` 段
    # 再注入。原来这里直接用 raw player.background 当"当前状态" → 与 short_summary 不对称,
    # 把上面注释声称已隔离的玩家秘密又原文放给 GM(实际泄漏路径)。card.* 来自 canon NPC 卡是干净的。
    from state.core import _strip_secret_sections
    _bg = _strip_secret_sections(player.get("background") or "")
    text = _format_card(name, {
        "identity": player.get("role") or card.get("identity", ""),
        "appearance": card.get("appearance", ""),
        "personality": card.get("personality", ""),
        "speech_style": card.get("speech_style", ""),
        "current_status": _bg or card.get("current_status", ""),
        # secrets 显式不注入 — 玩家秘密物理隔离
        "sample_dialogue": card.get("sample_dialogue", []),
    })
    return {"name": name, "text": text}


def _norm_card_name(s: Any) -> str:
    # #6 代入去重: 名字归一(去首尾空格/大小写/常见人名分隔点),判定"是否玩家本人"。
    return str(s or "").strip().casefold().replace(" ", "").replace("·", "").replace("・", "")


def _active_character_cards(scan_text: str, chars: dict[str, Any], player_name: str,
                            player_aliases: list[str] | None = None) -> list[dict[str, Any]]:
    # #6 代入去重: 玩家名 + player.aliases 归一后都算"玩家本人",对应同名 NPC 卡不再
    # 作为独立角色注入 — 否则玩家代入原作角色时 GM 会同时看到玩家与同名 NPC = 两个相同角色。
    skip_keys = {_norm_card_name(player_name)}
    for _a in (player_aliases or []):
        skip_keys.add(_norm_card_name(_a))
    skip_keys.discard("")
    active = []
    for name, card in chars.items():
        card_keys = {_norm_card_name(name)}
        for _a in (card.get("aliases") or []):
            card_keys.add(_norm_card_name(_a))
        if skip_keys & card_keys:
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
            "avatar_path": card.get("avatar_path", ""),
        })
    active.sort(key=lambda x: x["priority"], reverse=True)  # type: ignore[return-value]
    return active[:4]


def _active_worldbook(
    scan_text: str,
    world: dict[str, Any],
    state,
    script_id: int | None = None,
    book_id: int | None = None,
    save_id: int | None = None,
    mode: str = "omniscient",
) -> list[dict[str, Any]]:
    # 先取 DB worldbook 条目；为空时回退 JSON 内置条目
    # P4(S4):save_id+mode 透传给 _load_worldbook_db 做前沿门控(flag off 时无门控,行为不变)。
    entries: list[dict[str, Any]] = []
    if script_id or book_id:
        try:
            entries = _load_worldbook_db(script_id=script_id, book_id=book_id,
                                         save_id=save_id, mode=mode)
        except Exception:
            entries = []
    if not entries:
        entries = _worldbook_entries(world, state)
    active = []
    # 按 entry 唯一 id 去重(非 title):DB 条目 id 是 db_{id}、内置条目是 entry_id,均唯一。
    # 原来按 title 去重有 bug —— LLM 抽取的世界书常出现两条同 title(如都叫"势力""背景设定")
    # 或空 title(""),一旦其中一条激活,backfill 会把所有同 title 的高优先条目排除,
    # 使 keys 为空、只能靠基线出场的核心设定漏注入("世界书是摆设"残留变体)。
    seen_ids: set[str] = set()
    for entry in entries:
        matched = [key for key in entry["keys"] if key and key in scan_text]
        if entry.get("regex"):
            # regex_keys 是 LLM 抽取 / 用户编写,写入时未做 re.compile 校验。畸形正则
            # 会让 re.search 抛 re.error;若不隔离,会冒泡炸掉整个世界书激活 → 上下文组装
            # → 整轮 GM。逐条 try,坏正则只跳过该 pattern,不影响其余条目与关键词激活。
            for pattern in entry["regex"]:
                try:
                    if pattern and re.search(pattern, scan_text):
                        matched.append(pattern)
                except re.error:
                    continue
        if not matched:
            continue
        entry = dict(entry)
        entry["matched"] = matched[:5]
        entry["score"] = entry.get("priority", 50) + len(matched) * 6
        active.append(entry)
        seen_ids.add(str(entry.get("id", "")))
    active.sort(key=lambda x: (x["score"], x.get("priority", 0)), reverse=True)
    # 常驻基线:纯关键词门控会让 recent text 不含 key 时整个世界书都不注入,
    # GM 永远拿不到核心世界设定(用户报"世界书完全是摆设")。命中不足 MIN 时,
    # 用最高 priority 的条目补足,保证每轮至少注入核心设定。
    # 注:实测部分剧本(如 script 11)的 DB 世界书条目 keys 为空,关键词激活彻底失效,
    # 这些条目只能靠常驻基线出现 → 基线给厚一点(8 条),配合 10000 字层预算。
    MIN_CONSTANT = 8
    if len(active) < MIN_CONSTANT:
        backfill = [
            e for e in entries
            if str(e.get("id", "")) not in seen_ids
        ]
        backfill.sort(key=lambda x: x.get("priority", 50), reverse=True)
        for e in backfill:
            if len(active) >= MIN_CONSTANT:
                break
            e = dict(e)
            e["matched"] = ["(常驻设定)"]
            e["score"] = int(e.get("priority", 50))
            active.append(e)
            seen_ids.add(str(e.get("id", "")))
    return active[:10]


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
        "avatar_path": card.get("avatar_path", ""),
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
