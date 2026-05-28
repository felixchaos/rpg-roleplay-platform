"""context_engine.helpers — 小工具函数."""
from __future__ import annotations


def _neutralize_state_write_tags(text: str) -> str:
    """P0 #2：从检索内容里中和 `【状态写入：…】` / `【询问：…】` /
    `【时间推进：…】` 等会被 apply_structured_updates 当作 GM 写状态指令
    的标签。原文如果包含这类装饰括号，主 GM 在转述时会原样复述，
    apply_structured_updates 在 GM 输出上跑 re.findall(r"【([^】]+)】")
    就会把章节里的"假指令"当成真状态写入执行。

    修法：把检索内容里的 `【` `】` 替换成视觉上接近但 GM 解析时不会
    被识别的全形括号（U+FF3B / U+FF3D），保持人类可读的同时切断指令链路。
    """
    if not text:
        return text
    return text.replace("【", "［").replace("】", "］")


def _pending_jump_warning_text(state) -> str:
    """通用 pending_jump 警告：state 含 pending_confirmation 时，GM 必须遵守。
    这是 GM 运行契约的一部分，与 ContentPack 类型无关。
    novel-specific 的 anchor 信息由 NovelTimelineProvider 负责。"""
    data = getattr(state, "data", state) or {}
    timeline = (data.get("world") or {}).get("timeline") or {}
    pending = timeline.get("pending_jump") or {}
    if not pending:
        return ""
    pending_status = str(pending.get("status") or "")
    is_awaiting = pending_status in ("awaiting_gm_confirmation", "awaiting", "pending_confirmation")
    lines = [
        f"玩家请求时间跳跃：{pending.get('from', '')} -> {pending.get('to', '')}",
        f"pending 状态：{pending_status or '未知'}",
    ]
    if is_awaiting:
        lines.extend([
            "⚠ 本轮 anchor_state=pending_confirmation：禁止把玩家请求的未来时间/地点当作已发生的事实。",
            "禁止输出『翌日…』『次日…』『转眼已是…』等任何把场景叙事推进到目标时间的措辞；",
            "禁止输出标签【时间跳跃确认：…】【当前时间线：目标时间】【当前位置：新地点】【时间：目标时间】；",
            "禁止给出『新时间/新地点』场景里的对话、动作、选项；",
            "本轮只允许：① 给出冲突检查；② 列出风险/代价/前置条件；"
            "③ 输出【询问玩家：是否确认跳跃到 <目标时间>？】+ 1-3 个明确选项（确认 / 取消 / 修改目标）；",
            "下一轮若玩家明确回复『确认』或 /confirm，再正式推进时间线和场景。",
        ])
    else:
        lines.extend([
            "本轮必须先处理时间跳跃事务：默认尊重玩家的跳转/改线意图，",
            "接受则写出过渡/落点并输出【时间跳跃确认：目标时间】和【当前时间线：目标时间】；",
            "只有目标完全不可解析时才输出【询问玩家：...】。",
            "在确认前，不要把玩家请求的未来时间当作已经发生；确认后才允许推进场景与更新位置/目标。",
        ])
    return "\n".join(lines)


def _normalize_permission_mode(mode: str) -> str:
    """task 53/54：本地副本，避免循环 import state.py。和 state._normalize_permission_mode 保持同步。"""
    text = str(mode or "").strip().lower()
    mapping = {
        "只读": "read_only", "只读模式": "read_only", "suggest": "read_only",
        "read": "read_only", "read_only": "read_only", "plan": "read_only",
        "默认权限": "default", "default": "default",
        "auto": "auto_review", "自动审查": "auto_review",
        "auto_review": "auto_review", "review": "auto_review",
        "完全访问权限": "full_access", "full": "full_access", "full_access": "full_access",
    }
    return mapping.get(text, "full_access")


def _permission_label(mode: str) -> str:
    return {
        "read_only": "只读模式（仅叙事）",
        "default": "默认权限",
        "auto_review": "自动审查",
        "full_access": "完全访问权限",
    }.get(_normalize_permission_mode(mode), "完全访问权限")
