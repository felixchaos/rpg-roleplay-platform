"""
timeline_state.py - Runtime timeline jump protocol.

Time jumps are a two-step transaction:
1. Player requests a target time -> pending transition.
2. GM must confirm or reject -> locked timeline anchor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TimeDirective:
    target: str
    raw: str


# 叙述性「回忆/梦境/想象」框架:玩家在描述过去/幻想片段(闪回),不是发起时间线跳转。
# 行者无疆 实测:「我继续回想:在进入主神空间前我的母亲独自拉扯我长大」被误判成跳跃。
_RECALL_FRAMING = re.compile(
    r"回想|回忆|想起|记起|忆起|想当年|当年|梦到|梦见|梦回|脑海(?:中|里)|浮现|想象|设想|幻想"
)


def is_recall_framing(text: str) -> bool:
    """玩家是否在做回忆/闪回/幻想叙述(而非发起时间线跳转)。供确定性检测 + LLM 子代理跳跃门控共用。"""
    return bool(_RECALL_FRAMING.search(text or ""))


# 显式跳跃动词表(与下方 patterns[0] 同源;单独抽出来供 LLM 侧门控复用)。
_JUMP_VERB = re.compile(
    r"跳到|跳转到|快进到|切到|来到|推进到|过渡到|直接到|直接进入|进入|等到|等至|直到"
    r"|跳过到|略过到|越过到|/time\b|/timeline\b"
)
# 「整条输入就是一个时点」——玩家只打了「第30章」「1968年」「第30章 中秋夜」。
# 这是**唯一**允许省略跳跃动词的形态,见 detect_time_directives 的说明。
_BARE_TARGET = re.compile(
    r"(?:第\s*\d{1,5}\s*章[^，。！？\n]{0,24}"
    r"|(?:公元)?\d{3,5}\s*年[^，。！？\n]{0,24})"
)
_STRIP_EDGE = " \n\t：:。.，,！!？?、;；"


def has_jump_verb(text: str) -> bool:
    """输入里有没有显式跳跃动词 / 时间线斜杠命令。"""
    return bool(_JUMP_VERB.search(text or ""))


def is_bare_time_target(text: str) -> bool:
    """整条输入(去掉首尾标点空白后)恰好就是一个时点值。"""
    return bool(_BARE_TARGET.fullmatch((text or "").strip(_STRIP_EDGE)))


def mentions_time_without_intent(text: str) -> bool:
    """文本里提到了年份/章号,但**既没有跳跃动词、整条输入也不只是那个时点**
    → 这是叙述里顺带提到时间(投喂设定、写背景、讲人物生平),不是跳跃指令。

    行者无疆 实测(v1.73.2):推剧情时粘了一段 NPC 背景「起源故事: 1968年,年幼的威廉…
    参加了天空景观餐厅的开幕派对」,被判成跳转到 1968 年。
    """
    t = text or ""
    if not _BARE_TARGET.search(t):
        return False
    return not has_jump_verb(t) and not is_bare_time_target(t)


def detect_time_directives(text: str) -> list[TimeDirective]:
    t = text or ""
    # 闪回/回忆/幻想叙述 → 不当作跳跃指令(玩家在讲记忆,不是要把时间线跳过去)。
    if _RECALL_FRAMING.search(t):
        return []
    # 误判族第四案(v1.73.2,行者无疆:粘一段含「1968年」的 NPC 背景 → 被判跳转到 1968 年)。
    # 病灶:原 patterns 里「第N章」「N年」两条把跳跃动词写成**可选**前缀(`(?:跳到|…)?`),
    # 于是它们不再是「指令检测器」,而退化成「文本里出现章号/年份检测器」——任何叙述、
    # 设定投喂、人物生平只要带个年份就触发。而这两条在有动词时本就被 patterns[0] 覆盖
    # (动词表是它的子集),**唯一**的独立价值是玩家只打一个裸时点(「第30章」)。
    # 修:动词形态只留 patterns[0]/[1];裸时点单独走 fullmatch 判定(整条输入就是时点)。
    # 口径仍是宁漏勿误:漏检有 GM 确认两步事务兜底,误报直接把玩家的剧情推歪。
    patterns = [
        r"(?:时间线|时间|剧情|镜头|场景)?\s*(?:跳到|跳转到|快进到|切到|来到|推进到|过渡到|直接到|直接进入|进入|等到|等至|直到|跳过到|略过到|越过到)\s*([^，。！？\n]{2,48})",
        r"(?:/time|/timeline)\s+([^\n]{2,80})",
    ]
    out: list[TimeDirective] = []
    for pattern in patterns:
        for match in re.findall(pattern, t):
            target = clean_time_value(match)
            if looks_like_time_value(target) and target not in [x.target for x in out]:
                out.append(TimeDirective(target=target, raw=text))
    if not out and is_bare_time_target(t):
        target = clean_time_value(t.strip(_STRIP_EDGE))
        if looks_like_time_value(target):
            out.append(TimeDirective(target=target, raw=text))
    return out


def clean_time_value(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text).strip(" \n\t:：-—")).strip()
    value = re.sub(r"^(?:到|至|在)\s*", "", value)
    value = re.sub(r"(?:后?再)?(?:行动|出发|继续|调查|处理|会合|潜入|开场|开始)$", "", value)
    return re.sub(r"\s+", " ", value.strip(" \n\t:：-—")).strip()


# 时间形状 token(误判族第三案,行者无疆「进入后先用真气感知四周环境」被判跳跃后根治):
# 旧实现按【单字符】判时间值(日|天|周|后|前…),中文里根本不可靠——「四周环境」的
# 周=surroundings、「后先用真气」的后=then、「第二次」的次=times,全是假阳性源。
# 根修=必须命中【时间形状的 token】:数量+单位+后/前(三天后)、第N章/天、年份、
# 时段词(清晨/傍晚/翌日…)。宁漏勿误(同 weekday 验错器口径):漏检由 GM 确认
# 两步事务 + LLM curator 路径兜底,误报则直接骚扰玩家,代价不对称。
_TIME_TOKEN = re.compile(
    r"(?:\d+|[一二两三四五六七八九十百千]+|几|半|数)\s*个?\s*"
    r"(?:秒|分钟|小时|时辰|日|天|夜|晚|周|星期|月|年|载)\s*(?:之|以)?[后前]"  # 三天后/3小时前
    r"|第\s*(?:\d{1,5}|[一二两三四五六七八九十百千]+)\s*(?:章|天|日|夜|周|月|年)"  # 第3章/第二天
    r"|(?:公元)?\d{3,5}\s*年"
    r"|(?:\d+|[一二两三四五六七八九十百千]+)\s*点(?:钟|半|整)?"  # 八点/8点半
    r"|翌日|次日|明日|明天|明早|明晚|今晚|今夜|当晚|当夜|当天|次晨|隔天|隔日|来日"
    r"|清晨|黎明|拂晓|破晓|凌晨|早晨|早上|上午|正午|中午|午后|下午|黄昏|傍晚|晚上|入夜|深夜|午夜|半夜|夜里|夜晚|白天"
    r"|日出|日落|天亮|天黑|开春|入冬|春天|夏天|秋天|冬天|春季|夏季|秋季|冬季|雨季|旱季"
    r"|(?:数|多)(?:日|天|月|年)(?:之|以)?后"
    r"|片刻(?:之|以)?后|不久(?:之|以)?后|须臾"
    r"|柏林|图卢兹|基地"  # 本书(MuMu)时间线标签是地名制,保留既有行为
)


def looks_like_time_value(value: str) -> bool:
    if not (2 <= len(value) <= 80):
        return False
    # 时间锚点描述「何时」,不含人称主语。含「我/你/他/她/它/我的…」基本是被贪婪捕获的叙事从句
    # (如「主神空间前我的母亲独自拉扯我长大」——『前』命中但其实是回忆从句),否决,避免误判跳跃。
    if re.search(r"[我你他她它咱您俺]", value):
        return False
    return bool(_TIME_TOKEN.search(value))


def is_time_key(key: str) -> bool:
    return any(marker in key for marker in ("当前时间线", "时间线", "当前时间", "时间跳转", "时间推进", "跳转时间", "时点"))
