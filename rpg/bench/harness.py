"""RP harness 基准 — harness 抽象(replay 用)。

harness = 一个"给定真实 case 上下文 → 产出一条 GM 回复"的可调用对象。基准把不同 harness
(当前线上 / 候选模型 / 候选提示词 / 候选管线开关)放进同一 metrics+runner 打分对比 → A/B。

- RecordedHarness:返回存档里已记录的回复(=当前线上 harness 的真实产出,作基线)。
- OpenAICompatHarness:用 case 的前文 + 玩家输入重建 chat 请求,打任意 OpenAI 兼容端点
  (Anthropic/Vertex/DeepSeek/中转站皆可),现生成一条回复。系统提示 + canon 可换 →
  这就是"换模型/换提示词跑同一批真实上下文"的开关。

注:这是【简化 harness】—— 只重建"前文 + canon 摘要 + 玩家输入 + GM 系统提示",不跑完整
管线(curator/rules/recorder)。足够横评模型/提示词;要评完整管线另接。
"""
from __future__ import annotations

import json
import urllib.request

DEFAULT_GM_SYSTEM = (
    "你是一个中文沉浸式角色扮演游戏的 GM(游戏主持)。规则:\n"
    "- 以第二人称「你」称呼玩家,只推进世界与 NPC,绝不替玩家做决定或代写玩家台词。\n"
    "- 保持既定设定与角色性格一致,不要凭空造与设定冲突的人物或事实。\n"
    "- 输出纯叙事正文,不要输出任何 JSON、系统标记、工具调用或元说明。\n"
    "- 篇幅适中(几百字),聚焦本回合的即时反应,有画面感。"
)


def _canon_block(case: dict) -> str:
    aliases = case.get("canon_aliases") or {}
    names = list(aliases.keys())[:24]
    return ("\n\n【本剧本已知角色(保持一致,勿与之冲突)】" + "、".join(names)) if names else ""


class Harness:
    name = "harness"

    def generate(self, case: dict) -> str:
        raise NotImplementedError


class RecordedHarness(Harness):
    """基线:存档里已记录的 GM 回复(当前线上 harness 的真实产出)。"""
    name = "recorded(prod)"

    def generate(self, case: dict) -> str:
        return case.get("gm_response", "")


class OpenAICompatHarness(Harness):
    def __init__(self, name: str, model: str, base_url: str, api_key: str,
                 system_prompt: str = DEFAULT_GM_SYSTEM, canon_in_system: bool = True,
                 max_tokens: int = 900, temperature: float = 0.7, timeout: int = 120):
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.canon_in_system = canon_in_system
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕代理(见 harness_ua_403)

    def _messages(self, case: dict) -> list[dict]:
        sys = self.system_prompt + (_canon_block(case) if self.canon_in_system else "")
        msgs = [{"role": "system", "content": sys}]
        for h in (case.get("prior") or []):
            if h.get("role") in ("user", "assistant") and (h.get("content") or "").strip():
                msgs.append({"role": h["role"], "content": h["content"]})
        msgs.append({"role": "user", "content": (case.get("player_input") or "").strip() or "（继续）"})
        return msgs

    def chat(self, messages: list[dict], max_tokens: int | None = None) -> str:
        """裸 chat 调用(供 RP / 写作 复用)。失败返回 __GEN_ERROR__: 前缀。"""
        body = json.dumps({
            "model": self.model, "messages": messages,
            "max_tokens": max_tokens or self.max_tokens, "temperature": self.temperature,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}", "User-Agent": "Mozilla/5.0"})
        try:
            r = json.load(self._opener.open(req, timeout=self.timeout))
            return (r["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            return f"__GEN_ERROR__: {type(e).__name__}: {e}"

    def generate(self, case: dict) -> str:
        return self.chat(self._messages(case))
