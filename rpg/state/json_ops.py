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
            # 解析失败保留原 fence 让玩家看到
            stripped_parts.append(m.group(0))
        last_end = m.end()
    stripped_parts.append(text[last_end:])
    return ops, "".join(stripped_parts)


def strip_json_state_ops(text: str) -> str:
    """Return player-facing narrative text without JSON state-op fences."""
    return _extract_json_state_ops(text or "")[1].strip()
