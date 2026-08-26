"""阶梯化工具加载(tiered / progressive tool disclosure)的 provider 无关逻辑。

动机:把 91 个工具的完整 JSON schema 每轮全发给 LLM ≈ 9.5k token/轮,而酒馆/游戏大多数
回合根本不调工具。改成:只把「窗口内」(按 _rank 排序的前 N 个)完整 schema 发出去,其余进
一个 `tiered__load_tools` 元工具的目录描述里(name + 一句话),模型需要时先 load 再调用。
目录是稳定前缀 → 被各 provider 的前缀缓存命中,近乎免费。

三个 backend(openai_compat / anthropic / vertex)各自的「原生工具格式」和「循环装回」不同,
但「切窗口 / 建目录 / 解析 load 请求」这部分是一样的 —— 抽到这里,避免三处各写一遍、各踩一遍坑。
"""
from __future__ import annotations

import re
from typing import Any

SEP = "__"  # server_id 与 tool_name 的编码分隔符(三 backend 一致)
LOAD_TOOLS_SERVER = "tiered"
LOAD_TOOLS_TOOL = "load_tools"
LOAD_TOOLS_FULL_NAME = LOAD_TOOLS_SERVER + SEP + LOAD_TOOLS_TOOL

# load_tools 元工具的入参 schema(三 backend 共用)
LOAD_TOOLS_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要加载的工具完整 name(取自目录里列出的 name)",
        },
    },
    "required": ["names"],
}


def tool_full_name(t: dict[str, Any]) -> str:
    """unified tool({server_id,name,...}) → provider 工具名(safe 编码 + 截到 64)。

    三 backend 都用这套编码,保证目录里登记的 name 与各自 _mk 出来的 name 完全一致,
    模型按目录里的 name 调 load_tools 才能命中。"""
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(t.get("server_id", "")))
    tname = re.sub(r"[^A-Za-z0-9_-]", "_", str(t.get("name", "")))
    return f"{sid}{SEP}{tname}"[:64]


# 主回合闭环 + 首要读之外再留的机动名额。v1.82.1:首要读已经自己成一档(tier 1)并被
# 下面的不变量覆盖,所以这里从 4 降到 2 —— 名额不再靠「留几个然后字母序抢」来分配。
CORE_READ_RESERVE = 2
# 自动定容的上限:超过它宁可让工具掉进目录,也不让每轮 schema 无限膨胀(会 warn)。
MAX_AUTO_WINDOW = 32
# tier <= 这个值的工具视为「主回合必须直发」,不许掉进 load_tools 目录。
# 1 = 主回合闭环(0)+ 主回合首要读(1)。定义见 tools_dsl/chat_tool_router.classify_tool。
_CRITICAL_TIER = 1


def effective_window(mcp_tools: list[dict[str, Any]], window: int) -> int:
    """按不变量给直发窗口定容:主回合闭环工具**必须全部直发**,再留 CORE_READ_RESERVE。

    在此之前窗口是个手写常量(默认 18),而「多少个工具是闭环必需的」随模式(酒馆/小说)、
    随是否绑定剧本而变。于是每加一族工具就要有人重新手算一次「窗口需 ≥ 5 + rank0 + 3」——
    已经算过三次,每次都是有工具掉出窗口、玩家报「这个功能 GM 从来不用」之后才补算的。
    这里让它自己算:配置值当**下限**,不变量当**底线**,两者取大。
    """
    critical = sum(1 for t in mcp_tools
                   if isinstance(t.get("tier"), int) and t["tier"] <= _CRITICAL_TIER)
    if not critical:
        return window  # 全是 MCP 工具(没有 tier)→ 维持配置值
    need = critical + CORE_READ_RESERVE
    if need <= window:
        return window
    if need > MAX_AUTO_WINDOW:
        import logging
        logging.getLogger("gm.tiered").warning(
            "[tiered] 主回合闭环工具 %d 个 + 保留 %d 超过自动定容上限 %d,"
            "仍有闭环工具会掉进 load_tools 目录", critical, CORE_READ_RESERVE, MAX_AUTO_WINDOW)
        return MAX_AUTO_WINDOW
    return need


def split_window(
    mcp_tools: list[dict[str, Any]],
    window: int,
    enabled: bool,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    """把已排序的 unified 工具表切成「窗口内 / 目录」。

    Returns:
        window_tools: 直接发完整 schema 的工具(unified dict,前 N 个,
                      N = effective_window(mcp_tools, window))。
        overflow_index: {full_name: unified tool} —— 目录里、可被 load_tools 拉起的工具。
        catalog_lines: ["- full_name: 一句话描述", ...] 供拼进 load_tools 描述。

    enabled=False(RPG_TIERED_TOOLS=0)→ 退回旧行为:窗口外直接丢弃(overflow 为空)。
    """
    window = effective_window(mcp_tools, window)
    window_tools = list(mcp_tools[:window])
    overflow_index: dict[str, dict[str, Any]] = {}
    catalog_lines: list[str] = []
    if enabled:
        for t in mcp_tools[window:]:
            if not t.get("server_id") or not t.get("name"):
                continue
            fn = tool_full_name(t)
            overflow_index[fn] = t
            desc = (str(t.get("description") or "").splitlines() or [""])[0][:64]
            catalog_lines.append(f"- {fn}: {desc}")
    return window_tools, overflow_index, catalog_lines


def load_tools_description(catalog_lines: list[str]) -> str:
    """生成 load_tools 元工具的描述(内嵌目录)。"""
    return (
        "本对话还有以下工具未加载。需要用到时,先用本工具按 name 加载"
        "(加载后下一步才能调用)。可加载工具:\n" + "\n".join(catalog_lines)
    )


def is_load_tools(server_id: str, tool_name: str) -> bool:
    return server_id == LOAD_TOOLS_SERVER and tool_name == LOAD_TOOLS_TOOL


def resolve_load(
    args: dict[str, Any],
    overflow_index: dict[str, dict[str, Any]],
    already_loaded: set[str],
) -> tuple[list[dict[str, Any]], str]:
    """解析一次 load_tools 调用。

    Returns:
        newly: 这次新加载、需要 backend 转成原生格式 append 进 tools 的 unified 工具列表
               (已加载过的不重复 append → 维持 append-only,不破坏前缀缓存)。
        ack: 给模型的 tool_result 文本(加载了哪些 / 哪些没找到)。
    """
    want = args.get("names") or []
    if isinstance(want, str):
        want = [want]
    newly: list[dict[str, Any]] = []
    loaded: list[str] = []
    missing: list[str] = []
    for raw in want:
        nm = str(raw)
        t = overflow_index.get(nm)
        if not t:
            missing.append(nm)
            continue
        if nm not in already_loaded:
            newly.append(t)
            already_loaded.add(nm)
        loaded.append(nm)
    ack = ("已加载: " + ", ".join(loaded) + "。下一步可直接调用它们。") if loaded else "没有匹配到可加载的工具。"
    if missing:
        ack += " 未找到: " + ", ".join(missing) + "。"
    return newly, ack
