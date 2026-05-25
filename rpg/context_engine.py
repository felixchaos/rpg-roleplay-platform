"""
context_engine.py - SillyTavern-style context assembly for the RPG.

The current app is still a local single-save game, but this module keeps
character cards, worldbook entries, RAG snippets, state, and recent chat as
separate layers so the same pipeline can later be backed by Postgres/pgvector.
"""
from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path
from typing import Any
from timeline_index import timeline_filter_for_label

BASE = Path(__file__).parent
CHAR_IDX = BASE / "indexes" / "characters.json"
WORLD_IDX = BASE / "indexes" / "world.json"

MAX_LAYER_CHARS = {
    "rules": 1800,
    "agent_runtime": 1200,
    "timeline": 1400,
    "worldline": 1800,
    "context_agent": 1200,
    "player_card": 1300,
    "npc_cards": 1800,
    "worldbook": 2200,
    "rag": 2200,
    "state": 2200,
    "state_schema": 1600,   # task 59：字段 schema + 已知 NPC enum，前 20 个
    "write_results": 800,   # task 54：上轮标签结果反馈，简洁即可
    "fact_groups": 1600,    # task 76：canon / runtime / user_constraint 分组渲染
    "hypotheses": 700,      # task 75：未确认推测，最多 8 条 short label
    "candidate_actions": 800,  # task 82：curator 列的 2-5 个候选动作 anchor
    "recent_chat": 2200,
    "user_input": 900,
}


def _state_schema_layer(state, chars: dict[str, Any]) -> str:
    """task 59：把 state 字段的真实 schema + 当前 enum 候选喂给 LLM。

    痛点：之前 LLM 不知道
    - player.role 值是单字符串还是 {name, tier} 结构 → 瞎试
    - relationships.角色名 中"角色名"应是已存 NPC 还是任意新名字 → 不一致
    - memory.resources 是 list 但能写单值 / 多值 → 反复尝试

    本层给出明确 schema + 当前已知值，让 LLM 输出强类型。
    """
    p = state.data.get("player", {}) or {}
    w = state.data.get("world", {}) or {}
    m = state.data.get("memory", {}) or {}
    rels = state.data.get("relationships", {}) or {}
    worldline = state.data.get("worldline", {}) or {}

    # 已知人物列表（玩家 + 当前 relationships + 角色卡库）
    known_npcs = sorted(set(list(rels.keys()) + [name for name in chars.keys() if name != p.get("name")]))
    known_npcs_str = "、".join(known_npcs[:20]) if known_npcs else "（尚未识别任何 NPC）"

    # 用户变量当前值
    user_vars = (worldline.get("user_variables") or {})
    var_names = list(user_vars.keys())[:10]

    lines = [
        "## 状态字段 schema（写入时严格遵循）",
        "",
        "**player.\\*** — 单字符串类型字段：",
        f"- `player.name`: 字符串。当前 = {p.get('name', '') or '(空)'}",
        f"- `player.role`: 字符串。简短角色定位（如「史官」「侦探」「医师」），不是结构体。当前 = {p.get('role', '') or '(空)'}",
        f"- `player.background`: 字符串。一两句话背景。当前长度 = {len(p.get('background', ''))} 字符",
        f"- `player.current_location`: 字符串。简短地名（如「北港·灯塔下」「柏林·街头」）。当前 = {p.get('current_location', '') or '(空)'}",
        "",
        "**world.\\*** — 时间 / 已知事件：",
        f"- `world.time`: 字符串。中式（如「申时三刻」）或西式（如「1937年4月12日傍晚」）均可，本档要一致。当前 = {w.get('time', '') or '(空)'}",
        f"- `world.weather`: 字符串可选。当前 = {w.get('weather', '') or '(空)'}",
        "- `world.known_events`: 字符串数组。append 用【状态追加】或 JSON op=append。",
        "- `world.timeline.current_phase`: 字符串。剧情阶段名。",
        "",
        "**relationships.<角色名>** — 字符串值（关系状态：信任/戒备/敌意/亲近/中立 等）：",
        f"- 当前已识别角色：{known_npcs_str}",
        "- **优先使用已存在角色名**；新角色必须先在 GM 叙事里引入，再写 relationships。",
        "- 错误写法：`relationships = {name: 张三, tier: 5}` （不是对象，是 path）",
        "- 正确写法：`relationships.张三 = 信任` （path 含角色名，值是字符串）",
        "",
        "**memory.\\*** — 列表 vs 标量：",
        "- 列表字段（append 用【状态追加】或 JSON op=append）：`memory.resources` / `memory.abilities` / `memory.facts` / `memory.pinned` / `memory.notes`",
        "- 标量字段（直接覆盖）：`memory.main_quest` / `memory.current_objective` / `memory.mode`",
        "- 列表内每项是字符串。",
        "",
        "**worldline.user_variables.<变量名>** — 玩家用 /set 创建的硬约束变量。",
        f"- 当前已定义变量：{('、'.join(var_names) if var_names else '（无）')}",
        "- 你可以读，但禁止主动新建（属于玩家硬约束领域）。",
        "",
        "**禁止写入（硬黑名单）**：`permissions.*` / `history.*` / `schema_version` / `created_at`",
        "- 写入会被拒并写 audit_log。",
    ]
    return "\n".join(lines)


def _fact_groups_layer(state) -> str:
    """task 76：把记忆按 kind 分组渲染，让 LLM 视觉上明确区分
    "原著事实" vs "本局已发生" vs "玩家硬约束"——codex §1+2 强调。

    数据源：state.memory.items（task 74 引入的结构化数组）。
    回退：如果 items 为空（旧存档没积累新写入）就读 legacy memory.facts
    作为 runtime_fact 显示，保证向后兼容。
    """
    memory = state.data.get("memory", {}) or {}
    items = memory.get("items", []) or []
    # 按 kind 分桶（只取 active 状态）
    groups: dict[str, list[dict]] = {
        "canon_fact": [],
        "runtime_fact": [],
        "user_constraint": [],
    }
    for it in items:
        if it.get("status") and it.get("status") != "active":
            continue
        k = it.get("kind")
        if k in groups:
            groups[k].append(it)
    # 各取最近 N 条（按 turn 倒序，便于聚焦"新鲜"信息）
    for k in groups:
        groups[k].sort(key=lambda x: x.get("turn", 0), reverse=True)
    canon = groups["canon_fact"][:8]
    runtime = groups["runtime_fact"][:12]
    constraints = groups["user_constraint"][:6]
    # 回退：items 没积累 runtime_fact，但旧 memory.facts 有 → 显示 facts
    legacy_facts = []
    if not runtime:
        legacy_facts = [f for f in (memory.get("facts") or []) if f][:10]

    lines = []
    if canon:
        lines.append("## 原著事实 (canon) —— 设定边界，不是本局发生过的")
        for it in canon:
            lines.append(f"- {it.get('text', '?')[:80]}")
        lines.append("")
    if runtime:
        lines.append("## 本局已发生 (runtime) —— 玩家亲历，可叙事复述")
        for it in runtime:
            meta = []
            if it.get("time_label"):
                meta.append(it["time_label"])
            if it.get("characters"):
                meta.append("、".join(it["characters"][:3]))
            meta_str = f"（{' · '.join(meta)}）" if meta else ""
            lines.append(f"- {it.get('text', '?')[:80]} {meta_str}")
        lines.append("")
    elif legacy_facts:
        lines.append("## 本局已发生 (runtime, legacy) —— 旧存档迁移前数据")
        for f in legacy_facts:
            lines.append(f"- {f[:80]}")
        lines.append("")
    if constraints:
        lines.append("## 玩家硬约束 (user_constraint) —— 最高优先级，覆盖一切")
        for it in constraints:
            lines.append(f"- {it.get('text', '?')[:80]}")
    if not lines:
        return ""
    return "\n".join(lines).rstrip()


def _candidate_actions_layer(plan: dict[str, Any] | None) -> str:
    """task 82：把 curator 的 candidate_actions 显式作为 anchor 喂给主 GM。
    不是强制约束，是优先级提示——让 GM 优先在候选范围内选，减少自由发挥越界。
    """
    if not plan:
        return ""
    candidates = plan.get("candidate_actions") or []
    if not candidates:
        return ""
    lines = [
        "Curator 为本轮列出了以下候选动作；**优先在候选范围内**叙事或写状态，",
        "如果候选都不合适，可以选「其它」（在正文里说明你为什么偏离候选）：",
    ]
    for i, c in enumerate(candidates[:5], 1):
        lines.append(f"{i}. {str(c)[:120]}")
    lines.append("（候选是建议不是强制；最终输出仍由你判断。）")
    return "\n".join(lines)


def _active_hypotheses_layer(state) -> str:
    """task 75：暴露 active hypothesis 给 LLM，让模型知道自己已经登记过哪些推测，
    避免重复推测同一件事或把推测当事实复述。
    """
    try:
        hypos = state.list_active_hypotheses() if hasattr(state, "list_active_hypotheses") else []
    except Exception:
        hypos = []
    if not hypos:
        return ""
    lines = [
        "以下是本档**尚未确认的推测**（仅你/子代理的猜想，**绝不当作已发生事实复述**）：",
    ]
    for h in hypos[:8]:
        chars = "、".join(h.get("characters", []) or [])
        time_label = h.get("time_label") or ""
        meta = " · ".join(x for x in [time_label, chars] if x)
        meta_str = f"（{meta}）" if meta else ""
        lines.append(f"- [{h.get('id', '?')}] {h.get('text', '?')[:60]} {meta_str}")
    lines.append(
        "如有新信息验证了某条推测，输出 "
        "`{\"op\":\"confirm_hypothesis\",\"id\":\"...\"}` 升级为事实；"
        "若被推翻输出 `{\"op\":\"reject_hypothesis\",\"id\":\"...\"}`。"
    )
    return "\n".join(lines)


def _write_results_layer(state) -> str:
    """task 54：把上轮 GM 标签的处理结果反馈给模型，闭合 codex 流水线最后一环。

    构造一段简短的"上轮发生了什么"叙述：
    - 真生效的写入
    - 入 pending 的（玩家审批中）
    - 被硬黑名单拒的
    告诉 LLM 不必重写已 pending 的同一路径；让它知道 read_only/default
    模式下哪些标签起不到作用。
    """
    memory = (state.data.get("memory") or {})
    permissions = (state.data.get("permissions") or {})
    last_updates = memory.get("last_structured_updates") or []
    pending = permissions.get("pending_writes") or []
    audit_log = permissions.get("audit_log") or []

    lines = []
    if last_updates:
        lines.append("上轮你输出的标签实际结果：")
        for u in last_updates[:12]:
            lines.append(f"- {u}")

    if pending:
        lines.append("")
        lines.append(f"当前待玩家审批的写入（共 {len(pending)} 条 · 已入队，不要重写同一路径）：")
        for p in pending[-8:]:  # 最近 8 条
            risk = p.get("risk", "?")
            field = p.get("path") or p.get("field", "?")
            val = str(p.get("value", p.get("to", "")))[:50]
            lines.append(f"- [{risk}] {field} = {val}")

    blocked = [a for a in audit_log[-15:] if a.get("blocked") == "hard_forbidden"]
    if blocked:
        lines.append("")
        lines.append("上轮被硬黑名单拒绝（permissions.* / history.* 任何形式都禁止，不要再写）：")
        for a in blocked[-5:]:
            lines.append(f"- {a.get('path')} = {str(a.get('value',''))[:50]}")

    # task 60: 解析失败反馈 — 让 LLM 看到自己写的标签为什么没生效
    parse_errors = [a for a in audit_log[-20:] if a.get("kind") == "parse_error"]
    if parse_errors:
        lines.append("")
        lines.append("⚠️ 上轮你输出的标签**解析失败**（被静默丢弃前已记录，请改格式重试）：")
        for a in parse_errors[-5:]:
            lines.append(f"- {a.get('raw_spec', '?')[:60]}")
            if a.get("hint"):
                lines.append(f"  · 原因：{a['hint']}")
        lines.append("正确格式参考：")
        lines.append("- JSON：`{\"op\":\"set\",\"path\":\"player.role\",\"value\":\"史官\"}`")
        lines.append("- 【】：`【状态写入：player.role=史官】`（半角 = 号；path 不要含空格）")

    rejected = [a for a in audit_log[-15:] if "rejected" in str(a.get("source", "")) or a.get("kind") == "rejected"]
    if rejected:
        lines.append("")
        lines.append("玩家拒绝过的最近写入（不要立即重写，先在叙事里铺垫或改用询问）：")
        for a in rejected[-5:]:
            lines.append(f"- {a.get('path')} = {str(a.get('value',''))[:50]}")

    if not lines:
        return "（这是本档第一轮，或上轮没有任何标签输出）"
    return "\n".join(lines)


def _neutralize_state_write_tags(text: str) -> str:
    """P0 #2：从检索内容里中和 `【状态写入：…】` / `【询问：…】` /
    `【时间推进：…】` 等会被 apply_structured_updates 当作 GM 写状态指令
    的标签。原文如果包含这类装饰括号，主 GM 在转述时会原样复述，
    apply_structured_updates 在 GM 输出上跑 re.findall(r"【([^】]+)】")
    就会把章节里的"假指令"当成真状态写入执行。

    修法：把检索内容里的 `【` `】` 替换成视觉上接近但 GM 解析时不会
    被识别的全形括号（U+FF3B / U+FF3D），保持人类可读的同时切断指令链路。
    """
    if not text:
        return text
    return text.replace("【", "［").replace("】", "］")


def build_context_bundle(
    state,
    user_input: str,
    retrieved_context: str = "",
    curator_plan: dict[str, Any] | None = None,
    script_id: int | None = None,
    book_id: int | None = None,
) -> dict[str, Any]:
    """组装单轮 prompt 上下文。

    B3：当传入 script_id/book_id 时，优先从 DB (character_cards / worldbook_entries
    / chapter_facts) 取数据；DB 为空时退化到 indexes/*.json 静态数据。
    """
    chars = _load_characters(script_id=script_id, book_id=book_id)
    world = _load_world()
    history = state.history_messages()
    recent_text = _recent_text(history)
    scan_text = "\n".join([
        user_input or "",
        recent_text,
        state.data["player"].get("current_location", ""),
        state.data["world"].get("time", ""),
        "\n".join(state.data["world"].get("known_events", [])),
        state.data["memory"].get("current_objective", ""),
    ])

    player_card = _player_card(state, chars)
    npc_cards = _active_character_cards(scan_text, chars, player_card.get("name", ""))
    worldbook = _active_worldbook(scan_text, world, state, script_id=script_id, book_id=book_id)
    timeline_layer = _timeline_layer(state)
    worldline_layer = _worldline_layer(state)

    # 顺序按"稳定→半稳定→每轮变化"分组，让 prompt cache 能命中尽可能长的前缀。
    # 缓存关键：前缀任何字节变化就 miss。所以稳定层必须连续无缝放最前。
    layers = [
        # ─── 稳定前缀（每轮基本不变，可缓存）────────────────
        _layer("rules", "剧情规则", _story_rules(), sticky=True),
        _layer("agent_runtime", "主GM代理运行契约", _agent_runtime_rules(), sticky=True),
        _layer("player_card", "玩家角色卡", player_card["text"], sticky=True, source=player_card["name"]),
        # ─── 半稳定（玩家不切角色就同一份；切了就 miss）───
        _layer(
            "npc_cards",
            "当前角色卡",
            "\n\n".join(card["text"] for card in npc_cards),
            items=[_strip_card_text(card) for card in npc_cards],
        ),
        _layer(
            "worldbook",
            "激活世界书",
            "\n\n".join(entry["text"] for entry in worldbook),
            items=[_strip_worldbook_text(entry) for entry in worldbook],
        ),
        # ─── 动态尾部（每轮变化，缓存边界）──────────────────
        _layer(
            "timeline",
            "时间线事务",
            timeline_layer["text"],
            sticky=True,
            items=[timeline_layer["debug"]],
        ),
        _layer(
            "worldline",
            "世界线推演权限",
            worldline_layer["text"],
            sticky=True,
            items=[worldline_layer["debug"]],
        ),
        _layer("state", "当前状态", state.short_summary(), sticky=True),
        # task 76：按 kind 分组的事实层（canon vs runtime vs user_constraint）。
        # 让 LLM 视觉上明确区分"原著背景" vs "本局亲历"，避免把原著事件
        # 当成本局发生过的事来叙事。回退兼容旧 memory.facts。
        _layer("fact_groups", "事实分组（按 kind）", _fact_groups_layer(state), sticky=False),
        # task 59：状态字段 schema 层 —— 让 LLM 知道每个 path 的值类型、当前 enum
        # 候选（已知 NPC 名）、列表 vs 标量区别，减少盲试导致的 pending 队列爆炸。
        _layer("state_schema", "状态字段 schema", _state_schema_layer(state, chars), sticky=True),
        # task 54：结果回灌层 —— 把上轮 GM 输出的【...】标签处理结果告诉模型，
        # 闭合 codex 流水线最后一环（"执行结果返回给模型"）。
        # 之前 apply_structured_updates 的 updates 只写到 last_structured_updates，
        # state.short_summary 又不展开 → LLM 完全不知道自己上轮写的东西落没落、
        # 哪些入了 pending → 下一轮还会重写同样字段重新被挡，浪费 token + 玩家审批队列爆炸。
        _layer("write_results", "上轮标签处理结果", _write_results_layer(state), sticky=False),
        # task 75：active hypotheses（推测）单独一层，让 LLM 看到自己已登记
        # 的推测，避免重复猜或把推测复述成事实。
        _layer("hypotheses", "未确认推测", _active_hypotheses_layer(state), sticky=False),
        _layer(
            "context_agent",
            "子代理上下文决议",
            _context_agent_decision(curator_plan),
            items=[_context_agent_debug(curator_plan)],
        ),
        # task 82：candidate_actions 作为单独的 anchor 层，强调"GM 优先从候选选"
        # 而不是融在 context_agent 大段文本里被忽略。
        _layer(
            "candidate_actions",
            "本轮候选动作",
            _candidate_actions_layer(curator_plan),
            sticky=False,
        ),
        _layer("rag", "检索参考", _neutralize_state_write_tags(retrieved_context) or "（本轮无额外检索资料）"),
        _layer("recent_chat", "最近对话", _format_history(history)),
        _layer("user_input", "玩家本轮输入", user_input or "（空）"),
    ]

    prompt_parts = []
    debug_layers = []
    for layer in layers:
        trimmed = _trim(layer["content"], MAX_LAYER_CHARS.get(layer["id"], 1800))
        if not trimmed:
            continue
        prompt_parts.append(f"【{layer['title']}】\n{trimmed}")
        debug_layers.append({
            "id": layer["id"],
            "title": layer["title"],
            "chars": len(trimmed),
            "estimated_tokens": _estimate_tokens(trimmed),
            "sticky": layer.get("sticky", False),
            "source": layer.get("source", ""),
            "preview": _preview(trimmed),
            "items": layer.get("items", []),
        })

    prompt = "\n\n".join(prompt_parts)
    cache_plan = _cache_plan(debug_layers, prompt_parts)
    debug = {
        "total_chars": len(prompt),
        "estimated_tokens": _estimate_tokens(prompt),
        "layers": debug_layers,
        "cache_plan": cache_plan,
        "active_character_cards": [_strip_card_text(card) for card in npc_cards],
        "active_worldbook": [_strip_worldbook_text(entry) for entry in worldbook],
        "timeline": timeline_layer["debug"],
        "worldline": worldline_layer["debug"],
        "curator_plan": curator_plan or {},
    }
    return {"prompt": prompt, "debug": debug}


def _load_characters(script_id: int | None = None, book_id: int | None = None) -> dict[str, Any]:
    """优先从 DB character_cards 取，失败/为空时回退 JSON。"""
    if script_id or book_id:
        try:
            db_chars = _load_characters_db(script_id=script_id, book_id=book_id)
            if db_chars:
                return db_chars
        except Exception:
            pass
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


def _load_world() -> dict[str, Any]:
    with open(WORLD_IDX, "r", encoding="utf-8") as f:
        return json.load(f)


def _story_rules() -> str:
    return "\n".join([
        "这是沉浸式文字 RPG。GM 只描写玩家角色能感知或通过合理渠道获知的信息。",
        "保持原著风格：克制、精确、信息密度高，不把 NPC 写成答题机器。",
        "不要替玩家决定行动。结尾可以给压力、线索或抉择，但不代替玩家选择。",
        "玩家行动可能改变原著分支，世界书和角色卡优先维持人物逻辑与势力边界。",
        "本轮发生状态变化时，在正文末尾追加结构化标签，方便系统写回存档。",
    ])


def _agent_runtime_rules() -> str:
    # task 67：主体契约已合并到 gm.py _SYSTEM_BASE「主 GM 运行契约」段，
    # 这里只保留 "本轮特定" 的运行提醒（动态层用，每轮可重申）。
    return "\n".join([
        "本轮务必执行: 读子代理决议 → 裁定世界反应 → 输出正文 → 输出 JSON ops 数组（仅当真有变化时）。",
        "如上下文不足以推进，在正文里说明不确定性并输出 question op 让玩家选择，不要瞎编。",
    ])


def _context_agent_decision(plan: dict[str, Any] | None) -> str:
    """task 79：DemandLedger 渲染。显式分组展示 hard vs soft constraint，
    acceptance 单独 section，confidence + clarifying_question 单独提示。"""
    if not plan:
        return "本轮没有大模型子代理决议；主 GM 必须按时间线层和检索参考保守生成。"
    must_include = plan.get("must_include") or (plan.get("retrieval_plan", {}) or {}).get("must_include") or []
    risk_flags = plan.get("risk_flags") or []
    hard = plan.get("hard_constraints") or []
    soft = plan.get("soft_preferences") or []
    targets_e = plan.get("target_entities") or []
    acceptance = plan.get("acceptance") or []
    candidates = plan.get("candidate_actions") or []
    conf = plan.get("confidence", 1.0)
    clarify = (plan.get("clarifying_question") or "").strip()

    lines = [
        f"子代理意图：{plan.get('intent') or '未说明'}",
    ]
    if plan.get("active_goal"):
        lines.append(f"底层真实目标：{plan['active_goal']}")
    lines.append(f"目标时间线：{plan.get('timeline_target') or '未请求跳转'}")
    if plan.get("target_location"):
        lines.append(f"目标地点：{plan['target_location']}")
    if plan.get("target_time"):
        lines.append(f"目标时间：{plan['target_time']}")
    if targets_e:
        lines.append(f"涉及实体：{'、'.join(str(x) for x in targets_e[:8])}")
    if hard:
        lines.append("【硬约束】（必须满足）")
        for c in hard[:6]:
            lines.append(f"  · {c}")
    if soft:
        lines.append("【软偏好】（最好满足，可妥协）")
        for c in soft[:6]:
            lines.append(f"  · {c}")
    lines.append(f"检索查询：{plan.get('retrieval_query') or '未提供'}")
    lines.append("必含事实：" + ("；".join(str(x) for x in must_include) if must_include else "无"))
    if acceptance:
        lines.append("【本轮 acceptance 验收】（输出后系统会检查每条是否满足）")
        for a in acceptance[:6]:
            lines.append(f"  · {a}")
    if candidates:
        lines.append("【候选动作建议】（GM 可优先从中选；不强制）")
        for c in candidates[:5]:
            lines.append(f"  · {c}")
    lines.append("风险标记：" + ("；".join(str(x) for x in risk_flags) if risk_flags else "无"))
    lines.append(f"子代理置信度：{conf:.2f}")
    if clarify:
        lines.append(f"⚠️ 子代理建议先问玩家：{clarify}")
    lines.append(f"选择理由：{plan.get('reason') or '未说明'}")
    lines.append("主 GM 只能把这些作为上下文选择结果使用，不得把子代理理由写成玩家可见事实。")
    return "\n".join(lines)


def _context_agent_debug(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not plan:
        return {}
    return {
        "intent": plan.get("intent", ""),
        "active_goal": plan.get("active_goal", ""),
        "timeline_target": plan.get("timeline_target", ""),
        "retrieval_query": plan.get("retrieval_query", ""),
        "must_include": plan.get("must_include", []),
        "hard_constraints": plan.get("hard_constraints", []),
        "soft_preferences": plan.get("soft_preferences", []),
        "target_entities": plan.get("target_entities", []),
        "candidate_actions": plan.get("candidate_actions", []),
        "acceptance": plan.get("acceptance", []),
        "risk_flags": plan.get("risk_flags", []),
        "confidence": plan.get("confidence", 1.0),
        "clarifying_question": plan.get("clarifying_question", ""),
    }


def _timeline_layer(state) -> dict[str, Any]:
    world = state.data.get("world", {})
    timeline = world.get("timeline", {})
    pending = timeline.get("pending_jump") or {}
    locked_label = world.get("time") or timeline.get("current_label") or ""
    retrieval_label = locked_label
    anchor = _safe_timeline_filter(retrieval_label)
    if not anchor.get("anchor_chapter"):
        previous = (timeline.get("last_transition") or {}).get("from")
        if previous:
            anchor = _safe_timeline_filter(previous)
            retrieval_label = previous
    target_anchor = _safe_timeline_filter(pending.get("to", "")) if pending else {}

    lines = [
        f"当前锁定时间线：{locked_label}",
        f"当前阶段：{timeline.get('current_phase') or '未知'}",
        f"锚定状态：{timeline.get('anchor_state') or 'locked'}",
        f"原著检索锚点：第{anchor.get('anchor_chapter') or '?'}章 · {anchor.get('anchor_event') or '未命中'}",
        f"允许检索章节窗口：{anchor.get('chapter_min') or '?'} - {anchor.get('chapter_max') or '?'}",
    ]
    if pending:
        pending_status = str(pending.get("status") or "")
        # task 44：之前 prompt 鼓励 GM "默认接受、输出【时间跳跃确认】+【当前时间线：目标】"
        # —— 这让 state 处于 pending_confirmation 时 GM 正文还在叙事到目标时间。
        # 玩家用『先让子代理检查冲突，不要直接跳过确认』这类措辞触发的 pending，
        # 强制 GM 这一轮只输出冲突检查 + 风险清单 + 询问玩家确认，禁止：
        #   - 叙事推进到目标时间（不能写"翌日上午""转眼已是次日"等过去式时间过渡）
        #   - 输出【时间跳跃确认：目标】tag（state 端已 task 32/35 防御，但 prompt 也要主动禁）
        #   - 输出【当前时间线：目标】或【当前位置：新地点】tag（把未发生的事写进 state）
        #   - 声明在新地点新时间发生的具体场景/选项
        is_awaiting = pending_status in ("awaiting_gm_confirmation", "awaiting", "pending_confirmation")
        lines.extend([
            f"玩家请求时间跳跃：{pending.get('from', '')} -> {pending.get('to', '')}",
            f"目标原著匹配：第{target_anchor.get('anchor_chapter') or '?'}章 · {target_anchor.get('anchor_event') or '未能精确匹配'}",
            f"pending 状态：{pending_status or '未知'}",
        ])
        if is_awaiting:
            lines.extend([
                "⚠ 本轮 anchor_state=pending_confirmation：禁止把玩家请求的未来时间/地点当作已发生的事实。",
                "禁止输出『翌日…』『次日…』『转眼已是…』等任何把场景叙事推进到目标时间的措辞；",
                "禁止输出标签【时间跳跃确认：…】【当前时间线：目标时间】【当前位置：新地点】【时间：目标时间】；",
                "禁止给出『新时间/新地点』场景里的对话、动作、选项；",
                "本轮只允许：① 给出冲突检查（与世界书/时间线锚点是否一致）；② 列出风险/代价/前置条件；"
                "③ 输出【询问玩家：是否确认跳跃到 <目标时间>？】+ 1-3 个明确选项（确认 / 取消 / 修改目标）；",
                "下一轮若玩家明确回复『确认』或 /confirm，再正式推进时间线和场景。",
            ])
        else:
            lines.extend([
                "本轮必须先处理时间跳跃事务：默认尊重玩家的跳转/改线意图，接受则写出过渡/落点并输出【时间跳跃确认：目标时间】和【当前时间线：目标时间】；只有目标完全不可解析时才输出【询问玩家：...】。",
                "在确认前，不要把玩家请求的未来时间当作已经发生；确认后才允许推进场景与更新位置/目标。",
            ])
    else:
        lines.append("没有待确认时间跳跃；生成时必须保持当前时间线锚点，除非玩家本轮提出新跳跃。")

    debug = {
        "anchor_state": timeline.get("anchor_state") or "locked",
        "current_label": locked_label,
        "current_phase": timeline.get("current_phase") or "",
        "pending_jump": pending,
        "retrieval_label": retrieval_label,
        "chapter_min": anchor.get("chapter_min"),
        "chapter_max": anchor.get("chapter_max"),
        "anchor_chapter": anchor.get("anchor_chapter"),
        "anchor_event": anchor.get("anchor_event"),
        "story_time_label": anchor.get("story_time_label"),
        "confidence": anchor.get("confidence", 0.0),
        "target_anchor": target_anchor,
    }
    return {"text": "\n".join(lines), "debug": debug}


def _safe_timeline_filter(label: str) -> dict[str, Any]:
    try:
        return timeline_filter_for_label(label)
    except Exception:
        return {
            "chapter_min": None,
            "chapter_max": None,
            "anchor_chapter": None,
            "anchor_event": "",
            "story_time_label": "",
            "confidence": 0.0,
        }


def _worldline_layer(state) -> dict[str, Any]:
    permissions = state.data.get("permissions", {})
    worldline = state.data.get("worldline", {})
    variables = worldline.get("user_variables", {})
    mode = permissions.get("mode", "full_access")
    variable_lines = []
    for name, info in variables.items():
        variable_lines.append(f"- {name} = {info.get('value', '')}（硬约束）")
    if not variable_lines:
        variable_lines.append("- 暂无用户变量。")

    # task 53：把当前模式的具体行为讲清楚，让 LLM 在 read_only / default
    # 模式下减少无意义的【状态写入】（反正都会入 pending），改为多用
    # 【询问玩家】或在叙事中暗示。也防止 LLM 试图改 permissions.mode 自我提权
    # （已被硬黑名单挡，但 LLM 浪费 token 重试也烦）。
    mode_behavior = {
        "read_only": (
            "当前是【只读模式】：你的任何【状态写入】/【状态追加】都不会立即生效，"
            "全部进入玩家审批队列。所以这一轮请专注于讲叙事 + 用【询问玩家】把"
            "需要变更的地方做成选项让玩家决定，不要写多余的结构化标签。"
        ),
        "default": (
            "当前是【默认权限】：白名单内的字段（player.current_location / "
            "world.time / memory.main_quest / memory.current_objective / "
            "memory.resources / memory.abilities / memory.facts / "
            "world.known_events / relationships.*）会自动生效；其他字段进入审批队列。"
            "尽量只写白名单内的字段，少做需要审批的写入。"
        ),
        "auto_review": (
            "当前是【自动审查】：上面白名单字段 + worldline.user_variables.* "
            "+ relationships.* 自动生效；其他需要审批。"
        ),
        "full_access": (
            "当前是【完全访问】：除硬黑名单（permissions.* / history.* / "
            "schema_version）外，所有写入立即生效。你仍不能也不应该写"
            "permissions.* —— 那是用户权限边界，由 UI 切换。"
        ),
    }
    norm_mode = _normalize_permission_mode(mode)
    lines = [
        # task 58: 去重 — "你不得修改 permissions.mode" 之前重复 3 次
        # （gm.py 主提示 + 此层 + write_results 层）。强模型不需要，
        # 中等模型重复反而暗示"或许可以试试"。只在 gm.py 主提示保留权威说明。
        f"LLM 写入权限：{_permission_label(norm_mode)}",
        mode_behavior.get(norm_mode, mode_behavior["full_access"]),
        "用户变量与世界线推演规则：",
        *variable_lines,
        "推演机制：先把用户变量视作不可违背的硬条件，再结合当前时间线、世界书、角色卡和原著召回推演下一步局势。",
        "/set 生成的用户变量是最高优先级硬约束；如果它改变时间线、地点、世界观或人设，主 GM 必须按新设定写回结构化标签，而不是维护旧设定。",
        "如果推演满足全部用户变量，输出【设定校验：通过】；如果存在矛盾，输出【设定冲突：原因】，并不要把冲突推演写成事实。",
        "可输出【世界线推演：简要推演结果】供 UI 记录。",
        "当需要玩家决定下一步计划、分支方向或设定取舍时，输出【询问玩家：问题｜选项：选项A、选项B、选项C】；这类问题永远不因完全访问权限而自动跳过。",
    ]
    debug = {
        "permission_mode": mode,
        "permission_label": _permission_label(mode),
        "user_variables": variables,
        "last_validation": worldline.get("last_validation"),
        "last_projection": worldline.get("last_projection"),
        "pending_projection": worldline.get("pending_projection"),
        "custom_ui": worldline.get("custom_ui", {}),
        "pending_writes": permissions.get("pending_writes", [])[-5:],
    }
    return {"text": "\n".join(lines), "debug": debug}


def _normalize_permission_mode(mode: str) -> str:
    """task 53/54：本地副本，避免循环 import state.py。和 state._normalize_permission_mode 保持同步。"""
    text = str(mode or "").strip().lower()
    mapping = {
        "只读": "read_only", "只读模式": "read_only", "suggest": "read_only",
        "read": "read_only", "read_only": "read_only", "plan": "read_only",
        "默认权限": "default", "default": "default",
        "auto": "auto_review", "自动审查": "auto_review",
        "auto_review": "auto_review", "review": "auto_review",
        "完全访问权限": "full_access", "full": "full_access", "full_access": "full_access",
    }
    return mapping.get(text, "full_access")


def _permission_label(mode: str) -> str:
    return {
        "read_only": "只读模式（仅叙事）",
        "default": "默认权限",
        "auto_review": "自动审查",
        "full_access": "完全访问权限",
    }.get(_normalize_permission_mode(mode), "完全访问权限")


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
    active.sort(key=lambda x: x["priority"], reverse=True)
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
    sample = "；".join((card.get("sample_dialogue") or [])[:3])
    lines = [
        f"【{name}】",
        f"身份：{card.get('identity') or '未知'}",
        f"外貌：{card.get('appearance') or '未记录'}",
        f"性格：{card.get('personality') or '未记录'}",
        f"说话风格：{card.get('speech_style') or '未记录'}",
        f"当前状态：{card.get('current_status') or '未记录'}",
    ]
    if card.get("secrets"):
        lines.append(f"隐藏信息：{card.get('secrets')}")
    if sample:
        lines.append(f"台词示例：{sample}")
    return "\n".join(lines)


def _format_history(history: list[dict]) -> str:
    if not history:
        return "（暂无最近对话）"
    lines = []
    for msg in history:
        role = "玩家" if msg.get("role") == "user" else "GM"
        lines.append(f"{role}：{msg.get('content', '')}")
    return "\n\n".join(lines)


def _recent_text(history: list[dict]) -> str:
    return "\n".join(str(msg.get("content", "")) for msg in history)


def _layer(layer_id: str, title: str, content: str, **extra) -> dict[str, Any]:
    return {"id": layer_id, "title": title, "content": content or "", **extra}


def _trim(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n……（已按预算截断）"


def _preview(text: str, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 2)


def _cache_plan(debug_layers: list[dict[str, Any]], prompt_parts: list[str]) -> dict[str, Any]:
    # 与 build_context_bundle 的 layer 顺序对齐：rules → agent_runtime → player_card
    # 是真正的稳定前缀，每轮不变；后面接 npc_cards/worldbook 算半稳定（可选纳入）。
    strict_stable_ids = ["rules", "agent_runtime", "player_card"]
    semi_stable_ids = ["npc_cards", "worldbook"]

    stable_chars = 0
    stable_tokens = 0
    stable_titles: list[str] = []
    semi_chars = 0
    semi_tokens = 0
    semi_titles: list[str] = []
    # 严格按 layer 顺序累加，遇到第一个不属于"已知稳定前缀"的就 break
    i = 0
    for layer in debug_layers:
        lid = layer["id"]
        if lid == strict_stable_ids[i] if i < len(strict_stable_ids) else False:
            stable_chars += int(layer.get("chars", 0))
            stable_tokens += int(layer.get("estimated_tokens", 0))
            stable_titles.append(layer.get("title", ""))
            i += 1
            continue
        # 严格稳定结束后，紧接着如果是 semi-stable 也算缓存候选
        if lid in semi_stable_ids and i >= len(strict_stable_ids):
            semi_chars += int(layer.get("chars", 0))
            semi_tokens += int(layer.get("estimated_tokens", 0))
            semi_titles.append(layer.get("title", ""))
            continue
        break
    total_tokens = sum(int(layer.get("estimated_tokens", 0)) for layer in debug_layers)
    joined_stable = "\n\n".join(prompt_parts[:len(stable_titles)])
    extended_titles = stable_titles + semi_titles
    extended_chars = stable_chars + semi_chars
    extended_tokens = stable_tokens + semi_tokens
    joined_extended = "\n\n".join(prompt_parts[:len(extended_titles)])
    return {
        "strategy": "stable-prefix-first",
        "request_shape": "rules -> agent_runtime -> player_card -> (npc/world) -> dynamic -> user_input",
        # 严格稳定（rules/agent_runtime/player_card）
        "stable_prefix_layers": stable_titles,
        "stable_prefix_chars": stable_chars,
        "stable_prefix_tokens": stable_tokens,
        # 扩展候选（包含 npc_cards/worldbook，玩家不换角色时也稳定）
        "cacheable_prefix_layers": extended_titles,
        "cacheable_prefix_chars": extended_chars,
        "cacheable_prefix_tokens": extended_tokens,
        "volatile_tail_tokens": max(0, total_tokens - extended_tokens),
        "estimated_cacheable_ratio": round(extended_tokens / max(total_tokens, 1), 3),
        "strict_stable_ratio": round(stable_tokens / max(total_tokens, 1), 3),
        "stable_prefix_hash": hashlib.sha256(joined_stable.encode("utf-8")).hexdigest()[:16] if joined_stable else "",
        "cacheable_prefix_hash": hashlib.sha256(joined_extended.encode("utf-8")).hexdigest()[:16] if joined_extended else "",
        "note": "真实缓存命中率由模型厂商返回的用量字段确认；当前请求形状把动态 RAG/context_agent/recent_chat 都放到末尾。",
    }


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
