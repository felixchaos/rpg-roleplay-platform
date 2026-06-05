"""
command_tools_creative.py — 创意推荐工具

当前包含:
  recommend_player_identity
    新建存档时, 根据剧本 + 出生点 + 角色卡, 用 LLM 推荐 3-5 个契合出生点
    剧情阶段的初始身份 (玩家在剧本世界中的定位/职业/动机)。

scope="script", origins=_USER_ORIGINS_READ (任意 origin 可调, 纯 LLM 推荐, 无写入)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry

_log = logging.getLogger(__name__)

# 与 command_tools_saves.py 保持一致 — 任何 origin 都可以调只读工具
_USER_ORIGINS_READ = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat", "console_assistant",
})


# ────────────────────────────────────────────────────────────
# 数据拉取
# ────────────────────────────────────────────────────────────


def _fetch_script_info(script_id: int, user_id: int) -> dict[str, Any] | None:
    """从 DB 拉剧本 title + description, 验证 owner 或公开剧本订阅者归属。

    返回 None 代表无权访问或不存在。
    """
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            # task: union owned + subscribed(read-only,GM 推荐身份等内部工具调用)
            row = db.execute(
                """select s.id, s.title, s.description from scripts s
                where s.id = %s and (
                  s.owner_id = %s
                  or s.id in (select script_id from user_script_subscriptions where user_id = %s)
                )""",
                (script_id, user_id, user_id),
            ).fetchone()
        if not row:
            return None
        return {"id": row["id"], "title": row["title"], "description": row["description"]}
    except Exception:
        return None


def _fetch_phase_digest(script_id: int, phase: str) -> str:
    """按 story_phase 拉前 5 章 chapter_facts.summary 拼成阶段概要。

    返回空字符串表示没拿到 (软降级);此时 _build_system_prompt 不会附【阶段剧情参考】段。

    历史 bug: 旧版 SELECT 不存在的列 fact_text / chapter_index → 异常被 except 静默吞掉 →
    LLM 永远拿不到剧本信息 → 推荐永远跑到 fallback。chapter_facts 实际列名是 chapter/summary。
    """
    if not phase:
        return ""
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select chapter, title, summary from chapter_facts "
                "where script_id = %s and story_phase ilike %s "
                "  and coalesce(summary, '') <> '' "
                "order by chapter asc limit 5",
                (script_id, f"%{phase[:30]}%"),
            ).fetchall() or []
        if not rows:
            return ""
        parts: list[str] = []
        for r in rows:
            ch = r.get("chapter")
            title = (r.get("title") or "").strip()
            summ = (r.get("summary") or "").strip()
            if not summ:
                continue
            head = ""
            if ch is not None:
                head = f"第{ch}章"
                if title:
                    head += f"《{title}》"
            elif title:
                head = f"《{title}》"
            parts.append(f"[{head}] {summ[:160]}" if head else summ[:160])
        return "\n".join(parts)[:1600]
    except Exception:
        return ""


def _fetch_anchor_info(script_id: int, phase: str, label: str) -> str:
    """从 script_timeline_anchors 拉 story_time_label + sample_summary。"""
    if not label:
        return ""
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select story_time_label, story_phase, sample_summary, chapter_min, chapter_max "
                "from script_timeline_anchors "
                "where script_id = %s and story_time_label ilike %s "
                "order by chapter_min limit 1",
                (script_id, f"%{label[:30]}%"),
            ).fetchone()
        if row:
            parts = []
            if row.get("story_phase"):
                parts.append(f"剧情阶段: {row['story_phase']}")
            if row.get("story_time_label"):
                parts.append(f"时间锚点: {row['story_time_label']}")
            if row.get("chapter_min") is not None:
                parts.append(f"章节范围: {row['chapter_min']}–{row['chapter_max']}")
            if row.get("sample_summary"):
                parts.append(f"场景摘要: {row['sample_summary'][:200]}")
            return " | ".join(parts)
    except Exception:
        pass
    return ""


def _fetch_character_card(card_id: int, kind: str, user_id: int) -> dict[str, Any] | None:
    """拉角色卡信息 (persona / user_card / script_card)。"""
    if not card_id:
        return None
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if kind == "persona":
                row = db.execute(
                    "select name, identity as role, background, appearance, personality "
                    "from character_cards where id = %s and user_id = %s and card_type = 'persona'",
                    (card_id, user_id),
                ).fetchone()
            elif kind == "user_card":
                row = db.execute(
                    "select name, identity, appearance, personality "
                    "from character_cards where id = %s and user_id = %s and card_type = 'pc'",
                    (card_id, user_id),
                ).fetchone()
            elif kind == "script_card":
                # task: 公开剧本订阅者也能读其 character_card
                row = db.execute(
                    """select cc.name, cc.identity, cc.appearance, cc.personality
                    from character_cards cc
                    join scripts s on cc.script_id = s.id
                    where cc.id = %s and (
                      s.owner_id = %s
                      or s.id in (select script_id from user_script_subscriptions where user_id = %s)
                    )""",
                    (card_id, user_id, user_id),
                ).fetchone()
            else:
                return None
        if not row:
            return None
        return dict(row)
    except Exception:
        return None


# ────────────────────────────────────────────────────────────
# LLM 推荐
# ────────────────────────────────────────────────────────────


_IDENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "身份名称或角色名"},
                    "role": {"type": "string", "description": "职位/定位一句话"},
                    "background": {
                        "type": "string",
                        "description": "背景介绍 30-80 字, 描述与剧本世界的关联",
                    },
                },
                "required": ["name", "role", "background"],
            },
            "minItems": 1,
            "maxItems": 6,
        }
    },
    "required": ["recommendations"],
}


def _build_system_prompt(
    script_title: str,
    script_desc: str,
    phase: str,
    label: str,
    phase_digest: str,
    anchor_info: str,
    card: dict | None,
    n: int,
    player_origin: str = "soul",
) -> str:
    # 跟身份卡正交:player_origin 决定生成的身份候选是
    # - isekai: "现代灵魂穿越成 X" (X = 该剧本世界里的某个身份),角色卡是穿越后的身体
    # - native: "原作世界 X 身份" (X = 卧底/流亡贵族/避难所逃亡者...),无现代记忆
    # 身份候选风格:魂穿(soul)/肉穿(body)=外来灵魂视角(现代记忆)→"现代灵魂穿越成 X";
    # 一体双魂(dual)/彻底扮演(native)=原住民身份→"原作世界里 X 身份"。旧值 isekai 等同 soul。
    isekai = player_origin in ("isekai", "soul", "body")
    lines = [
        "你是 RPG 平台的身份推荐助手。",
        (
            "根据玩家选定的剧本、出生点和角色卡, 为玩家推荐【现代灵魂穿越成 X】的差异化初始身份候选。"
            "玩家本质上是从现代地球穿越来的灵魂, 现在占据着所选角色卡这具身体, 拥有现代记忆 + 异世界知识。"
            if isekai else
            "根据玩家选定的剧本、出生点和角色卡, 为玩家推荐【原作世界里 X 身份】的差异化初始身份候选。"
            "玩家本质上是这个剧本世界里土生土长的角色, 没有现代记忆/穿越者背景, 知识体系限定在剧本设定里。"
        ),
        "",
        f"【剧本标题】{script_title}",
    ]
    if script_desc:
        lines.append(f"【剧本概要】{script_desc[:400]}")
    if phase:
        lines.append(f"【出生点阶段】{phase}")
    if label:
        lines.append(f"【出生点时间锚点】{label}")
    if anchor_info:
        lines.append(f"【锚点详情】{anchor_info}")
    if phase_digest:
        lines.append(f"【阶段剧情参考】{phase_digest}")
    if card:
        card_lines = []
        if card.get("name"):
            card_lines.append(f"姓名: {card['name']}")
        role_or_id = card.get("role") or card.get("identity") or ""
        if role_or_id:
            card_lines.append(f"身份/职位: {role_or_id}")
        if card.get("personality"):
            card_lines.append(f"性格: {card['personality'][:80]}")
        if card.get("appearance"):
            card_lines.append(f"外貌: {card['appearance'][:80]}")
        if card_lines:
            lines.append(f"【已选角色卡】{' | '.join(card_lines)}")
    lines += [
        "",
        f"请生成 {n} 个差异化的初始身份选项。",
        "要求:",
        "  · 各身份视角各异 (例: 主动卷入者/被动目击者/外来者/权力内部者/底层幸存者 等)",
        "  · 每个身份与当前出生点阶段的剧情逻辑高度契合",
        "  · name: 角色全名或代号",
        "  · role: 在剧本世界中的定位/职业/关系, 一句话",
        "  · background: 30-80 字, 说明角色来历与出生点阶段的关联;"
        "**第三人称无主语的客观描述**(如「潜伏敌营的卧底, 背负灭族之仇…」), 禁用「你/你是」第二人称",
        (
            "  · 必须体现【穿越者属性】 — 现代灵魂 + 异世界身体 + 原世界记忆 + 对原作剧情的部分超前认知"
            if isekai else
            "  · 严禁出现【现代/穿越/转生/异世界来客/原作认知】等元素 — 玩家是这个世界土著, 知识限定剧本设定"
        ),
        "  · 不要把剧本全部 lore 塞进 background, 聚焦与出生点直接相关的动机",
        "",
        "通过 emit_identities 工具一次性输出 JSON, 不要写额外解释。",
    ]
    return "\n".join(lines)


def _call_llm_emit_identities(
    user_id: int,
    system: str,
    n: int,
) -> list[dict] | None:
    """调 LLM 生成身份推荐列表。Anthropic 走 native tool_use, 其余走 JSON mode。"""
    # 复用 character_card_generator 的 backend 选择逻辑
    try:
        from character_card_generator import _select_backend
        backend = _select_backend(user_id)
    except Exception:
        _log.exception("[identity-gen] _select_backend 失败 user_id=%s", user_id)
        return None

    backend_kind = type(backend).__name__

    tool_def = {
        "name": "emit_identities",
        "description": "输出身份推荐列表。",
        "input_schema": _IDENTITY_SCHEMA,
    }

    if backend_kind == "_AnthropicBackend":
        try:
            resp = backend.client.messages.create(
                model=backend.model_name,
                max_tokens=1200,
                temperature=0.85,
                system=system,
                messages=[{"role": "user", "content": f"请生成 {n} 个身份推荐。"}],
                tools=[tool_def],
                tool_choice={"type": "tool", "name": "emit_identities"},
            )
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "emit_identities":
                    inp = block.input or {}
                    if isinstance(inp, dict):
                        return inp.get("recommendations") or []
        except Exception:
            _log.exception("[identity-gen] Anthropic 调用失败 model=%s", getattr(backend, "model_name", "?"))
            return None
    else:
        # JSON mode fallback
        try:
            schema_hint = json.dumps(_IDENTITY_SCHEMA, ensure_ascii=False, indent=2)[:1500]
            full_sys = (
                system
                + "\n\n你必须只返回符合以下 JSON Schema 的 JSON 对象, 不要包含 Markdown 围栏:\n"
                + schema_hint
            )
            text = backend.call_structured(
                full_sys,
                [{"role": "user", "content": f"请生成 {n} 个身份推荐。"}],
                max_tokens=1200,
            )
            obj = _parse_json_safely(text)
            if obj and isinstance(obj.get("recommendations"), list):
                return obj["recommendations"]
            _log.warning("[identity-gen] %s 返回格式异常 model=%s text=%s",
                         backend_kind, getattr(backend, "model_name", "?"),
                         (text or "")[:800])
        except Exception:
            _log.exception("[identity-gen] %s 调用失败 model=%s",
                           backend_kind, getattr(backend, "model_name", "?"))
            return None
    return None


def _parse_json_safely(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


# harness:身份卡描述强制第三人称无主语。AI 常写成「你是XXX」(第二人称),文学上不对 ——
# 身份卡是对"这个身份"的客观描述,不是对玩家喊话。不靠提示词运气,这里确定性剥掉第二人称主语。
_SUBJ_DROP = (("作为你", ""), ("作为您", ""), ("你是", ""), ("您是", ""),
              ("你乃", ""), ("你即", ""), ("你的", ""), ("您的", ""))


def _to_subjectless(text: str) -> str:
    """把第二人称身份描述(「你是XXX」/「你的过去…」)确定性转成第三人称无主语客观叙述。
    无论 LLM 怎么写,保证输出不残留「你/您」第二人称主语。"""
    if not text:
        return text
    s = str(text).strip()
    for a, b in _SUBJ_DROP:
        s = s.replace(a, b)
    s = re.sub(r"[你您]", "", s)                       # 残留的「你/您」→ 删(无主语)
    s = re.sub(r"^[\s，,、。:：；;！!？?]+", "", s)       # 清理删词后留下的前导标点
    s = re.sub(r"[，,、]{2,}", "，", s)                 # 合并因删词产生的连续逗号
    return s.strip()


def _normalize_recommendation(item: Any) -> dict[str, str]:
    """确保 name/role/background 非 null 字符串;并把 role/background 强制成第三人称无主语。"""
    if not isinstance(item, dict):
        item = {}
    return {
        "name": str(item.get("name") or ""),
        "role": _to_subjectless(str(item.get("role") or "")),
        "background": _to_subjectless(str(item.get("background") or "")),
    }


# 注:v27 删除了 _fallback_recommendations。LLM 失败时不再返回硬编码模板,
# 而是显式报错给上层,让 UI 引导用户走"直接用角色卡"或"手动自定义身份"两条路径。
# 历史模板是"剧本主角"+"局外观察者",在前端表现为永远固定两个建议 → 用户体验等同于推荐
# 完全失效。删除是为了暴露真实的 LLM/数据问题(否则 fallback 永远兜底,bug 不会冒头)。


# ────────────────────────────────────────────────────────────
# 工具主函数
# ────────────────────────────────────────────────────────────


def _t_recommend_player_identity(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """推荐初始身份 — script 级工具, executor 签名 (user_id, script_id, args, state)。

    返回值约定:
      成功: {"ok": true, "recommendations": [{name, role, background}, ...]}
      失败: {"ok": false, "error": "<真实错误描述>"}  ← UI 应展示并引导用户改走自定义
    """
    # 参数解析
    sid = script_id or args.get("script_id")
    if not sid:
        return json.dumps({"ok": False, "error": "script_id 必填"}, ensure_ascii=False)
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "error": "script_id 必须是整数"}, ensure_ascii=False)

    phase = (args.get("birthpoint_phase") or "").strip()
    label = (args.get("birthpoint_label") or "").strip()
    card_id_raw = args.get("character_card_id")
    card_kind = (args.get("character_card_kind") or "").strip() or "user_card"
    n_raw = args.get("n", 4)
    try:
        n = max(1, min(6, int(n_raw)))
    except (TypeError, ValueError):
        n = 4
    # player_origin: 'isekai'(穿越/转生) | 'native'(原作角色)
    # 跟身份卡正交 — isekai 时生成"现代灵魂穿越成 X",native 时生成"原作世界 X 身份"
    player_origin = (args.get("player_origin") or "soul").strip().lower()
    if player_origin == "isekai":
        player_origin = "soul"  # 旧值兼容
    if player_origin not in ("soul", "body", "dual", "native"):
        player_origin = "soul"

    # 1) 验证剧本归属
    script_info = _fetch_script_info(sid, user_id)
    if not script_info:
        return json.dumps(
            {"ok": False, "error": "无权访问该剧本"},
            ensure_ascii=False,
        )

    # 2) 拉辅助数据 (软降级, 失败不崩)
    phase_digest = _fetch_phase_digest(sid, phase)
    anchor_info = _fetch_anchor_info(sid, phase, label)
    card: dict | None = None
    card_name_hint = (args.get("character_card_name") or "").strip()
    card_hint = (args.get("character_card_hint") or "").strip()
    import logging as _log2
    _log2.getLogger(__name__).info("[identity-gen] card_id_raw=%s card_name_hint=%s card_hint=%s",
                                    card_id_raw, card_name_hint, card_hint)
    if card_id_raw is not None:
        try:
            card = _fetch_character_card(int(card_id_raw), card_kind, user_id)
        except Exception:
            card = None
    elif card_name_hint:
        # 新建角色卡尚未入库，用前端传来的名称构建虚拟卡片供 prompt 引用
        card = {"name": card_name_hint, "identity": card_hint}
        _log2.getLogger(__name__).info("[identity-gen] 使用虚拟卡片: name=%s identity=%s", card_name_hint, card_hint)

    # 3) 构建 prompt
    system = _build_system_prompt(
        script_title=script_info["title"],
        script_desc=script_info.get("description") or "",
        phase=phase,
        label=label,
        phase_digest=phase_digest,
        anchor_info=anchor_info,
        card=card,
        n=n,
        player_origin=player_origin,
    )

    # 4) 调 LLM
    raw_recs = _call_llm_emit_identities(user_id=user_id, system=system, n=n)

    # 5) 处理结果 — v27: 失败显式报错,不再兜模板
    if not (raw_recs and isinstance(raw_recs, list) and len(raw_recs) > 0):
        # 区分两类失败:1) backend 选择/调用失败(返 None) 2) 返了但空 list
        detail = "LLM 未返回任何推荐"
        if raw_recs is None:
            detail = "LLM 调用失败 (后端未配置/网络错误/响应解析失败)"
        elif isinstance(raw_recs, list) and len(raw_recs) == 0:
            detail = "LLM 返回了空列表,可能是上下文不足或模型拒答"
        return json.dumps(
            {"ok": False, "error": f"身份推荐失败: {detail}。请使用手动创建身份卡或直接使用所选角色卡。"},
            ensure_ascii=False,
        )

    recommendations = [_normalize_recommendation(r) for r in raw_recs[:6]]
    return json.dumps(
        {"ok": True, "recommendations": recommendations},
        ensure_ascii=False,
        indent=2,
    )


# ────────────────────────────────────────────────────────────
# 注册
# ────────────────────────────────────────────────────────────


def register_creative_tools() -> None:
    registry = get_registry()

    spec = ToolSpec(
        name="recommend_player_identity",
        description=(
            "新建存档时, 根据剧本 + 出生点 + 角色卡, 用 LLM 推荐 3-5 个契合该出生点"
            "剧情阶段的初始身份 (玩家在剧本世界中的定位/职业/动机)。"
            "返回 JSON {ok, recommendations:[{name,role,background}]}。"
            "出生点可选: birthpoint_phase (阶段名) + birthpoint_label (时间锚点标签)。"
            "角色卡可选: character_card_id + character_card_kind (persona/user_card/script_card)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "script_id": {
                    "type": "integer",
                    "description": "目标剧本 id",
                },
                "birthpoint_phase": {
                    "type": "string",
                    "description": "出生点对应的剧情阶段名 (可选)",
                },
                "birthpoint_label": {
                    "type": "string",
                    "description": "出生点时间锚点标签 (可选, 如 story_time_label)",
                },
                "character_card_id": {
                    "type": "integer",
                    "description": "已选角色卡 id (可选)",
                },
                "character_card_kind": {
                    "type": "string",
                    "enum": ["persona", "user_card", "script_card"],
                    "description": "角色卡类型 (可选, 默认 user_card)",
                },
                "n": {
                    "type": "integer",
                    "description": "推荐身份数量 (默认 4, 范围 1-6)",
                    "default": 4,
                    "minimum": 1,
                    "maximum": 6,
                },
            },
            "required": ["script_id"],
        },
        executor=_t_recommend_player_identity,
        scope="script",
        origins=_USER_ORIGINS_READ,
        destructive=False,
    )

    if not registry.has(spec.name):
        registry.register(spec)


__all__ = ["register_creative_tools"]
