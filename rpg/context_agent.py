"""
context_agent.py - visible context-curation sub-agent.

The main GM should not receive the whole novel. This small agent runs before
the GM call, resolves timeline targets, retrieves only the relevant modules, and
emits inspectable steps for the UI.
"""
from __future__ import annotations

import time
import json
import re
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Generator
from typing import Any

from context_engine import build_context_bundle
from retrieval import retrieve_context
from timeline_index import timeline_filter_for_label
from timeline_state import detect_time_directives


AGENT_PROMPT = """\
你是 Demand Resolver 子代理。你的唯一任务是把玩家的自然语言输入翻译成
结构化的「本轮需求账本」（Demand Ledger），交给系统校验后再喂给主 GM。

边界：你**不写正文、不直接改状态、不推进时间线、不替主 GM 决策**——
你只抽取需求和制定上下文/检索计划。

工作步骤：
1. 解析玩家输入里的章节、年份、日期、阶段、地点和人物意图。
2. 若玩家请求时间跳跃，标记 timeline_target 但不直接推进；/set 是硬约束按当前状态处理。
3. 区分硬约束（必须满足）与软偏好（最好满足但可妥协）。
4. 列出本轮可执行的候选动作（叙事/询问/状态写入），让主 GM 在候选范围内决策。
5. 制定 acceptance：本轮 GM 输出在哪些方面满足就算成功。
6. 评估自己的 confidence；不确定时填 clarifying_question 让系统先问玩家。

必须返回 JSON（不要 markdown 围栏，不要解释文字）：

{
  "intent": "玩家意图一句话",
  "active_goal": "本轮玩家真正想达成的目标（不是字面，是底层意图）",
  "hard_constraints": ["必须满足的约束（违反这条本轮就算失败）"],
  "soft_preferences": ["希望满足但可妥协的偏好"],
  "target_entities": ["涉及的角色/势力名"],
  "target_location": "目标地点；无则空",
  "target_time": "目标时间；无则空",
  "timeline_target": "若玩家请求时间跳转的目标 label，否则空字符串",
  "retrieval_query": "用于检索的短查询",
  "retrieval_plan": {
    "must_include": ["必须进入主 GM 上下文的事实"],
    "should_include": ["有助但非必须的素材"]
  },
  "candidate_actions": [
    "本轮 GM 可以做的 2-5 个具体动作（如 '叙事：阿衡推开灯塔门，描写室内' / '询问：是否要先观察再进入' / '写状态：player.current_location=灯塔'）"
  ],
  "acceptance": [
    "本轮 GM 输出满足以下条件即算成功，每条要可验证（如 '正文里 GM 回应了玩家想去灯塔的请求' / '没把 1937 原著事件当本局已发生'）"
  ],
  "risk_flags": ["可能造成错位的风险（如 'pending_jump 待确认中，不要叙事到未来时间'）"],
  "confidence": 0.85,
  "clarifying_question": "",
  "reason": "为什么这样规划本轮（不会写给玩家）"
}

confidence 阈值：
- >= 0.7：清晰意图，正常调主 GM
- 0.5-0.7：有歧义但可推进，把歧义写进 risk_flags
- < 0.5：意图模糊，填 clarifying_question 让系统先问玩家，主 GM 本轮不出场

clarifying_question 写法：直接的封闭式问题 + 2-3 个候选答案。
例：「你想让阿衡先在塔下观察，还是直接推门进去？(A) 观察 (B) 推门进入 (C) 退后撤离」
"""


def run_context_agent(
    state,
    user_input: str,
    stop_requested: Callable[[], bool] | None = None,
    llm_curator: Callable[[str, str], str] | None = None,
    user_id: int | None = None,
    script_id: int | None = None,
    book_id: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    stop_requested = stop_requested or (lambda: False)
    started = time.time()
    steps: list[dict[str, Any]] = []

    def step(phase: str, message: str, status: str = "running", **data: Any) -> dict[str, Any]:
        payload = {
            "phase": phase,
            "message": message,
            "status": status,
            "elapsed_ms": int((time.time() - started) * 1000),
            **data,
        }
        steps.append(payload)
        return {"type": "step", "step": payload}

    def stopped() -> bool:
        if not stop_requested():
            return False
        yield_step = step("aborted", "玩家已停止上下文子代理，本轮不会调用主 GM。", "stopped")
        steps[-1] = yield_step["step"]
        return True

    mode = "llm_structured" if llm_curator else "local_fallback"
    yield step(
        "prompt",
        f"加载上下文子代理运行提示（模式：{mode}）。",
        "done",
        prompt=AGENT_PROMPT,
        mode=mode,
        request_isolated=True,
        writes_chat_history=False,
    )
    if stopped():
        yield {"type": "stopped", "steps": steps}
        return

    is_set = _is_set_command(user_input)
    directives = [] if is_set else detect_time_directives(user_input or "")
    if is_set:
        yield step("intent", "识别到 /set 强制设定；按已写入的用户硬约束构建上下文。", "done")
    elif directives:
        for directive in directives:
            state.request_time_jump(directive.target, directive.raw)
        yield step(
            "intent",
            f"识别到时间线请求：{directives[0].target}",
            "done",
            directives=[directive.__dict__ for directive in directives],
        )
    else:
        yield step("intent", "未发现显式时间跳跃；沿用当前锁定时间线。", "done")
    if stopped():
        yield {"type": "stopped", "steps": steps}
        return

    curator_plan: dict[str, Any] = {}
    if llm_curator:
        yield step(
            "llm_curator",
            "正在调用大模型子代理判断本轮上下文需求。",
            "running",
            request_isolated=True,
            expected_output="json",
            shared_with_main_gm=False,
        )
        llm_text = _call_llm_curator(
            llm_curator,
            _curator_task_prompt(state, user_input, directives),
            stop_requested,
        )
        if llm_text is None:
            yield {"type": "stopped", "steps": steps}
            return
        curator_plan = _parse_curator_json(llm_text)
        target = _normalize_timeline_target(curator_plan.get("timeline_target", ""))
        if target and not directives and not is_set:
            state.request_time_jump(target, user_input)
        yield step(
            "llm_curator",
            curator_plan.get("intent") or "大模型子代理已完成上下文判断。",
            "done",
            raw=llm_text,
            plan=curator_plan,
        )
    else:
        curator_plan = {
            "intent": "本地规则解析",
            "timeline_target": directives[0].target if directives else "",
            "retrieval_query": user_input,
            "must_include": [],
            "risk_flags": ["未启用大模型子代理，仅使用确定性规则。"],
            "reason": "没有传入 llm_curator。",
        }

    world = state.data.get("world", {})
    timeline = world.get("timeline", {})
    pending = timeline.get("pending_jump") or {}
    label = pending.get("to") or world.get("time", "")
    anchor = timeline_filter_for_label(label)
    yield step(
        "timeline",
        _timeline_message(label, anchor),
        "done",
        label=label,
        anchor=anchor,
        pending_jump=pending,
    )
    if stopped():
        yield {"type": "stopped", "steps": steps}
        return

    retrieval_query = _retrieval_query(user_input, curator_plan)
    # task 42：把 script_id 透传给 retrieve_context，让导入剧本不再读 MuMu 默认
    # .webnovel/* SQLite 和 indexes/*.json，只走 postgres script-scoped 检索。
    retrieved_context = retrieve_context(
        retrieval_query, state=state, user_id=user_id, script_id=script_id,
    )
    yield step(
        "retrieval",
        "已按时间线窗口裁剪 ChapterFact、原文片段和摘要。",
        "done",
        query=retrieval_query,
        chars=len(retrieved_context),
        estimated_tokens=max(1, len(retrieved_context) // 2),
        preview=_preview(retrieved_context),
    )
    if stopped():
        yield {"type": "stopped", "steps": steps}
        return

    bundle = build_context_bundle(
        state, user_input, retrieved_context,
        curator_plan=curator_plan, script_id=script_id, book_id=book_id,
    )
    cache = bundle["debug"].get("cache_plan", {})
    yield step(
        "assembly",
        "已生成主 GM 上下文清单；主模型只会收到裁剪后的层级。",
        "done",
        estimated_tokens=bundle["debug"].get("estimated_tokens", 0),
        layer_count=len(bundle["debug"].get("layers", [])),
        cache_plan=cache,
    )

    yield {
        "type": "result",
        "retrieved_context": retrieved_context,
        "bundle": bundle,
        "steps": steps,
        "agent_prompt": AGENT_PROMPT,
        "curator_plan": curator_plan,
    }


def _timeline_message(label: str, anchor: dict[str, Any]) -> str:
    if anchor.get("anchor_chapter"):
        return (
            f"时间线锚定到第{anchor.get('anchor_chapter')}章，"
            f"检索窗口 {anchor.get('chapter_min')} - {anchor.get('chapter_max')}。"
        )
    return f"未精确命中原著锚点：{label}"


def _preview(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _curator_task_prompt(state, user_input: str, directives: list[Any]) -> str:
    world = state.data.get("world", {})
    memory = state.data.get("memory", {})
    recent = state.history_messages(limit_turns=3)
    local_directives = [getattr(d, "target", "") for d in directives]
    return "\n".join([
        "请为本轮 RPG 生成前的上下文选择做判断，只返回 JSON。",
        "",
        "【玩家输入】",
        user_input or "",
        "",
        "【当前时间线】",
        str(world.get("time", "")),
        "",
        "【本地已识别时间线请求】",
        json.dumps(local_directives, ensure_ascii=False),
        "",
        "【强制设定规则】",
        "/set 开头的玩家输入代表用户显式改写设定、时间线、世界观或人设，必须作为硬约束交给主 GM，不得因为原时间线 locked 而忽略。",
        "",
        "【当前目标/主线】",
        f"{memory.get('main_quest', '')} / {memory.get('current_objective', '')}",
        "",
        "【最近对话】",
        json.dumps(recent, ensure_ascii=False)[:2400],
        "",
        "只输出 JSON，不要 Markdown。",
    ])


def _is_set_command(text: str) -> bool:
    return bool(re.match(r"^\s*/(?:set|设定|设置)\s+", text or "", re.I))


def _call_llm_curator(
    llm_curator: Callable[[str, str], str],
    task_prompt: str,
    stop_requested: Callable[[], bool],
) -> str | None:
    """轮询 future + 监听 stop。

    LLM 请求一旦发出无法在 HTTP 层硬中断（SDK 没暴露 cancel token），
    所以 stop_requested 触发后我们立即"放弃等待结果"，让上层马上响应用户。
    后台请求会继续跑完（继续计费），但返回的内容会被丢弃，不会进入存档/SSE。
    用更短的 poll 间隔（30ms）让 stop 响应快。
    """
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="curator")
    future = executor.submit(llm_curator, AGENT_PROMPT, task_prompt)
    try:
        while not future.done():
            if stop_requested():
                # 注意：future.cancel() 对已经在跑的请求不会真正取消
                # 后台请求会继续到完成；我们不再等待结果
                future.cancel()
                return None
            time.sleep(0.03)  # 之前 0.12s，现在 30ms 提高 stop 响应度
        return future.result()
    finally:
        # wait=False：不阻塞当前线程；如果 future 还在跑，由后台线程自然完成
        executor.shutdown(wait=False, cancel_futures=True)


def _parse_curator_json(text: str) -> dict[str, Any]:
    """task 79：Demand Ledger schema 解析。向后兼容旧 curator_plan 6 字段。"""
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.I | re.M).strip()
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        raw = match.group(0)
    try:
        data = json.loads(raw)
    except Exception:
        return {
            "intent": "大模型子代理返回无法解析，已回退到规则检索。",
            "active_goal": "",
            "hard_constraints": [],
            "soft_preferences": [],
            "target_entities": [],
            "target_location": "",
            "target_time": "",
            "timeline_target": "",
            "retrieval_query": "",
            "retrieval_plan": {"must_include": [], "should_include": []},
            "candidate_actions": [],
            "acceptance": [],
            "risk_flags": ["curator_json_parse_failed"],
            "confidence": 0.0,
            "clarifying_question": "",
            "reason": (text or "")[:300],
        }
    # retrieval_plan 嵌套对象处理 + 向后兼容老 must_include 顶层字段
    rp_raw = data.get("retrieval_plan") or {}
    must_include = _string_list(
        (rp_raw.get("must_include") if isinstance(rp_raw, dict) else None)
        or data.get("must_include")
    )
    should_include = _string_list(
        rp_raw.get("should_include") if isinstance(rp_raw, dict) else None
    )
    # confidence: 接受 number 或 string；0.0-1.0 范围裁剪
    try:
        conf = float(data.get("confidence", 1.0))
    except Exception:
        conf = 1.0
    conf = max(0.0, min(1.0, conf))
    return {
        "intent": str(data.get("intent") or ""),
        "active_goal": str(data.get("active_goal") or ""),
        "hard_constraints": _string_list(data.get("hard_constraints")),
        "soft_preferences": _string_list(data.get("soft_preferences")),
        "target_entities": _string_list(data.get("target_entities")),
        "target_location": str(data.get("target_location") or ""),
        "target_time": str(data.get("target_time") or ""),
        "timeline_target": str(data.get("timeline_target") or ""),
        "retrieval_query": str(data.get("retrieval_query") or ""),
        "retrieval_plan": {
            "must_include": must_include,
            "should_include": should_include,
        },
        # 向后兼容：保留顶层 must_include 让旧 _context_agent_decision 渲染不破
        "must_include": must_include,
        "candidate_actions": _string_list(data.get("candidate_actions")),
        "acceptance": _string_list(data.get("acceptance")),
        "risk_flags": _string_list(data.get("risk_flags")),
        "confidence": conf,
        "clarifying_question": str(data.get("clarifying_question") or "").strip(),
        "reason": str(data.get("reason") or ""),
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()][:8]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_timeline_target(value: str) -> str:
    value = " ".join((value or "").split()).strip()
    if not value:
        return ""
    if re.fullmatch(r"\d{1,5}", value):
        return f"第{value}章"
    return value


def _retrieval_query(user_input: str, plan: dict[str, Any]) -> str:
    parts = [
        user_input or "",
        _normalize_timeline_target(plan.get("timeline_target", "")),
        plan.get("retrieval_query", ""),
        " ".join(plan.get("must_include", []) or []),
    ]
    return "\n".join(part for part in parts if str(part).strip())
