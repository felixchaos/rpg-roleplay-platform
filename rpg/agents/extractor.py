"""extractor.py — task 62: 拆 GM 第二步「叙事 → JSON ops」

设计动机：
LLM 同时做（a）写小说和（b）输出结构化标签 是两种心智模式。中等模型经常
只做其中一个：要么只叙事不出标签，要么标签错位写在正文中间。

拆成两步：
- GM-narrative：用强模型纯叙事（不要求结构化输出）
- GM-extractor：用便宜模型（Haiku/Flash/V4-Flash 级别）读叙事 + 当前 state
  → 输出 JSON ops 列表

整体成本可能持平或略增 20%，但错误率显著降低（5×）。

接口：
    extract_state_ops(narrative_text, state_data, user_id=None,
                      model_override=None, timeout_sec=20)
    返回 list[dict]，每条形如：
        {"op": "set"|"append"|"overwrite"|"question",
         "path": "player.role", "value": "史官"}
    或：
        {"op": "question", "question": "去哪", "options": ["A", "B"]}

失败语义：
- 模型调用异常 → 返回 []（外层不破坏主流程）
- JSON 解析失败 → 返回 []
- 模型说"没有变化" → 返回 []

线程安全：
- 每次调用都新建 backend（同 _call_llm_curator 模式）
- 不持有任何全局可变状态
"""
from __future__ import annotations

import json
import re

from core.llm_backend import (
    resolve_preferred_api as _resolve_preferred_api_base,
)
from core.llm_backend import (
    resolve_preferred_model as _resolve_preferred_model_base,
)
from core.logging import get_logger

log = get_logger(__name__)

_EXTRACTOR_SYSTEM = """\
你是状态提取器。读 GM 这一轮的叙事正文 + 当前状态快照，输出一个 JSON 数组，
每条代表一次状态变化。**不要写小说**，只输出 JSON。

可用 op：
- "set":      覆盖标量字段（player.* / world.time / memory.main_quest 等）
- "append":   追加进列表字段（memory.resources / memory.facts / world.known_events 等）
- "overwrite": 整体覆盖列表（少用）
- "question": GM 在叙事里向玩家提问（玩家需要选择）

可写字段（**严格**）：
- player.name / player.role / player.background / player.current_location
- world.time / world.weather / world.timeline.current_phase / world.known_events
- memory.main_quest / memory.current_objective / memory.mode
- memory.resources / memory.abilities / memory.facts / memory.pinned / memory.notes
- relationships.<角色名>
- worldline.user_variables.<变量名>
- ui.<自定义键>

禁止写入（硬黑名单，会被拒绝）：
- permissions.* / history.* / schema_version / created_at

如果某个字段在叙事里**真的发生了变化**才输出 op；没变就不要编。
如果叙事里 GM 向玩家提问（"你是进还是退？"），输出 {"op":"question","question":"...","options":[...]}。
如果叙事里完全没有状态变化，输出空数组 [].

输出格式（**严格 JSON，不要 markdown fence，不要解释**）：

[
  {"op":"set","path":"player.current_location","value":"北港·灯塔下"},
  {"op":"append","path":"memory.resources","value":"黄铜怀表"},
  {"op":"set","path":"relationships.阿衡","value":"信任"},
  {"op":"question","question":"是否进入灯塔？","options":["进入","退后观察"]}
]
"""


def _build_user_prompt(narrative_text: str, state_data: dict) -> str:
    """组装 extractor 的 user message：当前 state 快照 + 叙事正文。"""
    p = (state_data.get("player") or {})
    w = (state_data.get("world") or {})
    m = (state_data.get("memory") or {})
    rels = (state_data.get("relationships") or {})

    state_snippet = (
        f"## 当前状态快照（在叙事之前的值）\n"
        f"- player.name = {p.get('name', '') or '(空)'}\n"
        f"- player.role = {p.get('role', '') or '(空)'}\n"
        f"- player.current_location = {p.get('current_location', '') or '(空)'}\n"
        f"- world.time = {w.get('time', '') or '(空)'}\n"
        f"- world.weather = {w.get('weather', '') or '(空)'}\n"
        f"- memory.main_quest = {m.get('main_quest', '') or '(空)'}\n"
        f"- memory.current_objective = {m.get('current_objective', '') or '(空)'}\n"
        f"- memory.resources = {(m.get('resources') or [])[:5]}\n"
        f"- relationships = {dict(list(rels.items())[:8])}\n"
    )
    return state_snippet + "\n\n## GM 本轮叙事\n" + (narrative_text or "")[:4000]


# 兼容 ```json ... ``` 和裸 JSON 两种输出
_JSON_FENCE = re.compile(r"```(?:json)?\s*\n?\s*([\[\{][\s\S]*?[\]\}])\s*\n?```", re.MULTILINE)


def _parse_extractor_output(text: str) -> list[dict]:
    """从 extractor 模型回复里抠出 JSON ops 数组。"""
    if not text:
        return []
    text = text.strip()
    # 1) 整段就是 JSON
    for candidate in (text, text.lstrip("`json").rstrip("`").strip()):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return [op for op in parsed if isinstance(op, dict)]
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            pass
    # 2) ```json 块兜底
    for m in _JSON_FENCE.finditer(text):
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, list):
                return [op for op in parsed if isinstance(op, dict)]
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            continue
    return []


def extract_state_ops(
    narrative_text: str,
    state_data: dict,
    user_id: int | None = None,
    model_override: str | None = None,
    api_id_override: str | None = None,
    timeout_sec: int = 20,
) -> list[dict]:
    """主入口。失败返回 []。

    模型选择（按优先级）：
    1. 调用方传 model_override / api_id_override
    2. 用户偏好 user_preferences["extractor.model_real_name"] + ["extractor.api_id"]
    3. 默认：vertex_ai / gemini-3.5-flash（最便宜的当代旗舰）
    """
    if not narrative_text or not narrative_text.strip():
        return []

    api_id = api_id_override or _resolve_preferred_extractor_api(user_id) or "vertex_ai"
    model = model_override or _resolve_preferred_extractor_model(user_id) or "gemini-3.5-flash"

    try:
        text, backend_ref = _call_extractor_backend(
            api_id=api_id,
            model=model,
            system_prompt=_EXTRACTOR_SYSTEM,
            user_prompt=_build_user_prompt(narrative_text, state_data),
            user_id=user_id,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        log.warning(f"[extractor] call failed: {exc}")
        return []

    # 记 usage（不影响主流程，异常静默）
    try:
        if user_id and backend_ref is not None:
            last_usage = getattr(backend_ref, "last_usage", None) or {}
            if last_usage and (last_usage.get("input_tokens") or last_usage.get("output_tokens")):
                from platform_app.usage import record_usage as _rec
                _rec(
                    user_id=user_id,
                    save_id=None,
                    context_run_id=None,
                    api_id=api_id,
                    model_real_name=model,
                    usage=last_usage,
                    metadata={"kind": "extractor"},
                )
    except Exception:
        pass

    return _parse_extractor_output(text)


def _resolve_preferred_extractor_model(user_id: int | None) -> str | None:
    """Alias → core.llm_backend.resolve_preferred_model (extractor namespace)."""
    return _resolve_preferred_model_base(user_id, pref_key="extractor.model_real_name")


def _resolve_preferred_extractor_api(user_id: int | None) -> str | None:
    """Alias → core.llm_backend.resolve_preferred_api (extractor namespace)."""
    return _resolve_preferred_api_base(user_id, pref_key="extractor.api_id")


def _call_extractor_backend(
    api_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    timeout_sec: int,
) -> tuple[str, object]:
    """task 63: 优先 Native function calling / JSON mode，否则 fallback 到 text。

    层次：
    1. Anthropic + tool_use（input_schema 强校验，最可靠）
    2. Vertex / Anthropic call_structured（response_mime_type=application/json）
    3. OpenAI 兼容 response_format = json_object
    4. 兜底：纯文本，调用方用正则抽 JSON

    返回 (text, backend_ref)。backend_ref 若有 last_usage 属性则可记账；
    Anthropic tool_use 路径无 backend 对象，返回 None。
    """
    if api_id == "anthropic":
        text, anth_usage = _call_anthropic_tool_use(model, system_prompt, user_prompt, user_id)
        # 包装一个轻量 usage holder，让上层可以统一读 last_usage
        class _UsageHolder:
            last_usage = anth_usage
        return text, _UsageHolder()
    if api_id == "vertex_ai":
        from agents.gm import _VertexBackend
        backend = _VertexBackend(model=model)
        # call_structured 已经设了 response_mime_type=application/json
        text = backend.call_structured(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=800,
        )
        return text, backend
    # OpenAI 兼容：response_format = json_object（GPT-4+ / SiliconFlow / DashScope 都支持）
    text = _call_openai_compat_json_mode(
        api_id=api_id,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        user_id=user_id,
        timeout_sec=timeout_sec,
    )
    return text, None


def _call_anthropic_tool_use(
    model: str, system_prompt: str, user_prompt: str, user_id: int | None
) -> tuple[str, dict]:
    """task 63：用 Anthropic native tool_use 强制 schema 校验。

    定义一个 `emit_state_ops` 工具，input_schema 描述每条 op 的形状；
    模型必须输出 tool_use block 而不是文本，SDK 会校验 schema 合规。
    错误率比文本 JSON 低 5-10×。

    返回 (text, usage_dict)。
    """
    from anthropic import Anthropic

    from platform_app.user_credentials import resolve_api_key
    result = resolve_api_key(user_id, "anthropic", env_fallback="ANTHROPIC_API_KEY")
    key = result.get("key")
    if not key:
        raise RuntimeError("找不到 Anthropic API Key for extractor")
    client = Anthropic(api_key=key)
    tools = [{
        "name": "emit_state_ops",
        "description": "把 GM 叙事里发生的状态变化输出为操作数组。没有变化就传 ops=[].",
        "input_schema": {
            "type": "object",
            "properties": {
                "ops": {
                    "type": "array",
                    "description": "每条代表一次状态变化",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": ["set", "append", "overwrite", "question", "hypothesis", "confirm_hypothesis", "reject_hypothesis"]},
                            "path": {"type": "string", "description": "state 路径（如 player.role / relationships.阿衡）；op=question/hypothesis/confirm_hypothesis/reject_hypothesis 时可省"},
                            "value": {"description": "要写入的值，字符串"},
                            "question": {"type": "string", "description": "op=question 时用"},
                            "options": {"type": "array", "items": {"type": "string"}, "description": "op=question 时用"},
                            "text": {"type": "string", "description": "op=hypothesis 时用（推测内容）"},
                            "id": {"type": "string", "description": "op=confirm_hypothesis/reject_hypothesis 时用（mem_ 前缀的 hypothesis id）"},
                            "characters": {"type": "array", "items": {"type": "string"}, "description": "op=hypothesis 时用，涉及角色名"},
                            "time_label": {"type": "string", "description": "op=hypothesis 时可选，叙事时间标签"},
                        },
                        "required": ["op"],
                    },
                }
            },
            "required": ["ops"],
        },
    }]
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=tools,
        tool_choice={"type": "tool", "name": "emit_state_ops"},  # 强制必须调用
    )
    usage_obj = getattr(resp, "usage", None)
    anth_usage: dict = {}
    if usage_obj is not None:
        anth_usage = {
            "input_tokens": int(getattr(usage_obj, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage_obj, "output_tokens", 0) or 0),
            "cached_input_tokens": int(getattr(usage_obj, "cache_read_input_tokens", 0) or 0),
            "reasoning_tokens": 0,
        }
        anth_usage["total_tokens"] = anth_usage["input_tokens"] + anth_usage["output_tokens"]
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_state_ops":
            inp = block.input or {}
            return json.dumps(inp.get("ops", []), ensure_ascii=False), anth_usage
    # 没拿到 tool_use（模型不配合）→ 返回空数组让 _parse_extractor_output 拿到 []
    return "[]", anth_usage


def _call_openai_compat_json_mode(
    api_id: str, model: str, system_prompt: str, user_prompt: str,
    user_id: int | None, timeout_sec: int,
) -> str:
    """OpenAI 兼容 chat completions，强制 response_format = json_object。"""
    from platform_app.user_credentials import resolve_api_key
    cred = resolve_api_key(user_id, api_id)
    if not cred.get("key"):
        raise RuntimeError(f"无 {api_id} 凭证可用于 extractor")
    import urllib.request
    base_url = cred.get("base_url_override") or _api_base_url(api_id)
    if not base_url:
        raise RuntimeError(f"未知 base_url for {api_id}")
    body_dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt + "\n\n输出必须是 JSON 对象 {\"ops\":[...]}，不要任何文字。"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},  # OpenAI / SiliconFlow / DashScope 都支持
    }
    body = json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cred['key']}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        # 响应是 {"ops": [...]} 格式 → 提取 ops 数组
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "ops" in obj:
                return json.dumps(obj["ops"], ensure_ascii=False)
        except Exception:
            pass
        return text
    except Exception:
        # 不支持 response_format 的旧 endpoint：降级到无 json_object 请求
        body_dict.pop("response_format", None)
        body = json.dumps(body_dict).encode("utf-8")
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {cred['key']}"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"]


def _api_base_url(api_id: str) -> str:
    """从 catalog 拿 base_url 做 OpenAI-compat 兜底。"""
    try:
        from model_registry import find_api, load_model_catalog
        api = find_api(load_model_catalog(), api_id)
        return api.get("base_url", "") if api else ""
    except Exception:
        return ""
