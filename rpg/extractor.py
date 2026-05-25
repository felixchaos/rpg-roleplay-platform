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
from typing import Any


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
        text = _call_extractor_backend(
            api_id=api_id,
            model=model,
            system_prompt=_EXTRACTOR_SYSTEM,
            user_prompt=_build_user_prompt(narrative_text, state_data),
            user_id=user_id,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        print(f"[extractor] call failed: {exc}")
        return []
    return _parse_extractor_output(text)


def _resolve_preferred_extractor_model(user_id: int | None) -> str | None:
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
            return row["preferences"].get("extractor.model_real_name") or None
    except Exception:
        return None
    return None


def _resolve_preferred_extractor_api(user_id: int | None) -> str | None:
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
            return row["preferences"].get("extractor.api_id") or None
    except Exception:
        return None
    return None


def _call_extractor_backend(
    api_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    timeout_sec: int,
) -> str:
    """轻量调用：复用现有 gm.py 的 backend 类，但只做单次同步生成。

    走 Anthropic / Vertex / OpenAI 兼容。失败抛异常。
    """
    if api_id == "anthropic":
        from gm import _AnthropicBackend
        backend = _AnthropicBackend(model=model, user_id=user_id)
        return backend.call(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=800,
        )
    if api_id == "vertex_ai":
        from gm import _VertexBackend
        backend = _VertexBackend(model=model, api_id="vertex_ai", user_id=user_id)
        return backend.call(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=800,
        )
    # OpenAI / 兼容：直接调 chat completions
    from platform_app.user_credentials import resolve_api_key
    cred = resolve_api_key(user_id, api_id)
    if not cred.get("key"):
        raise RuntimeError(f"无 {api_id} 凭证可用于 extractor")
    import urllib.request
    base_url = cred.get("base_url_override") or _api_base_url(api_id)
    if not base_url:
        raise RuntimeError(f"未知 base_url for {api_id}")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": 800,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cred['key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def _api_base_url(api_id: str) -> str:
    """从 catalog 拿 base_url 做 OpenAI-compat 兜底。"""
    try:
        from model_registry import load_model_catalog, find_api
        api = find_api(load_model_catalog(), api_id)
        return api.get("base_url", "") if api else ""
    except Exception:
        return ""
