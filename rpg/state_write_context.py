"""
state_write_context.py — task 87 Phase 6: chat 上下文 contextvar

chat handler 在调 state.apply_structured_updates / state.apply_state_write 之前
设置上下文,state 内部从 contextvar 拿 user_id/save_id/trace_id,把"GM JSON op
直接调 apply_state_write"路径转 dispatcher 工具调用。

为什么用 contextvar:
  · state 是纯数据容器,不持有 user_id/save_id
  · 不想给每个 state 方法加新参数 (破坏所有调用方)
  · chat handler 是协程,contextvars 自动隔离不同请求

陷阱:
  · 测试时记得 clear,否则上下文跨测试泄漏
  · 仅在 chat 路径内有效,demo.py CLI / 单元测试不会设置 → 走老路径
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass
class ChatWriteContext:
    user_id: int
    save_id: int | None
    script_id: int | None
    trace_id: str
    # task 87 Phase 6: GM JSON op 与 GM native tool_use 来自同一 LLM 回复,
    # 语义一致 (都是"GM 在叙事时想改状态"),所以共用 origin="llm_chat"。
    # 区分通过 trace_id 前缀 ("gm-" / "gm-jsop-") 在 audit_log 里看出。
    origin: str = "llm_chat"


_current: contextvars.ContextVar[ChatWriteContext | None] = contextvars.ContextVar(
    "rpg_chat_write_context", default=None,
)


def set_context(ctx: ChatWriteContext | None) -> contextvars.Token:
    """设上下文,返回 token 用于 reset。"""
    return _current.set(ctx)


def get_context() -> ChatWriteContext | None:
    return _current.get()


def clear_context(token: contextvars.Token) -> None:
    _current.reset(token)


__all__ = ["ChatWriteContext", "set_context", "get_context", "clear_context"]
