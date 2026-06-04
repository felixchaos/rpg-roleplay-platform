"""agents.gm.style_harness — GM 叙事「倾向性」线性可调底座(Phase 1)。

背景(用户需求):GM 的行为规则里,有一类是**审美倾向**(回应篇幅、镜头焦点、戏剧
密度、心理补写、悬念强度、剧情引导力度),原先以一刀切的硬编码写死在 _SYSTEM_BASE,
"一棒子打死",无法因人/因局调整。本模块把这 6 个维度抽成**线性旋钮**(0-100),由
旋钮值**确定性地**渲染出对应的提示词片段(数值插值 + 分级措辞),而非靠提示词求 LLM
"听话"。安全/正确性铁律(专名忠实、防剧透、信息不对称、玩家自主权、世界线必发生、
NSFW)不在本模块,永远硬编码。

Phase 1 只做:旋钮 schema + render_style_block + 四层 resolve 占位 + 默认值复刻现状。
默认 profile 渲染出的「# 叙事风格(本局)」段,在语义与关键数字上等价于原硬规则,
保证零回归。Phase 2 接配置存储(script_overrides.gm_style / 用户级 / 存档级),
Phase 3 接前端滑块 + 确定性后处理(按旋钮加权截断/校验)。
"""
from __future__ import annotations

from typing import Any

# ── 旋钮 schema ───────────────────────────────────────────────────────────────
# 每个旋钮:0-100 线性。default 复刻当前硬编码行为。
# lo/hi 给"数值插值"型旋钮的两端取值(如篇幅字数);levels 给"分级措辞"型旋钮的档位文案。

KNOBS: dict[str, dict[str, Any]] = {
    # 正文篇幅:数值插值。默认 45 → 约 300-450 字(复刻"≥300字实质推进"绑定规则)。
    "reply_length": {
        "default": 45,
        "lo": 120,   # knob=0  → ~120 字下限锚
        "hi": 900,   # knob=100 → ~900 字上限锚
    },
    # 镜头焦点(#28 维度):玩家动作 vs 对方反应的篇幅占比。默认 15 → 强对方反应优先。
    "player_action_focus": {"default": 15},
    # 戏剧密度:镜像玩家 vs 允许放大。默认 35 → 镜像不放大(复刻 task131 规则)。
    "drama_density": {"default": 35},
    # 心理/潜台词补写:字面处理 vs 主动补内心戏。默认 20 → 字面处理(复刻"不补潜台词")。
    "interiority": {"default": 20},
    # 结尾悬念强度:平稳收束 vs 强钩子留白。默认 60 → 有张力的留白(复刻留白铁律)。
    "cliffhanger": {"default": 60},
    # 剧情引导力度:高自由 vs 强收束往锚点引。默认 60 → 主动推进+适度收束。
    "guidance_force": {"default": 60},
}


def default_profile() -> dict[str, int]:
    return {k: int(v["default"]) for k, v in KNOBS.items()}


def _clamp(v: Any, lo: int = 0, hi: int = 100) -> int:
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def normalize_profile(profile: dict[str, Any] | None) -> dict[str, int]:
    """把任意(可能缺键/越界/None)profile 归一到完整、0-100 的 6 维 dict。缺键取默认。"""
    out = default_profile()
    if isinstance(profile, dict):
        for k in KNOBS:
            if k in profile and profile[k] is not None:
                out[k] = _clamp(profile[k])
    return out


def validate_patch(patch: Any) -> dict[str, int]:
    """校验前端/API 传来的 gm_style patch:只接受已知 6 键、值归一到 0-100 整数,
    未知键忽略。用于写端点入参清洗。空/非 dict → ValueError。"""
    if not isinstance(patch, dict):
        raise ValueError("gm_style 必须是对象(6 个旋钮 key → 0-100 整数)")
    out: dict[str, int] = {}
    for k, v in patch.items():
        if k in KNOBS and v is not None:
            out[k] = _clamp(v)
    return out


def _lerp(lo: int, hi: int, knob: int) -> int:
    return int(round(lo + (hi - lo) * (knob / 100.0)))


def _band(knob: int) -> int:
    """0-100 → 档位 0..4(五档),供分级措辞选择。"""
    return min(4, knob // 20)


# 各维度的分级措辞(五档:0-19 / 20-39 / 40-59 / 60-79 / 80-100)。
# 索引 = _band(knob)。默认值落在的那一档即"复刻现状"的文案。

_FOCUS_LEVELS = [
    # default 15 → band 0:强对方反应优先(复刻镜头铁律行162)
    "正文主体写【对方 NPC 与世界对玩家行动的反应、回应与后果】——神态、话语、肢体、情绪与立场变化、环境/局势变动;玩家自己的动作至多一两句承接带过,绝不替玩家加台词、加心理、加他没写的后续动作。玩家写得越短,越说明他在等对方反应,越不能把篇幅耗在给玩家动作加戏。",
    "正文以对方反应与后果为主体,玩家动作简短承接即可,不替玩家延展未写的动作或心理。",
    "玩家动作的承接与对方反应大致各半:既如实推进对方的回应,也可对玩家的动作做一两句有质感的描摹,但不替玩家做未写的决定。",
    "可以较细致地描摹玩家这一动作的展开、力度与质感,再写对方与环境的回应;仍不替玩家补未写的台词或决定。",
    "重点铺陈玩家这一行动本身的过程、细节与张力,把对方反应作为衬托;但绝不替玩家做未授权的后续决定。",
]

_DRAMA_LEVELS = [
    "戏剧密度严格镜像玩家本回合输入:玩家轻描淡写你也轻描淡写,玩家剧烈你才剧烈。不因检索注入的原文戏剧浓度高就升级当前场景;玩家括号里的状态词按字面处理('(昏迷)'=晕过去,不是濒死)。",
    # default 35 → band 1:基本镜像、略放大(复刻 task131"镜像而非放大")
    "戏剧密度基本镜像玩家输入,只在情节自然需要时极克制地加一点张力;不把日常场景升级成原文式的极端事件。",
    "戏剧密度可比玩家输入略高一档:在合理处主动加入张力与转折,但不脱离当前情境的真实强度。",
    "允许较明显地放大戏剧张力:主动制造冲突、转折与情绪起伏,推动场面更有戏。",
    "高戏剧张力:主动升级冲突与情绪强度,追求强烈的戏剧效果(注意仍不违背设定与人物)。",
]

_INTERIORITY_LEVELS = [
    "只描写外部可观察的事实(动作、表情、话语、环境),不替玩家或 NPC 补写没有依据的内心独白、潜台词或'情绪暗涌'。",
    # default 20 → band 1:字面为主,极少内心(复刻"不补潜台词")
    "以外部描写为主,仅在有明确依据时点到极少量人物情绪,不展开内心独白。",
    "适度补写 NPC 的内心活动与潜台词,帮助塑造人物;玩家内心仍以玩家所写为准。",
    "较多刻画 NPC 的心理、动机与潜台词,让人物更立体。",
    "深入铺陈 NPC 的内心戏与潜台词层次;但玩家角色的内心仍只反映玩家本人所写。",
]

_CLIFF_LEVELS = [
    "结尾平稳收束这一节拍,不刻意制造悬念。",
    "结尾给一个轻微的余韵或未尽之意。",
    "结尾留一个温和的张力点,让玩家自然有接话的方向。",
    # default 60 → band 3:有张力的留白(复刻留白铁律行158)
    "推进完这一轮后,用一个有张力的场景节拍收尾——NPC 的动作、环境变化、一句未尽的话、一个逼近的危机——让玩家自然接话;绝大多数回合不要在结尾显式问'你接下来想怎么做'这类话,只有真正的分叉抉择才用结构化 question 弹窗给选项。",
    "结尾抛出强钩子:一个迫近的危机、一句惊人的话或一个突变,制造强烈的悬念把玩家拽进下一轮(仍不在正文里显式反问玩家要怎么做)。",
]

_GUIDANCE_LEVELS = [
    "高自由度:跟随玩家的行动自然展开,不主动把剧情往原著锚点拉;仅在玩家明显卡住时给一点方向。",
    "以玩家为主,偶尔顺势把剧情往待发生锚点的方向轻推。",
    "主动推进剧情:每轮先让世界/NPC 行动起来,并适度往最近的待发生锚点引导。",
    # default 60 → band 3:主动推进+收束(复刻推进铁律行157+收束行88)
    "每轮先根据剧情流程把这一轮推进了(NPC 行动/事件发生/世界反应),不把'该怎么发展'丢回给玩家;玩家偏离待发生锚点时,1-3 轮内用巧合/误会/他人介入/环境压力等命运式手段把剧情拉回最近锚点,让玩家感觉不到强引导但锚点照样发生。",
    "强收束:连续主动地把剧情往待发生锚点推进,显著降低自由度,确保关键节点尽快发生(适用于 drift 过高需要强力拉回时)。",
]


def render_style_block(profile: dict[str, Any] | None) -> str:
    """旋钮 profile → 注入 system prompt 的「# 叙事风格(本局)」段。

    默认 profile 渲染出的内容,语义等价于 master.py 原先散落的 6 条硬规则
    (篇幅/镜头/戏剧密度/心理/悬念/引导),并带相同量级的字数目标。
    """
    p = normalize_profile(profile)
    target = _lerp(KNOBS["reply_length"]["lo"], KNOBS["reply_length"]["hi"], p["reply_length"])
    # 篇幅下限:默认档保留"实质推进、不敷衍"的硬约束(≥ 约 0.7*target)
    floor = int(round(target * 0.7))
    lines = [
        "# 叙事风格(本局可调倾向 — 由玩家偏好确定,不影响下方安全/正确性铁律)",
        f"- 正文篇幅:本轮正文目标约 {floor}-{target} 字的【实质推进】,要有场景、动作、对白、感官细节,推动情节明显往前走;严禁一两句话敷衍后立刻甩选项。",
        f"- 镜头焦点:{_FOCUS_LEVELS[_band(p['player_action_focus'])]}",
        f"- 戏剧密度:{_DRAMA_LEVELS[_band(p['drama_density'])]}",
        f"- 心理与潜台词:{_INTERIORITY_LEVELS[_band(p['interiority'])]}",
        f"- 收尾与悬念:{_CLIFF_LEVELS[_band(p['cliffhanger'])]}",
        f"- 剧情推进与引导:{_GUIDANCE_LEVELS[_band(p['guidance_force'])]}",
    ]
    return "\n".join(lines)


# ── 四层配置归并(Phase 2 接真实存储,Phase 1 先占位返回默认)──────────────────
def resolve_profile(
    user_id: int | None = None,
    script_id: int | None = None,
    save_id: int | None = None,
    *,
    platform_default: dict | None = None,
    user_default: dict | None = None,
    script_override: dict | None = None,
    save_override: dict | None = None,
) -> dict[str, int]:
    """四层归并:平台默认 → 用户默认 → 剧本 override → 存档 override(后者覆盖前者)。

    Phase 1:调用方还没接存储,传入的层都为 None → 返回 default_profile()。
    """
    merged = default_profile()
    for layer in (platform_default, user_default, script_override, save_override):
        if isinstance(layer, dict):
            for k in KNOBS:
                if k in layer and layer[k] is not None:
                    merged[k] = _clamp(layer[k])
    return merged
