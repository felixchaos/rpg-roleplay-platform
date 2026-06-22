"""state/json_ops.py — JSON state ops 提取 (_extract_json_state_ops, strip_json_state_ops)"""
from __future__ import annotations

import json
import re

_JSON_STATE_OPS_RE = re.compile(
    r"```(?:json|state-ops|state)?\s*\n?\s*"
    r"(\{[\s\S]*?\}|\[[\s\S]*?\])"
    r"\s*\n?```",
    re.MULTILINE,
)


def _extract_json_state_ops(text: str) -> tuple[list[dict], str]:
    """task 55：从 GM 输出里剥离 ```json {...}``` 状态操作块，返回 (ops_list, stripped_text)。

    现代 LLM (Claude 3.5+ / GPT-4o / Gemini 2.0+) 对 JSON 比对自定义中文模板
    熟悉得多，错误率低 1-2 个数量级。GM 可选地输出：

        ```json
        [
          {"op": "set", "path": "player.current_location", "value": "北港"},
          {"op": "append", "path": "memory.resources", "value": "怀表"},
          {"op": "question", "question": "去哪", "options": ["东", "西"]}
        ]
        ```

    单个对象（不在数组里）也接受。stripped_text 是剥离 JSON 块后的剩余正文，
    供 【】 协议继续抽。两种协议共存，模型自选熟悉的。
    """
    if not text or "```" not in text:
        return [], text or ""
    ops: list[dict] = []
    stripped_parts: list[str] = []
    last_end = 0
    for m in _JSON_STATE_OPS_RE.finditer(text):
        # 把上一个匹配尾到本次开始之间的文本保留
        stripped_parts.append(text[last_end:m.start()])
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, dict):
                # 启发：必须看着像 state op（含 op 或 path）才接受
                if "op" in parsed or "path" in parsed or "question" in parsed:
                    ops.append(parsed)
                else:
                    # 不是 state op JSON，保留原文（可能是其它结构化数据）
                    stripped_parts.append(m.group(0))
            elif isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and ("op" in item or "path" in item or "question" in item):
                        ops.append(item)
        except Exception:
            # 解析失败:若围栏内容明显是 ops(含 op/path/question 标记),仍从可见文本
            # 剥离 —— 玩家不该看到畸形的 ops JSON(GM 流式产出有时会残缺,如 `[,,`)。
            # ops 的应用由更宽容的 extractor 兜底,state 不受影响。
            inner = m.group(1)
            if not ('"op"' in inner or '"path"' in inner or '"question"' in inner):
                # 不像 ops 的其它结构化数据 → 保留原文
                stripped_parts.append(m.group(0))
        last_end = m.end()
    stripped_parts.append(text[last_end:])
    return ops, "".join(stripped_parts)


def _looks_like_ops_json(s: str) -> bool:
    """候选字符串是否解析为明确的 state-ops JSON(dict 含 op/path/question,
    或 list 全是 dict 且至少一个是 op)。用于保守剥离裸 ops。"""
    try:
        parsed = json.loads(s)
    except Exception:
        return False

    def _is_op(d: object) -> bool:
        return isinstance(d, dict) and ("op" in d or "path" in d or "question" in d)

    if _is_op(parsed):
        return True
    if (
        isinstance(parsed, list)
        and parsed
        and all(isinstance(x, dict) for x in parsed)
        and any(_is_op(x) for x in parsed)
    ):
        return True
    return False


def _strip_bare_json_ops(text: str) -> str:
    """剥离未加 ``` 围栏的裸 JSON ops 块。

    GM(尤其 Sonnet/Opus)偶尔不加围栏直接把 `[{"op":...}, ...]` 拼在正文里,
    `_extract_json_state_ops` 只认围栏 → 这些裸 ops 会漏进玩家可见文本并被持久化。
    这里用括号配对找出每个候选 JSON 块,仅当能解析且内容明确是 ops 时才剥离,
    避免误删正文里的合法 JSON / 代码示例。
    """
    # 快速预判:没有 ops 特征子串就直接返回,避免对正常正文做 O(n) 扫描。
    if not text or ('"op"' not in text and '"path"' not in text and '"question"' not in text):
        return text or ""
    result: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "[{":
            depth = 0
            in_str = False
            esc = False
            end = -1
            j = i
            while j < n:
                c = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                else:
                    if c == '"':
                        in_str = True
                    elif c in "[{":
                        depth += 1
                    elif c in "]}":
                        depth -= 1
                        if depth == 0:
                            end = j
                            break
                j += 1
            if end != -1 and _looks_like_ops_json(text[i : end + 1]):
                # 剥离该块,并吞掉紧邻的前导空白/换行,避免留下空行
                while result and result[-1] in " \t\n\r":
                    result.pop()
                i = end + 1
                continue
        result.append(ch)
        i += 1
    return "".join(result)


def _strip_trailing_unclosed_ops(text: str) -> str:
    """兜底:剥离截断的未闭合 ops 块。

    GM 响应被切断时(停止/报错/超 token),可能留下半个 ops:
    `...正文。\n\n```json\n[,,\n  {"op": "append", ...`(围栏没闭合 / JSON 残缺)。
    前面的围栏/裸 stripper 都要求结构完整,拦不住。这里:若文本里仍残留 ops 标记
    (`"op":` / `"path":`),从该标记回溯到最近的块起点(``` 或 [ 或 {)截断到末尾。
    保守:找不到合理块起点就不动,避免误删正文。
    """
    if not text or ('"op"' not in text and '"path"' not in text):
        return text or ""
    m = re.search(r'"(?:op|path|question)"\s*:', text)
    if not m:
        return text
    head = text[: m.start()]
    cut = max(head.rfind("```"), head.rfind("["), head.rfind("{"))
    if cut == -1:
        return text  # 没有块起点 → 可能是正文里恰好出现 "op":,保守不动
    return text[:cut].rstrip()


def strip_json_state_ops(text: str) -> str:
    """Return player-facing narrative text without JSON state-op fences.

    三层剥离(玩家永远不该看到 ops JSON,无论合法/畸形/截断):
      1. 围栏内 ops(```json [...] ```),含畸形围栏
      2. 裸 ops(未加围栏的 [{"op":...}])
      3. 截断的未闭合 ops(GM 响应被切断留下的半个块)
    ops 的"应用"由更宽容的 extractor 兜底,与可见文本剥离解耦。
    """
    fenced_stripped = _extract_json_state_ops(text or "")[1]
    bare_stripped = _strip_bare_json_ops(fenced_stripped)
    final = _strip_trailing_unclosed_ops(bare_stripped)
    return final.strip()


# GM 在 native tool_use 前常顺嘴泄漏一句英文"工具预告"元叙述,混进玩家正文,例如:
#   "The scene has progressed naturally. Let me mark the anchors that have been
#    satisfied through our gameplay."
# 这是给模型自己看的、不该出现在叙事里的自言自语。确定性剥离(不靠提示词约束 GM)。
_META_PREAMBLE_RE = re.compile(
    r"\b("
    r"let me (now )?(mark|record|update|note|save|log|set|track|reflect|call|advance)"
    r"|let'?s (now )?(mark|record|update|note|save|log|set|track|reflect)"
    r"|i(?:'ll| will| should|'m going to| am going to) (now )?(mark|record|update|note|save|log|set|track|reflect|call)"
    r"|now,?\s+(let me|i'?ll|i will|i should)\b"
    r"|the scene has (progressed|advanced)"
    r"|let me update the (state|anchors?|memor(?:y|ies)|world)"
    r"|i'?ve (marked|recorded|updated|noted|logged)"
    r")\b",
    re.IGNORECASE,
)
_QUOTE_CHARS = "「」“”‘’\"『』"


def strip_meta_tool_preamble(text: str) -> str:
    """剥离正文尾部泄漏的英文"工具预告"元叙述。

    只剥满足全部条件的尾段:① 处于正文末尾 ② 以英文(ASCII)为主、不在引号内
    (绝不动角色英文台词)③ 命中工具预告短语。中文叙事 + 引号内台词不受影响。
    紧贴中文句末(中文。English meta…)或独立成行两种形态都能处理。
    """
    if not text:
        return text
    result = text.rstrip()
    for _ in range(6):  # 可能连续泄漏多句,逐段剥
        s = result.rstrip()
        # 候选尾段:从换行 / 中文句末标点 / 串首 之后,到结尾的一段英文(无中文句末标点、无换行)
        m = re.search(r"(?:[\n。！？…」』”]|^)([ \t]*[A-Za-z][^\n。！？]*?)[ \t]*$", s)
        if not m:
            break
        seg = m.group(1).strip()
        if not seg or any(q in seg for q in _QUOTE_CHARS):
            break
        if not _META_PREAMBLE_RE.search(seg):
            break
        result = s[: m.start(1)].rstrip()
    return result if result.strip() else text.rstrip()


# === 检索/世界线脚手架 header 关键字 ===
# 这些 `=== … ===` 段落是后端注入给模型的【隐形上下文】(retrieval.py / 阶段摘要 / 世界线收束
# 等),绝不该出现在玩家可见正文里。弱模型(如 deepseek-v4-flash)有时把整段提示词上下文 + 自己
# 的内部推理直接吐进正文(线上反馈 #77:正文里出现「=== 时间线检索锚点 ===…待确认跳跃…」)。
# 这里做【确定性兜底】:命中这些 header 的整块剥掉,不依赖模型听"只输出正文"的提示词。
_LEAKED_SCAFFOLD_KEYS: tuple[str, ...] = (
    "时间线检索锚点", "存档独立时间线", "作者文风样本", "ChapterFact时间线",
    "世界线收束", "世界设定", "剧本时间线锚点", "剧本章节事实", "剧本阶段摘要",
    "当前剧情阶段", "最近剧情摘要", "相关原文片段", "相关角色", "组织/势力/地点",
    "跳跃进度说明", "锚点章节原文",
)
_SCAFFOLD_HEADER_RE = re.compile(r"^\s*=+\s*(.+?)\s*=+\s*$")
# 模型"内部推理→开始正文"的转场标记(中文 thinking 泄漏的收尾句)。
_OUTPUT_TRANSITION_RE = re.compile(
    r"^.{0,500}?(?:好[，,]?\s*)?(?:现在\s*)?开始(?:输出|正文|写正文)(?:正文)?\s*[。.!！]?\s*\n+",
    re.S,
)


def _is_leaked_scaffold_header(stripped_line: str) -> bool:
    m = _SCAFFOLD_HEADER_RE.match(stripped_line)
    if not m:
        return False
    inner = m.group(1)
    return any(k in inner for k in _LEAKED_SCAFFOLD_KEYS)


def strip_leaked_scaffold(text: str) -> str:
    """确定性剥离泄漏进正文的【检索/世界线脚手架块】+ 收尾的"开始输出"推理前言。

    只命中后端自己注入的固定 `=== … ===` header(见 _LEAKED_SCAFFOLD_KEYS),正常叙事
    永不会逐字产出这些 header,因此零误伤。整块剥除(header 到下一个空行 / 下一个脚手架
    header / 文末)。仅当确实检出脚手架泄漏时,才顺带剥一次开头的推理转场前言。

    若剥完为空(整轮纯泄漏,极端模型崩溃)则返回原文,避免把本回合 assistant 消息清空。
    """
    if not text or "===" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i, n, removed = 0, len(lines), False
    while i < n:
        if _is_leaked_scaffold_header(lines[i].strip()):
            removed = True
            i += 1
            while i < n:
                nxt = lines[i].strip()
                if nxt == "":
                    i += 1
                    break
                if _is_leaked_scaffold_header(nxt):
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if not removed:
        return text
    result = "\n".join(out).strip()
    # 检出脚手架泄漏后,正文若仍以"…开始输出。"推理前言开头(后面接真正文)则剥掉前言。
    m = _OUTPUT_TRANSITION_RE.match(result)
    if m and result[m.end():].strip():
        result = result[m.end():].strip()
    return result if result.strip() else text
