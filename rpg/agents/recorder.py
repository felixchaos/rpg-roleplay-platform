"""agents/recorder.py — Q_three_sage_pipeline Phase 2: 史官/Recorder

把三个现有 agent 合并为「单次 LLM 调用」的史官，实现：
  - extractor：读叙事 → ops[]（状态变化）
  - anchor_reconcile：读叙事 → reached[]（锚点到达）+ current_chapter
  - acceptance_verifier：读叙事 → unmet[]（未满足验收条款）

单次调用意味着：三个任务共享同一份 prompt 上下文 + 同一次推理 token，
比三次独立调用节省 60-70% 成本，同时减少三倍推理延迟。

公开 API
========
    record_turn(gm_prose, state_data, *, pending_anchors, chapter_map,
                acceptance_clauses, tasks, user_id, model_override,
                api_id_override, timeout_sec) -> dict

返回
====
    {
        "ops":            [...],    # 同 extractor 的 op 结构
        "reached":        [{"anchor_key": str, "drift_score": float}],
        "current_chapter": int | None,
        "unmet":          [...],    # 未满足的 acceptance 条款原文
    }
    任何 tasks 子集未启用 → 对应字段返回空（[]或 None）。
    任何异常 → 返回全空安全默认，绝不上抛。

模型选择（优先级）
==================
    1. api_id_override / model_override（调用方透传）
    2. user_preferences["recorder.api_id"] / ["recorder.model_real_name"]
    3. 回退：user_preferences["extractor.api_id"] / ["extractor.model_real_name"]
    4. _harness.resolve_api_and_model 通配 fallback（agent.api_id / agent.model_real_name）
    5. core.llm_backend DEFAULT_FALLBACK_*
"""
from __future__ import annotations

import json
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)

# 可用任务子集常量
_ALL_TASKS: frozenset[str] = frozenset(["ops", "anchors", "acceptance"])

# GM 正文截断（拼入 prompt 的上限）
_PROSE_CAP = 6000

# ── 合并系统提示（三 agent 指令合并）────────────────────────────────────

_SYSTEM_OPS = """\
【任务 OPS — 状态提取】
读 GM 本轮叙事 + 当前状态快照，提取状态变化到 ops 数组。**不要写小说，只输出 JSON 字段**。

可用 op：
- "set":      覆盖标量字段（player.* / world.time / memory.main_quest 等）
- "append":   追加进列表字段（memory.resources / memory.facts / world.known_events 等）
- "overwrite": 整体覆盖列表（少用）
- "question": GM 在叙事里向玩家提问（玩家需要选择）

可写字段（严格）：
- player.role / player.background / player.current_location（注意：**绝不写 player.name**——
  玩家姓名是玩家自己选的身份,原著里出现别的角色名也不要改成它,后端会硬拒）
- world.time / world.weather / world.timeline.current_phase / world.known_events
- memory.main_quest / memory.current_objective / memory.mode
- memory.resources / memory.abilities / memory.facts / memory.pinned / memory.notes
- relationships.<角色名>
- worldline.user_variables.<变量名>
- ui.<自定义键>

禁止写入：player.name(玩家身份) / permissions.* / history.* / schema_version / created_at

如果某个字段在叙事里真的发生了变化才输出 op；没变就不要编。
如果叙事里完全没有状态变化，ops 输出 []。
"""

_SYSTEM_ANCHORS = """\
【任务 ANCHORS — 世界线锚点判定 · 极度保守，宁漏勿误】
读本回合 GM 叙事，完成：
(A) 判断其中是否明确叙述到了某些「待发生的原著锚点事件」；
(B) 判断本回合剧情最接近原著第几章（进度估计）。

任务 A 铁律：
1. 只有当本回合正文【明确、确凿地叙述了】某锚点事件实际发生时，才列出来。
   仅仅提到、暗示、铺垫、计划、做梦、回忆、假设 —— 都不算，绝不列出。
2. 拿不准就不列；漏标远小于误标。
3. 只能从给定 pending 锚点列表里选，绝不发明新锚点、绝不改 anchor_key。
drift_score（偏离度 0.0–1.0）：0.0=完全按原著；0.3=核心保留但过程不同；0.7+=方式大改。拿不准给 0.2。

任务 B 铁律：
1. 只能返回章节地图里列出的章号；无法定位 → 返回 null。
2. 拿不准 / 正文太抽象 → 返回 null（漏估远小于误估推快）。
3. 两任务独立：章号推理绝不反过来改变锚点取舍。
"""

_SYSTEM_ACCEPTANCE = """\
【任务 ACCEPTANCE — 验收条款判定】
读 GM 本轮叙事 + 验收条款，判断每条是否被满足。把 unmet 条款原文放入 unmet 数组。

判定原则：
- 肯定条款（"应当 X"/"必须 X"/"包含 Y"）：GM 叙事里真的发生了对应行为才算 met；
  只出现关键词没展开 → unmet。
- 否定条款（"不要 X"/"禁止 X"）：叙事里没有违反才算 met；出现禁止行为 → unmet。
- 重点判断 GM 是否真的展开叙事推进/回应了事情，而不是把名词复读一遍。
- 全部通过 → unmet 为 []。
- unmet 每项必须与输入条款完全一致（用于回填 audit_log）。
"""

_SYSTEM_OUTPUT_HEADER = """\
你是史官（Recorder），在一次调用中完成以下所有启用任务。
**不要写小说**，只输出要求的 JSON 对象。
"""

_SYSTEM_OUTPUT_FORMAT = """\
【输出格式（严格 JSON，不要 markdown 围栏，不要解释）】
仅输出一个 JSON 对象，以 `{` 开头，以 `}` 结尾：
"""


def _build_system_prompt(tasks: frozenset[str]) -> str:
    """根据启用的任务集合组装 system prompt。"""
    parts = [_SYSTEM_OUTPUT_HEADER.strip()]
    if "ops" in tasks:
        parts.append(_SYSTEM_OPS.strip())
    if "anchors" in tasks:
        parts.append(_SYSTEM_ANCHORS.strip())
    if "acceptance" in tasks:
        parts.append(_SYSTEM_ACCEPTANCE.strip())

    # 输出格式说明
    schema_fields: list[str] = []
    if "ops" in tasks:
        schema_fields.append('"ops": [{"op":"set|append|overwrite|question","path":"player.xxx","value":"..."}]')
    if "anchors" in tasks:
        schema_fields.append('"reached": [{"anchor_key":"<来自列表>","drift_score":0.0}]')
        schema_fields.append('"current_chapter": <章号整数 或 null>')
        schema_fields.append('"progress_motion": <0|1|2>')
    if "acceptance" in tasks:
        schema_fields.append('"unmet": ["条款原文 1", ...]')

    schema_example = "{\n  " + ",\n  ".join(schema_fields) + "\n}"
    parts.append(_SYSTEM_OUTPUT_FORMAT.strip())
    parts.append(schema_example)
    parts.append("所有未启用任务的字段可省略。")
    return "\n\n".join(parts)


def _build_user_prompt(
    gm_prose: str,
    state_data: dict,
    tasks: frozenset[str],
    pending_anchors: list[dict] | None,
    chapter_map: list[dict] | None,
    acceptance_clauses: list[str] | None,
) -> str:
    """组装 user message：state 快照 + GM 正文 + 各任务附加材料。"""
    lines: list[str] = []

    # 状态快照（始终附带，ops 任务需要；其它任务只读正文但一并提供）
    p = (state_data.get("player") or {})
    w = (state_data.get("world") or {})
    m = (state_data.get("memory") or {})
    rels = (state_data.get("relationships") or {})
    lines.append("## 当前状态快照（叙事之前的值）")
    lines.append(f"- player.name = {p.get('name', '') or '(空)'}")
    lines.append(f"- player.role = {p.get('role', '') or '(空)'}")
    lines.append(f"- player.current_location = {p.get('current_location', '') or '(空)'}")
    lines.append(f"- world.time = {w.get('time', '') or '(空)'}")
    lines.append(f"- world.weather = {w.get('weather', '') or '(空)'}")
    lines.append(f"- memory.main_quest = {m.get('main_quest', '') or '(空)'}")
    lines.append(f"- memory.current_objective = {m.get('current_objective', '') or '(空)'}")
    lines.append(f"- memory.resources = {(m.get('resources') or [])[:5]}")
    lines.append(f"- relationships = {dict(list(rels.items())[:8])}")

    # 锚点材料（仅 anchors 任务）
    if "anchors" in tasks:
        lines.append("")
        lines.append("## 待发生原著锚点（任务 ANCHORS-A，只能从这里选）")
        if pending_anchors:
            for a in pending_anchors:
                key = a.get("anchor_key") or ""
                summ = (a.get("summary") or "").strip().replace("\n", " ")
                if len(summ) > 240:
                    summ = summ[:240]
                fatal = "[死神来了·必发生]" if a.get("is_fatal") else ""
                lines.append(f"- anchor_key={key} {fatal} 概要:{summ}")
        else:
            lines.append("（本窗口暂无待发生锚点）")

        if chapter_map:
            lines.append("")
            lines.append("## 原著章节地图（任务 ANCHORS-B，current_chapter 只能从这些章号里选或 null）")
            # 玩家为核心、原著为参照:玩家常是【自插入主角】,用自己的名字/视角跟着原著主角的剧情走。
            # 让史官(LLM)用语义理解判「在围着原著主角转 vs 干原著之外的事」,别因人物名对不上就一律 null
            # —— 实测:比对原著人物时结算回合判 null,改成语义判断后稳定判到正确章号(自插入卡章根因)。
            lines.append("判 current_chapter 前先做语义判断:玩家往往是【自插入主角】——用自己的名字/视角,"
                         "但可能正跟着原著某主角的剧情线走(原著主角的处境,玩家换个名字在演)。")
            lines.append("· 若本回合玩家的处境/事件对应原著某主角正经历的剧情(哪怕人物名、细节不同)"
                         "→ current_chapter=那一章号;**人物名不同绝不是答 null 的理由**。")
            lines.append("· 若玩家确实脱离原著、在干原著里没有的自己的事 → current_chapter=null。")
            for c in chapter_map:
                ch = c.get("chapter")
                label = (c.get("story_time_label") or c.get("label") or "").strip().replace("\n", " ")
                summ = (c.get("summary") or "").strip().replace("\n", " ")
                if len(summ) > 160:
                    summ = summ[:160]
                head = f"第{ch}章" + (f"「{label}」" if label else "")
                lines.append(f"- chapter={ch} {head}:{summ}")

        # 叙事推进度(ANCHORS-B 续):无论能否对上原著章节都要答 —— 发散/无限流副本里剧情
        # 已离开原著轨道时 current_chapter 往往只能给 null/低章,但故事仍在前进。progress_motion
        # 独立于「对不对得上原著」,只看本回合相对上一回合有没有实质推进:
        lines.append("")
        lines.append("## 本回合叙事推进度(任务 ANCHORS-B,必答整数 progress_motion)")
        lines.append("- 0 = 原地踏步:仍在同一场景/对话,未发生实质推进(如纯对话试探、环境描写、反复纠结)")
        lines.append("- 1 = 正常推进:场景、目标、冲突或关系向前走了一步")
        lines.append("- 2 = 重大跨越:时间跳转、进入新副本/新地点、达成或失败关键目标、重大转折")
        lines.append("只按本回合 GM 正文判断,与能否对上原著章节无关。")

    # GM 正文
    lines.append("")
    lines.append("## GM 本轮叙事")
    lines.append((gm_prose or "").strip()[:_PROSE_CAP])

    # 验收条款（仅 acceptance 任务）
    if "acceptance" in tasks and acceptance_clauses:
        lines.append("")
        lines.append("## 待判定 acceptance 条款（任务 ACCEPTANCE）")
        for i, cond in enumerate(acceptance_clauses, start=1):
            lines.append(f"{i}. {str(cond).strip()}")

    return "\n".join(lines)


# ── merged tool schema（Anthropic native tool_use）──────────────────

def _build_tool_schema(tasks: frozenset[str], acceptance_clauses: list[str] | None) -> dict:
    """构造合并后的 emit_record 工具 schema，只包含已启用任务对应的字段。"""
    properties: dict[str, Any] = {}
    required: list[str] = []

    if "ops" in tasks:
        properties["ops"] = {
            "type": "array",
            "description": "状态变化 op 列表。没有变化时传 []。",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["set", "append", "overwrite", "question",
                                 "hypothesis", "confirm_hypothesis", "reject_hypothesis"],
                    },
                    "path": {"type": "string", "description": "state 路径；op=question/hypothesis/* 时可省"},
                    "value": {"description": "要写入的值"},
                    "question": {"type": "string", "description": "op=question 时用"},
                    "options": {"type": "array", "items": {"type": "string"}, "description": "op=question 时用"},
                    "text": {"type": "string", "description": "op=hypothesis 时用"},
                    "id": {"type": "string", "description": "op=confirm_hypothesis/reject_hypothesis 时用"},
                    "characters": {"type": "array", "items": {"type": "string"}, "description": "op=hypothesis 时用"},
                    "time_label": {"type": "string", "description": "op=hypothesis 时可选"},
                },
                "required": ["op"],
            },
        }
        required.append("ops")

    if "anchors" in tasks:
        properties["reached"] = {
            "type": "array",
            "description": "本回合明确到达的锚点（极度保守，宁漏勿误）",
            "items": {
                "type": "object",
                "properties": {
                    "anchor_key": {"type": "string"},
                    "drift_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["anchor_key", "drift_score"],
            },
        }
        properties["current_chapter"] = {
            "type": ["integer", "null"],
            "description": "本回合最接近原著第几章（无法定位或不确定 → null）",
        }
        # fork 收编:progress_motion 之前只声明在 system prompt 文本里(_build_system_prompt:139),
        # 没进 tool schema → Anthropic 原生 tool-use / Vertex function-call 下 LLM 不吐它 →
        # _safe_progress_motion(None) → anchor_reconcile.do_pace=False → pace fallback 在主力
        # provider 上永不触发(发散局进度冻死的真根因)。tool-schema 与 prompt 必须同源。
        properties["progress_motion"] = {
            "type": ["integer", "null"],
            "description": "本回合叙事推进度(必答):0=原地/回忆无推进,1=正常推进一拍,2=重大跨越。发散局脱离原著也要答,与 current_chapter 无关。",
        }
        required.extend(["reached", "current_chapter", "progress_motion"])

    if "acceptance" in tasks:
        # enum 锁定到传入条款原文（与 acceptance_verifier 完全一致）
        enum_vals = [str(c).strip() for c in (acceptance_clauses or []) if str(c).strip()][:64]
        items_schema: dict = {"type": "string"}
        if enum_vals:
            items_schema["enum"] = enum_vals
        properties["unmet"] = {
            "type": "array",
            "description": "未满足的 acceptance 条款原文（必须与输入条款完全一致）",
            "items": items_schema,
        }
        required.append("unmet")

    return {
        "name": "emit_record",
        "description": (
            "史官（Recorder）一次性输出：状态 ops + 锚点到达 + 验收判定。"
            "未启用任务的字段可省略或传空。"
        ),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


# ── 结果解析 ─────────────────────────────────────────────────────────

def _safe_ops(raw: Any) -> list[dict]:
    """从 LLM 输出里抠出 ops 列表，容忍裸列表 / 错误类型。"""
    if isinstance(raw, list):
        return [op for op in raw if isinstance(op, dict)]
    if isinstance(raw, dict):
        ops = raw.get("ops")
        if isinstance(ops, list):
            return [op for op in ops if isinstance(op, dict)]
    return []


def _safe_reached(raw: Any) -> list[dict]:
    """从 LLM 输出里抠出 reached 列表，规范化每条的 drift_score。"""
    items: list[Any] = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        r = raw.get("reached")
        if isinstance(r, list):
            items = r
    result: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (item.get("anchor_key") or "").strip()
        if not key:
            continue
        try:
            drift = float(item.get("drift_score"))
        except (TypeError, ValueError):
            drift = 0.2
        drift = max(0.0, min(1.0, drift))
        result.append({"anchor_key": key, "drift_score": drift})
    return result


def _safe_current_chapter(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        v = int(raw)
        return v if v >= 1 else None
    except (TypeError, ValueError):
        return None


def _safe_progress_motion(raw: Any) -> int | None:
    """本回合叙事推进度,由 LLM 判定(发散/无限流副本对不上原著章节时也能答):
      0=原地踏步(同一场景、未实质推进) / 1=正常推进 / 2=重大跨越(时间跳转·进新副本·重大事件)。
    专治「current_chapter 被钳在原著章号窗口里、发散play永远估不出更高章 → 进度冻死」。
    **纯 LLM 信号**:字段缺省/非法 → None(本回合无判定,不累计、不推进),绝不用「默认前进」
    这种硬编码假设凭空推进度(那就退化成被否决的回合计数器了)。"""
    if raw is None:
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return 0 if v <= 0 else (2 if v >= 2 else 1)


def _safe_unmet(raw: Any, acceptance_clauses: list[str] | None) -> list[str]:
    """从 LLM 输出里抠出 unmet 列表，做 fuzzy 回填保证原文对齐（与 acceptance_verifier 一致）。"""
    items: list[Any] = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        u = raw.get("unmet")
        if isinstance(u, list):
            items = u
    if not items:
        return []

    acceptance: list[str] = [str(c).strip() for c in (acceptance_clauses or []) if str(c).strip()]
    out: list[str] = []
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        matched = None
        for orig in acceptance:
            if orig == s:
                matched = orig
                break
        if matched is None:
            for orig in acceptance:
                if s and (s in orig or orig in s):
                    matched = orig
                    break
        out.append(matched or s)
    # dedup 保序
    seen: set[str] = set()
    dedup: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup


def _parse_recorder_output(
    text: str,
    tasks: frozenset[str],
    acceptance_clauses: list[str] | None,
) -> dict:
    """从 LLM 原始文本 / tool_use JSON 中解析合并输出。

    容忍：裸 JSON 对象、裸 ops 数组、部分字段缺失、错误类型。
    始终返回完整结构（缺失字段用空默认）。
    """
    empty = _empty_result()
    if not text:
        return empty

    # 先尝试直接 parse（含 ```json 围栏兜底 → 委托 core.json_parse）
    from core.json_parse import parse_llm_json
    parsed = parse_llm_json(text, want=None)

    # 裸 ops 数组兼容（extractor 旧格式）
    if isinstance(parsed, list) and "ops" in tasks:
        # 假设是 ops 数组
        return {
            "ops": [op for op in parsed if isinstance(op, dict)],
            "reached": [],
            "current_chapter": None,
            "progress_motion": None,
            "unmet": [],
        }

    if not isinstance(parsed, dict):
        return empty

    result: dict = {}
    result["ops"] = _safe_ops(parsed.get("ops", [])) if "ops" in tasks else []
    if "anchors" in tasks:
        result["reached"] = _safe_reached(parsed.get("reached", []))
        result["current_chapter"] = _safe_current_chapter(parsed.get("current_chapter"))
        result["progress_motion"] = _safe_progress_motion(parsed.get("progress_motion"))
    else:
        result["reached"] = []
        result["current_chapter"] = None
        result["progress_motion"] = None
    result["unmet"] = (
        _safe_unmet(parsed.get("unmet", []), acceptance_clauses)
        if "acceptance" in tasks else []
    )
    return result


def _empty_result() -> dict:
    return {"ops": [], "reached": [], "current_chapter": None, "progress_motion": None, "unmet": []}


# ── 模型解析 ──────────────────────────────────────────────────────────

def _resolve_recorder_api_and_model(
    user_id: int | None,
    api_id_override: str | None,
    model_override: str | None,
) -> tuple[str, str]:
    """史官模型偏好：先找 recorder.*，回退到 extractor.*，再走 harness 通配。"""
    from agents._harness import resolve_api_and_model
    from core.llm_backend import (
        resolve_preferred_api as _rapi,
        resolve_preferred_model as _rmodel,
    )

    # recorder 专用偏好
    rec_api = api_id_override or _rapi(user_id, pref_key="recorder.api_id")
    rec_model = model_override or _rmodel(user_id, pref_key="recorder.model_real_name")

    # 有 recorder 偏好就直接用 harness 解析（带 BYOK 守卫）
    if rec_api or rec_model:
        return resolve_api_and_model(
            user_id,
            api_pref_key="recorder.api_id",
            model_pref_key="recorder.model_real_name",
            api_id_override=api_id_override,
            model_override=model_override,
        )

    # 无 recorder 偏好 → 回退 extractor.*（再走 harness 通配兜底）
    return resolve_api_and_model(
        user_id,
        api_pref_key="extractor.api_id",
        model_pref_key="extractor.model_real_name",
        api_id_override=api_id_override,
        model_override=model_override,
    )


# ── 主入口 ────────────────────────────────────────────────────────────

def record_turn(
    gm_prose: str,
    state_data: dict,
    *,
    pending_anchors: list[dict] | None = None,   # [{anchor_key, summary, is_fatal}]
    chapter_map: list[dict] | None = None,        # [{chapter, story_time_label, summary}]
    acceptance_clauses: list[str] | None = None,
    tasks: list[str] | None = None,               # subset of ["ops","anchors","acceptance"]; default all
    user_id: int | None = None,
    model_override: str | None = None,
    api_id_override: str | None = None,
    timeout_sec: int = 25,
) -> dict:
    """ONE LLM call merging extractor + anchor judge + acceptance verifier.
    Returns {"ops":[...], "reached":[{"anchor_key","drift_score"}], "current_chapter": int|None, "unmet":[...]}.
    Never raises; returns {"ops":[],"reached":[],"current_chapter":None,"unmet":[]} on any failure.
    Only include prompt sections + request output fields for tasks that are enabled."""
    # 规范化任务集合
    if tasks is None:
        active_tasks = _ALL_TASKS
    else:
        active_tasks = frozenset(t for t in tasks if t in _ALL_TASKS)
    if not active_tasks:
        log.debug("[recorder] 没有启用任何任务，直接返回空结果")
        return _empty_result()

    # 模型解析
    try:
        api_id, model = _resolve_recorder_api_and_model(user_id, api_id_override, model_override)
    except Exception as exc:
        log.debug("[recorder] 模型解析失败，跳过: %s", exc)
        return _empty_result()
    if not api_id or not model:
        log.debug("[recorder] 无可用 api_id/model，跳过")
        return _empty_result()

    system_prompt = _build_system_prompt(active_tasks)
    user_prompt = _build_user_prompt(
        gm_prose, state_data, active_tasks,
        pending_anchors, chapter_map, acceptance_clauses,
    )
    tool_schema = _build_tool_schema(active_tasks, acceptance_clauses)

    # 三通道 dispatch（优先 native tool_use / function calling）
    try:
        text, usage = _call_recorder(
            api_id=api_id,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool_schema=tool_schema,
            user_id=user_id,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        log.debug("[recorder] LLM 调用失败，返回空结果: %s", exc)
        return _empty_result()

    # 记 usage（史官三通道唯一记账点，不影响主流程，异常静默）。
    # dispatch 层不再自动记账,故这里记一次即准确、不双计。
    try:
        if user_id and usage and (usage.get("input_tokens") or usage.get("output_tokens")):
            from platform_app.usage import record_usage as _rec
            _rec(
                user_id=user_id,
                save_id=None,
                context_run_id=None,
                api_id=api_id,
                model_real_name=model,
                usage=usage,
                metadata={"kind": "recorder", "tasks": sorted(active_tasks)},
                scenario="extract",
            )
    except Exception:
        pass

    # 解析结果
    try:
        result = _parse_recorder_output(text, active_tasks, acceptance_clauses)
    except Exception as exc:
        log.debug("[recorder] 结果解析失败，返回空: %s", exc)
        return _empty_result()

    # 史官判定可观测:此前 progress_motion/reached 是黑盒,排查「进度不动是判断错误还是本就无节点」
    # 只能翻 DB。一行 INFO 记下本回合史官到底判了什么 —— motion=0/None 一眼可辨(0=判定无推进、
    # None=模型没吐该字段=结构化返回没生效)。
    try:
        _reached = [r.get("anchor_key") for r in (result.get("reached") or [])]
        log.info(
            "[recorder] uid=%s %s·%s → motion=%s reached=%s current_ch=%s ops=%d unmet=%d",
            user_id, api_id, model, result.get("progress_motion"),
            _reached or "[]", result.get("current_chapter"),
            len(result.get("ops") or []), len(result.get("unmet") or []),
        )
    except Exception:
        pass

    return result


# ── dispatch 层 ───────────────────────────────────────────────────────

def _call_recorder(
    api_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tool_schema: dict,
    user_id: int | None,
    timeout_sec: int,
) -> tuple[str, dict]:
    """三通道 dispatch，返回 (text, usage)。

    层次（同 extractor._call_extractor_backend + _harness 完全一致）：
    1. Anthropic + native tool_use（强制 emit_record，schema 校验）
    2. Vertex AI + function calling（ANY 模式强制必调）
    3. OpenAI 兼容 → 复用 extractor._call_openai_compat_json_mode，传合并 json_hint

    三通道都从同一份 tool_schema 取「必答字段」(schema 强制),不再手写第三份 shape。
    usage 三通道都不在此层记账(anthropic/vertex 不传 agent_kind、openai_compat 直接调
    _openai_function_call),统一由 record_turn 记一次(scenario="extract"),避免双计。
    """
    from agents._harness import call_agent_json as _call_json

    if api_id == "anthropic":
        # native tool_use，返回 tool.input JSON
        text, usage = _call_json(
            api_id=api_id,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            user_id=user_id,
            tool_schema=tool_schema,
            max_tokens=1200,
            timeout_sec=timeout_sec,
            # 不传 agent_kind：usage 由下方 record_turn 统一记一次(scenario="extract"+tasks)。
            # 传了 agent_kind call_agent_json 会自动再记一次(scenario="tool")→ 史官用量双计。
        )
        # harness Anthropic tool_use 返回 tool.input 的 JSON（整个 dict），
        # 直接解析用
        return text, usage

    if api_id == "vertex_ai":
        # Vertex function calling
        text, usage = _call_json(
            api_id=api_id,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            user_id=user_id,
            tool_schema=tool_schema,
            max_tokens=1200,
            timeout_sec=timeout_sec,
            # 不传 agent_kind：usage 由下方 record_turn 统一记一次(scenario="extract"+tasks)。
            # 传了 agent_kind call_agent_json 会自动再记一次(scenario="tool")→ 史官用量双计。
        )
        return text, usage

    # OpenAI 兼容：走 native function-calling（forced tool_choice，schema 强校验），
    # 与 anthropic native tool_use / vertex function-call **同源于 tool_schema** ——
    # 三通道结构化返回要求从此单一来源。progress_motion / reached / current_chapter 等
    # 必答字段由 schema.required 强制,弱模型(flash 等)也漏不掉。
    #
    # 【历史根因】此前这条通道是唯一手写第三份 shape(json_hint)的分支,而那份 hint 漏了
    # progress_motion(批次1 只补进 tool_schema + _build_system_prompt,漏了这里),且末尾
    # 「严格符合此形状」把它当权威 → 便宜模型据此永不吐 progress_motion → _safe_progress_motion(None)
    # → pace fallback 永不触发 → 发散局进度冻死(行者无疆实测)。删掉手写 hint,消除漂移。
    #
    # _openai_function_call 内部对「中转拒绝 tools 形态」(403/400/404/405/422,群反馈实锤 op/xf
    # 等中转)自动降级到 json_object 兼容模式,降级时用的正是这里传入的 base system_prompt
    # (_build_system_prompt 的 schema_fields 已含 progress_motion + ops item 形状,json_object
    # 下弱模型也不会把 ops 输出成裸字符串)。直接调(不经 call_agent_json)避免与下方 record_turn
    # 的 record_usage 双计。
    from agents._harness import _openai_function_call
    text, usage = _openai_function_call(
        api_id=api_id,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tool_schema=tool_schema,
        user_id=user_id,
        timeout_sec=timeout_sec,
        max_tokens=1200,
    )
    return text, usage


__all__ = ["record_turn"]
