"""set_parser.py — task 77：/set 自然语言参数解析子代理

设计动机（codex §4+7.3）：
当前 detect_set_directive 只能识别 /set path=value 这种简单形式。
玩家说 "/set 蕾穆丽娜对斯雷因的信任度降低，因为她发现他隐瞒情报"
不能精确落到多个字段。

set_parser 用便宜 LLM 把自然语言 /set 拆成多条 JSON ops：
  - relationships.<角色>: 关系变化
  - memory.facts (append): 本局事实
  - worldline.user_variables.<key>: 硬约束变量
  - player.* / world.*: 属性修改
  - hypothesis: 玩家想假设的内容

接口：
    parse_set_directive(set_text, state_data, user_id=None, ...) → list[dict]

失败语义：
- 模型异常 / JSON 解析失败 → 返回 []（外层不破坏 /set 流程）
- 模型说"无法拆分" → 返回 []

调用约定：
- 外层（ui.py）应在 apply_player_directives 之后调用本模块
- 本模块返回的 ops 由外层走 apply_state_write(source="user:/set:parser", force=True)
- 完全 opt-in: 用户 preferences.set_parser.enabled = true 才启用

模型选择（同 extractor）：
1. api_id_override / model_override
2. user_preferences.set_parser.api_id / model_real_name
3. 默认 vertex_ai / gemini-3.5-flash（最便宜的当代旗舰）
"""
from __future__ import annotations

import json
import re

_SET_PARSER_SYSTEM = """\
你是 /set 解析器。玩家用自然语言描述了想强制改写的设定，
你把它拆成精确的 JSON ops 列表让系统写入。**不要写小说**，只输出 JSON。

可写字段：
- player.name / player.role / player.background / player.current_location
- world.time / world.weather / world.timeline.current_phase
- memory.main_quest / memory.current_objective
- memory.facts (用 op=append)
- relationships.<角色名>  - 关系状态如 "信任/警惕/敌意/亲近/紧张/疏离" 等
- worldline.user_variables.<变量名>  - 玩家硬约束变量

禁止写入（硬黑名单）：
- permissions.* / history.* / schema_version

可用 op：
- "set":      覆盖字段
- "append":   往列表追加（仅 memory.facts/resources/abilities/pinned/notes 等列表字段）
- "hypothesis": 玩家想假设的内容，单独存放不入 facts

输出格式（**严格 JSON，不要 markdown，不要解释**）：
{
  "ops": [
    {"op":"set","path":"relationships.斯雷因","value":"信任下降"},
    {"op":"append","path":"memory.facts","value":"蕾穆丽娜发现斯雷因隐瞒情报"},
    {"op":"set","path":"worldline.user_variables.trust_slaine","value":"蕾穆丽娜对斯雷因信任下降"}
  ]
}

如果玩家话语模糊（如 "/set 让剧情更黑暗一点"），不要瞎拆，输出 ops=[]
让 GM 在叙事里自然吸收；不要硬塞 set 字段。

如果完全没有可拆解的字段意图，输出 {"ops":[]}.
"""


def _build_user_prompt(set_text: str, state_data: dict) -> str:
    """组装 set_parser 的 user message：当前 state 快照 + /set 文本。"""
    p = (state_data.get("player") or {})
    rels = (state_data.get("relationships") or {})
    m = (state_data.get("memory") or {})
    snippet = (
        f"## 当前状态快照\n"
        f"- player.name = {p.get('name', '') or '(空)'}\n"
        f"- player.role = {p.get('role', '') or '(空)'}\n"
        f"- player.current_location = {p.get('current_location', '') or '(空)'}\n"
        f"- memory.main_quest = {m.get('main_quest', '') or '(空)'}\n"
        f"- 已识别关系：{', '.join(list(rels.keys())[:10]) or '(无)'}\n"
    )
    return snippet + "\n\n## 玩家的 /set 文本\n" + (set_text or "")[:1200]


_JSON_FENCE = re.compile(r"```(?:json)?\s*\n?\s*([\[\{][\s\S]*?[\]\}])\s*\n?```", re.MULTILINE)


def _parse_parser_output(text: str) -> list[dict]:
    """从模型回复里抠出 ops 数组。支持裸 dict {ops:[...]} / 裸数组 / fence。"""
    if not text:
        return []
    text = text.strip()
    # 1) 整段是 JSON
    for candidate in (text, text.lstrip("`json").rstrip("`").strip()):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "ops" in parsed:
                ops = parsed["ops"]
                if isinstance(ops, list):
                    return [o for o in ops if isinstance(o, dict)]
            if isinstance(parsed, list):
                return [o for o in parsed if isinstance(o, dict)]
        except Exception:
            pass
    # 2) ```json 块兜底
    for m in _JSON_FENCE.finditer(text):
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, dict) and "ops" in parsed:
                ops = parsed["ops"]
                if isinstance(ops, list):
                    return [o for o in ops if isinstance(o, dict)]
            if isinstance(parsed, list):
                return [o for o in parsed if isinstance(o, dict)]
        except Exception:
            continue
    return []


def parse_set_directive(
    set_text: str,
    state_data: dict,
    user_id: int | None = None,
    model_override: str | None = None,
    api_id_override: str | None = None,
    timeout_sec: int = 15,
) -> list[dict]:
    """主入口。失败返回 []。

    复用 extractor.py 已经写好的 3 条 native function calling 通道
    （Anthropic tool_use / Vertex JSON mode / OpenAI response_format），
    没必要重新发明轮子——它的实现就是结构化 JSON 调用，set_parser 用法完全一样。
    """
    if not set_text or not set_text.strip():
        return []

    api_id = api_id_override or _resolve_preferred_api(user_id) or "vertex_ai"
    model = model_override or _resolve_preferred_model(user_id) or "gemini-3.5-flash"

    try:
        # 复用 extractor 的 backend dispatcher（同 schema 同协议同 fallback）
        from agents.extractor import _call_extractor_backend
        text = _call_extractor_backend(
            api_id=api_id,
            model=model,
            system_prompt=_SET_PARSER_SYSTEM,
            user_prompt=_build_user_prompt(set_text, state_data),
            user_id=user_id,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        print(f"[set_parser] call failed: {exc}")
        return []
    return _parse_parser_output(text)


def _resolve_preferred_model(user_id: int | None) -> str | None:
    if not user_id:
        return None
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (user_id,),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            return row["preferences"].get("set_parser.model_real_name") or None
    except Exception:
        return None
    return None


def _resolve_preferred_api(user_id: int | None) -> str | None:
    if not user_id:
        return None
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (user_id,),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            return row["preferences"].get("set_parser.api_id") or None
    except Exception:
        return None
    return None
