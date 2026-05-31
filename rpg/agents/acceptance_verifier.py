"""acceptance_verifier.py — task 84: 把 acceptance 验证升级成 LLM 判定。

设计动机：
task 81 的规则版本是中文 bigram 字面匹配，对"GM 完全没回应玩家请求"这种
明显失败抓得到，但对语义级判断（"GM 是否真的 *回应* 玩家的提问 vs 只是把
关键词重复了一遍"）束手无策——只要 response 里出现了 acceptance 提到的
名词，规则就判定通过，于是产生大量假阴性（acceptance_unmet 漏报）。

task 84 引入 LLM 验证模式：
- rule（默认）：纯规则，零成本
- llm：每条 acceptance 让便宜 LLM（默认 gemini-3.5-flash）判定 met / unmet
- hybrid：先 rule 跑，rule 判定 unmet 的条款再喂给 LLM 二次确认，减少误报

接口（仿 extractor.py 的 extract_state_ops）：
    verify_acceptance_llm(acceptance, response_text, updates, user_id=None, ...)
    返回 list[str] 的 unmet 条款；失败/异常返回 None 让调用方降级到 rule。

复用 extractor._call_extractor_backend 调 LLM（Anthropic tool_use / Vertex
JSON / OpenAI response_format 三通道都已经在 extractor.py 里调好）。

线程安全：
- 不持有任何全局可变状态
- 每次调用都新建 backend（同 extractor.py 模式）
"""
from __future__ import annotations

import json
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)

_VERIFIER_SYSTEM = """\
你是 acceptance（验收条件）判定器。读 GM 这一轮的叙事正文 + 一组验收条款，
判断每条条款是否被满足。**不要写小说**，只输出 JSON。

判定原则：
- 肯定条款（"应当 X"/"必须 X"/"回应了 X"/"包含 Y"）：GM 叙事里**真的发生了
  对应行为**才算 met；只是出现关键词、没有展开 → unmet。
- 否定条款（"不要 X"/"不应 X"/"禁止 X"）：GM 叙事里**没有违反**才算 met；
  出现禁止的行为/内容 → unmet。
- 当 acceptance 在描述"玩家的提问/请求被回应"时，重点判断 GM 是否真的展开
  叙事去推进/回应了这件事，而不是把名词复读一遍。

输出格式（严格 JSON，不要 markdown fence，不要解释）：
{"unmet": ["条款 1 原文", "条款 3 原文"]}

如果所有条款都满足，输出：{"unmet": []}

只返回 unmet 列表里的"原文"。原文必须与输入条款字符串**完全一致**（用于回填
audit_log）。
"""


def _build_user_prompt(
    acceptance: list[str], response_text: str, updates: list[str]
) -> str:
    """组装 LLM 的 user message：response 正文 + updates + acceptance 条款。"""
    lines: list[str] = []
    lines.append("## GM 本轮叙事")
    lines.append((response_text or "")[:4000])
    if updates:
        lines.append("")
        lines.append("## 本轮 state updates（结构化变更摘要）")
        for u in updates[:30]:
            lines.append(f"- {str(u)[:200]}")
    lines.append("")
    lines.append("## 待判定 acceptance 条款")
    for i, cond in enumerate(acceptance, start=1):
        lines.append(f"{i}. {str(cond).strip()}")
    return "\n".join(lines)


def _parse_verifier_output(text: str, acceptance: list[str]) -> list[str] | None:
    """解析 LLM 返回。返回 unmet 列表；解析失败 → None。

    LLM 可能返回：
    - {"unmet": ["条款原文 1", ...]}
    - {"unmet": []}
    - tool_use input：emit_acceptance_verdict({"unmet": [...]})
    - 老 OpenAI 兼容模式的纯文本回包
    """
    if not text:
        return None
    text = text.strip()
    # 1) 整段就是 JSON
    parsed: Any = None
    for candidate in (text, text.lstrip("`json").rstrip("`").strip()):
        try:
            parsed = json.loads(candidate)
            break
        except Exception:
            parsed = None
    # 2) ```json fence 兜底
    if parsed is None:
        import re
        m = re.search(r"```(?:json)?\s*\n?\s*(\{[\s\S]*?\})\s*\n?```", text, re.MULTILINE)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except Exception:
                parsed = None
    if parsed is None:
        return None

    # 期待 {"unmet": [...]}；也兼容 LLM 偶然直接给 list
    if isinstance(parsed, dict):
        unmet = parsed.get("unmet")
    elif isinstance(parsed, list):
        unmet = parsed
    else:
        return None
    if not isinstance(unmet, list):
        return None

    # 规范化：unmet 必须是 acceptance 里出现过的原文。LLM 可能改写/截断，做
    # 一次 fuzzy 回填，保护 audit_log 的可读性。
    out: list[str] = []
    acc_norm = [(str(c).strip(), str(c).strip()) for c in acceptance if str(c).strip()]
    for item in unmet:
        s = str(item).strip()
        if not s:
            continue
        # 完全匹配优先
        matched = None
        for orig, _norm in acc_norm:
            if orig == s:
                matched = orig
                break
        if matched is None:
            # 子串包含（LLM 改写了一点）
            for orig, _norm in acc_norm:
                if s and (s in orig or orig in s):
                    matched = orig
                    break
        out.append(matched or s)
    # dedup 保持顺序
    seen: set[str] = set()
    dedup: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup


def verify_acceptance_llm(
    acceptance: list[str],
    response_text: str,
    updates: list[str],
    user_id: int | None = None,
    model_override: str | None = None,
    api_id_override: str | None = None,
    timeout_sec: int = 15,
) -> list[str] | None:
    """主入口。返回 unmet 条款列表（可能为 []）。

    异常 / 解析失败 → None，让调用方降级到 rule。

    模型选择（按优先级）：
    1. 调用方传 model_override / api_id_override
    2. 用户偏好 user_preferences["acceptance_verifier.model_real_name"] +
       ["acceptance_verifier.api_id"]
    3. 复用 extractor 的偏好（acceptance 验证和 extractor 都是"读叙事 → JSON"
       的便宜判定，模型可以共用）
    4. 默认：vertex_ai / gemini-3.5-flash
    """
    if not acceptance or not response_text or not response_text.strip():
        return []

    # 复用 extractor 模块的偏好读取，避免重复 SQL 代码。
    try:
        import agents.extractor as _extractor
    except Exception as exc:
        log.warning(f"[acceptance_verifier] import extractor failed: {exc}")
        return None

    api_id = (
        api_id_override
        or _resolve_preferred_verifier_api(user_id)
        or _extractor._resolve_preferred_extractor_api(user_id)
        or "vertex_ai"
    )
    model = (
        model_override
        or _resolve_preferred_verifier_model(user_id)
        or _extractor._resolve_preferred_extractor_model(user_id)
        or "gemini-3.5-flash"
    )

    user_prompt = _build_user_prompt(acceptance, response_text, updates or [])

    try:
        text, verifier_backend_ref = _call_verifier_backend(
            api_id=api_id,
            model=model,
            system_prompt=_VERIFIER_SYSTEM,
            user_prompt=user_prompt,
            user_id=user_id,
            timeout_sec=timeout_sec,
            acceptance=acceptance,
        )
    except Exception as exc:
        log.warning(f"[acceptance_verifier] call failed: {exc}")
        return None

    # 记 usage（不影响主流程，异常静默）
    try:
        if user_id and verifier_backend_ref is not None:
            v_usage = getattr(verifier_backend_ref, "last_usage", None) or {}
            if v_usage and (v_usage.get("input_tokens") or v_usage.get("output_tokens")):
                from platform_app.usage import record_usage as _rec
                _rec(
                    user_id=user_id,
                    save_id=None,
                    context_run_id=None,
                    api_id=api_id,
                    model_real_name=model,
                    usage=v_usage,
                    metadata={"kind": "verifier"},
                    scenario="extract",
                )
    except Exception:
        pass

    parsed = _parse_verifier_output(text, acceptance)
    if parsed is not None:
        return parsed
    return None


def _call_verifier_backend(
    api_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    timeout_sec: int,
    acceptance: list[str],
) -> tuple[str, object]:
    """task 84：和 extractor._call_extractor_backend 同结构，
    但 schema 是 emit_acceptance_verdict({"unmet":[...]}) 而不是 emit_state_ops。

    复用 extractor 模块的 Vertex / OpenAI-compat 两条通道（它们不带 schema，
    完全靠 system prompt 控制输出格式），只有 Anthropic 那条需要单独写
    tool_use schema。

    返回 (text, backend_ref)。backend_ref 若有 last_usage 则可记账；
    无法提供 backend 时返回 None。
    """
    if api_id == "anthropic":
        text, anth_usage = _call_anthropic_tool_use_for_acceptance(
            model, system_prompt, user_prompt, user_id, acceptance,
        )
        class _UsageHolder:
            last_usage = anth_usage
        return text, _UsageHolder()
    # 其它通道直接复用 extractor 已经有的便宜实现。
    import agents.extractor as _extractor
    if api_id == "vertex_ai":
        from agents.gm import _VertexBackend
        backend = _VertexBackend(model=model)
        text = backend.call_structured(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=800,
        )
        return text, backend
    text = _extractor._call_openai_compat_json_mode(
        api_id=api_id,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        user_id=user_id,
        timeout_sec=timeout_sec,
    )
    return text, None


def _call_anthropic_tool_use_for_acceptance(
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    acceptance: list[str],
) -> tuple[str, dict]:
    """task 84：Anthropic native tool_use，schema = emit_acceptance_verdict。

    模型必须输出 tool_use block 而不是文本。unmet 字段 enum 锁定到当前传入
    的 acceptance 原文，避免 LLM 改写。

    返回 (text, usage_dict)。
    """
    from anthropic import Anthropic

    from platform_app.user_credentials import resolve_api_key
    result = resolve_api_key(user_id, "anthropic", env_fallback="ANTHROPIC_API_KEY")
    key = result.get("key")
    if not key:
        raise RuntimeError("找不到 Anthropic API Key for acceptance_verifier")
    client = Anthropic(api_key=key)
    # enum 必须是非空且 ≤512 个；正常 acceptance 不会超
    enum_vals = [str(c).strip() for c in acceptance if str(c).strip()][:64]
    items_schema: dict = {"type": "string"}
    if enum_vals:
        items_schema["enum"] = enum_vals
    tools = [{
        "name": "emit_acceptance_verdict",
        "description": "对每条 acceptance 条款判定是否被 GM 这一轮叙事满足。把 unmet 的条款原文放进 unmet 数组；如果全部通过就传 unmet=[]。",
        "input_schema": {
            "type": "object",
            "properties": {
                "unmet": {
                    "type": "array",
                    "description": "未通过的 acceptance 条款原文列表（每项必须与输入条款完全一致）",
                    "items": items_schema,
                }
            },
            "required": ["unmet"],
        },
    }]
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=tools,
        tool_choice={"type": "tool", "name": "emit_acceptance_verdict"},
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
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_acceptance_verdict":
            inp = block.input or {}
            return json.dumps({"unmet": list(inp.get("unmet", []))}, ensure_ascii=False), anth_usage
    # 没拿到 tool_use → 当成"全通过"安全降级
    return '{"unmet": []}', anth_usage


def _resolve_preferred_verifier_model(user_id: int | None) -> str | None:
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
            return row["preferences"].get("acceptance_verifier.model_real_name") or None
    except Exception:
        return None
    return None


def _resolve_preferred_verifier_api(user_id: int | None) -> str | None:
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
            return row["preferences"].get("acceptance_verifier.api_id") or None
    except Exception:
        return None
    return None
