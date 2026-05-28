"""context_engine.layers — 各上下文层构建函数."""
from __future__ import annotations

from typing import Any
from context_engine._constants import MAX_LAYER_CHARS  # noqa: F401 (re-exported via __init__)
from context_engine._utils import _layer, _trim, _preview, _estimate_tokens
from timeline_index import timeline_filter_for_label


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
        f"- `player.current_location`: 字符串。简短地名（如「北港·灯塔下」「废弃矿道入口」「酒馆楼上」）。当前 = {p.get('current_location', '') or '(空)'}",
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


def _timeline_layer(state) -> dict[str, Any]:
    world = state.data.get("world", {})
    timeline = world.get("timeline", {})
    pending = timeline.get("pending_jump") or {}
    locked_label = world.get("time") or timeline.get("current_label") or ""
    retrieval_label = locked_label

    # 真相源:state.world.timeline.{anchor_chapter, chapter_min, chapter_max, anchor_phase}
    # 由 chat handler 在 /set 后调 script_timeline.resolve_timeline_anchor 写入。
    # 没写入时退化到 SQLite vectors.db 索引 (旧 _safe_timeline_filter)。
    real_anchor_chapter = timeline.get("anchor_chapter")
    real_chapter_min = timeline.get("chapter_min")
    real_chapter_max = timeline.get("chapter_max")
    real_anchor_phase = timeline.get("anchor_phase")
    real_anchor_event = timeline.get("anchor_event")
    if real_anchor_chapter and real_chapter_min and real_chapter_max:
        anchor = {
            "anchor_chapter": real_anchor_chapter,
            "chapter_min": real_chapter_min,
            "chapter_max": real_chapter_max,
            "anchor_event": real_anchor_event or "",
            "story_time_label": locked_label,
            "story_phase": real_anchor_phase or "",
            "confidence": timeline.get("anchor_confidence") or 0.0,
        }
    else:
        anchor = _safe_timeline_filter(retrieval_label)
        if not anchor.get("anchor_chapter"):
            previous = (timeline.get("last_transition") or {}).get("from")
            if previous:
                anchor = _safe_timeline_filter(previous)
                retrieval_label = previous
    target_anchor = _safe_timeline_filter(pending.get("to", "")) if pending else {}

    # 阶段显示:anchor_phase (用户 /set 后查锚点拿到) 优先于 current_phase (可能过时)
    effective_phase = (
        timeline.get("anchor_phase")
        or timeline.get("current_phase")
        or anchor.get("story_phase")
        or "未知"
    )
    lines = [
        f"当前锁定时间线：{locked_label}",
        f"当前阶段：{effective_phase}",
        f"锚定状态：{timeline.get('anchor_state') or 'locked'}",
        f"原著检索锚点：第{anchor.get('anchor_chapter') or '?'}章 · {anchor.get('anchor_event') or anchor.get('story_phase') or '未命中'}",
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
        # 新增分支:用户当回合刚用 /set 硬覆盖时间线 (last_transition.source="user_set")。
        # 这是**覆盖式跳跃**,不是叙事过渡:state.world.time 已经直接变了,GM 必须
        # 把新时间视作既定事实,从新时间点的场景开始叙事,不能写"穿越/醒来/拨回时钟"
        # 等过渡剧情。
        # task 86：除了 last_transition.source 判定,还检查 user_set_jump_turn —
        # 因为 GM 在响应中可能再调 update_time(source="gm") 把 last_transition
        # 改写,但 user_set_jump_turn 只会被新的 user_set 跳跃覆盖,可靠地表示
        # 本回合是否有过用户硬覆盖。
        last_t = timeline.get("last_transition") or {}
        try:
            _last_turn = int(last_t.get("turn") or -1)
            _cur_turn = int(state.data.get("turn") or 0)
        except (TypeError, ValueError):
            _last_turn = -1
            _cur_turn = 0
        try:
            _user_jump_turn = int(timeline.get("user_set_jump_turn")) if timeline.get("user_set_jump_turn") is not None else None
        except (TypeError, ValueError):
            _user_jump_turn = None
        _is_user_set_now = (
            (last_t.get("source") == "user_set" and _last_turn == _cur_turn)
            or (_user_jump_turn == _cur_turn)
        )
        if _is_user_set_now:
            _from = last_t.get("from") or "(未知)"
            _to = last_t.get("to") or locked_label or "(未知)"
            lines.extend([
                "",
                f"⚠ 本轮玩家用 /set 硬覆盖时间线:from『{_from}』→ to『{_to}』",
                "这是**覆盖式跳跃**,不是叙事过渡。GM 的任务是直接在新时间点开场,不要叙事时间过渡过程。",
                "**禁止**输出以下措辞或剧情(违反会被 strip / warn):",
                "  · 『穿越』『时空错位/穿梭』『回到过去』『时空裂缝』『时间倒流』",
                "  · 『醒来』『再次睁开眼睛/眼眸』『从昏迷/沉睡中』",
                "  · 『拨回时钟』『时钟被拨回』『时间被(一双)?手?[拉拨]回』",
                "  · 『重启世界』『重置场景/时间』『世界被重写』",
                "  · 『刺骨的冷』『冷得发抖』等惊厥/失忆/无意识开场",
                "  · 『当你再次X』『睁开X时,X已经不在了』等模板化过渡",
                "**应该**:把 state.world.time + state.player.current_location 当成既定事实,"
                "从『此时此刻』玩家角色正在做什么、看到什么、跟谁在一起开始叙述。"
                "玩家是有意识地推进游戏,**时间线标签的切换 = 镜头切到新时间点**,角色没有失忆/穿越感。",
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


def _worldline_layer(state) -> dict[str, Any]:
    from context_engine.helpers import _normalize_permission_mode, _permission_label
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
