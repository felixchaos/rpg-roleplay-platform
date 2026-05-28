"""state/time_ops.py — 时间相关 helpers (_gm_is_asking_for_time_confirm, _clean_time_value, _looks_like_time_value, _format_pending_timeline, _phase_for_time)"""
from __future__ import annotations

from timeline_state import clean_time_value, looks_like_time_value


_ASKING_FOR_CONFIRM_PATTERNS = (
    r"是否(?:要|要不要|确认|继续|推进|跳到|跳转)",
    r"请(?:玩家|你)?(?:确认|选择|决定|回答)",
    r"等(?:待|待玩家)?(?:玩家|你)?(?:确认|选择|决定|回答|回应)",
    r"待确认",
    r"awaiting[_ ]?(?:gm|player)?[_ ]?confirm",
    r"pending[_ ]?confirm",
    r"询问玩家",
    r"向玩家提问",
    r"先(?:让|请)?(?:子代理|GM|你)?(?:检查|确认|核对)",
    r"不要(?:直接|立即)?(?:跳过|改写|锁定)",
)


def _gm_is_asking_for_time_confirm(gm_response: str, tags: list[str]) -> bool:
    """task 22 + task 32：判断 GM 这一轮是在询问/标 pending，而不是在锁定时间。

    task 32 真实案例：GM 同时输出了 `【时间跳跃确认：待确认（当前处于 pending_confirmation 状态）】`
    和 `【询问玩家：...】`/`【设定校验：冲突】`。原 task 22 实现一旦看到任何含
    "时间跳跃确认" 的标签就立刻 return False，把后面所有"等待玩家回答""冲突"信号全无视，
    导致主 GM 锁定时间线。

    新规则（更保守）：
      1. 先扫一遍 tags 把信号分类：
         - has_explicit_confirm  ← "时间跳跃确认" 且 value 里没有 pending/待确认 等回退措辞
         - has_pending_signal    ← 任一意图标 OR "时间跳跃确认" 的 value 含 pending/待确认 OR "等待玩家"等
      2. 正文里如果命中 _ASKING_FOR_CONFIRM_PATTERNS → 也算 pending 信号
      3. has_pending_signal 优先于 has_explicit_confirm（user 报告里两者会同时出现）
    """
    import re
    blob = gm_response or ""
    has_explicit_confirm = False
    has_pending_signal = False

    pending_value_markers = ("待确认", "未确认", "暂不", "暂缓", "pending", "awaiting")
    pending_tag_keywords = (
        "询问玩家", "向玩家提问", "澄清问题",
        "时间跳跃待确认", "时间提案", "时间冲突",
        "设定冲突", "设定校验",  # 冲突/校验通常表示"先不要写入"
        "等待玩家回答", "等待玩家",
    )

    for tag in tags or []:
        if not tag:
            continue
        # 把 "key：value" 拆开看 value
        if "：" in tag:
            _key, _val = tag.split("：", 1)
        elif ":" in tag:
            _key, _val = tag.split(":", 1)
        else:
            _key, _val = tag, ""
        if "时间跳跃确认" in _key or "时间跳跃确认" in tag:
            val_low = _val.lower()
            # value 里出现"待确认/pending/awaiting"=不是真的同意确认
            if any(m in _val for m in pending_value_markers) or any(m in val_low for m in pending_value_markers):
                has_pending_signal = True
            else:
                has_explicit_confirm = True
            continue
        if any(kw in tag for kw in pending_tag_keywords):
            has_pending_signal = True

    if not has_pending_signal:
        for pat in _ASKING_FOR_CONFIRM_PATTERNS:
            if re.search(pat, blob, flags=re.IGNORECASE):
                has_pending_signal = True
                break

    # 关键决定：pending 信号优先；只有完全没有 pending 信号且有显式 confirm 才视为真确认
    if has_pending_signal:
        return True
    if has_explicit_confirm:
        return False
    # 兼容老返回值：纯正文询问也算 asking
    return False


def _clean_time_value(text: str) -> str:
    return clean_time_value(text)


def _looks_like_time_value(value: str) -> bool:
    return looks_like_time_value(value)


def _format_pending_timeline(pending: dict | None) -> str:
    if not pending:
        return "无"
    return f"{pending.get('from', '')} → {pending.get('to', '')}"


def _phase_for_time(time_desc: str) -> str:
    """从时间描述推断 phase 标签。

    通用 fallback:任何剧本都用 "玩家分支" 这个中性标签。
    真实的 phase 解析走 rpg/script_timeline.py 的 resolve_timeline_anchor —
    在 chat handler 里把 anchor.story_phase 写到 state.world.timeline.current_phase,
    覆盖本函数的 fallback。

    之前这里 hardcoded 柏林剧本专有词("柏林/图卢兹/哈布斯堡/北城/内城/基地"
    → "柏林暗流篇"),完全无法泛化到别的剧本。已删。
    """
    return "玩家分支"
