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


# ── 时间连续性护栏 v0(天数倒退检测)─────────────────────────────────────
# 哲学同星期验错器(a80ef39d8):确定性检测→surface 提示,不拦截不改写;标签里
# 解析不出「第N天」就休眠(玄幻/无天数计法的存档零副作用)。

_DAY_NUM_RE = None  # 惰性编译

_CJK_DIGITS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
               "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cjk_to_int(s: str) -> int | None:
    """常见中文数字→int(支持到 999:三/十/二十三/一百零五/首)。解析不出返 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    total = 0
    section = 0
    num = 0
    for ch in s:
        if ch in _CJK_DIGITS:
            num = _CJK_DIGITS[ch]
        elif ch == "十":
            section += (num if num else 1) * 10
            num = 0
        elif ch == "百":
            section += (num if num else 1) * 100
            num = 0
        else:
            return None
    total = section + num
    return total if total > 0 or s == "零" else None


def _day_number(label: str) -> int | None:
    """从时间标签抽「第N天」的 N。抽不出(无天数计法)返 None=护栏休眠。"""
    global _DAY_NUM_RE
    import re as _re
    if _DAY_NUM_RE is None:
        _DAY_NUM_RE = _re.compile(r"第([0-9零一两二三四五六七八九十百]+)天")
    m = _DAY_NUM_RE.search(label or "")
    if not m:
        return None
    return _cjk_to_int(m.group(1))


def detect_day_regression(old_label: str, new_label: str) -> str | None:
    """新时间标签的天数计数比旧的小 → 返回警示文案;否则 None。

    只在新旧标签【都】带可解析的「第N天」时才判(缺任一侧=休眠,零误伤);
    倒退是强信号(梦境/回忆通常不重置天数计法,生产 t18「第四天·入夜⟶梦境」
    后仍是「第五天」)。文案不拦截:有意的设定回调(玩家 /set 改写)可忽略。
    """
    old_day = _day_number(old_label or "")
    new_day = _day_number(new_label or "")
    if old_day is None or new_day is None:
        return None
    if new_day < old_day:
        return (
            f"时间疑似倒退(第{old_day}天 → 第{new_day}天)。"
            "若非玩家有意的设定回调,建议 /retry 或用 /set 纠正时间线。"
        )
    return None
