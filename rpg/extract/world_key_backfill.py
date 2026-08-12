"""extract/world_key_backfill.py — world_key 模型批次 3a(结构先验)+ 3b LLM 窄确认(可选)。

设计 docs/design/world_key_model_v1.md §2/§3/§4/§7(3a 行 + 3b LLM 确认行)。

第一层·结构先验(免费、确定性、零 LLM,批次 3a 上线,默认路径不变):
  · 以卷(volume_title)为段粒度,无卷则每 20 章一窗;
  · 逐段相对上一段判 {continuous | time_skip | new_world};
  · new_world 起新 world 标签,continuous/time_skip 沿用上段 world;
  · 过切回退:产出 world 数 ≥ 段数 × 0.8 → 整书退回单世界(null)。

纯函数层(classify_segments)零 DB、零 IO,可单测全覆盖。

第二层·LLM 窄确认(§3 第二层,本次新增,默认不跑 —— use_llm=False 时零行为变化):
  · 只对结构先验产出的段边界追加确认:相对上一段,本段是
    continuous / time_skip / new_world?new_world 必须举证(引用 summary 原话),
    无证据降级 continuous;
  · 纯函数层(confirm_segments_llm)同样零 DB、零 IO,call_fn 由调用方注入
    (admin 工具/CLI 传真 LLM 调用,单测传假函数零外网);
  · 合并结果仍受 §3 第三层过切回退约束(distinct world 数 ≥ 段数 × 0.8 → 全退单世界)。

IO 层(backfill_worldlines)只读 chapter_facts + script_chapters,
幂等 UPDATE 两列(worldline_key/in_world_time),不碰其它列。
use_llm=True 时在结构先验之后追加第二层确认(admin 手动触发,BYOK,默认 False)。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

# 第一层结构先验词表(§3):卷/章标题命中即视为 new_world 候选。
# 宁漏勿误——词表故意窄,过切有 §3 第三层回退兜底。
_NEW_WORLD_KEYWORDS = (
    "副本", "世界", "位面", "穿越", "轮回",
    "序幕", "尾声", "梦境", "回忆篇", "if线", "平行", "异世界",
)
# 「第X世」(X 可以是中文数字或阿拉伯数字)
_NEW_WORLD_RE = re.compile(
    r"第[一二三四五六七八九十0-9]+世"
)
# 【】括号编号(如「【第一副本】」「【位面3】」)——编号变化也是候选信号,
# 但只要词表已命中同一标题就不用重复判定,这里只处理"仅括号编号、无其它关键词"的情况。
_BRACKET_RE = re.compile(r"【([^】]*)】")

_FALLBACK_WINDOW = 20  # 无卷信息时的章窗大小
_OVERCUT_RATIO = 0.8  # §3 第三层:world 数 / 段数 达到此比例即判过切


def _clean_norm_label(text: str) -> str:
    """归一化去标点，供 world_label 与过切判定使用。"""
    s = (text or "").strip()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[，。！？、；：,.!?;:\"'“”‘’()（）\[\]]+", "", s)
    return s


def _normalize_world_label(text: str) -> str:
    """world_label 归一化:去标点截断 24 字(§3)。"""
    s = _clean_norm_label(text)
    return s[:24]


def _hits_keyword(text: str) -> str:
    """返回命中的关键词/短语(用作 world_label 素材),未命中返回空串。"""
    if not text:
        return ""
    for kw in _NEW_WORLD_KEYWORDS:
        if kw in text:
            return kw
    m = _NEW_WORLD_RE.search(text)
    if m:
        return m.group(0)
    return ""


def _bracket_tag(text: str) -> str:
    """取标题里的【】括号编号(如「【第一副本】」→「第一副本」),无则空串。"""
    if not text:
        return ""
    m = _BRACKET_RE.search(text)
    return m.group(1).strip() if m else ""


def _is_new_world_candidate(seg: dict, prev_seg: dict | None) -> tuple[bool, str, str]:
    """第一层结构先验(§3):标题/卷名命中词表 或 【】括号编号变化 → new_world 候选。

    注意:分段本身已经是"以 volume_title 为粒度"切的(§3"对每个段…判定"——段边界
    即卷/部切换处),所以"volume_title 变化"这条结构先验在【段级分类】上不能再重复
    当独立候选信号——段与段之间 volume_title 恒不同(否则早已被 _split_raw_segments
    并入同段),若把"变了"本身也算候选,则任意多卷书都会 100% 段判 new_world,
    过切回退(§3 第三层,world 数 ≥ 段数×0.8)恒真触发,整条结构先验层失去区分度
    (无限流/线性书退化成同一结果:全 null)。只有当卷名/章标题本身带出「这是不同
    世界」的实证(词表命中、【】编号变化)才提升为候选;单纯换了卷名/部名(常见的
    "第一卷/第二卷"式线性分卷)默认沿用上段 world——留给 §3 第二层 LLM 窄确认兜底
    误判,而不是在零证据时就升级。

    返回 (是否候选, 用于 world_label 的原始短语, 信号种类 "keyword"|"bracket")。
    信号强度:bracket(【】编号变化)=结构性强信号,单命中可信;keyword(词表)=弱信号,
    无卷名书上需 ≥2 处佐证(见 classify_segments 稀疏护栏)。
    """
    vt = (seg.get("volume_title") or "").strip()
    title = (seg.get("title") or "").strip()
    prev_title = (prev_seg.get("title") or "").strip() if prev_seg else ""

    hit = _hits_keyword(vt) or _hits_keyword(title)
    if hit:
        # world_label 素材优先用命中所在的完整短语(卷名优先于章标题)
        source = vt if _hits_keyword(vt) else title
        return True, (source or hit), "keyword"

    bracket = _bracket_tag(title)
    prev_bracket = _bracket_tag(prev_title) if prev_seg else ""
    if bracket and bool(prev_seg) and bracket != prev_bracket:
        return True, bracket, "bracket"

    return False, "", ""


def _split_raw_segments(ordered: list[dict]) -> list[dict]:
    """分段:卷粒度优先(volume_title 连续相同即同段),无任何卷信息则回退 20 章窗。"""
    raw_segments: list[dict] = []
    has_any_volume = any((c.get("volume_title") or "").strip() for c in ordered)

    if has_any_volume:
        for c in ordered:
            vt = (c.get("volume_title") or "").strip()
            ci = int(c.get("chapter_index", 0))
            title = (c.get("title") or "").strip()
            if raw_segments and raw_segments[-1]["volume_title"] == vt:
                raw_segments[-1]["ch_max"] = ci
            else:
                raw_segments.append({
                    "volume_title": vt,
                    "title": title,  # 段首章标题
                    "ch_min": ci,
                    "ch_max": ci,
                })
    else:
        for c in ordered:
            ci = int(c.get("chapter_index", 0))
            title = (c.get("title") or "").strip()
            if raw_segments and ci - raw_segments[-1]["ch_min"] < _FALLBACK_WINDOW:
                raw_segments[-1]["ch_max"] = ci
            else:
                raw_segments.append({
                    "volume_title": "",
                    "title": title,  # 段首章标题
                    "ch_min": ci,
                    "ch_max": ci,
                })
    return raw_segments


def _classify_raw_segments(raw_segments: list[dict]) -> list[dict]:
    """逐段三分类(未做过切回退)。返回段列表,每段:
        {"world_label": str | None, "verdict": "continuous"|"new_world", "ch_min": int, "ch_max": int}
    """
    segments: list[dict] = []
    current_world: str | None = None
    prev_raw: dict | None = None
    for raw in raw_segments:
        is_candidate, label_source, signal_kind = _is_new_world_candidate(raw, prev_raw)
        if is_candidate:
            verdict = "new_world"
            current_world = _normalize_world_label(label_source) or current_world
        else:
            verdict = "continuous"
            # current_world 保持不变(沿用上段;time_skip 在结构先验层不产生候选,同样落此分支)
        segments.append({
            "world_label": current_world,
            "verdict": verdict,
            "signal": signal_kind,
            "ch_min": raw["ch_min"],
            "ch_max": raw["ch_max"],
        })
        prev_raw = raw
    return segments


def _is_overcut(segments: list[dict]) -> bool:
    """§3 第三层过切回退判定:distinct world 数 ≥ 段数 × 0.8。"""
    if not segments:
        return False
    distinct_worlds = {s["world_label"] for s in segments if s["world_label"]}
    return len(distinct_worlds) > 0 and len(distinct_worlds) >= len(segments) * _OVERCUT_RATIO


def classify_segments(chapters: list[dict]) -> list[dict]:
    """输入每章 {chapter_index, title, volume_title, summary}(乱序也可,内部会按 chapter_index
    排序),以卷(volume_title)为段粒度(无卷则每 20 章一窗)分段,逐段相对上一段三分类
    {continuous | time_skip | new_world}(结构先验层不单独产生 time_skip,恒落 continuous;
    time_skip 的检出留待 §3 第二层 LLM 确认,批次 3a 只做第一层)。

    返回段列表,每段:
        {"world_label": str | None, "verdict": str, "ch_min": int, "ch_max": int, "overcut": bool}
    world_label=None 表示主世界(未命中任何 new_world,或触发过切回退后全部退回主世界)。
    overcut 对列表中每段都相同(整书级判定),供调用方零额外调用即可读到全局回退状态。

    过切回退(§3 第三层,纯函数内):distinct world 数 ≥ 段数 × 0.8 → 全部退 null(单世界)。
    """
    if not chapters:
        return []

    ordered = sorted(chapters, key=lambda c: int(c.get("chapter_index", 0)))
    raw_segments = _split_raw_segments(ordered)
    segments = _classify_raw_segments(raw_segments)

    overcut = _is_overcut(segments)
    # 孤立单命中护栏(生产 dry-run 实证,script 133):**仅当书没有卷名结构、分段退化
    # 为 20 章合成窗时**,词表的孤立单命中(如某章标题带「轮回」)不可信——会产出横跨
    # 几百章的错误伪世界,比全 null 更糟 → 整书退单世界,留给批次 3b 的 LLM 确认层
    # 用 summary 证据做正确切分。有真实卷名结构的书不受此护栏影响:单次世界切换是
    # 合法场景(穿越书恰好一个边界),卷结构本身就是佐证。
    # 信号强度分级:bracket(【】编号变化)=结构性强信号,单命中可信(如两段式穿越书);
    # keyword(词表)=弱信号,无卷名合成窗上孤立单命中不可信。
    _has_volumes = any((c.get("volume_title") or "").strip() for c in ordered)
    _weak_hits = sum(1 for s in segments
                     if s.get("verdict") == "new_world" and s.get("signal") == "keyword")
    _strong_hits = sum(1 for s in segments
                       if s.get("verdict") == "new_world" and s.get("signal") == "bracket")
    _too_sparse = (not _has_volumes) and _strong_hits == 0 and 0 < _weak_hits < 2
    if overcut or _too_sparse:
        for s in segments:
            s["world_label"] = None
            s["verdict"] = "continuous"

    for s in segments:
        s["overcut"] = overcut
        s["sparse_signal"] = _too_sparse

    return segments


def clean_in_world_time(label: str) -> str:
    """v1:透传该章已存 story_time_label 清洗版。优先复用 extract.resolve._clean_timeline_label,
    导入失败时内联同款逻辑(占位 label 归一空串)。"""
    try:
        from extract.resolve import _clean_timeline_label
        return _clean_timeline_label(label)
    except Exception:
        pass
    s = (label or "").strip()
    if not s or len(s) > 40:
        return ""
    placeholder_re = re.compile(
        r'^\s*(?:ch\s*\d+\s*节点'
        r'|第\s*[零一二三四五六七八九十百千两\d]+\s*[章回话](?:\s*节点)?'
        r'|chapter\s*\d+)\s*$',
        re.IGNORECASE,
    )
    if placeholder_re.match(s):
        return ""
    return s


def _segments_to_chapter_map(segments: list[dict]) -> dict[int, str | None]:
    """段列表展开成 {chapter_index: world_label} 查表,供 IO 层逐章写入。"""
    out: dict[int, str | None] = {}
    for seg in segments:
        for ci in range(seg["ch_min"], seg["ch_max"] + 1):
            out[ci] = seg["world_label"]
    return out


# ── 第二层·LLM 窄确认(§3 第二层,可选增强,默认不跑)────────────────

_LLM_MAX_SUMMARY_CHAPTERS = 6  # 每段拼摘要最多取段内前 N 章(控 token,够定性判断)
_LLM_VALID_VERDICTS = ("continuous", "time_skip", "new_world")


def _segment_summary_text(seg: dict, chapter_summaries: dict[int, str]) -> str:
    """从 chapter_facts.summary 拼段内前 _LLM_MAX_SUMMARY_CHAPTERS 章摘要,供 LLM 读证据。"""
    parts: list[str] = []
    ch_max = min(seg["ch_max"], seg["ch_min"] + _LLM_MAX_SUMMARY_CHAPTERS - 1)
    for ci in range(seg["ch_min"], ch_max + 1):
        s = (chapter_summaries.get(ci) or "").strip()
        if s:
            parts.append(f"第{ci}章:{s}")
    return "\n".join(parts)


def _build_confirm_prompt(prev_seg: dict, cur_seg: dict, chapter_summaries: dict[int, str]) -> tuple[str, str]:
    """构造 (system_prompt, user_prompt),供 call_fn 调用。"""
    system_prompt = (
        "你是小说时间线世界边界判定助手。给你上一段和本段的章节摘要,"
        "判断本段相对上一段是 continuous(延续同一世界)、time_skip(仅时间跳跃,"
        "世界未变)还是 new_world(进入了不同的世界/副本/位面/穿越目的地)。"
        "new_world 判定必须给出证据——引用摘要中的原话说明世界确实变了"
        "(例如场景/规则/身份的根本性转变);找不到这样的原话就不要判 new_world,"
        "宁可判 continuous。只输出严格 JSON,不要输出任何其它文字,格式:\n"
        '{"verdict": "continuous|time_skip|new_world", "world_label": "...", "evidence": "..."}'
    )
    prev_text = _segment_summary_text(prev_seg, chapter_summaries)
    cur_text = _segment_summary_text(cur_seg, chapter_summaries)
    user_prompt = (
        f"## 上一段(第{prev_seg['ch_min']}-{prev_seg['ch_max']}章,"
        f"当前世界标签={prev_seg.get('world_label') or '(主世界)'})\n"
        f"{prev_text or '(无摘要)'}\n\n"
        f"## 本段(第{cur_seg['ch_min']}-{cur_seg['ch_max']}章)\n"
        f"{cur_text or '(无摘要)'}\n\n"
        "请判定本段相对上一段的关系,输出严格 JSON。"
    )
    return system_prompt, user_prompt


def _parse_llm_verdict(raw_text: str) -> dict | None:
    """解析 call_fn 返回文本为严格 JSON,校验字段。非法/字段缺失返回 None(调用方降级)。"""
    if not raw_text or not isinstance(raw_text, str):
        return None
    try:
        obj = json.loads(raw_text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    verdict = obj.get("verdict")
    if verdict not in _LLM_VALID_VERDICTS:
        return None
    return {
        "verdict": verdict,
        "world_label": str(obj.get("world_label") or "").strip(),
        "evidence": str(obj.get("evidence") or "").strip(),
    }


class WorldKeyCancelled(Exception):
    """用户在 LLM 窄确认途中请求取消。由 backfill_worldlines 捕获 → 不落任何写入。"""


def confirm_segments_llm(
    segments: list[dict],
    chapter_summaries: dict[int, str],
    *,
    call_fn: Callable[[str, str], str],
    should_cancel=None,
) -> list[dict]:
    """第二层·LLM 窄确认(§3 第二层)。纯函数,不做 IO——call_fn 由调用方注入。

    输入:
      segments: 结构先验(classify_segments)产出的段列表,每段至少含
        {"world_label", "verdict", "ch_min", "ch_max"}(overcut/sparse_signal 等
        额外键会被忽略,不要求存在)。
      chapter_summaries: {chapter_index: summary_text},通常从 chapter_facts.summary 拼。
      call_fn: (system_prompt, user_prompt) -> raw_text 的可注入闭包。
        约定 raw_text 应是 LLM 原始输出(严格 JSON 字符串);调用方可用
        agents._harness.call_agent_json 包一层只取其 text 返回值来构造。
        call_fn 抛异常或返回非法 JSON → 该边界降级 continuous,不崩、不中断其它边界。

    输出:段列表(与输入等长,顺序一致),每段:
        {"world_label": str | None, "verdict": "continuous"|"time_skip"|"new_world",
         "ch_min": int, "ch_max": int, "overcut": bool, "llm_evidence": str | None}
    合并规则:
      · 第一段(无 prev)无边界可问,原样保留结构先验结果(llm_evidence=None)。
      · LLM 判 new_world **且 evidence 非空** → 采用 LLM 判定,起新 world
        (world_label 取 LLM 给的标签,归一化处理同结构先验层;LLM 未给标签则
         沿用结构先验候选标签,仍为空则退回 "（LLM确认世界）" 占位)。
      · LLM 判 new_world 但 evidence 为空(未举证)→ 降级 continuous,沿用上段 world。
      · LLM 判 time_skip → 不起新 world,沿用上段 world(§3:仅时间跳跃,世界未变)。
      · LLM 判 continuous → 沿用上段 world。
      · call_fn 异常 / 返回非法 JSON / verdict 不在三态内 → 视同该边界降级 continuous
        (不采用结构先验的 new_world 候选——LLM 确认层的语义是"复核",复核失败即保守)。
      · 过切回退(§3 第三层,与结构先验层同一判据):合并后 distinct world 数
        ≥ 段数 × 0.8 → 整书退单世界(全部 world_label=None, verdict="continuous")。
    """
    if not segments:
        return []

    merged: list[dict] = []
    prev_out: dict | None = None
    for idx, seg in enumerate(segments):
        # 取消检查点:每个边界一次 LLM 调用,段多时整轮可达数分钟。抛出去让调用方整体放弃
        # (半程确认的结果没有意义,不落库)。注意必须在下面那个 except Exception 之外,
        # 否则会被 parsed=None 的兜底吞掉。
        if should_cancel is not None:
            try:
                _hit = bool(should_cancel())
            except Exception:
                _hit = False  # 取消信号查询失败不该中断正在跑的任务
            if _hit:
                raise WorldKeyCancelled()
        ch_min, ch_max = seg["ch_min"], seg["ch_max"]
        if idx == 0 or prev_out is None:
            # 首段无上一段可比较,原样沿用结构先验结果(不发起 LLM 调用)。
            out = {
                "world_label": seg.get("world_label"),
                "verdict": seg.get("verdict", "continuous"),
                "ch_min": ch_min,
                "ch_max": ch_max,
                "llm_evidence": None,
            }
            merged.append(out)
            prev_out = out
            continue

        parsed: dict | None = None
        try:
            system_prompt, user_prompt = _build_confirm_prompt(prev_out, seg, chapter_summaries)
            raw_text = call_fn(system_prompt, user_prompt)
            parsed = _parse_llm_verdict(raw_text)
        except Exception:
            parsed = None

        if parsed is None:
            # call_fn 失败或输出非法 → 降级 continuous,沿用上段 world(不崩其它边界)。
            out = {
                "world_label": prev_out["world_label"],
                "verdict": "continuous",
                "ch_min": ch_min,
                "ch_max": ch_max,
                "llm_evidence": None,
            }
        elif parsed["verdict"] == "new_world" and parsed["evidence"]:
            label = _normalize_world_label(parsed["world_label"] or seg.get("world_label") or parsed["evidence"])
            out = {
                "world_label": label or "(LLM确认世界)",
                "verdict": "new_world",
                "ch_min": ch_min,
                "ch_max": ch_max,
                "llm_evidence": parsed["evidence"],
            }
        elif parsed["verdict"] == "new_world":
            # new_world 未举证 → 降级 continuous(§3:no evidence, no promotion)。
            out = {
                "world_label": prev_out["world_label"],
                "verdict": "continuous",
                "ch_min": ch_min,
                "ch_max": ch_max,
                "llm_evidence": None,
            }
        else:
            # continuous / time_skip 都沿用上段 world(结构先验层同一语义)。
            out = {
                "world_label": prev_out["world_label"],
                "verdict": parsed["verdict"],
                "ch_min": ch_min,
                "ch_max": ch_max,
                "llm_evidence": parsed["evidence"] or None,
            }
        merged.append(out)
        prev_out = out

    overcut = _is_overcut(merged)
    if overcut:
        for s in merged:
            s["world_label"] = None
            s["verdict"] = "continuous"
            s["llm_evidence"] = None

    for s in merged:
        s["overcut"] = overcut

    return merged


def _make_llm_call_fn(
    user_id: int | None,
    api_id_override: str | None,
    model_override: str | None,
) -> Callable[[str, str], str] | None:
    """构造 confirm_segments_llm 需要的 call_fn,内部走 agents._harness.call_agent_json。

    模型解析复用 agents.recorder._resolve_recorder_api_and_model(严格 BYOK,
    走 resolve_api_and_model 的 guard_byok_usable 守卫,与史官同一套解析规则)。
    解析失败(无可用 api_id/model)→ 返回 None,调用方跳过 LLM 层(不崩)。
    """
    try:
        from agents._harness import call_agent_json_guarded
        from agents.recorder import _resolve_recorder_api_and_model
    except Exception:
        return None

    try:
        api_id, model = _resolve_recorder_api_and_model(user_id, api_id_override, model_override)
    except Exception:
        return None
    if not api_id or not model:
        return None

    def _call(system_prompt: str, user_prompt: str) -> str:
        # 结构化微任务禁深思(268 实锤族)+空正文护栏
        text, _usage = call_agent_json_guarded(
            api_id=api_id,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            user_id=user_id,
            max_tokens=400,
            timeout_sec=30,
            agent_kind="world_key_confirm",
            no_think=True,
            log_tag="world_key_confirm",
        )
        return text

    return _call


def backfill_worldlines(
    script_id: int,
    *,
    dry_run: bool = True,
    use_llm: bool = False,
    user_id: int | None = None,
    api_id_override: str | None = None,
    model_override: str | None = None,
    should_cancel=None,
) -> dict[str, Any]:
    """回填 chapter_facts.worldline_key/in_world_time + script_timeline_anchors.worldline_key。

    dry_run=True(默认):只读不写,返回 {"segments": [...], "overcut": bool, "would_write": N}。
    dry_run=False:幂等 UPDATE(纯函数式覆盖两列,可重跑),返回同样的统计 + "written": N。

    use_llm=False(默认):只跑第一层结构先验(批次 3a 行为,零变化)。
    use_llm=True:结构先验之后追加第二层 LLM 窄确认(§3 第二层,admin 手动触发/BYOK)——
      模型解析走 agents.recorder._resolve_recorder_api_and_model(api_id_override/
      model_override 可显式指定,否则走用户偏好 + BYOK 守卫);解析不出可用模型时
      静默跳过 LLM 层,退回结构先验结果(不因缺凭证而报错中断整个回填)。

    真实列名(已核对 platform_app/db/init.py):
      chapter_facts(script_id, chapter, title, summary, story_time_label, worldline_key, in_world_time, ...)
      script_chapters(script_id, chapter_index, title, volume_title, ...)
    """
    from platform_app.db import connect

    with connect() as db:
        chapters = _load_chapters(db, script_id)

    segments = classify_segments(chapters)

    if use_llm:
        call_fn = _make_llm_call_fn(user_id, api_id_override, model_override)
        if call_fn is not None:
            chapter_summaries = {int(c["chapter_index"]): c.get("summary") or "" for c in chapters}
            try:
                segments = confirm_segments_llm(segments, chapter_summaries, call_fn=call_fn,
                                                should_cancel=should_cancel)
            except WorldKeyCancelled:
                # 用户取消:半程确认的分段没有意义,整轮放弃、一列都不写。
                return {"segments": [], "overcut": False, "would_write": 0,
                        "written": 0, "cancelled": True}

    overcut = bool(segments and segments[0].get("overcut"))

    chapter_map = _segments_to_chapter_map(segments)
    would_write = len(chapter_map)

    result: dict[str, Any] = {
        "segments": [
            {
                "world_label": s["world_label"],
                "ch_min": s["ch_min"],
                "ch_max": s["ch_max"],
            }
            for s in segments
        ],
        "overcut": overcut,
        "would_write": would_write,
    }

    if dry_run:
        return result

    written = 0
    with connect() as db:
        fact_rows = db.execute(
            "select chapter, story_time_label from chapter_facts where script_id=%s",
            (script_id,),
        ).fetchall()
        for row in fact_rows:
            ch = int(row["chapter"])
            if ch not in chapter_map:
                continue
            world_label = chapter_map[ch]
            in_world_time = clean_in_world_time(row.get("story_time_label") or "")
            db.execute(
                "update chapter_facts set worldline_key=%s, in_world_time=%s "
                "where script_id=%s and chapter=%s",
                (world_label, in_world_time or None, script_id, ch),
            )
            written += 1

        # script_timeline_anchors:按 chapter_min 落在哪个段来标记 worldline_key(幂等覆盖)。
        anchor_rows = db.execute(
            "select id, chapter_min from script_timeline_anchors where script_id=%s",
            (script_id,),
        ).fetchall()
        for row in anchor_rows:
            ch_min = int(row["chapter_min"])
            world_label = chapter_map.get(ch_min)
            db.execute(
                "update script_timeline_anchors set worldline_key=%s where id=%s",
                (world_label, row["id"]),
            )

    result["written"] = written
    return result


def _load_chapters(db, script_id: int) -> list[dict]:
    """读 chapter_facts(title/chapter)+ script_chapters(volume_title),按 chapter_index 合并。

    chapter_facts.title 与 script_chapters.title 理论同源，优先用 script_chapters
    (提取产物层可能滞后于原始章节标题编辑)。summary 取 chapter_facts。
    """
    sc_rows = db.execute(
        "select chapter_index, title, volume_title from script_chapters where script_id=%s",
        (script_id,),
    ).fetchall()
    sc_by_index = {int(r["chapter_index"]): r for r in sc_rows}

    cf_rows = db.execute(
        "select chapter, title, summary from chapter_facts where script_id=%s",
        (script_id,),
    ).fetchall()

    chapters: list[dict] = []
    for r in cf_rows:
        ci = int(r["chapter"])
        sc = sc_by_index.get(ci, {})
        chapters.append({
            "chapter_index": ci,
            "title": (sc.get("title") or r.get("title") or ""),
            "volume_title": (sc.get("volume_title") or ""),
            "summary": r.get("summary") or "",
        })
    return chapters


def _print_dry_run(script_id: int, result: dict[str, Any]) -> None:
    print(f"script_id={script_id} dry_run 分段结果(overcut={result['overcut']}):")
    for seg in result["segments"]:
        label = seg["world_label"] or "(主世界)"
        print(f"  ch{seg['ch_min']}-{seg['ch_max']}: {label}")
    print(f"would_write={result['would_write']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="world_key 结构先验回填(批次 3a)+ 可选 LLM 窄确认(批次 3b 第二层)")
    parser.add_argument("script_id", type=int)
    parser.add_argument("--apply", action="store_true", help="默认 dry-run；传此参数才真正写库")
    parser.add_argument("--llm", action="store_true", help="结构先验后追加 LLM 窄确认(§3 第二层,BYOK,默认不跑)")
    parser.add_argument("--user-id", type=int, default=None, help="--llm 时用于解析模型偏好/BYOK 凭据的 user_id")
    parser.add_argument("--api-id", default=None, help="--llm 时显式指定 api_id(覆盖用户偏好)")
    parser.add_argument("--model", default=None, help="--llm 时显式指定 model(覆盖用户偏好)")
    args = parser.parse_args()

    out = backfill_worldlines(
        args.script_id,
        dry_run=not args.apply,
        use_llm=args.llm,
        user_id=args.user_id,
        api_id_override=args.api_id,
        model_override=args.model,
    )
    if args.apply:
        print(f"script_id={args.script_id} 已写入 written={out.get('written')} overcut={out['overcut']}")
    else:
        _print_dry_run(args.script_id, out)
