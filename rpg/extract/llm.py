"""extract/llm.py — 提取用 LLM 客户端(便宜模型 + 鲁棒 JSON 解析)。

复用 GameMaster 的 backend(call_structured)。**只用便宜模型**(gemini-3.5-flash / claude-haiku-4-5),
逐章提取走 flash;Pass0/2 少量精判可临时升 haiku。绝不全程 frontier(成本铁律)。
"""
from __future__ import annotations

import json
import re
from typing import Any

# 默认便宜模型(成本铁律)
CHEAP_VERTEX = ("gemini-3.5-flash", "vertex_ai")
CHEAP_ANTHROPIC = ("claude-haiku-4-5", "anthropic")


class ExtractLLM:
    """薄封装:一次性 system+user → JSON。"""

    def __init__(self, model: str = CHEAP_VERTEX[0], api_id: str = CHEAP_VERTEX[1],
                 user_id: int | None = None):
        from agents.gm.master import GameMaster
        self._gm = GameMaster(model=model, api_id=api_id, user_id=user_id)
        self._backend = self._gm._backend
        self.model = model
        self.api_id = api_id

    def complete_text(self, system: str, user: str, max_tokens: int = 2000) -> str:
        return self._backend.call_structured(system, [{"role": "user", "content": user}], max_tokens)

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> Any:
        """返回解析后的 JSON(dict/list)。解析失败抛 ValueError(调用方决定重试)。"""
        raw = self.complete_text(system, user, max_tokens)
        return parse_json(raw)


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def parse_json(raw: str) -> Any:
    """鲁棒 JSON 解析:剥 ```json 围栏 / 取首个 {..} 或 [..] / 容忍前后散文。"""
    if not raw:
        raise ValueError("空响应")
    raw = raw.strip()
    # 1. 直接解析
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 2. 剥围栏
    m = _FENCE_RE.search(raw)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    # 3. 截取首个平衡的 {..} 或 [..]
    #    取**最早出现**的开括号(否则 list 响应里的内层 {} 会被先抓)
    candidates = [(raw.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if raw.find(o) != -1]
    candidates.sort()
    for start, open_ch, close_ch in candidates:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(raw)):
            c = raw[i]
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
                elif c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw[start:i + 1])
                        except Exception:
                            break
    raise ValueError(f"无法从响应解析 JSON: {raw[:200]!r}")
