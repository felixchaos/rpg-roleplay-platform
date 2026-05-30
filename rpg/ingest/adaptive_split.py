"""ingest/adaptive_split.py — 规则融合自适应切分 (Phase A.0 §3)。

零书本调参。流程:
  build_candidate_rules(text) → 预设规则 + 自派生规则
  对每条规则 split → structural_score(5 维) → 取最优 + 次优
  fuse(best, runnerup): 主切 + 离群超长块递归次优找漏切 + 编号对账
设计:docs/design/A0_ingestion.md §3。**确定性,无 LLM。**

章节 dict 形状对齐 chapter_splitter:{title, content, chapter_number, volume_title?}。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from re import Pattern

NUMBER_TOKEN = r"零一二三四五六七八九十百千万〇两\d０-９"
# 序号抽取:阿拉伯数字 与 中文数字 互斥匹配,避免 "925一大波" 被解析成 921933 等混合数值
_NUM_RUN = re.compile(r"[\d０-９]+|[零一二三四五六七八九十百千万〇两]+")

# ─── 评分权重 (针对"切分质量"普遍属性,非针对某本书) ─────────────────────────
SPLIT_SCORE_WEIGHTS = {
    "seq_continuity": 0.35,
    "size_uniformity": 0.20,
    "count_sanity": 0.15,
    "marker_consistency": 0.15,
    "coverage": 0.15,
}

# ─── 预设候选规则 (self-contained,避免与 chapter_splitter 形成 import 环) ──────
# 每条 = 逐行 heading 判定正则 (anchored ^...);删去任何书本特化。
_PRESET_RULES: list[tuple[str, str]] = [
    ("chapter_cn", rf"^.{{0,30}}(?:第[{NUMBER_TOKEN}]+[章节集回]|[序楔]章|楔子|引[子言]|前言|番外).*$"),
    ("corpus", rf"^.{{0,40}}(?:第[{NUMBER_TOKEN}]+[章节集回卷]|第[{NUMBER_TOKEN}]+部|[序楔终]章|楔子|引[子言]|前言|正文|番外|外传|大结局).*$"),
    ("chapter_en", r"^(?:chapter|chap\.|part)\s+[0-9０-９ivxlcdm]+.*$"),
    ("number_dot", r"^[0-9０-９]+[.、]\s*.*$"),
    ("paren_num", rf"^.{{0,10}}[（(]\s*[{NUMBER_TOKEN}]+\s*[)）].*$"),
    ("hua", rf"^.{{0,8}}(?:【\s*第?[{NUMBER_TOKEN}]+话\s*】|第[{NUMBER_TOKEN}]+话).*$"),
    ("juan_zhang", rf"^.{{0,20}}第[{NUMBER_TOKEN}]+卷.*第[{NUMBER_TOKEN}]+[章节].*$"),
    # 水平分隔符:命中整行 ---/===/***/___/──/━━ 等(4+).split_by_heading_regex 会把分隔符
    # 之后的第一行非空行提为标题。常见于编辑器导出/某些网文体例(如「我的二战不可能这么萌」)。
    ("hr_divider", r"^[-=*_─━─]{4,}\s*$"),
]
_DIVIDER_ONLY = re.compile(r"^[-=*_─━─]{4,}\s*$")

_CN_DIGIT = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_FULLWIDTH = {ord("０") + i: ord("0") + i for i in range(10)}


@dataclass
class Rule:
    id: str
    kind: str  # 'preset' | 'derived'
    regex: Pattern[str]


@dataclass
class Candidate:
    rule: Rule
    chapters: list[dict]
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)


# ─── 数字解析 ────────────────────────────────────────────────────────────────
def _cn_to_int(s: str) -> int | None:
    """解析章节序号 (阿拉伯/全角/中文,支持到万)。失败返 None。"""
    s = s.strip().translate(_FULLWIDTH)
    if not s:
        return None
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    # 中文数字
    total = 0
    section = 0
    current = 0
    for ch in s:
        if ch in _CN_DIGIT:
            current = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if unit == 10000:
                section = (section + current) * unit
                total += section
                section = 0
            else:
                if current == 0:
                    current = 1
                section += current * unit
            current = 0
        else:
            return None
    return total + section + current


def extract_seq(title: str) -> int | None:
    """从标题抽取首个序号 (第X / X. / (X) / 【第X话】 等)。"""
    if not title:
        return None
    m = _NUM_RUN.search(title)
    if not m:
        return None
    return _cn_to_int(m.group(0))


# ─── 候选规则生成 ────────────────────────────────────────────────────────────
def _derive_rule(lines: list[str]) -> Rule | None:
    """自派生规则:扫全文找最频繁+最一致的疑似标记行骨架,反编译成正则。

    抓预设没覆盖的本书约定 (如 001. / 【第X话】 等已在预设,但作者自创格式也能抓)。
    """
    # 候选 heading 行:短 + 含序号 + 行首附近
    lead = re.compile(rf"^(\s*.{{0,12}}?)([{NUMBER_TOKEN}]+)(.{{0,4}}?)(?:\s|$)")
    skel_count: dict[tuple[str, str], int] = {}
    skel_seqs: dict[tuple[str, str], list[int]] = {}
    for line in lines:
        s = line.strip()
        if not s or len(s) > 40:
            continue
        m = lead.match(s)
        if not m:
            continue
        prefix, num, suffix = m.group(1).strip(), m.group(2), m.group(3).strip()
        # 骨架忽略自由标题,只看 前缀+紧邻单位
        skel = (prefix, suffix)
        skel_count[skel] = skel_count.get(skel, 0) + 1
        seq = _cn_to_int(num)
        if seq is not None:
            skel_seqs.setdefault(skel, []).append(seq)

    if not skel_count:
        return None
    # 选 频次 + 序号连续性 综合最高的骨架
    def _skel_quality(skel: tuple[str, str]) -> float:
        cnt = skel_count[skel]
        seqs = sorted(skel_seqs.get(skel, []))
        cont = 0.0
        if len(seqs) >= 2:
            span = seqs[-1] - seqs[0] + 1
            cont = len(set(seqs)) / span if span > 0 else 0.0
        return cnt * (0.4 + 0.6 * cont)

    best_skel = max(skel_count, key=_skel_quality)
    if skel_count[best_skel] < 3:
        return None
    prefix, suffix = best_skel
    pat = r"^\s*" + re.escape(prefix) + rf"[{NUMBER_TOKEN}]+"
    if suffix:
        pat += re.escape(suffix)
    pat += r".*$"
    try:
        return Rule(id="derived", kind="derived", regex=re.compile(pat))
    except re.error:
        return None


def build_candidate_rules(text: str) -> list[Rule]:
    rules: list[Rule] = [
        Rule(id=rid, kind="preset", regex=re.compile(pat)) for rid, pat in _PRESET_RULES
    ]
    derived = _derive_rule(text.split("\n"))
    if derived is not None:
        rules.append(derived)
    return rules


# ─── 按 heading 正则逐行切 ────────────────────────────────────────────────────
def split_by_heading_regex(text: str, regex: Pattern[str]) -> list[dict]:
    """逐行:命中 regex 的行 = 新章 heading。返回 chapter dict 列表。"""
    lines = text.split("\n")
    heading_idx = [i for i, ln in enumerate(lines) if ln.strip() and regex.match(ln.strip())]
    if not heading_idx:
        return []
    chapters: list[dict] = []
    # 首个 heading 前的正文 → 前言
    if heading_idx[0] > 0:
        preface = "\n".join(lines[: heading_idx[0]]).strip()
        if len(preface) >= 200:
            chapters.append({"title": "前言", "content": preface, "chapter_number": 1})
    for i, start in enumerate(heading_idx):
        end = heading_idx[i + 1] if i + 1 < len(heading_idx) else len(lines)
        raw_title = lines[start].strip()
        body_start = start + 1
        if _DIVIDER_ONLY.match(raw_title):
            # 分隔符行本身不是真标题:跳过随后的空行,把第一行非空内容提为标题
            while body_start < end and not lines[body_start].strip():
                body_start += 1
            if body_start < end:
                title = lines[body_start].strip()[:200]
                body_start += 1
            else:
                title = ""
        else:
            title = raw_title[:200]
        body = "\n".join(lines[body_start:end]).strip()
        chapters.append({"title": title, "content": body, "chapter_number": len(chapters) + 1})
    return [c for c in chapters if c["content"] or c["title"]]


# ─── 结构性评分 (5 维) ───────────────────────────────────────────────────────
def structural_score(chapters: list[dict], text: str) -> tuple[float, dict]:
    if not chapters:
        return 0.0, {k: 0.0 for k in SPLIT_SCORE_WEIGHTS}
    sizes = [len(c.get("content") or "") for c in chapters]
    total_chars = max(1, len(text))
    n = len(chapters)

    # 1. 序号连续性 (最强):有序号章里,连续无跳号的占比
    seqs_raw = [s for s in (extract_seq(c.get("title") or "") for c in chapters) if s is not None]
    # 剔除离群序号:网文标题常含日期/比分等数字(如"08932年第一场雪"=893+"2年..."),
    # 用 p99 + 安全 buffer 作为上限,防止 span 被极少数离群标题污染。
    if len(seqs_raw) >= 20:
        ss_tmp = sorted(seqs_raw)
        p99 = ss_tmp[int(len(ss_tmp) * 0.99)]
        cap = max(p99 * 2, n * 2)  # p99 双倍 (容跳号/分卷连号),至少 2n
        seqs = [s for s in seqs_raw if s <= cap]
    else:
        seqs = seqs_raw
    if len(seqs) < 2:
        seq_continuity = 0.3  # 无编号信号 → 中低,不是 0 (可能是合法无编号体例)
    else:
        ss = sorted(seqs)
        span = ss[-1] - ss[0] + 1
        uniq = len(set(ss))
        coverage_ratio = uniq / span if span > 0 else 0.0  # 跳号 → < 1
        have_ratio = len(seqs) / n  # 有编号的章占比
        seq_continuity = coverage_ratio * (0.5 + 0.5 * have_ratio)

    # 2. 长度均匀度:1 - clip(cv)
    mean_sz = sum(sizes) / n
    if mean_sz <= 0:
        size_uniformity = 0.0
    else:
        var = sum((x - mean_sz) ** 2 for x in sizes) / n
        cv = (var ** 0.5) / mean_sz
        size_uniformity = max(0.0, 1.0 - min(cv, 1.0))

    # 3. 数量合理性:网文每章 1.5k-8k 字
    lo, hi = total_chars / 8000, total_chars / 1500
    if lo <= n <= hi:
        count_sanity = 1.0
    elif n < lo:
        count_sanity = max(0.0, n / lo) if lo > 0 else 0.0
    else:
        count_sanity = max(0.0, hi / n) if n > 0 else 0.0

    # 4. marker 一致性:命中同一骨架的标题占比
    skels: dict[tuple, int] = {}
    for c in chapters:
        t = (c.get("title") or "").strip()
        m = re.match(rf"^(\s*.{{0,12}}?)([{NUMBER_TOKEN}]+)(.{{0,4}}?)(?:\s|$)", t)
        key = (m.group(1).strip(), m.group(3).strip()) if m else ("__none__", "")
        skels[key] = skels.get(key, 0) + 1
    marker_consistency = max(skels.values()) / n if skels else 0.0

    # 5. 覆盖率:被分进章的正文字符 / 总字符
    assigned = sum(sizes) + sum(len(c.get("title") or "") for c in chapters)
    coverage = min(1.0, assigned / total_chars)

    breakdown = {
        "seq_continuity": round(seq_continuity, 4),
        "size_uniformity": round(size_uniformity, 4),
        "count_sanity": round(count_sanity, 4),
        "marker_consistency": round(marker_consistency, 4),
        "coverage": round(coverage, 4),
    }
    score = sum(SPLIT_SCORE_WEIGHTS[k] * breakdown[k] for k in SPLIT_SCORE_WEIGHTS)
    return round(score, 4), breakdown


# ─── 规则融合 ────────────────────────────────────────────────────────────────
# 一个正常网文章节顶天 ~8k 字。超过此且远高于均值,才疑似"多章粘连"漏切。
# 防止近似均匀的章长里,某章略大就被误判离群(那样会把正文行误拆)。
_MIN_OUTLIER_CHARS = 12000
_HARD_OUTLIER_CHARS = 50000  # 绝对超长 (对齐 chapter_splitter._post_process)


def fuse(best: list[dict], text: str) -> tuple[list[dict], list[dict]]:
    """主切 + 离群超长块递归找漏切子章 + 编号对账。

    返回 (fused_chapters, gaps)。gaps = [{after_chapter, expected_index, recovered}]。
    离群判定:HARD 绝对超长,或(统计离群 mean+2σ 且 远大于 median 且 超 _MIN_OUTLIER_CHARS)。
    递归子切只在子切分**结构性评分够好**时才采纳,否则保留原块(不乱拆正文)。
    """
    if not best:
        return [], []
    sizes = [len(c.get("content") or "") for c in best]
    n = len(sizes)
    mean_sz = sum(sizes) / n
    median = sorted(sizes)[n // 2]
    std = (sum((x - mean_sz) ** 2 for x in sizes) / n) ** 0.5
    stat_thresh = mean_sz + 2 * std

    fused: list[dict] = []
    for ch in best:
        body = ch.get("content") or ""
        L = len(body)
        is_outlier = L > _HARD_OUTLIER_CHARS or (
            L > stat_thresh and L > max(2 * median, _MIN_OUTLIER_CHARS)
        )
        sub: list[dict] = []
        if is_outlier:
            block_text = (ch.get("title", "") + "\n" + body) if ch.get("title") else body
            sub = _recover_subchapters(block_text)
        if len(sub) > 1:
            for s in sub:
                s["volume_title"] = ch.get("volume_title", "")
                fused.append(s)
        else:
            fused.append(ch)

    # renumber
    for i, c in enumerate(fused):
        c["chapter_number"] = i + 1

    # 编号对账:检测序号跳号
    gaps: list[dict] = []
    prev = None
    for c in fused:
        seq = extract_seq(c.get("title") or "")
        if seq is None:
            continue
        if prev is not None and seq > prev + 1:
            for missing in range(prev + 1, seq):
                gaps.append({"after_chapter": prev, "expected_index": missing, "recovered": False})
        if prev is None or seq > prev:
            prev = seq
    return fused, gaps


def _recover_subchapters(block_text: str) -> list[dict]:
    """对单个离群超长块,用候选规则再切找漏切子章。

    **按结构性评分选优,非按子章数**(按数量会让贪婪规则乱拆正文);
    丢弃空内容碎片;只在最优子切分质量够好(score >= 0.5)时采纳,否则返 [] 保留原块。
    """
    best_sub: list[dict] = []
    best_score = 0.0
    for rule in build_candidate_rules(block_text):
        sub = [c for c in split_by_heading_regex(block_text, rule.regex) if (c.get("content") or "").strip()]
        if len(sub) <= 1:
            continue
        score, _ = structural_score(sub, block_text)
        if score > best_score:
            best_score, best_sub = score, sub
    return best_sub if best_score >= 0.5 else []


# ─── 主入口 ──────────────────────────────────────────────────────────────────
def adaptive_split(text: str) -> tuple[list[dict], dict]:
    """规则融合自适应切分。返回 (chapters, report_fragment)。

    report_fragment = {rule_chosen, rule_runnerup, score_breakdown, gaps}。
    """
    if not text or not text.strip():
        return [], {"rule_chosen": None, "rule_runnerup": None, "score_breakdown": {}, "gaps": []}

    candidates: list[Candidate] = []
    for rule in build_candidate_rules(text):
        chapters = split_by_heading_regex(text, rule.regex)
        if not chapters:
            continue
        score, breakdown = structural_score(chapters, text)
        candidates.append(Candidate(rule=rule, chapters=chapters, score=score, breakdown=breakdown))

    if not candidates:
        return [], {"rule_chosen": None, "rule_runnerup": None, "score_breakdown": {}, "gaps": []}

    candidates.sort(key=lambda c: c.score, reverse=True)
    best = candidates[0]
    runnerup = candidates[1] if len(candidates) > 1 else None

    fused, gaps = fuse(best.chapters, text)
    report = {
        "rule_chosen": {"id": best.rule.id, "kind": best.rule.kind, "score": best.score},
        "rule_runnerup": (
            {"id": runnerup.rule.id, "kind": runnerup.rule.kind, "score": runnerup.score}
            if runnerup else None
        ),
        "score_breakdown": best.breakdown,
        "gaps": gaps,
    }
    return fused, report
