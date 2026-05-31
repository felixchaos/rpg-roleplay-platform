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
import re
from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry

# 与 command_tools_saves.py 保持一致 — 任何 origin 都可以调只读工具
_USER_ORIGINS_READ = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat", "console_assistant",
})


# ────────────────────────────────────────────────────────────
# 数据拉取
# ────────────────────────────────────────────────────────────


def _fetch_script_info(script_id: int, user_id: int) -> dict[str, Any] | None:
    """从 DB 拉剧本 title + description, 并验证 owner 归属。

    返回 None 代表无权访问或不存在。
    """
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select id, title, description from scripts "
                "where id = %s and owner_id = %s",
                (script_id, user_id),
            ).fetchone()
        if not row:
            return None
        return {"id": row["id"], "title": row["title"], "description": row["description"]}
    except Exception:
        return None


def _fetch_phase_digest(script_id: int, phase: str) -> str:
    """按 story_phase 模糊匹配, 拉 phase_digests / chapter_facts summary。

    返回空字符串表示没拿到 (软降级)。
    """
    if not phase:
        return ""
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            # 先查 save_phase_digests (汇总更好, 但要 save_id — 这里按 script 查全局摘要)
            # 实际上 save_phase_digests 是 save 级的, 无 script 直接索引;
            # 退而查 chapter_facts 按 story_phase 模糊匹配, 取前 3 条 sample
            rows = db.execute(
                "select summary from chapter_facts "
                "where script_id = %s and story_phase ilike %s "
                "order by chapter limit 5",
                (script_id, f"%{phase[:30]}%"),
            ).fetchall() or []
        if rows:
            return " | ".join(r["summary"][:120] for r in rows if r.get("summary"))[:800]
    except Exception:
        pass
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
                row = db.execute(
                    "select cc.name, cc.identity, cc.appearance, cc.personality "
                    "from character_cards cc "
                    "join scripts s on cc.script_id = s.id "
                    "where cc.id = %s and s.owner_id = %s",
                    (card_id, user_id),
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
) -> str:
    lines = [
        "你是 RPG 平台的身份推荐助手。",
        "根据玩家选定的剧本、出生点和角色卡, 为玩家推荐契合该剧情阶段的初始身份。",
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
        "  · background: 30-80 字, 说明角色来历与出生点阶段的关联",
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
        except Exception:
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


def _normalize_recommendation(item: Any) -> dict[str, str]:
    """确保 name/role/background 都是非 null 字符串。"""
    if not isinstance(item, dict):
        item = {}
    return {
        "name": str(item.get("name") or ""),
        "role": str(item.get("role") or ""),
        "background": str(item.get("background") or ""),
    }


def _fallback_recommendations(n: int) -> list[dict[str, str]]:
    """LLM 失败时返回通用模板 (1-2 条)。"""
    templates = [
        {
            "name": "",
            "role": "剧本主角",
            "background": "基于剧本设定推演的默认主角视角, 与核心事件直接相关的亲历者。",
        },
        {
            "name": "",
            "role": "局外观察者",
            "background": "以旁观者身份卷入剧本事件, 掌握独特视角与信息, 逐渐成为关键参与方。",
        },
    ]
    return templates[:max(1, min(n, 2))]


# ────────────────────────────────────────────────────────────
# 工具主函数
# ────────────────────────────────────────────────────────────


def _t_recommend_player_identity(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """推荐初始身份 — script 级工具, executor 签名 (user_id, script_id, args, state)。"""
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
    if card_id_raw is not None:
        try:
            card = _fetch_character_card(int(card_id_raw), card_kind, user_id)
        except Exception:
            card = None

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
    )

    # 4) 调 LLM
    raw_recs = _call_llm_emit_identities(user_id=user_id, system=system, n=n)

    # 5) 处理结果
    if raw_recs and isinstance(raw_recs, list) and len(raw_recs) > 0:
        recommendations = [_normalize_recommendation(r) for r in raw_recs[:6]]
    else:
        # fallback — 不崩, 返模板
        recommendations = _fallback_recommendations(n)

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
