"""RP harness 基准 — LLM 裁判层(pairwise)。

对同一 case 的两条 GM 回复(A=基线 / B=候选)做逐维度裁判:
  faithfulness    — 对原著忠实度(锚点/章节事件保留)
  coherence       — 前文一致性(记忆/状态延续,无遗忘/矛盾)
  identity        — 人物人格稳定(台词/行为与既定性格吻合)
  spoiler_control — 剧透控制(不提前泄露当前章节外的情节)

裁判全部走 OpenAICompatHarness.chat(),不引入新 HTTP 客户端。
输出 JSON: {"winner": "A"|"B"|"tie", "reason": "<=50chars"}
对非 JSON 输出做 regex 兜底;解析失败则 winner="tie", reason="parse_error"。
"""
from __future__ import annotations

import json
import re
from typing import Any

DIMS = ["faithfulness", "coherence", "identity", "spoiler_control"]

# ── 每维度 rubric 文本 ──────────────────────────────────────────────────────
_RUBRIC: dict[str, str] = {
    "faithfulness": (
        "只判【对原著的忠实度】,不评文笔好坏。"
        "标准:\n"
        "- 哪个回复更好地保留了 must_preserve 列表里的要素\n"
        "- 哪个回复引入了与 anchor_summary/chapter_event_snippet 冲突的人名、事件或设定\n"
        "- 哪个回复对玩家输入的叙事响应更贴近章节事件的走向\n"
        "不得因为文字更华丽而选择该回复。"
    ),
    "coherence": (
        "只判【与前文的延续一致性】。"
        "标准:\n"
        "- 哪个回复对前几轮对话的人物位置、状态、已发生事件更一致\n"
        "- 哪个回复出现了与前文矛盾的细节(遗忘/人物位置错误/已发生事件被撤销等)\n"
        "- 哪个回复更自然地承接了 prior 中最后的玩家输入"
    ),
    "identity": (
        "只判【出场角色的人格稳定性】。"
        "标准:\n"
        "- 哪个回复里,角色的台词和行为更符合 canon_aliases 里建立的既定性格\n"
        "- 哪个回复出现了人格跳变(突然变温柔/突然变冷漠)或与既定立场矛盾的行为\n"
        "如有 canon_worldbook 补充设定,也应参考。"
    ),
    "spoiler_control": (
        "只判【剧透控制】。"
        "标准:\n"
        "- 哪个回复严格限制在 current_chapter_range 范围内的信息\n"
        "- 哪个回复泄露了玩家当前章节还不应知晓的未来情节、角色归宿或关键反转\n"
        "current_chapter_range 之外的任何具体事件、结局、新角色出场均视为剧透。"
    ),
}

# ── prompt builder ──────────────────────────────────────────────────────────

def judge_dim_prompt(dim: str, case: dict, resp_a: str, resp_b: str) -> str:
    """按维度构建裁判 prompt。只注入该维度需要的上下文字段。"""
    lines = [
        f"你是一个专业的叙事质量裁判。你的任务是对以下【{dim}】维度做出判断。",
        "",
        f"【评判标准】\n{_RUBRIC[dim]}",
        "",
    ]

    # 注入各维度专用上下文
    if dim == "faithfulness":
        anchor_summary = (case.get("anchor_summary") or "").strip()
        must_preserve = case.get("must_preserve") or []
        chapter_event_snippet = (case.get("chapter_event_snippet") or "").strip()
        player_input = (case.get("player_input") or "").strip()
        if anchor_summary:
            lines.append(f"【锚点摘要(已发生)】\n{anchor_summary[:800]}\n")
        if must_preserve:
            mp = must_preserve if isinstance(must_preserve, list) else [str(must_preserve)]
            lines.append(f"【must_preserve 要素】\n" + "\n".join(f"- {x}" for x in mp[:20]) + "\n")
        if chapter_event_snippet:
            lines.append(f"【当前章节事件参考】\n{chapter_event_snippet[:600]}\n")
        if player_input:
            lines.append(f"【玩家输入】\n{player_input[:300]}\n")

    elif dim == "coherence":
        prior = case.get("prior") or []
        # 只取最后 4 轮(judge_cases 已裁到 4 轮,这里做二次防护)
        recent = prior[-4:] if len(prior) > 4 else prior
        if recent:
            prior_text = "\n".join(
                f"[{h.get('role','?')}]: {(h.get('content') or '')[:300]}"
                for h in recent
            )
            lines.append(f"【前文(最近 {len(recent)} 轮)】\n{prior_text}\n")

    elif dim == "identity":
        canon = case.get("canon_aliases") or {}
        names = list(canon.keys())[:20]
        if names:
            lines.append(f"【已知 canon 角色】\n" + "、".join(names) + "\n")
        worldbook = (case.get("canon_worldbook") or "").strip()
        if worldbook:
            lines.append(f"【世界书角色补充设定】\n{worldbook[:400]}\n")

    elif dim == "spoiler_control":
        rng = case.get("current_chapter_range") or []
        if rng and len(rng) >= 2:
            lines.append(f"【当前章节范围】第 {rng[0]} 章 ~ 第 {rng[1]} 章(玩家当前视野上限)\n")
        elif rng and len(rng) == 1:
            lines.append(f"【当前章节】第 {rng[0]} 章\n")

    lines += [
        "【回复 A】",
        resp_a[:1200],
        "",
        "【回复 B】",
        resp_b[:1200],
        "",
        '请判断哪个回复在上述维度上更好。只输出一个 JSON 对象,不要输出其他任何内容:',
        '{"winner": "A" 或 "B" 或 "tie", "reason": "不超过50字的理由"}',
    ]
    return "\n".join(lines)


# ── 单次裁判调用 ────────────────────────────────────────────────────────────

def judge_pair(case: dict, resp_a: str, resp_b: str, dim: str, harness) -> dict[str, Any]:
    """对一个 case 的一个维度做裁判。复用 harness.chat()。"""
    prompt = judge_dim_prompt(dim, case, resp_a, resp_b)
    messages = [
        {"role": "system", "content": "你是一个叙事质量裁判,只输出 JSON。"},
        {"role": "user", "content": prompt},
    ]
    raw = ""
    try:
        raw = harness.chat(messages, max_tokens=120)
    except Exception as e:
        return {"dim": dim, "winner": "tie", "reason": "harness_error", "raw": str(e)}

    # 解析 JSON
    winner, reason = _parse_judge_json(raw)
    return {"dim": dim, "winner": winner, "reason": reason, "raw": raw}


def _parse_judge_json(raw: str) -> tuple[str, str]:
    """从 LLM 输出里提取 {"winner":..., "reason":...}。失败则 winner=tie。"""
    # 先尝试直接 json.loads
    try:
        obj = json.loads(raw.strip())
        w = str(obj.get("winner", "tie")).strip()
        r = str(obj.get("reason", ""))[:80]
        if w in ("A", "B", "tie"):
            return w, r
    except Exception:
        pass

    # regex 兜底:抓第一个 {...}
    m = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            w = str(obj.get("winner", "tie")).strip()
            r = str(obj.get("reason", ""))[:80]
            if w in ("A", "B", "tie"):
                return w, r
        except Exception:
            pass

    # 最后兜底:从文本中直接提取 winner 关键字
    if re.search(r'\bwinner\s*[:\"].*?"A"', raw):
        return "A", "fallback_parse"
    if re.search(r'\bwinner\s*[:\"].*?"B"', raw):
        return "B", "fallback_parse"

    return "tie", "parse_error"


# ── 单 case 全维度裁判 ──────────────────────────────────────────────────────

def judge_case(case: dict, resp_a: str, resp_b: str, harness,
               dims: list[str] = DIMS) -> dict[str, Any]:
    """对一个 case 跑所有维度裁判。"""
    per_dim: dict[str, dict] = {}
    for dim in dims:
        per_dim[dim] = judge_pair(case, resp_a, resp_b, dim, harness)
    return {"per_dim": per_dim, "save_id": case.get("save_id"), "turn_idx": case.get("turn_idx")}


# ── 批量裁判 + 聚合 ─────────────────────────────────────────────────────────

def batch_judge(cases: list[dict], resps_a: list[str], resps_b: list[str],
                harness, dims: list[str] = DIMS,
                max_cases: int = 50) -> dict[str, Any]:
    """批量裁判,聚合 per-dim 胜负统计 + overall B_win_rate 结论。

    返回:
      {
        dim: {"A_wins": int, "B_wins": int, "tie": int, "B_win_rate": float},
        "overall": {"B_win_rate": float, "verdict": "B_better"|"A_better"|"inconclusive"},
        "n_cases": int,
        "case_results": [judge_case result, ...],
      }
    """
    n = min(len(cases), len(resps_a), len(resps_b), max_cases)
    dim_counts: dict[str, dict[str, int]] = {
        d: {"A_wins": 0, "B_wins": 0, "tie": 0} for d in dims
    }
    case_results: list[dict] = []

    for i in range(n):
        res = judge_case(cases[i], resps_a[i], resps_b[i], harness, dims)
        case_results.append(res)
        for dim, dr in res["per_dim"].items():
            w = dr.get("winner", "tie")
            if w == "A":
                dim_counts[dim]["A_wins"] += 1
            elif w == "B":
                dim_counts[dim]["B_wins"] += 1
            else:
                dim_counts[dim]["tie"] += 1

    dim_stats: dict[str, Any] = {}
    total_a = total_b = 0
    for dim, cnt in dim_counts.items():
        a, b = cnt["A_wins"], cnt["B_wins"]
        total_a += a
        total_b += b
        decisive = a + b
        rate = b / decisive if decisive > 0 else 0.5
        dim_stats[dim] = {"A_wins": a, "B_wins": b, "tie": cnt["tie"],
                          "B_win_rate": round(rate, 4)}

    # overall(合并所有维度的决定性对局)
    decisive_total = total_a + total_b
    overall_rate = total_b / decisive_total if decisive_total > 0 else 0.5
    if overall_rate > 0.55:
        verdict = "B_better"
    elif overall_rate < 0.45:
        verdict = "A_better"
    else:
        verdict = "inconclusive"

    result: dict[str, Any] = dict(dim_stats)
    result["overall"] = {"B_win_rate": round(overall_rate, 4), "verdict": verdict}
    result["n_cases"] = n
    result["case_results"] = case_results
    return result
