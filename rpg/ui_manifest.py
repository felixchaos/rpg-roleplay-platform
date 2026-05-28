"""
ui_manifest.py — task 68/72: 统一 UI Action 机制 (Claude-in-Chrome 模式)。

设计核心:
  · 助手只见 5 把工具 — ui_describe / ui_invoke / ask_user_choice
    / ask_user_text / navigate_to_setting,而不是 52 把。
  · ui_describe(intent) → 模糊匹配后返 top N 工具卡片 (含参数表 + 是否 destructive)
  · ui_invoke(tool, args) → 检查 required 字段缺失 → 缺则返 NEEDS_USER_INPUT
    哨兵触发选择/输入框;不缺就走原 dispatcher。
  · 工具的 intent_keywords + side_effect_topics 在这里集中注入,
    不污染各 command_tools_*.py 模块。

机制层强制 "先问后做": ui_invoke 检测 required 缺失 → console_assistant 看到
NEEDS_USER_INPUT: 哨兵 → 强制 yield user_choice_required SSE → LLM 没机会
"凭直觉直接调"。这取代了之前一行行 prompt 规则。
"""
from __future__ import annotations

import json
from dataclasses import replace as dc_replace
from typing import Any

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope,
    ToolSpec,
    get_registry,
)

# ────────────────────────────────────────────────────────────
# 工具 → (intent_keywords, side_effect_topics) 标签表
# ────────────────────────────────────────────────────────────


# 维护准则:
#   keywords 写口语化的用户表达,3-6 个就够 (供 ui_describe 模糊匹配);
#   topics 是状态变更广播主题,前端按主题订阅页面刷新。
_TAG_TABLE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # ─── 存档 ───────────────────────────────────────────
    "create_save": (
        ("新建存档", "新局", "开局", "新游戏", "create save", "new save"),
        ("saves",),
    ),
    "delete_save": (("删除存档", "删档", "delete save"), ("saves",)),
    "activate_save": (("切换存档", "切到", "用存档", "进入存档", "activate save"), ("saves",)),
    "rename_save": (("重命名存档", "改存档名", "rename save"), ("saves",)),
    "list_my_saves": (("查看存档", "我的存档", "存档列表", "list saves"), ()),
    "get_save_detail": (("存档详情", "看存档", "save detail"), ()),
    # ─── 剧本 ───────────────────────────────────────────
    "list_scripts": (("查看剧本", "我的剧本", "剧本列表", "list scripts"), ()),
    "delete_script": (("删除剧本", "删剧本", "delete script"), ("scripts",)),
    "start_script_import": (("导入剧本", "新建剧本", "import script"), ("scripts",)),
    "cancel_import_job": (("取消导入", "cancel import"), ("scripts",)),
    "resplit_script": (("重新切章", "重切剧本", "resplit"), ("scripts",)),
    "get_script_chapters": (("剧本章节", "看章节"), ()),
    "list_script_npcs": (("剧本里的 NPC", "NPC 列表", "script npcs"), ()),
    "get_chapter_facts": (("章节事实",), ()),
    "get_worldbook": (("世界设定", "worldbook"), ()),
    "get_import_status": (("导入进度", "import status"), ()),
    "list_my_import_jobs": (("导入任务", "import jobs"), ()),
    # ─── 分支 ───────────────────────────────────────────
    "list_branches": (("分支列表", "branches"), ()),
    "activate_branch": (("切换分支", "activate branch"), ("branches",)),
    "continue_branch": (("继续分支", "continue branch"), ("branches",)),
    "delete_branch": (("删除分支", "delete branch"), ("branches",)),
    # ─── 用户角色卡 / persona ─────────────────────────
    "create_character_card": (
        ("创建角色", "建角色卡", "建用户角色", "新人设", "做一张卡",
         "create character", "create card"),
        ("cards",),
    ),
    "delete_character_card": (("删除角色", "删卡", "delete card"), ("cards",)),
    "list_my_character_cards": (("我的角色卡", "我的卡", "list cards"), ()),
    "generate_character_card_draft": (
        ("扩展人设", "生成草稿", "完善角色", "generate draft"),
        (),
    ),
    "refine_character_card_draft": (
        ("调整人设", "再改改", "改角色", "refine draft"),
        (),
    ),
    "create_persona": (
        ("创建 persona", "建身份", "新 persona", "create persona"),
        ("personas",),
    ),
    "delete_persona": (("删除 persona", "删身份", "delete persona"), ("personas",)),
    "list_my_personas": (("我的 persona", "身份列表", "list personas"), ()),
    # ─── 设置 / 模型 ───────────────────────────────────
    "set_preference": (("设置偏好", "改偏好", "set pref"), ("preferences",)),
    "select_model": (("切换模型", "改模型", "select model"), ("preferences",)),
    "list_available_models": (("可用模型", "模型列表"), ()),
    "probe_models": (("探测模型", "probe models"), ()),
    "list_my_credentials_meta": (("我的 API key", "凭证列表", "credentials"), ()),
    # ─── 模组 (rules) ───────────────────────────────
    "list_modules": (("模组列表", "list modules"), ()),
    # ─── MCP server ───────────────────────────────────
    "mcp_server_enable": (("启用 MCP", "禁用 MCP", "mcp toggle"), ("mcp",)),
    "mcp_server_start": (("启动 MCP", "mcp start"), ("mcp",)),
    "mcp_server_stop": (("停止 MCP", "mcp stop"), ("mcp",)),
    # ─── 游戏 save 内 (查询) ─────────────────────────
    "get_game_state": (("游戏状态", "game state"), ()),
    "get_current_scene": (("当前场景", "scene"), ()),
    "get_known_events": (("已知事件", "events"), ()),
    "get_user_variables": (("用户变量", "user variables"), ()),
    "get_pending_questions": (("待回答问题", "pending questions"), ()),
    "get_pending_writes": (("待审批写入", "pending writes"), ()),
    "list_relationships": (("关系", "relationships"), ()),
    "query_memory": (("查记忆", "memory"), ()),
    "recent_audit_log": (("审计日志", "audit log"), ()),
    "get_my_usage": (("用量", "usage"), ()),
    "get_my_stats": (("统计", "stats"), ()),
    # ─── 工具自查 ────────────────────────────────────
    "list_available_tools": (("可用工具", "available tools"), ()),
    # ─── 助手内建 (sentinel 类) ────────────────────────
    "ask_user_choice": (("询问选择",), ()),
    "navigate_to_setting": (("跳转到设置", "去设置", "navigate"), ()),
}


def apply_tags() -> None:
    """对已注册的 ToolSpec 注入 intent_keywords / side_effect_topics / input_examples。
    没有标签的工具不变 (仍可见,但 ui_describe 不会主动推它)。"""
    reg = get_registry()
    for name, (kw, topics) in _TAG_TABLE.items():
        spec = reg.get(name)
        if spec is None:
            continue
        if not spec.intent_keywords and not spec.side_effect_topics:
            reg.replace(dc_replace(spec, intent_keywords=kw, side_effect_topics=topics))
    # task 98: 给关键工具加 input_examples (Anthropic 2025-11 advanced tool use)
    # 实验数据: 72% → 90% 复杂参数准确率。
    for name, examples in _INPUT_EXAMPLES.items():
        spec = reg.get(name)
        if spec is None or spec.input_examples:
            continue
        reg.replace(dc_replace(spec, input_examples=tuple(examples)))


# task 98: 给最常用工具配 2-3 个具体调用样本。
_INPUT_EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "create_character_card": [
        {"name": "晓星", "summary": "开朗元气、自来熟", "identity": "女高中生穿越者"},
        {"name": "蓝魅", "summary": "冷静腹黑、谋略型", "identity": "流亡贵族"},
        {"name": "测试角色", "summary": "傲娇内向", "identity": "失忆剑士"},
    ],
    "create_persona": [
        {"name": "晓卡", "summary": "穿越者视角玩家", "role": "现代穿越来的高中生"},
    ],
    "create_save": [
        {"script_id": 9803, "title": "主线第一周目"},
        {"script_id": 9803, "title": "重温柏林段", "persona_id": 46},
    ],
    "generate_character_card_draft": [
        {"brief": "20 岁女法师, 流亡贵族", "kind": "user"},
        {"brief": "薇瑟帝国郡主, 源血脉者, 前世记忆", "kind": "user", "script_id": 9803},
    ],
    "refine_character_card_draft": [
        {"previous_draft": {"name": "晓星", "personality": "开朗"},
         "feedback": "再傲娇一点, 增加冷淡气质"},
    ],
    "ask_user_choice": [
        {"question": "给新角色取个什么名字?",
         "options": ["晓星", "阿狸", "凌儿", "蓝魅"],
         "allow_free_text": True},
        {"question": "性格偏哪种?",
         "options": ["开朗元气", "冷静腹黑", "傲娇内向", "温柔治愈"],
         "allow_free_text": True,
         "context": "影响后续 generate_character_card_draft 的 brief"},
    ],
    "select_model": [
        {"api_id": "vertex_ai", "model": "gemini-3.5-flash"},
        {"api_id": "anthropic", "model": "claude-opus-4-7"},
    ],
    "set_preference": [
        {"key": "ui.theme", "value": "dark"},
        {"key": "chat.default_model_kind", "value": "vertex_ai"},
    ],
    "activate_save": [{"save_id": 13735}],
    "delete_save": [{"save_id": 13744}],
    "rename_save": [{"save_id": 13735, "title": "周目二存档"}],
    "delete_character_card": [{"card_id": 306}],
    "delete_persona": [{"persona_id": 46}],
}


# ────────────────────────────────────────────────────────────
# Human-required 字段 — 语义上必问用户但 schema 没 required 的
# ────────────────────────────────────────────────────────────


# 这个表的存在是因为:
#   后端 schema required 是"DB 不报错的最小集",通常只一两个字段;
#   但用户语义上"创建角色卡"必须要性格/外貌等才有意义,空 summary 是垃圾数据。
# ui_invoke 把这表和 schema.required 取并集, 缺即弹询问框。
# 加新功能时,把"语义必填但不强制"的字段加到这里即可,不需要碰 prompt。
_HUMAN_REQUIRED: dict[str, list[str]] = {
    "create_character_card": ["summary", "identity"],  # 性格 + 身份必问
    "create_persona": ["summary"],
    "create_save": ["script_id", "title"],
    "generate_character_card_draft": ["brief"],
    "refine_character_card_draft": ["previous_draft", "feedback"],
    "rename_save": ["save_id", "title"],
    "delete_save": ["save_id"],
    "delete_character_card": ["card_id"],
    "delete_persona": ["persona_id"],
    "delete_script": ["script_id"],
    "activate_save": ["save_id"],
    "activate_branch": ["save_id", "branch_node_id"],
    "set_preference": ["key", "value"],
    "select_model": ["api_id", "model"],
    "start_script_import": ["title"],
}


def human_required(action_id: str) -> list[str]:
    return list(_HUMAN_REQUIRED.get(action_id, []))


# Field-level 友好提示 + 候选值 (供 NEEDS_USER_INPUT 渲染选择/输入框)
# task 94: 所有 free-text 字段都给一些建议选项,允许"自由输入"兜底。
# 这样用户永远看到的是"选 + 自由输入"的双轨卡片,而不是孤零零的文本框。
_FIELD_HINTS: dict[str, dict[str, Any]] = {
    "summary": {
        "question": "角色性格是什么样?",
        "options": ["开朗元气", "冷静腹黑", "傲娇内向", "温柔治愈"],
        "free_text_ok": True,
    },
    "identity": {
        "question": "角色的身份背景?",
        "options": ["女高中生穿越者", "流亡贵族", "失忆剑士", "落魄世家继承人"],
        "free_text_ok": True,
        "placeholder": "或自由描述",
    },
    "name": {
        "question": "给角色取个名字?",
        "options": ["晓星", "阿狸", "凌儿", "蓝魅"],
        "free_text_ok": True,
        "placeholder": "或自定义",
    },
    "title": {
        "question": "起个标题?",
        "options": ["主线第一周目", "支线探索", "重温段落", "自由实验"],
        "free_text_ok": True,
        "placeholder": "或自定义",
    },
    "script_id": {
        "question": "选哪个剧本?",
        "placeholder": "剧本 ID (先调 list_scripts 看选项)",
    },
    "feedback": {
        "question": "想怎么改?",
        "options": ["再傲娇一点", "年龄改小一些", "加一段秘密往事", "性格更冷淡"],
        "free_text_ok": True,
        "placeholder": "或自由描述",
    },
    "brief": {
        "question": "用一句话描述这个新角色?",
        "options": ["20 岁女法师,流亡贵族",
                    "15 岁少年剑士,失忆穿越者",
                    "中性向魔女,无限魔力",
                    "薇瑟帝国郡主,源血脉者"],
        "free_text_ok": True,
        "placeholder": "或自由描述",
    },
}


def field_hint(field: str) -> dict[str, Any]:
    return dict(_FIELD_HINTS.get(field, {}))


# ────────────────────────────────────────────────────────────
# ui_describe — 模糊匹配工具
# ────────────────────────────────────────────────────────────


def _score_match(spec: ToolSpec, query: str, query_terms: list[str]) -> int:
    """中英文友好匹配:
    1. 任一 intent_keyword 是 query 子串 → +5 (反向匹配 — 用户原话包含关键词)
    2. 任一 query_term 是 keyword/name/desc 子串 → 按位置加分
    """
    if not query and not query_terms:
        return 0
    score = 0
    q_lo = (query or "").lower()
    for kw in spec.intent_keywords:
        if kw and kw.lower() in q_lo:
            score += 5
    hay_kw = " ".join(spec.intent_keywords).lower()
    hay_name = spec.name.lower()
    hay_desc = (spec.description or "").lower()
    for q in query_terms:
        q = q.lower().strip()
        if not q:
            continue
        if q in hay_kw:
            score += 3
        if q in hay_name:
            score += 2
        if q in hay_desc:
            score += 1
    return score


def _render_param(name: str, spec: dict[str, Any], required: bool) -> dict[str, Any]:
    return {
        "name": name,
        "type": spec.get("type", "string"),
        "description": spec.get("description") or "",
        "required": required,
        "enum": spec.get("enum"),
        "example": spec.get("example"),
    }


def _spec_to_card(spec: ToolSpec) -> dict[str, Any]:
    schema = spec.input_schema or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    params = [_render_param(n, props.get(n) or {}, n in required) for n in props.keys()]
    return {
        "id": spec.name,
        "label": spec.description,
        "scope": spec.scope,
        "destructive": spec.destructive,
        "intent_keywords": list(spec.intent_keywords),
        "side_effect_topics": list(spec.side_effect_topics),
        "params": params,
    }


def ui_describe(user_id: int, args: dict) -> str:
    """工具 executor (user scope, signature: user_id, args)。

    args: { intent?: str, page?: str, limit?: int=8 }
    返回: JSON 列表 of action cards,最多 limit 个,按匹配分降序。
    intent 为空时返通用 top-N (按字母序前 limit 个)。
    """
    intent = (args.get("intent") or "").strip()
    limit = max(1, min(20, int(args.get("limit") or 8)))
    page = (args.get("page") or "").strip().lower()
    reg = get_registry()
    visible = [s for s in reg.list_all() if "console_assistant" in s.origins]
    # 内建工具不进 describe 列表 (避免循环)
    visible = [s for s in visible if s.name not in
               ("ui_describe", "ui_invoke", "ask_user_choice",
                "ask_user_text", "navigate_to_setting")]

    if intent:
        terms = [t for t in intent.replace(",", " ").replace(",", " ").split() if t.strip()]
        # 同时支持 intent + page: 先按 intent 评分,page 命中再额外 +2 分
        page_kw_for_score = {
            "saves": ["save", "branch"],
            "scripts": ["script", "import", "chapter"],
            "cards": ["card", "character", "persona"],
            "settings": ["preference", "model", "credential", "mcp"],
        }.get(page, []) if page else []
        def _full_score(s: ToolSpec) -> int:
            base = _score_match(s, intent, terms)
            if page_kw_for_score:
                blob = (" ".join(s.intent_keywords) + " " + s.name).lower()
                if any(pk in blob for pk in page_kw_for_score):
                    base += 2
            return base
        scored = [(_full_score(s), s) for s in visible]
        scored = [(sc, s) for sc, s in scored if sc > 0]
        scored.sort(key=lambda x: (-x[0], x[1].name))
        picks = [s for _, s in scored[:limit]]
        # task 95: 0 命中也兜底返前 N 个 (按字母序),让 LLM 一次就拿到候选,
        # 不会因为 matched=0 就反复 retry 不同 intent 浪费 iteration。
        if not picks:
            picks = sorted(visible, key=lambda s: s.name)[:limit]
    elif page:
        page_kw = {
            "saves": ["save", "branch"],
            "scripts": ["script", "import", "chapter"],
            "cards": ["card", "character", "persona"],
            "settings": ["preference", "model", "credential", "mcp"],
        }.get(page, [page])
        picks = [s for s in visible
                 if any(k in (" ".join(s.intent_keywords) + " " + s.name).lower()
                        for k in page_kw)][:limit]
    else:
        picks = sorted(visible, key=lambda s: s.name)[:limit]

    cards = [_spec_to_card(s) for s in picks]
    return json.dumps({"matched": len(cards), "actions": cards},
                      ensure_ascii=False, indent=2)


# ────────────────────────────────────────────────────────────
# ui_invoke — 缺字段哨兵 + dispatch
# ────────────────────────────────────────────────────────────


# 当用户 args 缺失任何 required field 时, ui_invoke 返此前缀,
# console_assistant.stream_chat 检测后强制 yield user_choice_required SSE。
NEEDS_INPUT_SENTINEL = "NEEDS_USER_INPUT:"


def _value_is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def _missing_required(spec: ToolSpec, args: dict) -> list[str]:
    """合并 schema.required 与 _HUMAN_REQUIRED, 检查 args 里这些键是否非空。"""
    schema = spec.input_schema or {}
    schema_req = list(schema.get("required") or [])
    human_req = human_required(spec.name)
    # 合并保序、去重
    seen: set[str] = set()
    all_req: list[str] = []
    for n in schema_req + human_req:
        if n not in seen:
            seen.add(n)
            all_req.append(n)
    return [n for n in all_req if _value_is_empty(args.get(n))]


def _build_needs_input_payload(spec: ToolSpec, args: dict,
                               missing: list[str]) -> dict[str, Any]:
    props = (spec.input_schema or {}).get("properties") or {}
    first = missing[0]
    p = props.get(first) or {}
    # 优先用 _FIELD_HINTS 的更友好版本, 否则 fallback 用 schema 里的 description
    hint = field_hint(first)
    question = hint.get("question") or (
        f"创建 {spec.description.split('。')[0] if spec.description else spec.name} "
        f"需要先告诉我 `{first}`"
    )
    options = hint.get("options") or p.get("enum") or p.get("examples") or []
    allow_free = hint.get("free_text_ok", True) if options else True
    placeholder = hint.get("placeholder") or (p.get("description") or "")
    return {
        "action_id": spec.name,
        "missing": missing,
        "next_field": first,
        "question": question,
        "options": list(options) if options else [],
        "allow_free_text": allow_free,
        "placeholder": placeholder,
        "context": (
            f"还差 {len(missing)} 项: {', '.join(missing)}. "
            f"答完后由助手再次执行 {spec.name}。"
        ),
    }


def ui_invoke(user_id: int, args: dict) -> str:
    """工具 executor (user scope, signature: user_id, args)。

    args: { action_id: str, args: dict }
    返回:
      · 缺 required → "NEEDS_USER_INPUT:<json payload>"  (前端弹选择/输入框)
      · 否则 → 原 dispatcher 执行结果 (失败也走原文返回)
    """
    # 延迟 import 防循环
    from tools_dsl.command_dispatcher import (
        ToolCallEnvelope as _Env,
    )
    from tools_dsl.command_dispatcher import (
        ToolDispatcher as _Disp,
    )
    from tools_dsl.command_dispatcher import (
        get_registry as _get_reg,
    )

    action_id = (args.get("action_id") or "").strip()
    sub_args = args.get("args") if isinstance(args.get("args"), dict) else {}
    if not action_id:
        return "失败: action_id 为空"
    reg = _get_reg()
    spec = reg.get(action_id)
    if spec is None:
        return f"失败: 未知 action_id={action_id}"
    if "console_assistant" not in spec.origins:
        return (f"失败: 工具 {action_id} 不允许 console_assistant 调用 "
                f"(允许: {sorted(spec.origins)})")

    missing = _missing_required(spec, sub_args)
    if missing:
        payload = _build_needs_input_payload(spec, sub_args, missing)
        return f"{NEEDS_INPUT_SENTINEL}{json.dumps(payload, ensure_ascii=False)}"

    # 真正 dispatch — 拿一个临时 dispatcher,复用全局 registry。
    # 注意: ui_invoke 自己是 user scope 工具,被 console_assistant 调过来,
    # 这里用 origin="console_assistant" 转调子工具。
    disp = _Disp(registry=reg)
    env = _Env(
        user_id=user_id, tool=action_id, args=sub_args,
        origin="console_assistant",
    )
    result = disp.dispatch_sync(env)
    if not result.ok:
        return f"失败: {result.error or result.result or '未知'}"
    return result.result


__all__ = [
    "apply_tags",
    "ui_describe",
    "ui_invoke",
    "NEEDS_INPUT_SENTINEL",
]
