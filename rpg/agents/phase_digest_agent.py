"""
phase_digest_agent.py — task 107D: save 级阶段摘要 LLM 子代理。

设计动机
========
GM 在长游戏 (100+ turn) 中,context_engine 只能塞下 6 轮 recent_chat,
对 100 turn 之前的事彻底失忆。107A 已建好 save_phase_digests 表,107C
已建好 phase boundary 检测,本文件负责唯一剩下的一环: 把每段 phase
内的 player+gm 对话喂给一个轻量 LLM,产出结构化摘要 (summary,
key_events, key_npcs, key_locations, key_decisions, emotion_arc),
回写 save_phase_digests,并标记对应 branch_commits.digested_in_phase。

107E 读取这些摘要注入下一轮 GM context;107F bulk backfill 老存档时
也会调本文件的 compact_phase。

公开 API
========
    compact_phase(save_id, phase_index, *, user_id=None, force=False) -> dict

只暴露这一个入口。LLM prompt / 解析 / DB 写入 全部内部封装。

Backend 选择
============
默认复用 gm._VertexBackend(model='gemini-3.5-flash')。是否切其他 backend
由调用方 / 用户偏好决定 (传 model_override / api_id_override);"复杂任务用
opus" 的设计指导针对的是实现工程师本人,被实现的子系统不必用 opus。

错误处理语义
============
- LLM 输出不是合法 JSON → 第二次重试 (temperature=0.3) → 还失败抛 ValueError
- LLM 调用失败 (网络/凭证) → 返 {"error": ..., "save_id": ..., "phase_index": ...}
- DB 写入失败 → 抛异常,不静默吞 (上层 worker 决定是否重试)
- force=False + status='closed' + summary 非空 → 不重摘,直接返现状

线程 / 异步
===========
本函数全同步;打算异步触发的调用方应把它放到自己的 Thread / Future 里。
搭配 rpg/scripts/phase_digest_worker.py 可以拿来跑 cron / fire-and-forget。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any


# ────────────────────────────────────────────────────────────
#  LLM Prompt
# ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是 TRPG 阶段摘要器。读玩家与 GM 的多轮对话原文,产出一段结构化摘要,让一个
完全不知情的 GM 能在 100 turn 之后还记得这段剧情发生了什么。

【硬规则】
1. 只看输入材料,绝不发挥想象。材料里没说的人物、地点、决定一律不写。
2. summary 用 300-500 个汉字 (注意是汉字数,不是 token 数),第三人称、中性语
   气、像史官在记录。不要直接引用大段原文,要做提炼。
3. key_events 最多 5 条,挑剧情转折点 (新人物登场、关键道具变化、重大冲突、
   选择带来后果)。每条形如 {"turn": <整数>, "summary": "<一句话>"}。
4. key_npcs 最多 8 个,只列对剧情/玩家有持续影响的 NPC。每个形如
   {"name": "<姓名>", "first_turn": <整数>, "role": "<身份/职业>",
    "current_status": "<截至这段末尾对玩家的态度或处境>"}.
5. key_locations 最多 6 条,列玩家实际涉足过、对剧情有意义的地点,纯字符串
   数组。
6. key_decisions 最多 5 条,只列玩家显式做出的选择,以及那次选择"短期内"已经
   显现的后果。每条形如 {"turn": <整数>, "choice": "...", "consequence": "..."}.
7. emotion_arc 用 2-5 个汉语短词连成"→"分隔的链,描述玩家心境在这段内的变化,
   例如 "好奇 → 紧张 → 怀疑 → 坚定"。
8. 如果某字段没有合适内容 (例如这段没有显式选择),输出空数组 [] 或空字符串
   ""。不要编。
9. 你将得到上一段摘要 (如果存在) 和剧本预期段落 (如果存在),仅作衔接参考,
   不要把它们的内容当成本段发生过。
10. 注意去重: 同一个 NPC 不要在 key_npcs 里写两次。

【输出格式 (严格)】
仅输出一个 JSON object,直接以 `{` 开头,以 `}` 结尾。不要 markdown,不要
``` 代码围栏,不要任何解释文字。Schema:

{
  "summary": "<300-500 汉字>",
  "key_events": [{"turn": 5, "summary": "..."}, ...],
  "key_npcs": [{"name": "...", "first_turn": 3, "role": "...",
                "current_status": "..."}, ...],
  "key_locations": ["...", "..."],
  "key_decisions": [{"turn": 12, "choice": "...", "consequence": "..."}, ...],
  "emotion_arc": "好奇 → 紧张 → 坚定"
}
"""


# ────────────────────────────────────────────────────────────
#  公共 API
# ────────────────────────────────────────────────────────────


def compact_phase(
    save_id: int,
    phase_index: int,
    *,
    user_id: int | None = None,
    force: bool = False,
    model_override: str | None = None,
    api_id_override: str | None = None,
    _backend=None,  # 测试注入: 任意实现了 .call_structured() 的对象
) -> dict[str, Any]:
    """生成或重生成指定 (save_id, phase_index) 的阶段摘要。

    流程 (与 ARCH_107 §4.3 对齐):
      1. 拉 save_phase_digests 行 (确认存在 + 拿 turn_start/turn_end)
      2. force=False + status='closed' + summary 非空 → 直接返当前行
      3. 拉这段 branch_commits 的 player_input + gm_output
      4. 拉前 1 个 phase digest 作为衔接上下文 (避免摘要孤立)
      5. 拉剧本 context anchor (如果 chapter_facts 系统已就绪)
      6. 调 LLM,产出 JSON
      7. UPDATE save_phase_digests, 状态置 closed, 清空 metadata.needs_rebuild
      8. UPDATE branch_commits SET digested_in_phase, digest_at
      9. 返回写入的 digest dict

    返回的 dict 形如:
      {"save_id": ..., "phase_index": ..., "summary": ..., "key_events": [...],
       "key_npcs": [...], "key_locations": [...], "key_decisions": [...],
       "emotion_arc": ..., "turn_start": ..., "turn_end": ...,
       "elapsed_ms": <int>, "model": <str>, "commit_count": <int>}

    失败时返回 {"error": ..., "save_id": ..., "phase_index": ...} 而不抛异常,
    除非是 DB 写入异常或 LLM 重试后仍解析失败 (那是真的 bug,需要上抛)。
    """
    t0 = time.time()
    if not save_id or phase_index is None:
        return {"error": "missing save_id or phase_index", "save_id": save_id,
                "phase_index": phase_index}

    phase_row = _load_phase_row(save_id, phase_index)
    if not phase_row:
        return {"error": f"phase {phase_index} not found", "save_id": save_id,
                "phase_index": phase_index}

    # 短路: 已 closed + 有 summary + 不强制 → 不重做
    status = (phase_row.get("status") or "").lower()
    existing_summary = (phase_row.get("summary") or "").strip()
    if not force and status == "closed" and existing_summary:
        return {
            "save_id": save_id, "phase_index": phase_index,
            "summary": phase_row["summary"],
            "key_events": phase_row.get("key_events") or [],
            "key_npcs": phase_row.get("key_npcs") or [],
            "key_locations": phase_row.get("key_locations") or [],
            "key_decisions": phase_row.get("key_decisions") or [],
            "emotion_arc": phase_row.get("emotion_arc") or "",
            "turn_start": phase_row["turn_start"],
            "turn_end": phase_row["turn_end"],
            "elapsed_ms": int((time.time() - t0) * 1000),
            "model": "(cached)", "commit_count": 0,
            "skipped": "already_closed",
        }

    turn_start = int(phase_row["turn_start"])
    turn_end = int(phase_row["turn_end"])
    commits = _load_phase_commits(save_id, turn_start, turn_end)
    if not commits:
        return {"error": f"no branch_commits in turn {turn_start}-{turn_end}",
                "save_id": save_id, "phase_index": phase_index}

    prev_digest = _load_previous_digest(save_id, phase_index)
    script_anchor = _load_script_anchor(save_id, phase_row.get("phase_label") or "")
    user_prompt = _build_user_prompt(
        save_id=save_id,
        phase_index=phase_index,
        phase_row=phase_row,
        commits=commits,
        prev_digest=prev_digest,
        script_anchor=script_anchor,
    )

    # ── LLM 调用 ─────────────────────────────────────────────
    backend, model_name = _build_backend(
        _backend, model_override=model_override, api_id_override=api_id_override,
        user_id=user_id,
    )
    try:
        digest = _call_llm_with_retry(backend, _SYSTEM_PROMPT, user_prompt)
    except Exception as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "save_id": save_id, "phase_index": phase_index,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "model": model_name,
            "commit_count": len(commits),
        }

    # 规范化字段 (LLM 偶尔会缺字段)
    digest = _normalize_digest(digest)

    # ── 写 DB ────────────────────────────────────────────────
    _persist_digest(
        save_id=save_id, phase_index=phase_index,
        digest=digest, turn_start=turn_start, turn_end=turn_end,
        model=model_name,
    )
    _mark_commits_digested(save_id, turn_start, turn_end, phase_index)

    elapsed_ms = int((time.time() - t0) * 1000)
    return {
        "save_id": save_id, "phase_index": phase_index,
        **digest,
        "turn_start": turn_start, "turn_end": turn_end,
        "elapsed_ms": elapsed_ms,
        "model": model_name,
        "commit_count": len(commits),
    }


# ────────────────────────────────────────────────────────────
#  DB 读
# ────────────────────────────────────────────────────────────


def _load_phase_row(save_id: int, phase_index: int) -> dict[str, Any] | None:
    from platform_app.db import connect, init_db

    init_db()
    with connect() as db:
        row = db.execute(
            """
            select id, save_id, phase_index, turn_start, turn_end,
                   story_time_label, phase_label, summary, key_events,
                   key_npcs, key_locations, key_decisions, emotion_arc,
                   status, generated_by, metadata
              from save_phase_digests
             where save_id = %s and phase_index = %s
            """,
            (save_id, phase_index),
        ).fetchone()
    return dict(row) if row else None


def _load_phase_commits(save_id: int, turn_start: int, turn_end: int) -> list[dict[str, Any]]:
    """拉这段 turn 内的 branch_commits, 取每个 turn 的最新一条 (id 最大)。"""
    from platform_app.db import connect, init_db

    init_db()
    with connect() as db:
        rows = db.execute(
            """
            with ranked as (
              select id, turn_index, kind, player_input, gm_output, created_at,
                     row_number() over (partition by turn_index order by id desc) as rn
                from branch_commits
               where save_id = %s
                 and turn_index between %s and %s
            )
            select id, turn_index, kind, player_input, gm_output, created_at
              from ranked
             where rn = 1
             order by turn_index asc
            """,
            (save_id, turn_start, turn_end),
        ).fetchall()
    return [dict(r) for r in rows]


def _load_previous_digest(save_id: int, phase_index: int) -> dict[str, Any] | None:
    if phase_index <= 0:
        return None
    from platform_app.db import connect, init_db

    init_db()
    with connect() as db:
        row = db.execute(
            """
            select phase_index, phase_label, story_time_label, summary,
                   key_events, key_npcs, emotion_arc
              from save_phase_digests
             where save_id = %s
               and phase_index < %s
               and status = 'closed'
             order by phase_index desc
             limit 1
            """,
            (save_id, phase_index),
        ).fetchone()
    return dict(row) if row else None


def _load_script_anchor(save_id: int, phase_label: str) -> dict[str, Any] | None:
    """从 game_saves 反查 script_id, 再用 phase_label 查 script-level phase_digests。

    剧本期望线只用作衔接提示,缺了不影响摘要,所以全程吞异常。
    """
    if not phase_label:
        return None
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            srow = db.execute(
                "select script_id from game_saves where id = %s",
                (save_id,),
            ).fetchone()
            if not srow:
                return None
            script_id = int(srow["script_id"])
            try:
                row = db.execute(
                    """
                    select phase_label, summary, key_events,
                           story_time_label_start, story_time_label_end
                      from phase_digests
                     where script_id = %s and phase_label = %s
                     limit 1
                    """,
                    (script_id, phase_label),
                ).fetchone()
            except Exception:
                # script-level phase_digests 表可能未建 (新 server 上线时)
                return None
            return dict(row) if row else None
    except Exception:
        return None


# ────────────────────────────────────────────────────────────
#  Prompt 组装
# ────────────────────────────────────────────────────────────


def _build_user_prompt(
    *,
    save_id: int,
    phase_index: int,
    phase_row: dict[str, Any],
    commits: list[dict[str, Any]],
    prev_digest: dict[str, Any] | None,
    script_anchor: dict[str, Any] | None,
) -> str:
    lines: list[str] = []
    lines.append(f"# 阶段元信息")
    lines.append(f"- save_id = {save_id}")
    lines.append(f"- phase_index = {phase_index}")
    lines.append(f"- phase_label = {(phase_row.get('phase_label') or '(未命名)')!r}")
    lines.append(f"- story_time_label = {(phase_row.get('story_time_label') or '(未知)')!r}")
    lines.append(f"- turn_start = {phase_row.get('turn_start')}")
    lines.append(f"- turn_end = {phase_row.get('turn_end')}")
    lines.append(f"- commit_count = {len(commits)}")

    if prev_digest:
        lines.append("")
        lines.append("# 衔接参考: 上一段阶段摘要 (仅参考,不要复述)")
        lines.append(f"- 上段 phase_index = {prev_digest.get('phase_index')}")
        lines.append(f"- 上段 phase_label = {prev_digest.get('phase_label') or '(未命名)'}")
        lines.append(f"- 上段时间 = {prev_digest.get('story_time_label') or '(未知)'}")
        lines.append(f"- 上段 summary: {(prev_digest.get('summary') or '')[:600]}")
        ev = prev_digest.get("key_events") or []
        if ev:
            lines.append("- 上段 key_events:")
            for e in ev[:5]:
                if isinstance(e, dict):
                    lines.append(f"    · turn {e.get('turn', '?')}: {e.get('summary', '')[:80]}")
        if prev_digest.get("emotion_arc"):
            lines.append(f"- 上段 emotion_arc: {prev_digest['emotion_arc']}")

    if script_anchor:
        lines.append("")
        lines.append("# 剧本期望参考 (剧本本来在这段大概应该发生什么,仅参考)")
        lines.append(f"- 剧本 phase_label = {script_anchor.get('phase_label')}")
        lines.append(f"- 剧本时间段 = {script_anchor.get('story_time_label_start') or ''} "
                     f"→ {script_anchor.get('story_time_label_end') or ''}")
        s_sum = (script_anchor.get("summary") or "")[:800]
        if s_sum:
            lines.append(f"- 剧本摘要: {s_sum}")

    lines.append("")
    lines.append("# 本段对话原文 (要摘要的就是这个)")
    for c in commits:
        turn = c.get("turn_index")
        kind = c.get("kind") or ""
        # turn 0 一般是 'root' commit, player_input 为空
        player = _truncate((c.get("player_input") or "").strip(), 800)
        gm = _truncate((c.get("gm_output") or "").strip(), 1600)
        block: list[str] = [f"## turn {turn}"]
        if kind and kind != "user":
            block.append(f"[kind={kind}]")
        if player:
            block.append(f"[玩家] {player}")
        if gm:
            block.append(f"[GM] {gm}")
        lines.append("\n".join(block))
        lines.append("")

    lines.append("# 输出要求")
    lines.append("严格按 system prompt 的 JSON schema 输出。仅 JSON object,不要任何"
                 "额外文字。")
    return "\n".join(lines)


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


# ────────────────────────────────────────────────────────────
#  Backend
# ────────────────────────────────────────────────────────────


def _build_backend(
    injected: Any,
    *,
    model_override: str | None,
    api_id_override: str | None,
    user_id: int | None,
) -> tuple[Any, str]:
    """返回 (backend, model_name) 。

    优先级: 注入 > model_override + api_id_override > 默认 vertex_ai/gemini-3.5-flash。
    返回的 backend 必须实现 .call_structured(system, messages, max_tokens) → str。
    """
    if injected is not None:
        return injected, getattr(injected, "model_name", "<injected>")

    api_id = api_id_override or "vertex_ai"
    model = model_override or "gemini-3.5-flash"
    if api_id == "vertex_ai":
        from agents.gm import _VertexBackend
        return _VertexBackend(model=model), model
    # 不支持的 backend: 抛错,让调用方知道。
    raise ValueError(f"phase_digest_agent 暂只支持 vertex_ai (传入 api_id={api_id!r})")


# ────────────────────────────────────────────────────────────
#  LLM 调用 + 解析
# ────────────────────────────────────────────────────────────


_JSON_FENCE = re.compile(r"```(?:json)?\s*\n?\s*(\{[\s\S]*?\})\s*\n?```", re.MULTILINE)


def _call_llm_with_retry(backend: Any, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """call_structured + 一次重试 + 解析。

    重试逻辑:
      - 首次: 走 backend.call_structured (Vertex 的 call_structured 已经设了
              response_mime_type=application/json + temperature=0.1)
      - 重试: 同样接口,但调用方 (我们) 在 system prompt 里追加一段"上次输出
              不是 valid JSON,请严格按 schema 重输出"的提醒,温度逻辑由 backend
              内部控制(call_structured 已经设了较低温度)。
    """
    messages = [{"role": "user", "content": user_prompt}]
    # 第一次
    try:
        text = backend.call_structured(system_prompt, messages, max_tokens=2400)
        parsed = _parse_json(text)
        if parsed is not None:
            return parsed
    except Exception as exc:
        _last_err = exc
    else:
        _last_err = ValueError("first call: not valid JSON")

    # 第二次: 在 system prompt 后追加一句"上次失败"的提醒
    repaired_system = (
        system_prompt + "\n\n【重要】上一次输出无法解析为 JSON。请严格按上文的"
        "JSON schema 重新输出,不要 markdown,不要解释,直接以 `{` 开始。"
    )
    text2 = backend.call_structured(repaired_system, messages, max_tokens=2400)
    parsed2 = _parse_json(text2)
    if parsed2 is not None:
        return parsed2

    raise ValueError(
        f"LLM 输出两次都不是合法 JSON。第一次异常: {_last_err}; "
        f"第二次输出片段: {text2[:200]!r}"
    )


def _parse_json(text: str) -> dict[str, Any] | None:
    """从 LLM 文本里提 JSON object。返回 None 表示提不到 → 触发重试。"""
    if not text:
        return None
    text = text.strip()
    # 1) 整段就是 JSON object
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # 2) ```json fence
    m = _JSON_FENCE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    # 3) 最宽松: 第一个 { 到最后一个 } 之间
    lo = text.find("{")
    hi = text.rfind("}")
    if 0 <= lo < hi:
        try:
            obj = json.loads(text[lo : hi + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _normalize_digest(d: dict[str, Any]) -> dict[str, Any]:
    """规范化 LLM 输出, 补齐缺失字段 / 截掉过长字段。"""

    def _as_list_of_dict(v: Any) -> list[dict[str, Any]]:
        if not isinstance(v, list):
            return []
        return [x for x in v if isinstance(x, dict)]

    def _as_list_of_str(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if isinstance(x, (str, int, float)) and str(x).strip()]

    summary = str(d.get("summary") or "").strip()
    key_events = _as_list_of_dict(d.get("key_events"))[:5]
    key_npcs = _as_list_of_dict(d.get("key_npcs"))[:8]
    key_locations = _as_list_of_str(d.get("key_locations"))[:6]
    key_decisions = _as_list_of_dict(d.get("key_decisions"))[:5]
    emotion_arc = str(d.get("emotion_arc") or "").strip()[:200]

    # 单条上限
    summary = summary[:2000]
    for ev in key_events:
        if "summary" in ev:
            ev["summary"] = str(ev["summary"])[:300]
        if "turn" in ev:
            try:
                ev["turn"] = int(ev["turn"])
            except Exception:
                ev["turn"] = 0
    for npc in key_npcs:
        if "name" in npc:
            npc["name"] = str(npc["name"])[:60]
        if "role" in npc:
            npc["role"] = str(npc["role"])[:120]
        if "current_status" in npc:
            npc["current_status"] = str(npc["current_status"])[:200]
        if "first_turn" in npc:
            try:
                npc["first_turn"] = int(npc["first_turn"])
            except Exception:
                npc["first_turn"] = 0
    for dec in key_decisions:
        if "choice" in dec:
            dec["choice"] = str(dec["choice"])[:200]
        if "consequence" in dec:
            dec["consequence"] = str(dec["consequence"])[:300]
        if "turn" in dec:
            try:
                dec["turn"] = int(dec["turn"])
            except Exception:
                dec["turn"] = 0

    return {
        "summary": summary,
        "key_events": key_events,
        "key_npcs": key_npcs,
        "key_locations": key_locations,
        "key_decisions": key_decisions,
        "emotion_arc": emotion_arc,
    }


# ────────────────────────────────────────────────────────────
#  DB 写
# ────────────────────────────────────────────────────────────


def _persist_digest(
    *,
    save_id: int, phase_index: int,
    digest: dict[str, Any],
    turn_start: int, turn_end: int,
    model: str,
) -> None:
    from platform_app.db import connect, init_db
    from psycopg.types.json import Jsonb

    init_db()
    with connect() as db:
        # 先读 metadata,合并 needs_rebuild=False
        row = db.execute(
            "select metadata from save_phase_digests where save_id = %s and phase_index = %s",
            (save_id, phase_index),
        ).fetchone()
        meta = dict((row or {}).get("metadata") or {})
        meta["needs_rebuild"] = False
        meta["last_compact_model"] = model
        meta["last_compact_at"] = time.time()

        db.execute(
            """
            update save_phase_digests
               set summary       = %s,
                   key_events    = %s,
                   key_npcs      = %s,
                   key_locations = %s,
                   key_decisions = %s,
                   emotion_arc   = %s,
                   status        = 'closed',
                   generated_by  = 'llm',
                   metadata      = %s,
                   updated_at    = now()
             where save_id = %s and phase_index = %s
            """,
            (
                digest["summary"],
                Jsonb(digest["key_events"]),
                Jsonb(digest["key_npcs"]),
                Jsonb(digest["key_locations"]),
                Jsonb(digest["key_decisions"]),
                digest["emotion_arc"],
                Jsonb(meta),
                save_id, phase_index,
            ),
        )


def _mark_commits_digested(save_id: int, turn_start: int, turn_end: int, phase_index: int) -> None:
    from platform_app.db import connect, init_db

    init_db()
    with connect() as db:
        db.execute(
            """
            update branch_commits
               set digested_in_phase = %s,
                   digest_at        = now()
             where save_id = %s
               and turn_index between %s and %s
            """,
            (phase_index, save_id, turn_start, turn_end),
        )


__all__ = ["compact_phase"]
