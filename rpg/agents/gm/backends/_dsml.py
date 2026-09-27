"""DeepSeek 原生工具调用标记(DSML)的兜底解析。

DeepSeek V3.2 起,模型输出里的工具调用长这样:

    <｜DSML｜function_calls>
    <｜DSML｜invoke name="get_weather">
    <｜DSML｜parameter name="location" string="true">杭州</｜DSML｜parameter>
    </｜DSML｜invoke>
    </｜DSML｜function_calls>

官方 API 在服务端把它解析成 tool_calls。中转站 / 自部署推理框架没接这个解析器,或者模型在
text-marker 降级路径里没被告知我们的 <<TOOL_CALL>> 格式时,它就原样落进 content —— 我们的
解析器只认 <<TOOL_CALL>>,于是整段标记漏给用户(反馈 #106:助手侧栏里出现
`<｜｜DSML｜｜ invoke name="dispatcher/list_worldbook_entries">`;生产 message 2296 同族)。

实际漏出来的形态会走样:竖线成对(｜｜)、半角 |、标签名前多空格、丢了 function_ 前缀。
所以这里按结构认(`<` + 竖线 + DSML + 竖线 + 标签名),不按字面。

两个入口:
  · DsmlStreamFilter —— 流式过滤:DSML 块不外发,块内的 invoke 收进 .calls;
  · resolve_tool_ref —— 把模型写的工具名(常见 `dispatcher/xxx`)还原成 (server_id, tool)。
"""
from __future__ import annotations

import json
import re
from typing import Any

_BAR = r"[｜|]+"
_TAG_HEAD = rf"<\s*{_BAR}\s*DSML\s*{_BAR}\s*"
_TAG_TAIL = rf"<\s*/\s*{_BAR}\s*DSML\s*{_BAR}\s*"

# 任意一个 DSML 开标签 = 块开始(模型偶尔省掉最外层 function_calls,直接从 invoke 起)。
_BLOCK_START = re.compile(_TAG_HEAD + r"(?:function_)?(?:calls|invoke)\b", re.I)
# 最外层收尾;走样形态里 function_ 会丢,只剩 calls。
_BLOCK_END = re.compile(_TAG_TAIL + r"(?:function_)?calls\s*>", re.I)
_INVOKE = re.compile(
    _TAG_HEAD + r"invoke\s+name\s*=\s*\"([^\"]+)\"[^>]*>(.*?)" + _TAG_TAIL + r"invoke\s*>",
    re.I | re.S,
)
_INVOKE_OPEN = re.compile(_TAG_HEAD + r"invoke\b", re.I)
_PARAM = re.compile(
    _TAG_HEAD + r"parameter\s+name\s*=\s*\"([^\"]+)\"([^>]*)>(.*?)" + _TAG_TAIL + r"parameter\s*>",
    re.I | re.S,
)
# DSML 标记出现过、但一个完整调用都没解析出来时发的 tool_error 文案。native 循环
# (openai_compat.stream_with_mcp_loop)与 text-marker 降级循环(helpers._openai_text_marker_loop)
# 共用;chat 层据 tool_error 事件把空回合分诊为「工具调用标记解析不了」,而不是「上游返空」。
DSML_UNPARSED_ERROR = "模型输出了无法解析的工具调用标记,本轮未执行"

# 清残渣用:任何一枚 DSML 标签(开/闭),包括丢了 `<` 的半截(生产 2296:`｜DSML｜parameter name=…>`)。
_ANY_TAG = re.compile(r"<?\s*/?\s*" + _BAR + r"\s*DSML\s*" + _BAR + r"[^>\n]{0,120}>", re.I)
# 流尾可能停在开标签的半截上(`<`、`<｜`、`<｜DS`…),先扣住别外发。
_PARTIAL_HEAD = re.compile(r"<\s*/?\s*(?:[｜|]+\s*(?:D(?:S(?:M(?:L\s*[｜|]*\s*\w*)?)?)?)?)?$")


def _param_value(attrs: str, raw: str) -> Any:
    """DSML 用 string="true" 标原样字符串,string="false" 标 JSON 值;没标就尽量按 JSON 读。"""
    raw = raw.strip()
    m = re.search(r"string\s*=\s*\"(\w+)\"", attrs or "")
    if m and m.group(1).lower() == "true":
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def parse_invokes(block: str) -> list[tuple[str, dict[str, Any]]]:
    """从一段 DSML 里取出全部完整的 invoke → [(工具名, 参数)]。"""
    calls: list[tuple[str, dict[str, Any]]] = []
    for m in _INVOKE.finditer(block):
        args = {p.group(1).strip(): _param_value(p.group(2), p.group(3)) for p in _PARAM.finditer(m.group(2))}
        calls.append((m.group(1).strip(), args))
    return calls


def strip_dsml(text: str) -> str:
    """去掉完整 invoke 与零散 DSML 标签后剩下的正文。停在半截的 invoke(max_tokens 截断)整段丢弃。"""
    t = _INVOKE.sub("", text)
    m = _INVOKE_OPEN.search(t)
    if m:
        t = t[: m.start()]
    return _ANY_TAG.sub("", t)


def resolve_tool_ref(name: str, tools: list[dict[str, Any]] | None) -> tuple[str, str]:
    """模型写的工具名 → (server_id, tool)。

    text-marker 的工具清单按 `server/tool` 列出,模型照抄进 DSML(#106 就是
    `dispatcher/list_worldbook_entries`);native 路径发的是 `server__tool`。都认。
    只写了裸工具名时按清单反查 server_id。
    """
    name = (name or "").strip()
    for sep in ("__", "/"):
        if sep in name:
            sid, _, tool = name.partition(sep)
            return sid.strip(), tool.strip()
    for t in tools or []:
        if str(t.get("name", "")) == name:
            return str(t.get("server_id", "")), name
    return "", name


class DsmlStreamFilter:
    """按增量喂模型输出;返回此刻可以外发的正文,DSML 块扣下解析。

    用法:每个 chunk 调 feed(),流结束调 finish();解析出的调用在 .calls。
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_block = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.seen = False  # 是否出现过 DSML(调用方据此打日志)

    def _take_block(self, block: str) -> str:
        self.calls.extend(parse_invokes(block))
        rest = strip_dsml(block)
        return rest if rest.strip() else ""

    def feed(self, chunk: str) -> str:
        self._buf += chunk or ""
        out = ""
        while True:
            if not self._in_block:
                m = _BLOCK_START.search(self._buf)
                if m:
                    out += self._buf[: m.start()]
                    self._buf = self._buf[m.start():]
                    self._in_block = self.seen = True
                    continue
                # 末尾若是开标签的半截,扣住等下一个 chunk
                lt = self._buf.rfind("<")
                if lt != -1 and len(self._buf) - lt <= 32 and _PARTIAL_HEAD.match(self._buf[lt:]):
                    out += self._buf[:lt]
                    self._buf = self._buf[lt:]
                else:
                    out += self._buf
                    self._buf = ""
                return out
            m = _BLOCK_END.search(self._buf)
            if not m:
                return out
            out += self._take_block(self._buf[: m.end()])
            self._buf = self._buf[m.end():]
            self._in_block = False

    def finish(self) -> str:
        buf, self._buf = self._buf, ""
        if self._in_block:
            # 没有最外层收尾(模型省了,或被截断):完整的 invoke 照收,其余残渣清掉
            self._in_block = False
            return self._take_block(buf)
        return buf
