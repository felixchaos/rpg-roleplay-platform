"""
command_agent.py — task 86: /set 命令的 LLM 工具调用入口。

用户反馈:
> 规则判断永远会有 bug。让 LLM 接管命令,用大模型理解用户自然语言,
> 然后对照实际支持的命令表用"工具调用"完成指令。

设计:
  · system prompt 明示工具调用语义,列出工具表能做什么
  · 用 Anthropic tool_use(强 schema) 让模型可以**并行**调多个工具
    (一次 /set 可能写多个字段,如 "/set 关系=X,主线=Y" 直接拆成 2 个工具调用)
  · Vertex Gemini / OpenAI 兼容 fallback: 走 JSON mode 返回
    [{"name":...,"input":{...}}, ...] 列表
  · 失败时 (模型没产 tool_call / 解析失败) 返回 [],由调用方决定 fallback

公开 API:
    parse_set_command(set_text, state_data, user_id=None) → list[dict]
        返回 [{"name": str, "input": dict}, ...] 准备给 command_tools.execute_tool。
"""
from __future__ import annotations

import json
import os
from typing import Any

from tools_dsl.command_tools import COMMAND_TOOLS


# ────────────────────────────────────────────────────────────
# Prompts
# ────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
你是 /set 命令的解析助手。玩家用自然语言写了想强制修改游戏状态的指令,
你的任务是**调用工具表里的工具**来完成,而不是输出文本。

关键原则:
1. **只调工具,不写小说**。即便玩家话语带剧情色彩,你也只负责拆出操作。
2. **多操作合并**:如果一句话含多项操作(如"设置时间为月球,关系蕾穆丽娜=信任,主线=营救她"),
   一次性返回多个工具调用。
3. **工具表里没有的事就不做**。如果玩家想改权限/历史/schema_version 等元数据,
   工具表里没有对应工具,直接用 clarify 工具问玩家"我不能改这个字段"。
   **不要**尝试用其他工具绕开,这是安全设计。
4. **模糊话就 clarify**。如果玩家话语真的不清楚,用 clarify 工具问明白。
   不要瞎拆。
5. **保留用户原话**:工具 args 里的 text/target/value 用玩家自己的语言,
   不要替玩家二次叙述(除非用户写的是"我想让X发生",才剥掉"我想让")。

工具表选择指南 (常见映射):

  时间相关:
    "设置时间为X" / "时间线=X" / "切换到X" / "进入X章" → set_world_time(target=X)
  位置:
    "位置改为X" / "现在在X" → set_player_location(location=X)
  玩家档案:
    "名字=X" → set_player_name
    "身份/职业/定位=X" → set_player_role
    "背景=X" → set_player_background
  关系:
    "NPC关系=信任" → set_relationship(character=NPC, status=信任)
  记忆:
    "主线=X" / "目标=X(长远)" → set_main_quest
    "当前目标=X" → set_current_objective
    "事实:X" → add_memory_fact
    "资源:X" / "我有X" → add_memory_resource
    "能力:X" → add_memory_ability
    "重要:X" / "钉住:X" → pin_memory
    "笔记:X" → add_memory_note
    "记忆模式=concise/normal/deep" → set_memory_mode
  推测/约束:
    "假设/我猜/可能X" → add_hypothesis
    "硬约束变量X=Y" → set_user_variable
"""


def _build_user_prompt(set_text: str, state_data: dict) -> str:
    """组装 user message:当前 state 快照 + /set 文本。"""
    p = (state_data.get("player") or {})
    rels = (state_data.get("relationships") or {})
    m = (state_data.get("memory") or {})
    w = (state_data.get("world") or {})
    snippet = (
        "## 当前状态快照\n"
        f"- player.name = {p.get('name', '') or '(空)'}\n"
        f"- player.role = {p.get('role', '') or '(空)'}\n"
        f"- player.current_location = {p.get('current_location', '') or '(空)'}\n"
        f"- world.time = {w.get('time', '') or '(空)'}\n"
        f"- world.timeline.current_label = {(w.get('timeline') or {}).get('current_label', '') or '(空)'}\n"
        f"- memory.main_quest = {m.get('main_quest', '') or '(空)'}\n"
        f"- memory.current_objective = {m.get('current_objective', '') or '(空)'}\n"
        f"- 已识别关系: {', '.join(list(rels.keys())[:10]) or '(无)'}\n"
    )
    return snippet + "\n\n## 玩家 /set 文本\n" + (set_text or "")[:1500]


# ────────────────────────────────────────────────────────────
# Backend dispatch
# ────────────────────────────────────────────────────────────


def parse_set_command(
    set_text: str,
    state_data: dict,
    user_id: int | None = None,
    model_override: str | None = None,
    api_id_override: str | None = None,
    timeout_sec: int = 15,
) -> list[dict]:
    """主入口。返回 [{"name":..., "input":...}, ...] 工具调用列表。

    失败 (异常 / 模型不配合) 返回 [],外层决定 fallback。
    """
    if not set_text or not set_text.strip():
        return []

    # 模型偏好与 set_parser 共享(用户在前端 preferences 里设的同一项)。
    # 默认 backend: Vertex Gemini (与 GM 一致;部署里 vertex_sa.json 已配),
    # 而不是 Anthropic — 多数本地部署没配 ANTHROPIC_API_KEY,导致 401 → fallback。
    api_id = api_id_override or _resolve_preferred_api(user_id) or _detect_default_api()
    model = model_override or _resolve_preferred_model(user_id) or _default_model_for_api(api_id)

    user_prompt = _build_user_prompt(set_text, state_data)

    try:
        if api_id == "anthropic":
            return _call_anthropic_tools(model, user_prompt, user_id)
        if api_id == "vertex_ai":
            return _call_vertex_tools(model, user_prompt, user_id)
        # OpenAI 兼容
        return _call_openai_compat_tools(api_id, model, user_prompt, user_id, timeout_sec)
    except Exception as exc:
        print(f"[command_agent] parse failed: {type(exc).__name__}: {exc}")
        return []


# ────────────────────────────────────────────────────────────
# Anthropic native tool_use
# ────────────────────────────────────────────────────────────


def _call_anthropic_tools(model: str, user_prompt: str, user_id: int | None) -> list[dict]:
    """Anthropic native tool_use,允许并行多个 tool_use blocks。"""
    from anthropic import Anthropic
    from platform_app.user_credentials import resolve_api_key
    result = resolve_api_key(user_id, "anthropic", env_fallback="ANTHROPIC_API_KEY")
    key = result.get("key")
    if not key:
        raise RuntimeError("无 Anthropic API Key for command_agent")
    client = Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        tools=COMMAND_TOOLS,
        # 不强制 single tool — 允许模型并行多调
    )
    calls: list[dict] = []
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            calls.append({
                "name": block.name,
                "input": dict(block.input or {}),
            })
    return calls


# ────────────────────────────────────────────────────────────
# Vertex Gemini fallback: JSON mode (没 native function calling 兜底)
# ────────────────────────────────────────────────────────────


_JSON_MODE_INSTRUCTION = """
**输出格式**:返回 JSON 数组,每项是一个工具调用:
[
  {"name": "set_world_time", "input": {"target": "..."}},
  {"name": "set_relationship", "input": {"character": "...", "status": "..."}}
]
- 不要写任何 markdown / 自然语言解释,只输出 JSON 数组。
- 一次 /set 命令可以并行多个工具调用,数组依次执行。
- 工具名必须严格匹配上面列出的工具表。
- 如果用户话语真的无法映射,返回 [{"name": "clarify", "input": {"question": "..."}}].
"""


def _build_tool_table_doc() -> str:
    """把工具表序列化成一段文本,放进 system prompt 让 fallback 模型也能选工具。"""
    lines: list[str] = []
    for t in COMMAND_TOOLS:
        lines.append(f"- {t['name']}({_schema_args(t['input_schema'])}): {t['description']}")
    return "\n".join(lines)


def _schema_args(schema: dict) -> str:
    props = schema.get("properties", {})
    required = set(schema.get("required") or [])
    parts = []
    for name, spec in props.items():
        suffix = "" if name in required else "?"
        parts.append(f"{name}{suffix}")
    return ", ".join(parts)


def _call_vertex_tools(model: str, user_prompt: str, user_id: int | None) -> list[dict]:
    from agents.gm import _VertexBackend
    backend = _VertexBackend(model=model)
    system_prompt = (
        _SYSTEM_PROMPT
        + "\n\n## 可用工具表\n"
        + _build_tool_table_doc()
        + "\n\n"
        + _JSON_MODE_INSTRUCTION
    )
    text = backend.call_structured(
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=1500,
    )
    return _parse_tool_call_json_array(text)


def _call_openai_compat_tools(
    api_id: str, model: str, user_prompt: str, user_id: int | None, timeout_sec: int
) -> list[dict]:
    """OpenAI 兼容 JSON mode。"""
    from agents.extractor import _api_base_url
    from platform_app.user_credentials import resolve_api_key
    cred = resolve_api_key(user_id, api_id)
    key = cred.get("key")
    if not key:
        raise RuntimeError(f"无 {api_id} 凭证 for command_agent")
    base_url = cred.get("base_url_override") or _api_base_url(api_id)
    if not base_url:
        raise RuntimeError(f"未知 base_url for {api_id}")
    import urllib.request
    system_prompt = (
        _SYSTEM_PROMPT
        + "\n\n## 可用工具表\n"
        + _build_tool_table_doc()
        + "\n\n"
        + _JSON_MODE_INSTRUCTION
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 1500,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    content = parsed["choices"][0]["message"]["content"]
    return _parse_tool_call_json_array(content)


def _parse_tool_call_json_array(text: str) -> list[dict]:
    """解析模型输出的 JSON,容错地抽出 tool calls 列表。"""
    if not text:
        return []
    text = text.strip()
    # 1) 整段是 JSON
    try:
        parsed = json.loads(text)
        return _coerce_calls(parsed)
    except Exception:
        pass
    # 2) ```json fence
    import re
    fence = re.search(r"```(?:json)?\s*\n?\s*([\[\{][\s\S]*?[\]\}])\s*\n?```", text, re.M)
    if fence:
        try:
            return _coerce_calls(json.loads(fence.group(1)))
        except Exception:
            return []
    # 3) 抓第一个 [ ... ] 或 { "calls": [...] }
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            return _coerce_calls(json.loads(match.group(0)))
        except Exception:
            return []
    return []


def _coerce_calls(parsed: Any) -> list[dict]:
    """把不同形状的输出统一成 [{name,input}, ...]"""
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        if isinstance(parsed.get("calls"), list):
            items = parsed["calls"]
        elif isinstance(parsed.get("tool_calls"), list):
            items = parsed["tool_calls"]
        elif parsed.get("name"):
            items = [parsed]
        else:
            items = []
    else:
        items = []
    out: list[dict] = []
    valid_names = {t["name"] for t in COMMAND_TOOLS}
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or it.get("tool")
        args = it.get("input") or it.get("arguments") or it.get("args") or {}
        if not isinstance(args, dict):
            continue
        if name in valid_names:
            out.append({"name": name, "input": args})
    return out


# ────────────────────────────────────────────────────────────
# Model preference resolution (与 set_parser 复用同一项)
# ────────────────────────────────────────────────────────────


def _detect_default_api() -> str:
    """启动时检测可用 backend: 优先 vertex_ai (SA 文件), 然后 anthropic (env key)."""
    import os as _os
    from pathlib import Path as _Path
    sa_path = _Path(__file__).parent.parent / "vertex_sa.json"
    if sa_path.exists():
        return "vertex_ai"
    if _os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "vertex_ai"  # 默认仍兜底 vertex,失败时调用方走 fallback


def _default_model_for_api(api_id: str) -> str:
    if api_id == "anthropic":
        return "claude-haiku-4-5-20251001"
    return "gemini-3.5-flash"


def _resolve_preferred_model(user_id: int | None) -> str | None:
    if not user_id:
        return None
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(user_id),),
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
                (int(user_id),),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            return row["preferences"].get("set_parser.api_id") or None
    except Exception:
        return None
    return None


__all__ = ["parse_set_command"]
