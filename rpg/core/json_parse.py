"""core/json_parse.py — 通用鲁棒 LLM JSON 解析(单一实现,多入口共用)。

LLM 输出的 JSON 常被散文、```json 围栏、前后噪声包裹。本模块提供一个最健壮的
解析器:直接解析 → 剥 ```json 围栏 → 带字符串/转义感知的平衡括号扫描(取**最早**
出现的开括号的平衡块,避免 list 响应里的内层 {} 被先抓)。

本地/自托管弱模型(ollama 上的思考型模型、小参数量模型)还有三种额外挂法,
本模块一并接住(2026-08-24,反馈 #99 实测):
- **推理块**:`<think>…</think>` / `<reasoning>…</reasoning>` 里常自带花括号,
  平衡扫描会先抓到它、解析失败即放弃 → 先确定性剥掉推理块再解析。
  (`no_think` 那套 `thinking.disabled` 是厂商方言,ollama 之类根本不认,不能指望。)
- **截断**:max_tokens 打断在半路,没有收尾括号 → `allow_truncated=True` 时打捞
  已完整的部分(丢掉最后一个残缺字段/元素,补齐闭合括号)。默认关,调用方显式开。
- **尾逗号**:`{"a": 1,}` —— 最后一跳兜底清掉再试。

调用方各自决定失败语义:
- 需要 dict:  parse_llm_json(raw, want=dict) → 拿不到 None
- 需要 list:  parse_llm_json(raw, want=list) → 拿不到 None
- 任意类型:  parse_llm_json(raw)             → 拿不到 None
解析不到统一返回 None;调用方自行 None / [] / raise(见各处 GUARD)。

⚠️ 语义边界:state/json_ops.py 是【不同语义入口】——它从面向玩家的叙事里
**安全剥离 state-ops 块并保留正文 JSON、容忍截断半块**,误并会让玩家看到畸形
ops 或正文被误删。底层平衡括号扫描思路可借鉴,但**入口不合并**。本模块只服务
"整段响应就是一份 JSON(可能裹散文/围栏)"这一通用场景。
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)
_MISS = object()  # 哨兵:区分「解析失败」与「合法解析出 None/null」

# 思考型模型的推理块。闭合的整块剥掉;未闭合的(被 max_tokens 打断在推理里)
# 从开标签起全部丢弃 —— 那之后不会再有正文。
_THINK_TAGS = ("think", "thinking", "reasoning", "reflection", "scratchpad")
_THINK_BLOCK_RE = re.compile(
    r"<(" + "|".join(_THINK_TAGS) + r")\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_OPEN_RE = re.compile(
    r"<(" + "|".join(_THINK_TAGS) + r")\b[^>]*>",
    re.IGNORECASE,
)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _strip_reasoning(raw: str) -> str:
    """剥掉推理块。剥完为空则原样返回(说明整段都是推理,交给后续步骤照常失败)。"""
    if "<" not in raw:
        return raw
    stripped = _THINK_BLOCK_RE.sub("", raw)
    m = _THINK_OPEN_RE.search(stripped)
    if m:  # 未闭合的开标签:其后全是推理
        stripped = stripped[:m.start()]
    stripped = stripped.strip()
    return stripped or raw


def _salvage_truncated(raw: str) -> Any | None:
    """打捞被 max_tokens 打断的 JSON:补齐闭合括号,必要时丢掉末尾残缺的字段/元素。

    只在 allow_truncated=True 时调用。返回 None 表示打捞不出任何完整内容。
    """
    start = min(
        (i for i in (raw.find("{"), raw.find("[")) if i != -1),
        default=-1,
    )
    if start < 0:
        return None
    stack: list[str] = []
    in_str = False
    esc = False
    # 记录每个「安全截断点」:栈深 >0 且不在字符串内的逗号后 / 开括号后
    safe_cuts: list[tuple[int, tuple[str, ...]]] = []
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            safe_cuts.append((i + 1, tuple(stack)))
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
            if not stack:
                return None  # 本就闭合,轮不到打捞
        elif ch == "," :
            safe_cuts.append((i, tuple(stack)))
    if not stack:
        return None
    # 切点优先级:**越浅越好**(顶层元素边界),同深度取最靠后。
    # 深切点会留下半个元素(list 里 {"name": "落霞谷"} 缺 content 这种残件);
    # 浅切点把残缺元素整个丢掉,留下的每一条都是完整的。
    ordered = sorted(safe_cuts, key=lambda x: (len(x[1]), -x[0]))
    for cut, open_stack in ordered:
        candidate = raw[start:cut].rstrip().rstrip(",")
        candidate += "".join(reversed([c for c in open_stack]))
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        # 空壳(所有字段都没抢救出来)不算成功,让调用方走失败路径
        if value in ({}, []):
            return None
        return value
    return None


def _scan_blocks(raw: str) -> list[tuple[int, Any]]:
    """按出现序扫出**互不嵌套**的顶层 JSON 块,返回 [(起点, 解析值)]。

    - 带字符串/转义感知:正文里的 `}` 不会提前闭合。
    - 只取互不嵌套的块:`[{...},{...}]` 只会产出整个 list,内层对象不会被单独取走。
    - 多块并存(弱模型爱先摆一个示例再给答案)由调用方裁定取哪个。
    """
    blocks: list[tuple[int, Any]] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch not in "{[":
            i += 1
            continue
        close_ch = "}" if ch == "{" else "]"
        depth = 0
        in_str = False
        esc = False
        end = -1
        for j in range(i, n):
            c = raw[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end < 0:
            # 这个开括号没闭合(多半被 max_tokens 打断)——它之后的内容都在它内部,
            # 不是并列的答案块,不再往里找。
            break
        try:
            blocks.append((i, json.loads(raw[i:end + 1])))
        except Exception:
            pass
        i = end + 1
    return blocks


def _earliest_opener_unclosed(raw: str) -> bool:
    """最早的那个开括号是否没有配对闭合(= 响应被截断在结构中间)。"""
    blocks = _scan_blocks(raw)
    first_open = min(
        (i for i in (raw.find("{"), raw.find("[")) if i != -1), default=-1,
    )
    if first_open < 0:
        return False
    return not any(start == first_open for start, _ in blocks)


def _pick_block(blocks: list[tuple[int, Any]], want: type | None) -> Any:
    """多块并存时选哪个:**信息量最大的那个,同分取最后一个**。

    模型常先复述一遍 schema 示例(`{"is_character": false}`)再给真答案;
    取最早的块 = 把示例当答案 —— 比解析失败更糟(一个静默的错答案)。
    真答案的字段/元素总比示例多;完全同分时答案在后。
    """
    if not blocks:
        return _MISS
    candidates = [(i, v) for i, v in blocks] if want is None else [
        (i, v) for i, v in blocks if isinstance(v, want)
    ]
    if not candidates:
        return _MISS
    def _size(v: Any) -> int:
        return len(v) if isinstance(v, (dict, list)) else 0
    best_size = max(_size(v) for _, v in candidates)
    return [v for _, v in candidates if _size(v) == best_size][-1]


def parse_llm_json(
    raw: str, *, want: type | None = None, allow_truncated: bool = False,
) -> Any | None:
    """从 LLM 文本里鲁棒解析 JSON。解析不到返回 None。

    步骤:① 直接 json.loads → ② 剥推理块(<think> 等)后重试 → ③ 剥 ```json 围栏
    → ④ 平衡括号扫描(按出现序试所有候选)→ ⑤ 清尾逗号重扫
    → ⑥ allow_truncated 时打捞被 max_tokens 打断的半份 JSON。

    want=dict / want=list 时做类型过滤:解析出的顶层值类型不符也返回 None。
    want=None 时不限类型。

    allow_truncated=True:截断响应按「已完整的部分」打捞(丢掉末尾残缺字段 + 补闭合)。
    默认 False —— 半份数据对某些调用方比没数据更糟,要打捞的自己开。
    """
    if not raw:
        return None
    raw = raw.strip()

    result: Any = _MISS

    # 1. 直接解析
    try:
        result = json.loads(raw)
    except Exception:
        result = _MISS

    # 2. 剥推理块后重试(思考型模型:<think>…</think> 里自带花括号,
    #    不先剥掉,后面的扫描会被推理块里的假括号带偏)
    if result is _MISS:
        stripped_raw = _strip_reasoning(raw)
        if stripped_raw != raw:
            raw = stripped_raw
            try:
                result = json.loads(raw)
            except Exception:
                result = _MISS

    # 3. 剥 ```json 围栏
    if result is _MISS:
        m = _FENCE_RE.search(raw)
        if m:
            try:
                result = json.loads(m.group(1).strip())
            except Exception:
                result = _MISS

    # 4. 最外层那个括号就没闭合(被 max_tokens 打断)→ 先打捞整体,
    #    否则会退而取到它内部的某个完整子块(截断的 list 返回半个 dict,
    #    调用方按 list 迭代就是垃圾)。
    if result is _MISS and allow_truncated and _earliest_opener_unclosed(raw):
        salvaged = _salvage_truncated(raw)
        if salvaged is not None:
            result = salvaged

    # 5. 互不嵌套的顶层块扫描(多块取最后一个 —— 示例在前、答案在后)
    if result is _MISS:
        picked = _pick_block(_scan_blocks(raw), want)
        if picked is not _MISS:
            result = picked

    # 6. 清尾逗号再来一次(`{"a": 1,}` —— 小模型高频)
    if result is _MISS:
        decommaed = _TRAILING_COMMA_RE.sub(r"\1", raw)
        if decommaed != raw:
            try:
                result = json.loads(decommaed)
            except Exception:
                picked = _pick_block(_scan_blocks(decommaed), want)
                if picked is not _MISS:
                    result = picked

    # 7. 兜底打捞(截断发生在更里层的情形)
    if result is _MISS and allow_truncated:
        salvaged = _salvage_truncated(raw)
        if salvaged is not None:
            result = salvaged

    if result is _MISS:
        return None

    # 类型过滤
    if want is not None and not isinstance(result, want):
        return None
    return result
